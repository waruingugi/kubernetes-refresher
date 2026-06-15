import os
import socket
from flask import Flask

app = Flask(__name__)
POD_NAME = socket.gethostname()  # inside a pod, the hostname is the pod name

@app.route("/")
def home():
    greeting = os.environ.get("GREETING", "Hello from Flask on Kubernetes!")
    return f"{greeting}\nServed by pod: {POD_NAME}\n"

@app.route("/healthz")
def healthz():
    return "ok\n", 200

if __name__ == "__main__":
    # 0.0.0.0, NOT 127.0.0.1 - it must accept connections from outside the container
    app.run(host="0.0.0.0", port=5000)