# Module 4 — Services & Networking: Stable Addresses in a Shifting World

> **Hands-on rule:** type every command. The magic of this module — load-balancing across pods, finding a backend by name — is invisible until you watch different pod names answer the same request. Run the loops.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner. Docker Desktop gives `LoadBalancer` services a real `localhost` address, so we finally retire `port-forward` for real traffic.
>
> **Where we're picking up:** Module 3 left you with three identical, self-healing Flask pods — but no stable way to *reach* them (every `port-forward` hit just one), and no way for them to reach a backend. This module fixes both. By the end, the Flask + Redis + Postgres stack is talking to itself by name — exactly the trick you pulled with container names in the Docker course, now generalized for a world of replicas that come and go.

---

## Chunk 1 — The problem: pods are moving targets

Every pod gets an IP address. So why not just connect to it? Because **pod IPs are ephemeral**, and Module 3 is the reason. Self-healing means a pod that dies is replaced by a *new* pod — with a *new* IP. Prove it to yourself. Grab a pod's IP (the `jsonpath` trick from Module 2), delete it, and **predict**: will the replacement have the same IP?

```bash
kubectl get pods -l app=flask -o wide          # note the IP of one pod
kubectl delete pod <that-pod-name>
kubectl get pods -l app=flask -o wide          # the replacement has a DIFFERENT IP
```

Different IP, every time. So hardcoding a pod IP anywhere is building on sand. And it's worse with replicas: you have *three* Flask pods — which of the three IPs would you even put in a config? You don't want any single pod's address. You want **one stable address that fronts all of them** and quietly tracks which pods exist right now.

**The Docker bridge.** In the Docker course you hit this exact wall and solved it by putting everything on a user-defined network and talking by *container name* instead of IP. A Kubernetes **Service** is that same idea — a stable name instead of a brittle IP — but built for a world where the thing behind the name is a *shifting set of replicas* that Kubernetes is constantly killing and recreating.

---

## Chunk 2 — The Service: a stable front door

> A **Service** is a stable virtual IP and DNS name that load-balances traffic across a set of pods chosen by a **label selector**.

Read that selector part carefully, because it's the Module 3 mechanism returning yet again: a Service doesn't point at specific pods or IPs — it points at a *label query* ("all pods with `app: flask`") and continuously resolves that query to whatever pods match *at this instant*. Pods appear, disappear, get new IPs; the Service absorbs all of it and presents one unchanging address.

That gives you a clean separation of concerns to hold onto:

> **The Deployment keeps the pods alive. The Service makes them reachable.** Two independent jobs — one manages lifecycle, the other manages addressing — wired together by the labels they share.

---

## Chunk 3 — Your first Service, and the load-balancing payoff

Make sure your Flask Deployment from Module 3 is running (3 replicas). Now give it a Service — create `flask-service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: flask
spec:
  selector:
    app: flask          # front the pods labelled app=flask (the Deployment's pods)
  ports:
  - port: 8080          # the port the Service listens on
    targetPort: 5000    # the container port to forward to
```

Two ports, two meanings — the distinction that confuses everyone once: `port` is where the *Service* answers; `targetPort` is the *container's* port it forwards to. Clients hit `flask:8080`; the Service delivers to a pod's `5000`. Apply and inspect:

```bash
kubectl apply -f flask-service.yaml
kubectl get svc
# NAME    TYPE        CLUSTER-IP      EXTERNAL-IP   PORT(S)    AGE
# flask   ClusterIP   10.102.33.18    <none>        8080/TCP   5s
```

That `CLUSTER-IP` is stable — it will not change as pods churn behind it. `ClusterIP` is the default Service type: reachable only *inside* the cluster (we go external in Chunk 5).

Now the payoff I promised at the end of Module 3. Launch a throwaway debug pod *inside* the cluster (the Module 2 technique) and hit the Service by name, several times. **Predict:** same pod each time, or different ones?

```bash
kubectl run tmp --rm -it --image=busybox --restart=Never -- sh
# now inside the pod:
wget -qO- flask:8080
wget -qO- flask:8080
wget -qO- flask:8080
# exit
```

