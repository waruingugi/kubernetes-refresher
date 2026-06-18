# Module 7 — Health & Resources: Teaching the Cluster What "Working" Means

> **Hands-on rule:** type every command. This module is built on *manufactured failures* — you'll break the app on purpose and watch Kubernetes react. You won't trust probes until you've seen one restart a wedged pod and another quietly pull a sick pod out of rotation.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner.
>
> **Where we're picking up:** your stack is resilient in two ways now — Deployments heal *deleted* pods (Module 3), and data survives pod death (Module 6). But a gap remains: Kubernetes still assumes "the container process is running" means "the app is fine." It isn't always. A pod can be `Running`, `READY 1/1`, and yet deadlocked, looping, or unable to reach its database — serving errors to your users while Kubernetes looks on, content. This module closes that gap, and adds the resource governance that stops one greedy pod from starving the rest.

---

## Chunk 1 — "Running" is not "working"

By default, Kubernetes' definition of a healthy container is brutally simple: *is the main process still alive?* If your Python process hasn't exited, the pod is `Running` and considered fine. But "the process is alive" and "the app is working" are very different claims. The process can be alive and:

- deadlocked, accepting connections but never responding,
- stuck in an infinite loop, pegging the CPU and serving nothing,
- unable to reach Postgres or Redis, returning 500s to every request.

In all three the pod cheerfully reports `Running`, and the Service keeps routing real users to it. Kubernetes can't know what "working" means for *your* app — only you can. **Probes are how you teach it.** This is the same idea as the `healthcheck:` blocks you wrote in Docker Compose — but Kubernetes splits the concept into three probes answering three genuinely different questions, and the splitting is the whole art.

---

## Chunk 2 — The three probes, and the three questions

| Probe | The question | On failure | Cures |
|---|---|---|---|
| **Liveness** | "Is it alive, or wedged?" | **Restart** the container | deadlocks, hangs, infinite loops |
| **Readiness** | "Is it ready for traffic *right now*?" | **Remove** from Service endpoints (no restart) | warming up, overloaded, a dependency is down |
| **Startup** | "Has it finished booting?" | Restart, but holds off the other two until it passes | slow-starting apps |

Burn one distinction into memory above all others: **liveness *restarts*, readiness *reroutes*.** Liveness is a sledgehammer — fail it and the container is killed and recreated. Readiness is a traffic cop — fail it and you're simply taken out of rotation until you recover, no restart. Confusing the two causes the classic outage: people put a *database check* in the **liveness** probe, so a brief Postgres hiccup makes every Flask pod fail liveness and restart *at once* — turning a two-second blip into a full restart storm. Dependency checks belong in **readiness**, never liveness.

Each probe can check health three ways: `httpGet` (hit an HTTP path — most common for web apps), `tcpSocket` (can we open this port?), or `exec` (run a command inside, success = exit 0). And each has timing knobs: `initialDelaySeconds` (wait this long before the first check), `periodSeconds` (how often), `timeoutSeconds`, `failureThreshold` (how many fails before acting), and `successThreshold`.

---

## Chunk 3 — Evolve the app: controllable health (flaskapp:5.0)

To *demonstrate* probe behavior cleanly, the app needs endpoints we can break and fix on command. Update `app.py` to v5.0 — note the `/healthz` route you planted way back in Module 2 finally gets its purpose:

```python
import os, socket
from flask import Flask
import redis, psycopg2

app = Flask(__name__)
POD_NAME = socket.gethostname()
APP_VERSION = "5.0"

health = {"live": True, "ready": True}   # in-memory switches; reset on container restart
_ballast = []                             # for the memory-limit demo

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

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

Add `curl` to the image so we can poke endpoints from inside a specific pod — update the `Dockerfile`:

```dockerfile
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3 redis==5.0.8 psycopg2-binary==2.9.9
COPY app.py .
EXPOSE 5000
CMD ["python", "app.py"]
```

```bash
docker build -t flaskapp:5.0 .
```

---

## Chunk 4 — Add liveness and readiness probes

Add both probes to the Flask container in `flask-deployment.yaml` (and bump the image to `5.0`):

```yaml
      containers:
      - name: flaskapp
        image: flaskapp:5.0
        imagePullPolicy: IfNotPresent
        envFrom:
        - configMapRef:
            name: flask-config
        env:
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: db-secret
              key: password
        ports:
        - containerPort: 5000
        livenessProbe:
          httpGet:
            path: /healthz
            port: 5000
          initialDelaySeconds: 5
          periodSeconds: 5
          failureThreshold: 3
        readinessProbe:
          httpGet:
            path: /ready
            port: 5000
          initialDelaySeconds: 3
          periodSeconds: 5
          failureThreshold: 3
