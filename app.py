import os, socket
from flask import Flask
import redis, psycopg2


app = Flask(__name__)
POD_NAME = socket.gethostname()
APP_VERSION = "5.0"

health = {"live": True, "ready": True} # in-memory switches; reset on container restart
_ballast = [] # for the memory limit demo

r = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                socket_connect_timeout=2)

def db():
    return psycopg2.connect(host=os.environ.get("DB_HOST", "postgres"),
                            dbname=os.environ.get("DB_NAME", "postgres"),
                            user=os.environ.get("DB_USER", "postgres"),
                            password=os.environ.get("DB_PASSWORD", ""),
                            connect_timeout=2)

@app.route("/")
def home():
    greeting = os.environ.get("GREETING", "Hello from Flask on Kubernetes")
    try:
        counter = f"Visit #{r.incr('visits')} (shared via Redis)"
    except redis.exceptions.RedisError:
        counter = "Visit counter unavailable"
    return f"{greeting} (v{APP_VERSION})\nServed by pod: {POD_NAME}\n{counter}\n"


@app.route("/healthz")                    # liveness target
def healthz():
    return ("ok\n", 200) if health["live"] else ("unhealthy\n", 500)

@app.route("/ready")                      # readiness target
def ready():
    return ("ready\n", 200) if health["ready"] else ("not ready\n", 503)


@app.route("/toggle/<what>")              # flip 'live' or 'ready' on this pod
def toggle(what):
    if what in health:
        health[what] = not health[what]
        return f"{what} = {health[what]}\n"
    return "unknown\n", 404

@app.route("/eat/<int:mb>")               # allocate memory, for the OOM demo
def eat(mb):
    _ballast.append(bytearray(mb * 1024 * 1024))
    return f"allocated {mb}MB\n"

@app.route("/notes/add/<text>")
def add_note(text):
    try:
        conn = db()
        with conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, text TEXT)")
            cur.execute("INSERT INTO notes (text) VALUES (%s)", (text,))
        conn.close()
        return f"Saved note: {text}\n"
    except psycopg2.Error as e:
        return f"Database error: {e}\n", 500

@app.route("/notes")
def list_notes():
    try:
        conn = db()
        with conn, conn.cursor() as cur:
            cur.execute("CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, text TEXT)")
            cur.execute("SELECT text FROM notes ORDER BY id DESC")
            rows = cur.fetchall()
        conn.close()
        return "".join(f"- {row[0]}\n" for row in rows) or "No notes yet\n"
    except psycopg2.Error as e:
        return f"Database error: {e}\n", 500


@app.route("/compute")
def compute():
    x = 0
    for _ in range(5_000_000):
        x += 1
    return f"done {x}\n"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)

