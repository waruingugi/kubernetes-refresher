import os
import socket
from flask import Flask
import redis

app = Flask(__name__)
POD_NAME = socket.gethostname()
APP_VERSION = "3.0"

# Connect to Redis by SERVICE NAME - never a IP. Configurable for Module 5
r = redis.Redis(
    host=os.environ.get("REDIS_HOST", "redis"),
    port=int(os.environ.get("REDIS_PORT", "6379")),
    socket_connect_timeout=2,
)

@app.route("/")
def home():
    greeting = os.environ.get("GREETING", "Hello from Flask on Kubernetes!")
    try:
        count = r.incr("visits")
        counter = f"Visit #{count} (shared across all pods via Redis)"
    except redis.exceptions.RedisError:
        counter = "Visit counter unavailable (Redis not reachable)"
    return f"{greeting} (v{APP_VERSION})\nServed by pod: {POD_NAME}\n{counter})\n"

@app.route("/healthz")
def healthz():
    return "ok\n", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)