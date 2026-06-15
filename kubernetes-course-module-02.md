# Module 2 — Pods: The Atom, and the Leap to YAML

> **Hands-on rule:** type every command, and create every file by hand. This module is where Kubernetes stops being commands you run and becomes state you *declare* — your fingers need to feel that shift.
>
> **Environment:** macOS + Docker Desktop, Kubernetes enabled via the **Kubeadm** (single-node) provisioner. That choice pays off in this exact module, as you'll see in Chunk 2.
>
> **Where we're picking up:** in Module 1 you ran a *borrowed* nginx pod with `kubectl run`, watched the reconciliation loop heal a container, and felt the gap that bare pods leave (delete the pod, nothing brings it back). Now two things happen: you run *your own* Flask app — the same one from the Docker course — and you graduate from imperative commands to declarative YAML, the way real Kubernetes is operated.

---

## Chunk 1 — What a pod really is

In Module 1 I gave you the cartoon: "a pod is one container with some Kubernetes paperwork around it." That got you running. Here's the real definition, because it explains design decisions in every later module.

> **A pod is one or more containers that share a network and storage, are always placed on the same node, and live and die as a unit.** It is the smallest thing Kubernetes schedules — Kubernetes never runs a bare container, only a pod wrapping it.

Unpack "share a network": every container in a pod gets the *same* IP address and the same `localhost`. Two containers in one pod talk to each other over `localhost:<port>` as if they were two processes on one machine — because, network-wise, they are. They also share storage volumes you attach to the pod. And they're inseparable: the scheduler places the whole pod on one node, and if the pod dies, all its containers die together.

**So why would a pod ever hold more than one container?** The dominant case is one main container plus a small helper that needs to sit *right next to* it — sharing its network or files. That helper is called a **sidecar**. Classic examples: a log-shipping sidecar that reads the app's log files off a shared volume, or a proxy sidecar that the app reaches over `localhost`. The defining test: *do these processes need to share localhost or a disk?* If yes, one pod. If no, separate pods.

**But the 90% case is one container per pod.** Don't cram your Flask app and your Postgres into one pod because they "go together" — they don't share localhost or disk, they scale independently, and they fail independently. They belong in separate pods (and separate Deployments, Module 3). We'll meet a real sidecar briefly in Chunk 7 so the concept is concrete, but most of this course is one-container pods.

The mental model to carry:

> **The container is the *what* (your image, running). The pod is the *unit Kubernetes manages* — the smallest thing it schedules, heals, scales, and exposes.** Everything from here up (Deployments, Services, scaling) operates on pods, not containers.

---

## Chunk 2 — Bring your own app: building the Flask image

We're picking the Flask app back up from the Docker course. So we're all working from an identical starting point, here it is fresh — deliberately minimal, because you know Dockerfiles cold by now and the lesson here is Kubernetes, not the app.

Make a working folder and create two files. First `app.py`:

```python
import os
import socket
from flask import Flask

app = Flask(__name__)
POD_NAME = socket.gethostname()   # inside a pod, the hostname IS the pod name

@app.route("/")
def home():
    greeting = os.environ.get("GREETING", "Hello from Flask on Kubernetes")
    return f"{greeting}\nServed by pod: {POD_NAME}\n"

@app.route("/healthz")
def healthz():
    return "ok\n", 200

if __name__ == "__main__":
    # 0.0.0.0, NOT 127.0.0.1 — it must accept connections from outside the container
    app.run(host="0.0.0.0", port=5000)
```

Two details worth pausing on. The app prints its own **hostname**, which inside a pod equals the **pod name** — that becomes a great visual aid in Module 3 when we scale and watch different pods answer. And it binds `0.0.0.0`, not `127.0.0.1`; bind to loopback and nothing outside the container can ever reach it, a mistake that produces baffling "connection refused" later. (The `GREETING` env var is a deliberate hook for ConfigMaps in Module 5.)

Now a minimal `Dockerfile`:

```dockerfile
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir flask==3.0.3
COPY app.py .
EXPOSE 5000
CMD ["python", "app.py"]
```

Build it — note the explicit tag, which matters in a moment:

```bash
docker build -t flaskapp:1.0 .
```