```
Hello from Flask on Kubernetes (v2.0)
Served by pod: flask-6d4b8c9f7-2xk9p
...
Served by pod: flask-6d4b8c9f7-w8n2d     ← a different pod
...
Served by pod: flask-6d4b8c9f7-h5r3c     ← and another
```

There it is — one stable name, traffic spread across all three replicas. The Service is load-balancing at the connection level (each new connection may land on a different pod). *This* is why you run replicas: one address, many workers behind it.

---

## Chunk 4 — DNS: how the name `flask` resolves

How did `wget -qO- flask:8080` know what `flask` meant? The cluster runs a DNS server (**CoreDNS**, one of those `kube-system` pods from Module 1), and every Service automatically gets a DNS record. Prove it from a debug pod:

```bash
kubectl run tmp --rm -it --image=busybox --restart=Never -- sh
nslookup flask
# Name:   flask.default.svc.cluster.local
# Address: 10.102.33.18      ← the Service's stable ClusterIP
```

Notice the full name: **`flask.default.svc.cluster.local`**, which decodes as `<service>.<namespace>.svc.cluster.local`. Within the *same* namespace you can use the short form `flask`; across namespaces you'd use the fully-qualified name (namespaces are Module 8). This is the cluster-scale version of Docker's container-name DNS — with one upgrade: in Docker the name pointed at *one container*, but here `flask` resolves to a Service that fans out across *all matching pods*. Same ergonomics, built for scale.

This DNS name is exactly why naming matters (the conversation we had about `flask` vs `flask-deployment`): other pods will connect to `redis`, `postgres`, `flask` — short, clean service names become the hostnames your whole stack speaks.

---

## Chunk 5 — Reaching it from your Mac: LoadBalancer (goodbye, port-forward)

`port-forward` was always a dev-only tunnel to a single pod. The real way to expose a Service outside the cluster is the Service *type*. You've seen `ClusterIP`; here are all three:

- **ClusterIP** (default) — internal only. Pod-to-pod traffic. (Redis and Postgres will use this.)
- **NodePort** — opens a high port (30000–32767) on *every node*, reachable from outside via `<node-ip>:<nodeport>`. Crude but dependency-free.
- **LoadBalancer** — asks the environment to provision a real external load balancer. In the cloud that's an actual cloud LB (and a real bill); on Docker Desktop it's mapped to `localhost` for free. It builds *on top of* NodePort.

Promote your Flask Service to `LoadBalancer` — edit `flask-service.yaml`:

```yaml
spec:
  type: LoadBalancer      # add this line
  selector:
    app: flask
  ports:
  - port: 8080
    targetPort: 5000
```

```bash
kubectl apply -f flask-service.yaml
kubectl get svc flask
# NAME    TYPE           CLUSTER-IP     EXTERNAL-IP   PORT(S)          AGE
# flask   LoadBalancer   10.102.33.18   localhost     8080:31xxx/TCP   2m
```

`EXTERNAL-IP: localhost`. Now hit it straight from your Mac — no tunnel, no `port-forward`:

```bash
curl localhost:8080      # run it a few times — different pods answer, just like inside the cluster
```

That's the real front door. From here on, `curl localhost:8080` is how we reach Flask.

---

## Chunk 6 — Endpoints: what's actually behind the Service

A Service is an abstraction — but it has to resolve to *real pod IPs* somehow. That list is called the **Endpoints** (newer clusters call them EndpointSlices). It's the Service's selector, evaluated into concrete addresses:

```bash
kubectl get endpoints flask
# NAME    ENDPOINTS                                      AGE
# flask   10.1.0.41:5000,10.1.0.42:5000,10.1.0.43:5000   3m
```

Three pod IPs — your three replicas. Now watch this list track reality on its own. **Predict** what happens to the endpoints when you scale:

```bash
kubectl scale deployment flask --replicas=5
kubectl get endpoints flask        # now FIVE addresses
kubectl scale deployment flask --replicas=3
kubectl get endpoints flask        # back to THREE
```