```

```bash
kubectl apply -f flask-deployment.yaml
kubectl rollout status deployment/flask
kubectl describe pod -l app=flask | grep -A2 -i "liveness\|readiness"
```

Read the probes as English: *liveness* — every 5s, GET `/healthz`; after 3 straight failures, restart the container. *Readiness* — every 5s, GET `/ready`; after 3 failures, stop sending traffic. Now let's break each on purpose.

---

## Chunk 5 — Demo readiness: the gate Module 4 promised

Back in Module 4 I said a pod only becomes a Service endpoint once it's `READY`, and that this is what makes rolling updates zero-downtime. Here's that gate, live. Pick one Flask pod and flip its readiness off from inside it:

```bash
POD=$(kubectl get pod -l app=flask -o jsonpath='{.items[0].metadata.name}')
kubectl exec $POD -- curl -s localhost:5000/toggle/ready
# ready = False
```

**Predict:** does that pod get restarted? Watch (give it ~15s — three failed checks at 5s each):

```bash
kubectl get pods -l app=flask
# NAME                     READY   STATUS    RESTARTS   AGE
# flask-...-abcde          0/1     Running   0          ...   ← READY dropped to 0/1, but NOT restarted
# flask-...-fghij          1/1     Running   0          ...
# flask-...-klmno          1/1     Running   0          ...

kubectl get endpoints flask
# the sick pod's IP is GONE from the list — only the two ready pods remain
```

No restart, no error to users — the pod is simply pulled out of rotation while it says it isn't ready, and the other two replicas absorb the traffic. `curl localhost:8080` never touches the sick pod. Flip it back and it returns:

```bash
kubectl exec $POD -- curl -s localhost:5000/toggle/ready    # ready = True
# within ~5s: READY 1/1 again, and its IP reappears in the endpoints
```

*This* is the readiness gate. During a rolling update it's the same mechanism: a new pod gets zero traffic until its readiness probe passes, and the old pod isn't removed until the new one is ready — which is exactly why Module 3's rollouts dropped no requests.

---

## Chunk 6 — Demo liveness: healing a pod that's up but broken

Module 3 could heal a *deleted* pod. Liveness heals a different ailment — a pod that's running but wedged. Flip liveness off on a pod and watch:

```bash
POD=$(kubectl get pod -l app=flask -o jsonpath='{.items[0].metadata.name}')
kubectl exec $POD -- curl -s localhost:5000/toggle/live    # live = False  → /healthz now returns 500
kubectl get pods -l app=flask -w
```

After three failed liveness checks (~15s):

```
flask-...-abcde   1/1   Running   1   ...   ← RESTARTS went 0 → 1
```

The kubelet restarted the container. Confirm *why* in the events:

```bash
kubectl describe pod $POD | grep -A4 Events
# Warning  Unhealthy  ...  Liveness probe failed: HTTP probe failed with statuscode: 500
# Normal   Killing    ...  Container flaskapp failed liveness probe, will be restarted
```

And notice it healed itself completely: because the `health["live"]` switch lives in memory, the restart reset it to `True`, so the new container is healthy again. That's self-healing at a level Module 3 couldn't reach. Stack the two together and you have real resilience: **Module 3 heals pods that vanish; liveness heals pods that are present but broken.**

---

## Chunk 7 — Startup probes for slow starters

There's a tension hidden in liveness. Imagine an app that takes 60 seconds to boot (loading a big model, warming a cache). If your liveness probe starts checking after 5 seconds, it'll fail repeatedly *during normal startup* and kill the app before it ever finishes — an endless crash loop. You could crank `initialDelaySeconds` up to 90, but then a pod that wedges *after* booting takes 90 seconds to be caught. You can't win with one knob.

The **startup probe** resolves it. It runs first, with a generous budget, and *suspends* liveness and readiness until it succeeds once. After that, the fast liveness/readiness checks take over. Patient at boot, responsive afterward:

```yaml
        startupProbe:
          httpGet:
            path: /healthz
            port: 5000
          failureThreshold: 30
          periodSeconds: 2          # up to 60s (30 × 2s) to start before liveness engages
