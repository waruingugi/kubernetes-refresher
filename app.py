import os, socket
from flask import Flask
import redis, psycopg2


app = Flask(__name__)
POD_NAME = socket.gethostname()
APP_VERSION = "4.0"

r = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"),
                port=int(os.environ.get("REDIS_PORT", 6379)),
                socket_connect_timeout=2
                )

def db():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        connect_timeout=2,
    )

@app.route("/")
def home():
    greeting = os.getenv("GREETING", "Hello from Flask on Kubernetes!")
    try:
        counter = f"Visit #{r.incr('visits')} (shared via Redis)"
    except redis.exceptions.RedisError:
        counter = "Visit counter unavailable"
    return f"{greeting} (v{APP_VERSION})\nServed by pod: {POD_NAME}\n{counter}\n"


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
        return f"Database error: {e}", 500


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

@app.route("/healthz")
def healthz():
    return "ok\n", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)