The Service never changed; its endpoint list re-resolved automatically as pods came and went. Delete a pod and you'd see the same — the dead IP drops, the replacement's IP appears, all without touching the Service. That's the selector doing continuous reconciliation, the same spirit as everything since Module 1.

One crucial detail that sets up Module 7: **a pod only becomes an endpoint once it's `READY`.** A pod that's still starting (or has failed its readiness check) is *excluded* from the Service, so traffic never reaches a pod that can't serve it. That readiness gate is what makes zero-downtime rollouts actually zero-downtime — and we'll wire it up properly with readiness probes in Module 7.

---

## Chunk 7 — Wiring the stack: Flask finds Redis by name

Now the project thread comes alive. We'll give Flask a Redis-backed visit counter, so it has a real reason to talk to a backend. Evolve `app.py` to v3:

```python
import os
import socket
from flask import Flask
import redis

app = Flask(__name__)
POD_NAME = socket.gethostname()
APP_VERSION = "3.0"

# Connect to Redis by SERVICE NAME — never an IP. Configurable for Module 5.
r = redis.Redis(
    host=os.environ.get("REDIS_HOST", "redis"),
    port=int(os.environ.get("REDIS_PORT", "6379")),
    socket_connect_timeout=2,
)

@app.route("/")
def home():
    greeting = os.environ.get("GREETING", "Hello from Flask on Kubernetes")
    try:
        count = r.incr("visits")
        counter = f"Visit #{count} (shared across all pods via Redis)"
    except redis.exceptions.RedisError:
        counter = "Visit counter unavailable (Redis not reachable)"
    return f"{greeting} (v{APP_VERSION})\nServed by pod: {POD_NAME}\n{counter}\n"

@app.route("/healthz")
def healthz():
    return "ok\n", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

Add `redis` to the image — update the `Dockerfile`'s pip line to `pip install --no-cache-dir flask==3.0.3 redis==5.0.8`, then build:

```bash
docker build -t flaskapp:3.0 .
```

Now stand up Redis. Note the `---` separator — it lets one file hold multiple resources, which is how you'll bundle related objects from now on. Create `redis.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: redis
spec:
  replicas: 1
  selector:
    matchLabels:
      app: redis
  template:
    metadata:
      labels:
        app: redis
    spec:
      containers:
      - name: redis
        image: redis:7-alpine
        ports:
        - containerPort: 6379
---
apiVersion: v1
kind: Service
metadata:
  name: redis
spec:
  selector:
    app: redis
  ports:
  - port: 6379
    targetPort: 6379
```

```bash
kubectl apply -f redis.yaml
```

That Service is `ClusterIP` (no `type:` = the default) — Redis should be reachable *inside* the cluster only, never from your Mac. Exactly right for a backend.

Finally, roll Flask to v3. Use the declarative path — edit `image: flaskapp:3.0` in `flask-deployment.yaml` and apply (this sidesteps the container-name snag from before, and it's the habit we want):

```bash
kubectl apply -f flask-deployment.yaml
kubectl rollout status deployment/flask
```

Now hit it and watch two things at once:

```bash
curl localhost:8080      # run it five or six times
```

```
Hello from Flask on Kubernetes (v3.0)
Served by pod: flask-7c9d5f8b6-k2p4n
Visit #1 (shared across all pods via Redis)
...
Served by pod: flask-7c9d5f8b6-m8x2q     ← different pod...
Visit #2 (shared across all pods via Redis)   ← ...but the count keeps climbing
```

Sit with that. The Flask pods are **stateless and load-balanced** — a different one answers each time — yet the counter increments monotonically, because every pod talks to the *same* Redis through the stable name `redis`. That's the entire microservices networking lesson in one demo: stateless workers, shared state behind a named Service.

**Now two `predict-then-try` deletions that teach opposite lessons.** First, delete a *Flask* pod and curl again — does the count survive? (Yes: state lives in Redis, not in Flask.) Then delete the *Redis* pod:

```bash
kubectl delete pod -l app=redis
kubectl get pods -l app=redis      # a new redis pod, new IP
curl localhost:8080                # Flask still reaches "redis" (stable name)... but Visit #1 again
```

Flask keeps working — the Redis *service name* is stable even though the pod's IP changed, which is the whole point of this module. But the **counter reset to 1**, because the Redis pod was recreated empty: it had no persistent storage. Networking gave you a stable *address*; it did nothing for stable *data*. That gap is precisely what Module 6 (storage) exists to close — and you just felt it.

---

## Chunk 8 — Wiring Postgres by name

Add the third tier so the stack topology is complete. Create `postgres.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: postgres
spec:
  replicas: 1
  selector:
    matchLabels:
      app: postgres
  template:
    metadata:
      labels:
        app: postgres
    spec:
      containers:
      - name: postgres
        image: postgres:16-alpine
        env:
        - name: POSTGRES_PASSWORD
          value: "devpassword"      # ⚠️ plaintext password in a manifest — see note
        ports:
        - containerPort: 5432