```

Our Flask app boots instantly, so this is mostly here for recognition — but the rule is worth keeping: **any app with a slow or variable startup wants a startup probe**, so you can keep liveness aggressive without it murdering healthy boots.

---

## Chunk 8 — Resources: requests and limits

Now the other half of the module — stopping one pod from hogging a node. You met this in Docker as `--memory` and `--cpus`; Kubernetes splits it into two numbers per resource, and the split matters enormously. Add a `resources` block to the Flask container:

```yaml
        resources:
          requests:
            cpu: "100m"
            memory: "64Mi"
          limits:
            cpu: "500m"
            memory: "128Mi"
```

- **`requests`** is the *guaranteed reservation*. The **scheduler** uses it to decide whether a node has room: a pod requesting 64Mi only lands on a node with at least 64Mi free, and that amount is then reserved for it. Request too much across your pods and some stay `Pending` with "Insufficient memory" — there's simply nowhere to put them.
- **`limits`** is the *hard ceiling* the container may not exceed.

The behavior at the ceiling is wildly different for the two resources, and this is the single most important thing to internalize:

- **CPU** is measured in *millicores* (`1000m` = 1 full core) and is **compressible** — exceed your CPU limit and you're simply **throttled** (slowed down), never killed. Your app just runs at the speed of its allowance.
- **Memory** is **incompressible** — you can't "slow down" memory. Exceed your memory limit and the kernel **kills the container** (OOMKilled), which the kubelet then restarts.

```bash
kubectl apply -f flask-deployment.yaml
kubectl rollout status deployment/flask
```

---

## Chunk 9 — Demo OOMKilled: the memory ceiling is real

Your Flask pods now have a 128Mi memory limit. Use the `/eat` endpoint to blow past it on one pod, and predict its fate:

```bash
POD=$(kubectl get pod -l app=flask -o jsonpath='{.items[0].metadata.name}')
kubectl exec $POD -- curl -s localhost:5000/eat/200
# the connection drops — the process is killed mid-allocation
kubectl get pods -l app=flask
# the pod shows RESTARTS 1 (or briefly STATUS OOMKilled)
kubectl describe pod $POD | grep -A3 "Last State"
# Last State:  Terminated
#   Reason:    OOMKilled
#   Exit Code: 137
```

`OOMKilled`, exit code 137 — the textbook memory-limit death. The lesson lands hard: a memory limit is a *hard kill*, no negotiation. Contrast it with CPU — a pod stuck in a tight loop would just be throttled and keep limping along, never killed. This is why memory limits are the dangerous ones to set too low: under real load, an under-provisioned pod doesn't slow down, it dies. Right-sizing memory is a real production discipline, not a formality.

---

## Chunk 10 — QoS classes: who gets sacrificed first

Your `requests`/`limits` choices silently assign each pod a **Quality of Service class**, which decides the order pods get evicted when a *whole node* runs out of memory:

- **Guaranteed** — every container sets `requests` *equal to* `limits` for both CPU and memory. Highest priority; evicted last.
- **Burstable** — at least one request is set, but it's not Guaranteed. Middle priority.
- **BestEffort** — no requests or limits at all. First against the wall when a node is under pressure.

```bash
kubectl get pod $POD -o jsonpath='{.status.qosClass}'
# Burstable      (our Flask requests 64Mi but limits 128Mi — request ≠ limit)
```

The practical takeaway: **always set requests and limits on real workloads.** It lets the scheduler place pods sensibly *and* tells the kernel whom to spare when a node is starving. A `BestEffort` pod with nothing set is living on borrowed time — it's the first thing killed when the node gets tight.

---

## Chunk 11 — Seeing actual usage, and the whole resilience picture

To see *live* CPU/memory (the cluster's `docker stats`), you need **metrics-server**, which Docker Desktop doesn't install by default — recall the "Metrics API not available" note from Module 1. To enable it (optional):

```bash
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
# then patch it for Docker Desktop's self-signed kubelet certs:
kubectl patch -n kube-system deployment metrics-server --type=json \
  -p='[{"op":"add","path":"/spec/template/spec/containers/0/args/-","value":"--kubelet-insecure-tls"}]'