```bash
docker images flaskapp
# REPOSITORY   TAG   IMAGE ID       CREATED         SIZE
# flaskapp     1.0   a1b2c3d4e5f6   5 seconds ago   ...MB
```

**Here's the Kubeadm payoff I promised.** That image lives in Docker's local store. Because Docker Desktop's Kubeadm cluster shares the *same* image store as the Docker engine (both inside the one Linux VM), Kubernetes can run `flaskapp:1.0` directly — **no registry, no push, no pull.** This is exactly why we chose Kubeadm over the kind provisioner: kind nodes have their own separate stores and would force you to load the image in manually. You build, Kubernetes runs it. Done.

**But there's one gotcha that bites everyone once.** Kubernetes decides whether to pull an image from a registry based on `imagePullPolicy`, and the *default* depends on your tag:

- A specific tag like `:1.0` → default policy `IfNotPresent` → "already local? use it." ✅ This is why we tagged `:1.0`.
- The tag `:latest` (or no tag) → default policy `Always` → "always try to pull from the registry." Kubernetes would go to Docker Hub looking for `flaskapp:latest`, not find it, and fail with `ErrImagePull`.

So: when running locally-built images, **always use a real tag, never `:latest`.** We'll make this explicit in the YAML anyway.

---

## Chunk 3 — Run it imperatively (the quick win), then graduate

Apply your Module 1 reflexes to your own app first — it's the fastest way to confirm the image works:

```bash
kubectl run flask --image=flaskapp:1.0
# pod/flask created
kubectl get pods
# NAME    READY   STATUS    RESTARTS   AGE
# flask   1/1     Running   0          8s
```

Tunnel in and check it (host 8080 → container 5000):

```bash
kubectl port-forward pod/flask 8080:5000
```

In another terminal:

```bash
curl localhost:8080
# Hello from Flask on Kubernetes
# Served by pod: flask
```

There it is — *your* app, on the cluster, reporting its pod name. Stop the port-forward with `Ctrl+C`.

That worked, and it took ten seconds. So why not stop here? Because `kubectl run` is **imperative** — "do this now" — and it leaves no durable record of *what you wanted*. You saw the cost in Module 1: delete the pod and nothing restores it, because no file or controller is holding the intent. Real Kubernetes is run from files you can review, commit, and re-apply. So tear this down and let's do it properly:

```bash
kubectl delete pod flask
# pod "flask" deleted
```

---

## Chunk 4 — The pivot: describing a pod in YAML