---
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  selector:
    app: postgres
  ports:
  - port: 5432
    targetPort: 5432
```

```bash
kubectl apply -f postgres.yaml
```

> ⚠️ **That plaintext `POSTGRES_PASSWORD` is bad practice** — a credential sitting in a manifest you'd commit to git. We're doing it deliberately as a placeholder so Postgres can start; **Module 5 (Secrets)** replaces it with the proper mechanism. Flag it in your mind now.

Prove the wiring end-to-end without baking anything into Flask — spin up a throwaway Postgres client pod and connect *through the service name*:

```bash
kubectl run pgtest --rm -it --image=postgres:16-alpine \
  --env=PGPASSWORD=devpassword -- psql -h postgres -U postgres -c '\l'
```

```
                                List of databases
   Name    |  Owner   | Encoding | ...
-----------+----------+----------+------
 postgres  | postgres | UTF8     | ...
 ...
```

That connection resolved `postgres` via DNS, reached the pod through the Service, and authenticated — proof the third tier is wired and reachable by name. Flask's *real* use of Postgres (and doing that password properly) lands in the next two modules; for now, the stack's three names — `flask`, `redis`, `postgres` — all resolve and connect.

---

## Chunk 9 — Inspecting and debugging the network

Your daily reflexes, plus the network-specific ones:

```bash
kubectl get svc                          # all services, types, cluster-IPs, external-IPs
kubectl get svc -o wide                  # + the selector each service uses
kubectl describe svc flask               # selector, ports, AND the resolved Endpoints
kubectl get endpoints flask              # the live pod IPs behind a service
kubectl get svc flask -o jsonpath='{.spec.clusterIP}'
```

When something can't reach something else — the most common cluster headache — `describe svc` is your first stop: if its **Endpoints list is empty**, the Service's selector matches no ready pods (a label typo, or pods not ready). That single check resolves most "connection refused" mysteries. From there, the debug-pod pattern triangulates the rest: does `nslookup <svc>` resolve (DNS)? Does `wget -qO- <svc>:<port>` connect (Service + pod)? That "is it DNS, the Service, or the pod?" flow is the backbone of network debugging, and we'll formalize it in Module 12.

---

## Chunk 10 — Keep it running (a note on cleanup)

This module is a turning point: **don't tear everything down.** From here on, the Flask + Redis + Postgres stack is our *running system*, and later modules build directly on it. You should now have these manifest files, which together describe the whole stack:

```
flask-deployment.yaml
flask-service.yaml
redis.yaml
postgres.yaml
```

Apply the whole stack at once by pointing at the folder (the directory trick from Module 2's rare-but-real):

```bash
kubectl apply -f .         # applies every manifest in the current directory
```

To stop for the day and reclaim the VM's RAM, you can either toggle Kubernetes off in Docker Desktop, or tear the stack down and rebuild it later from the files:

```bash
kubectl delete -f .        # remove everything these files describe
kubectl apply -f .         # ...and bring it all back, identical, whenever you want
```

That "the files *are* the system" property — destroy and recreate at will, identically — is the declarative payoff compounding. (It's also the seed of why a packaging tool like Helm exists, which closes out the course.)

---

## Chunk 11 — Rare-but-real (recognize, don't memorize)

```bash
kubectl expose deployment flask --port=8080 --target-port=5000 --type=LoadBalancer   # create a Service imperatively
```

Service variants you'll meet in other people's manifests:

- **Headless** (`clusterIP: None`) — no load-balancing virtual IP; DNS returns the pod IPs *directly*. Essential for StatefulSets, where each pod needs its own stable identity — you'll meet it in Module 6.
- **ExternalName** — maps a Service name to an external DNS name (e.g. point `postgres` at a managed cloud database) with no proxying; a CNAME alias.
- **sessionAffinity: ClientIP** — pin a given client to the same pod instead of load-balancing each connection.
- **Multi-port** — a Service can expose several named ports at once (e.g. `http` and `metrics`).

---

## Chunk 12 — Command cheat sheet

| Goal | Command |
|---|---|
| Create/update a Service | `kubectl apply -f flask-service.yaml` |
| List services | `kubectl get svc` (`-o wide` for selectors) |
| Service detail + endpoints | `kubectl describe svc <n>` |
| Live pod IPs behind a service | `kubectl get endpoints <n>` |
| Expose a Deployment imperatively | `kubectl expose deployment <n> --port=8080 --target-port=5000 --type=LoadBalancer` |
| Test from inside the cluster | `kubectl run tmp --rm -it --image=busybox --restart=Never -- sh` |
| Resolve a service name (DNS) | `nslookup <svc>` (inside a pod) |
| Hit a service internally | `wget -qO- <svc>:<port>` (inside a pod) |
| Hit a LoadBalancer externally | `curl localhost:8080` |
| Apply a whole directory | `kubectl apply -f .` |
| Service's ClusterIP | `kubectl get svc <n> -o jsonpath='{.spec.clusterIP}'` |

---

## Chunk 13 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — stable address, load-balanced**
1. Confirm your Flask Deployment has 3 pods. Grab one pod's IP, delete that pod, and show the replacement's IP differs — then state in one sentence why this makes a Service necessary.
2. Write and apply a `ClusterIP` Service named `flask` that listens on 8080 and targets container port 5000. Explain the difference between `port` and `targetPort`.
3. From a throwaway debug pod, hit the Service by name several times and show that different pod names answer. Name the component that resolved the name to an IP.
4. List the Service's endpoints. Scale the Deployment to 5 and show the endpoint list changed *without* you touching the Service. Explain who updated it.

**Challenge B — wire the stack**
1. Make the Flask Service externally reachable from your Mac, and `curl` it directly (no `port-forward`). Name the Service type you used and what Docker Desktop maps its external IP to.
2. Stand up Redis (Deployment + `ClusterIP` Service named `redis`) and roll Flask to the v3.0 image that uses it.
3. `curl` Flask several times and explain why the *pod name* changes but the *visit count* keeps rising.
4. Delete the Redis pod. Explain why Flask keeps working afterward but the counter resets — and name the module that will fix the reset.

**Bonus question (mental model):** Your Flask app connects to Redis using the literal string `redis`, never an IP — and it keeps working even after the Redis pod is deleted and recreated with a brand-new IP. Walk through *exactly* what happens, end to end, when Flask opens that connection: what resolves `redis`, what it resolves to, how that thing knows which pod IP to use right now, and why none of it breaks when the underlying pod changes. (Hint: name four things — CoreDNS, the Service's ClusterIP, the selector, and the Endpoints list.)

---

*End of Module 4. Next: Module 5 — ConfigMaps & Secrets, where that embarrassing plaintext Postgres password gets done properly, and all the scattered configuration (greetings, hostnames, credentials) moves out of your images and manifests into objects built for the job — the cluster-scale evolution of the `-e` flag you knew in Docker.*