```

After a minute:

```bash
kubectl top nodes
kubectl top pods
# NAME              CPU(cores)   MEMORY(bytes)
# flask-...-abcde   2m           48Mi
```

Step back and see what you've assembled. Four independent mechanisms now keep this service genuinely production-grade:

- **Deployment** — recreates pods that are *deleted* or whose *node* dies (Module 3).
- **Liveness probe** — restarts pods that are *present but wedged* (this module).
- **Readiness probe** — routes traffic *around* pods that aren't ready, and gates zero-downtime rollouts (this module + Module 4).
- **Requests/limits** — fair resource sharing, sane scheduling, and an eviction order under pressure (this module).

That combination is the difference between "it runs" and "it stays up."

---

## Chunk 12 — Rare-but-real (recognize, don't memorize)

```yaml
        livenessProbe:
          tcpSocket: { port: 5432 }      # just "can I open the port?" — good for databases
        readinessProbe:
          exec:
            command: ["sh", "-c", "pg_isready -U postgres"]   # success = exit 0
```

- **`terminationGracePeriodSeconds`** — how long a pod gets to shut down cleanly after `SIGTERM` before it's force-killed (default 30s); raise it for apps that need to drain connections.
- **gRPC probes** (`grpc:`) — native health checks for gRPC services.
- **`LimitRange`** and **`ResourceQuota`** — namespace-level objects that set *default* requests/limits and *cap* total usage, so a team can't accidentally consume a whole cluster. These are Module 8 territory.
- **In-place resize** — newer Kubernetes can change a running pod's resources without recreating it; you'll see it referenced as a beta feature.
- **The liveness anti-pattern, one more time** — never check an external dependency (DB, another service) in a liveness probe. Readiness only.

---

## Chunk 13 — Command cheat sheet

| Goal | Command / field |
|---|---|
| Add a liveness probe | `livenessProbe: {httpGet: {path, port}}` → restarts on fail |
| Add a readiness probe | `readinessProbe: {httpGet: {path, port}}` → reroutes on fail |
| Add a startup probe | `startupProbe: {...}` → protects slow boots |
| See a pod's probes | `kubectl describe pod <n>` |
| Reserve resources | `resources.requests: {cpu, memory}` |
| Cap resources | `resources.limits: {cpu, memory}` |
| Find why a pod restarted | `kubectl describe pod <n>` → Events / Last State |
| Check QoS class | `kubectl get pod <n> -o jsonpath='{.status.qosClass}'` |
| Live resource usage | `kubectl top pods` / `kubectl top nodes` (needs metrics-server) |
| See endpoints (readiness gate) | `kubectl get endpoints <svc>` |

---

## Chunk 14 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — break it on purpose**
1. Add a liveness probe (`/healthz`) and a readiness probe (`/ready`) to Flask, and roll out `flaskapp:5.0`. State in one sentence what each probe does *on failure*.
2. Toggle readiness off on one pod. Show that the pod is *not* restarted, that its `READY` column drops, and that its IP leaves the Service endpoints. Explain why users see no errors.
3. Toggle liveness off on one pod. Show the `RESTARTS` count increment and find the event that names the cause. Explain why the pod comes back *healthy* rather than re-failing.
4. Explain the outage that results from putting a Postgres connectivity check in the *liveness* probe, and where that check belongs instead.

**Challenge B — resource governance**
1. Give Flask `requests` of 64Mi/100m and `limits` of 128Mi/500m. Explain which number the scheduler uses to place the pod, and what happens if no node can satisfy it.
2. Use `/eat/200` to exceed the memory limit on one pod. Name the status and exit code you expect, and explain why the same overshoot on *CPU* would behave completely differently.
3. Report the pod's QoS class and explain what would make it `Guaranteed` instead.

**Bonus question (mental model):** A pod is `Running` and `READY 1/1`, yet users report errors. Then a second pod is `Running` but `READY 0/1`, and users report *nothing* wrong. Explain, using probes, how both situations are possible — and describe the probe configuration that would have caught the first case. Then name all four mechanisms now protecting the stack and the distinct failure each one handles.

---

*End of Module 7. Next: Module 8 — Namespaces & the Declarative Workflow, where we stop letting everything pile into `default`, carve the stack into isolated namespaces, set guardrails with ResourceQuotas and LimitRanges, and tighten the apply/diff/prune workflow that's quietly been holding the whole system together.*