You don't write manifests from a blank page — you let `kubectl` write the skeleton and then edit it. This is the rusty learner's best friend (and honestly everyone's). Generate the manifest without creating anything, and save it:

```bash
kubectl run flask --image=flaskapp:1.0 --dry-run=client -o yaml > flask-pod.yaml
```

Open `flask-pod.yaml`. It'll have some generated noise (`creationTimestamp: null`, an empty `resources: {}`, a `status:` block). Clean it up and add two things, so it reads like this:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: flask
  labels:
    app: flask
spec:
  containers:
  - name: flask
    image: flaskapp:1.0
    imagePullPolicy: IfNotPresent
    ports:
    - containerPort: 5000
```

Walk it field by field — these four top-level keys anchor *every* manifest you'll ever write:

- **apiVersion: v1** — pods live in the core API group, which is just `v1`. (Deployments, next module, live in `apps/v1` — the version tells Kubernetes which schema to expect.)
- **kind: Pod** — what you're describing.
- **metadata** — identity. The `name` is how you address it. The `labels` (`app: flask`) are arbitrary key/value tags; they look pointless now but they're the glue that Services and Deployments use to *find* pods — we lean on them hard from Module 3 on. Adding the label now is a deliberate down-payment.
- **spec** — the desired state. Inside, `containers` is a *list* (the dash `-`), because a pod can hold several. Each has a `name`, an `image`, our explicit `imagePullPolicy: IfNotPresent` (belt-and-suspenders for the local-image gotcha), and `ports`.

One correction to a near-universal misconception: `containerPort: 5000` **does not publish or expose anything.** It's purely informational documentation that "this container listens on 5000." Reaching the app from your Mac still needs `port-forward` (now) or a Service (Module 4). Deleting the `ports:` block entirely wouldn't change reachability — it's there for humans and tooling. Don't expect it to open a door.

Forgot what a field does? The self-documentation from Module 1 is right there:

```bash
kubectl explain pod.spec.containers
```

---

## Chunk 5 — `kubectl apply`: declaring desired state

Now hand the file to the cluster:

```bash
kubectl apply -f flask-pod.yaml
# pod/flask created
```

Same running pod as before — but everything important is different. `apply` is **declarative and idempotent**: it means "make the cluster match this file," not "do this action." Watch what that buys you. Run the exact same command again:

```bash
kubectl apply -f flask-pod.yaml
# pod/flask unchanged
```

`unchanged` — it didn't error, didn't recreate, didn't duplicate. It compared the file to reality, saw no gap, and did nothing. *That* is the reconciliation mindset in your hands: you state what should be true, and re-stating it is always safe. Contrast the three ways to create things:

- `kubectl run` — imperative, one-shot, no record. Fine for quick experiments.
- `kubectl create -f file` — creates from a file, but **errors if it already exists**. Brittle.
- `kubectl apply -f file` — declarative, idempotent, reconciles the difference. **This is the one you use.**

Before applying a change, you can preview exactly what would change — a habit worth building now:

```bash
kubectl diff -f flask-pod.yaml
# (no output = no difference; otherwise a +/- diff)
```

Confirm the pod and reach it once more, the same way:

```bash
kubectl get pods
kubectl port-forward pod/flask 8080:5000   # then curl localhost:8080 in another terminal
```

The shift is complete: `flask-pod.yaml` is now the source of truth. The pod is just the cluster's current attempt at honoring it.

---

## Chunk 6 — Pod lifecycle: the phases, and the statuses you'll actually fight

A pod moves through a small set of high-level **phases**:

- **Pending** — accepted, but not yet running (being scheduled, or pulling images).
- **Running** — bound to a node, at least one container is up.
- **Succeeded** — all containers exited cleanly (0) and won't restart. (Run-to-completion work; Jobs in Module 10.)
- **Failed** — all containers terminated, at least one with an error.
- **Unknown** — the node stopped reporting.

But the `STATUS` column in `kubectl get pods` usually shows something more *specific* than the phase — a reason string — and learning to read these is most of real-world pod debugging. The catalog that matters:

| STATUS you see | What it means |
|---|---|
| `ContainerCreating` | Pending — pulling the image or setting up. Normal, briefly. |
| `Running` | Up and (per `READY`) ready. |
| `Completed` | Exited 0 — done, not an error. |
| `CrashLoopBackOff` | Container keeps crashing; Kubernetes is *backing off* between restarts. The one you'll fight most. |
| `ImagePullBackOff` / `ErrImagePull` | Can't fetch the image — wrong name/tag, `:latest` gotcha, or a private registry. |
| `OOMKilled` | Killed for exceeding its memory limit (Module 7). |

These aren't trivia — let's *manufacture* the two you'll hit most, so you recognize them instantly in the wild.

**CrashLoopBackOff, on purpose.** Run a container whose only job is to fail:

```bash
kubectl run crasher --image=busybox --restart=Never --command -- sh -c 'echo "starting"; exit 1'
```

Wait — that `--restart=Never` makes it a one-shot. To see the *loop*, run one that restarts (the default):

```bash
kubectl run crasher --image=busybox --command -- sh -c 'echo "boom"; exit 1'
kubectl get pods -w
```

Watch the dance: `Running` → `Error` → `CrashLoopBackOff`, with `RESTARTS` climbing `1, 2, 3…` and increasing delay between attempts (that's the "back off"). Now debug it exactly as you would in production:

```bash
kubectl describe pod crasher        # Events show the restart loop and last exit code
kubectl logs crasher                # "boom" — the container's own output
kubectl logs --previous crasher     # the prior crashed attempt's logs (your Module 1 tool)
```

That sequence — `describe` for the timeline, `logs --previous` for the dead container's last words — is the bread and butter of fixing a crash loop. Clean up:

```bash
kubectl delete pod crasher
```

**ImagePullBackOff, on purpose.** Reference a tag that doesn't exist:

```bash
kubectl run typo --image=flaskapp:9.9
kubectl get pods            # STATUS: ImagePullBackOff
kubectl describe pod typo   # Events: "Failed to pull image ... not found"
kubectl delete pod typo
```

The `describe` Events tell you precisely *why* the pull failed — the same place you'd discover a misspelled image name or the `:latest`-tried-to-pull trap from Chunk 2.

---

## Chunk 7 — A pod with two containers: the sidecar, proven

Time to make Chunk 1's "they share localhost" claim concrete, with the smallest honest demo. Create `sidecar-pod.yaml`:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: flask-with-probe
spec:
  containers:
  - name: flask
    image: flaskapp:1.0
    imagePullPolicy: IfNotPresent
    ports:
    - containerPort: 5000
  - name: probe
    image: busybox
    command: ["sh", "-c", "while true; do wget -qO- localhost:5000; sleep 5; done"]
```

Two containers, one pod. The `probe` container does nothing but hit `localhost:5000` every five seconds. **Predict before you apply:** the probe never mentions the Flask container's address — just `localhost`. Will it reach Flask?

```bash
kubectl apply -f sidecar-pod.yaml
kubectl get pods               # flask-with-probe   2/2   Running   (note: 2/2 containers)
kubectl logs flask-with-probe -c probe
```

```
Hello from Flask on Kubernetes
Served by pod: flask-with-probe
...repeating every 5s...
```

It works — because both containers share one network namespace, so the probe's `localhost` *is* Flask's `localhost`. That `2/2` in the `READY` column is your tell that a pod has multiple containers. The new wrinkle is `-c`: with more than one container, you must say *which* one for `logs` and `exec`:

```bash
kubectl logs flask-with-probe -c flask      # the Flask container's logs
kubectl exec -it flask-with-probe -c flask -- sh   # shell into a specific container
```

That's the sidecar pattern in miniature. Real sidecars ship logs, proxy traffic, or refresh secrets — but the mechanism is always this: co-located containers sharing the pod's network and storage. Tear it down:

```bash
kubectl delete -f sidecar-pod.yaml
```

---

## Chunk 8 — Inspecting pods (your Module 1 reflexes, sharpened)

The daily-driver four return — `get`, `describe`, `logs`, `exec` — now with a bit more reach. Most of this is reinforcement; the new pieces are the structured-output flags.

```bash
kubectl get pod flask -o yaml
```

This is the pod's *full live state* as Kubernetes holds it. Scroll to the `status:` block — absent from your manifest, filled in by the cluster: `phase: Running`, the assigned `podIP`, `conditions`, and `containerStatuses` with restart counts and the running image. Your `spec` is what you *asked for*; `status` is what *is*. The gap between them is what the reconciliation loop works to close.

To pull a single field out of that wall of YAML — the Kubernetes answer to `docker inspect --format` — use `jsonpath`:

```bash
kubectl get pod flask -o jsonpath='{.status.podIP}'
# 10.1.0.37   (the pod's cluster-internal IP)

kubectl get pod flask -o jsonpath='{.status.phase}'
# Running
```

The path mirrors the YAML structure, exactly like the Go templates you used on `docker inspect`. And the rest, straight from muscle memory:

```bash
kubectl describe pod flask     # human-readable detail + the all-important Events
kubectl logs flask             # the Flask request log
kubectl exec -it flask -- sh   # step inside (single container, no -c needed)
```

One genuinely useful pattern that feels like `docker run --rm -it`: a disposable debug pod that deletes itself on exit. Reach for it constantly when you want a shell *inside the cluster's network* (to test connectivity, DNS, etc., from Module 4 on):

```bash
kubectl run tmp --rm -it --image=busybox --restart=Never -- sh
# you're in a throwaway pod; 'exit' removes it automatically
```

---

## Chunk 9 — Cleanup

Because you applied from a file, you can delete from the same file — the clean inverse of `apply`, and a nice demonstration that the manifest is the source of truth:

```bash
kubectl delete -f flask-pod.yaml
# pod "flask" deleted
```

Sweep up anything else from the demos, then confirm a clean namespace:

```bash
kubectl get pods
# No resources found in default namespace.
```

If a stray demo pod lingers, `kubectl delete pod <name>` clears it. As in Module 1, the background cost to be aware of on macOS is the idling control plane in the Docker Desktop VM — toggle Kubernetes off when you're done for the day if you want the RAM back.

---

## Chunk 10 — Rare-but-real (recognize, don't memorize)

```bash
kubectl apply -f ./manifests/         # apply every YAML in a directory at once
kubectl apply -f https://example/x.yaml   # apply straight from a URL (common in tutorials)
kubectl edit pod flask                 # open the live object in $EDITOR, save to apply
kubectl replace -f flask-pod.yaml      # hard overwrite (vs apply's merge) — rarely needed
kubectl get pod flask -o jsonpath='{.spec.containers[*].image}'   # list images via path
kubectl get pods --show-labels         # see every pod's labels (sets up Module 3)
kubectl attach flask                   # attach to a container's main process (like docker attach)
```

Two pod fields you'll meet later but should recognize now, both in `spec`: `restartPolicy` (`Always` — the default — vs `OnFailure` / `Never`, which matter for run-to-completion Jobs in Module 10) and `nodeName` (pins a pod to a specific node; relevant only once you have more than one, in the Module 10 scheduling cameo).

---

## Chunk 11 — Command cheat sheet

| Goal | Command |
|---|---|
| Build your app image (local) | `docker build -t flaskapp:1.0 .` |
| Run a pod imperatively | `kubectl run <name> --image=<img>` |
| Generate a manifest skeleton | `kubectl run <name> --image=<img> --dry-run=client -o yaml > pod.yaml` |
| Apply (declarative, idempotent) | `kubectl apply -f pod.yaml` |
| Preview a change first | `kubectl diff -f pod.yaml` |
| Delete from a manifest | `kubectl delete -f pod.yaml` |
| List pods (with restart counts) | `kubectl get pods` |
| Watch status changes live | `kubectl get pods -w` |
| Full live state (spec + status) | `kubectl get pod <name> -o yaml` |
| Extract one field | `kubectl get pod <name> -o jsonpath='{.status.podIP}'` |
| Show labels | `kubectl get pods --show-labels` |
| Detail + event timeline | `kubectl describe pod <name>` |
| Logs (now/previous/follow) | `kubectl logs [--previous \| -f] <name>` |
| Logs of a specific container | `kubectl logs <name> -c <container>` |
| Shell into a container | `kubectl exec -it <name> [-c <container>] -- sh` |
| Throwaway debug pod | `kubectl run tmp --rm -it --image=busybox --restart=Never -- sh` |
| Field docs | `kubectl explain pod.spec.containers` |
| Dev tunnel | `kubectl port-forward pod/<name> 8080:5000` |

---

## Chunk 12 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — your app, declared**
1. Build `flaskapp:1.0` from the app in Chunk 2 (or confirm it's already in `docker images`).
2. Generate a pod manifest skeleton for it into a file, *without* creating anything on the cluster.
3. Edit the file: name the pod `web`, give it the label `app: web`, set `imagePullPolicy: IfNotPresent`, and declare `containerPort: 5000`.
4. Apply it. Then apply it a second time — explain why the second apply says `unchanged` instead of erroring.
5. Port-forward and `curl` it; confirm the response names the pod `web`.
6. Pull *just* the pod's cluster IP out with a single command.
7. Delete it using the file, not the name.

**Challenge B — read the wreckage**
1. Run a pod from `busybox` whose command is `sh -c 'echo failing; exit 1'` (let it use the default restart policy).
2. Watch it reach `CrashLoopBackOff` live, and note the `RESTARTS` count climbing.
3. Using two commands, find (a) the event timeline showing the restarts and (b) the log output of the *previous* crashed attempt.
4. In one sentence, explain why the delay between restarts grows.
5. Now run a pod referencing `flaskapp:5.5` (a tag you never built). Name the STATUS you expect and the one command that reveals *why* it's stuck.
6. Clean both up.

**Bonus question (mental model):** You write a pod manifest with *two* containers — your Flask app and a `busybox` sidecar — and the sidecar reaches the app at `localhost:5000` with no IP, no service name, nothing else. Explain *why that works*, using the precise definition of a pod from Chunk 1. Then explain why you could **not** reach Postgres at `localhost:5432` the same way if Postgres were running in a *separate* pod — and name the Module-4 thing that will let pods in different pods find each other.

---

*End of Module 2. Next: Module 3 — Deployments, where we stop hand-placing pods and hand the job to a controller that always keeps N of them alive, scales them on command, and rolls out new versions without dropping a request — finally closing the gap you felt at the end of Module 1.*
