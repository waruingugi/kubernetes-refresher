# Module 3 — Deployments: Hand the Job to a Controller

> **Hands-on rule:** type every command. This module has a lot of moving parts — replicas appearing, pods rolling, ReplicaSets swapping — and you only believe it once you've watched it happen in `kubectl get pods -w`.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner. Local images still Just Work, so we'll build a `flaskapp:2.0` mid-module to roll out.
>
> **Where we're picking up:** in Module 1 you deleted a bare pod and *nothing brought it back*. In Module 2 you declared that pod cleanly in YAML — but it was still one fragile pod with no resilience, no scaling, no safe way to update. This module introduces the controller that fixes all three, and it's the resource you'll spend more time with than any other in Kubernetes.

---

## Chunk 1 — The three things a bare pod cannot do

You've now run pods two ways and hit the same wall both times. A bare pod — whether from `kubectl run` or a Pod manifest — has three fatal gaps for running real software:

1. **It doesn't recover.** Delete it, or let its node die, and it's simply gone. You proved this in Module 1: no controller was holding the intent "this should exist," so nothing restored it.
2. **It doesn't scale.** Want five copies? You'd hand-write five manifests with five different names, and manage them by hand forever. Absurd.
3. **It can't update without downtime.** To move from v1 to v2 you'd delete the pod and create a new one — and in the gap between, your app is *down*.

The fix is the **controller pattern**, the beating heart of Kubernetes. A controller is a process that holds a piece of *desired state* and runs the reconciliation loop from Module 1 to enforce it — forever. You've already met one without naming it: the kubelet, restarting a crashed container. Now you'll meet the controller that manages *pods themselves*, and you'll never hand-place a pod again.

---

## Chunk 2 — The ReplicaSet: a controller that can count

The first piece is the **ReplicaSet**. Its entire job, stated as desired state, is:

> "Keep exactly **N** pods matching **this label selector** alive. If there are fewer, create more. If there are more, delete some."

That's it — it's a thermostat for pod *count*. It needs two things to do this: a **selector** (how it recognizes "its" pods, by their labels) and a **template** (the pod blueprint it stamps out when it needs more). This is why the labels you've been dutifully adding since Module 2 matter — they're how a controller claims and counts its pods.

But here's the thing: **you almost never create a ReplicaSet directly.** A ReplicaSet can keep N pods alive, but it has no concept of *changing* them — no rolling update, no rollback. The moment you want to ship v2, a bare ReplicaSet is useless. So in practice you always reach one level higher, to the resource that *manages* ReplicaSets and adds exactly those missing superpowers: the **Deployment**. You'll see ReplicaSets in this module, but always as something a Deployment created *for* you.

The hierarchy, which you'll watch assemble itself in Chunk 4:

> **Deployment** (handles updates & rollback) → **ReplicaSet** (keeps N pods alive) → **Pods** (your running containers)

---

## Chunk 3 — Writing the Deployment

Generate a skeleton, just like you did for the pod — different verb, same trick:

```bash
kubectl create deployment flask --image=flaskapp:1.0 --dry-run=client -o yaml > flask-deployment.yaml
```

Clean it up and bump the replica count, so it reads like this:

```yaml
apiVersion: apps/v1            # NOTE: apps/v1, not the bare v1 that Pods use
kind: Deployment
metadata:
  name: flask
  labels:
    app: flask
spec:
  replicas: 3                  # desired state: keep 3 pods alive
  selector:
    matchLabels:
      app: flask               # "the pods I manage are the ones labelled app=flask"
  template:                    # ↓ everything below is a POD definition
    metadata:
      labels:
        app: flask             # the label the selector above looks for — THESE MUST MATCH
    spec:
      containers:
      - name: flask
        image: flaskapp:1.0
        imagePullPolicy: IfNotPresent
        ports:
        - containerPort: 5000
```

Three things to notice, because they're where people stumble:

**The `template` is just a pod.** Look at `spec.template` — from `metadata.labels` down, it's exactly the Pod manifest from Module 2. A Deployment doesn't replace pods; it's a *factory* for them, and the template is the mold. Everything you learned about pods still applies; it just lives one level down now.

**The selector must match the template's labels.** `spec.selector.matchLabels` (`app: flask`) has to match `spec.template.metadata.labels` (`app: flask`). This is the single most common Deployment error — if they don't match, Kubernetes rejects the manifest, because you'd have a controller looking for pods it can never recognize. Say it out loud: *the selector finds the pods the template makes.*

**`apiVersion: apps/v1`.** Pods are core (`v1`); Deployments live in the `apps` API group. The skeleton fills this in correctly — just know *why* it differs.

Apply it:

```bash
kubectl apply -f flask-deployment.yaml
# deployment.apps/flask created
```

---

## Chunk 4 — What just happened: the ownership chain

One `apply` created a small organization. Look at each layer:

```bash
kubectl get deploy
# NAME    READY   UP-TO-DATE   AVAILABLE   AGE
# flask   3/3     3            3           15s

kubectl get rs
# NAME               DESIRED   CURRENT   READY   AGE
# flask-6d4b8c9f7    3         3         3       15s

kubectl get pods
# NAME                     READY   STATUS    RESTARTS   AGE
# flask-6d4b8c9f7-2xk9p    1/1     Running   0          15s
# flask-6d4b8c9f7-7tq4m    1/1     Running   0          15s
# flask-6d4b8c9f7-w8n2d    1/1     Running   0          15s
```

Read the pod names — they tell the whole story: `flask` (the Deployment) → `6d4b8c9f7` (the ReplicaSet's hash) → `2xk9p` (the individual pod's random suffix). You declared *one* Deployment; it created *one* ReplicaSet; that ReplicaSet created *three* pods. The chain made itself. See it all at once:

```bash
kubectl get all
kubectl describe deployment flask     # strategy, replica status, and Events
```

In the Deployment's `describe` output, note the `StrategyType: RollingUpdate` line — that's the rolling-update machinery sitting ready, which we'll trigger in Chunk 8.

---

## Chunk 5 — Self-healing, at last

This is the moment Module 1 was building toward. Delete one of the three pods (copy a real name from your `get pods`) and **predict** what happens to the count:

```bash
kubectl delete pod flask-6d4b8c9f7-2xk9p
kubectl get pods -w
```

```
flask-6d4b8c9f7-2xk9p   1/1   Terminating         ...
flask-6d4b8c9f7-h5r3c   0/1   ContainerCreating   ...   ← a NEW one, instantly
flask-6d4b8c9f7-h5r3c   1/1   Running             ...
```

A replacement appeared the instant the old one began terminating — because the ReplicaSet's loop saw `2 ≠ 3` and reconciled. Contrast this directly with Module 1: there, the deleted bare pod stayed dead, because nothing held its desired state. Here, the ReplicaSet *is* that something. `Ctrl+C` to stop watching.

You can't win this fight. Delete two at once, force-delete them — the count snaps back to three every time. *That* is self-healing, and it's the whole reason you run Deployments instead of pods. (The same loop is what reschedules pods onto other machines when a node dies — invisible on our single node, but the mechanism is identical.)

---

## Chunk 6 — Scaling

Now the second superpower the bare pod lacked. Two ways to scale, and the difference matters.

**Imperative — fast, for a quick bump:**

```bash
kubectl scale deployment flask --replicas=5
kubectl get pods        # five now; watch two new ones spin up
```

**Declarative — the real way:** edit `replicas: 5` in `flask-deployment.yaml`, then:

```bash
kubectl apply -f flask-deployment.yaml
```

There's a gotcha here that trips everyone, and it's a *good* gotcha because it reinforces the whole declarative model. Suppose you `kubectl scale` to 5 imperatively, but your file still says `replicas: 3`. The next time anyone runs `kubectl apply -f flask-deployment.yaml`, the Deployment snaps back to **3** — because the file is the source of truth, and `apply` reconciles reality to the file. The lesson: pick a lane. Use `scale` for throwaway experiments; for anything that should stick, change the file. (This is also a preview of why autoscaling, Module 10, needs special handling — something *else* manages the replica count, and your file must step aside.)

Scale back down and confirm pods are removed:

```bash
kubectl scale deployment flask --replicas=3
```

You now have three Flask pods, each with a different name. **A forward hook:** you might want to `curl` all three and watch different pod names answer — but `port-forward` only ever targets *one* pod, so you can't load-balance across them yet. That satisfying "watch traffic spread across replicas" demo is the payoff of Module 4's Service. For now, the win is simply that three identical, self-healing pods exist.

---

## Chunk 7 — Labels & selectors, the universal glue

Labels have been quietly accumulating since Module 2; now they earn a proper look, because the *same* mechanism you learn here drives Services (Module 4), and most of how you'll query and operate the cluster.

A **label** is an arbitrary `key: value` tag on an object. A **selector** is a query against those tags. Filter pods by label with `-l`:

```bash
kubectl get pods -l app=flask        # only pods labelled app=flask
kubectl get pods --show-labels       # reveal every label each pod carries
```

That second command surfaces something you didn't write:

```
NAME                    ...   LABELS
flask-6d4b8c9f7-7tq4m   ...   app=flask,pod-template-hash=6d4b8c9f7
```

You wrote `app=flask`. The Deployment *automatically added* `pod-template-hash=6d4b8c9f7`. This little label is doing crucial work: it's how the ReplicaSet's real selector scopes itself to *exactly its own generation* of pods. And it's the secret to how a rolling update works — during a rollout there are briefly *two* ReplicaSets (old and new), each managing only the pods carrying *its* hash, so they never fight over each other's pods. Keep this in your back pocket for the next chunk; it'll make the rollout legible.

Two rules worth banking:

- **The Deployment's `selector` is immutable.** You can change replicas, the image, almost anything — but not the selector, after creation. Choose labels you won't need to change.
- **The same selector idea routes traffic.** When you write a Service in Module 4, you'll give it a selector like `app: flask`, and it'll find these exact pods to send requests to. Labels are the connective tissue of the entire cluster — not bookkeeping, but *wiring*.

---

## Chunk 8 — Rolling updates: shipping v2 with zero downtime

The third superpower, and the one with no real Docker or Compose equivalent — Compose recreated containers with a gap; this drops *zero* requests.

First, build a v2 of the app so there's something visible to roll to. Edit `app.py` to add a version:

```python
APP_VERSION = "2.0"

@app.route("/")
def home():
    greeting = os.environ.get("GREETING", "Hello from Flask on Kubernetes")
    return f"{greeting} (v{APP_VERSION})\nServed by pod: {POD_NAME}\n"

@app.route("/version")
def version():
    return f"{APP_VERSION}\n"
```

Build the new image (a new tag — never `:latest`, per Module 2):

```bash
docker build -t flaskapp:2.0 .
```

Now roll the Deployment from `1.0` to `2.0`. The declarative way is to change `image: flaskapp:2.0` in your file and `apply`; the quick imperative way is:

```bash
kubectl set image deployment/flask flask=flaskapp:2.0
```

(That's `deployment/flask`, container name `flask`, new image.) Immediately watch it happen, in two terminals:

```bash
# terminal 1 — the official progress bar
kubectl rollout status deployment/flask
# Waiting for deployment "flask" rollout to finish: 1 out of 3 new replicas...
# deployment "flask" successfully rolled out

# terminal 2 — the mechanics, live
kubectl get rs -w
```

In terminal 2 you'll see the story from Chunk 7 play out: a **new** ReplicaSet (new hash) scaling *up* from 0, while the **old** one scales *down* to 0. They never collide, because `pod-template-hash` keeps their pods separate. At every instant during the swap, enough pods are serving — that's the zero-downtime guarantee.

Two knobs control the swap, both in the Deployment's `strategy` (defaults shown):

- **maxUnavailable: 25%** — how many pods may be *down* at once during the roll. Lower = safer, slower.
- **maxSurge: 25%** — how many *extra* pods (above the desired count) may exist temporarily. Higher = faster, more resource use.

With 3 replicas and the defaults, Kubernetes adds new pods before removing old ones, keeping the service continuously available. Confirm the new version landed:

```bash
kubectl port-forward deployment/flask 8080:5000
# then in another terminal:
curl localhost:8080      # ...Hello from Flask on Kubernetes (v2.0)...
```

---

## Chunk 9 — Rollout history, and undoing a bad deploy

Rolling forward is half the feature; rolling *back* is the half that lets you sleep. Every rollout is a numbered revision:

```bash
kubectl rollout history deployment/flask
# REVISION  CHANGE-CAUSE
# 1         <none>
# 2         <none>
```

Now the lesson that earns Deployments their keep — **a broken deploy that doesn't take you down.** Roll to an image that doesn't exist and predict what happens to your *live* traffic:

```bash
kubectl set image deployment/flask flask=flaskapp:9.9
kubectl rollout status deployment/flask
# Waiting for deployment "flask" rollout to finish: 1 old replicas are pending termination...
#   (it hangs — the rollout is stuck)
```

In another terminal:

```bash
kubectl get pods
# the NEW pod is ImagePullBackOff... but the OLD v2.0 pods are STILL RUNNING
```

This is the payoff: because `maxUnavailable` won't let the old pods go until new ones are *ready*, and the new ones never become ready, the rollout simply **stalls with your last good version still serving.** A bad deploy stuck itself in the doorway instead of bringing down your app. Recover with one command:

```bash
kubectl rollout undo deployment/flask        # back to the previous revision
kubectl rollout status deployment/flask      # successfully rolled out
curl-check via port-forward → v2.0 again
```

`undo` rolls back to the prior revision; `--to-revision=N` targets a specific one (that's what the history list is for). The old ReplicaSets are kept around precisely so this works — capped by `revisionHistoryLimit` (default 10).

One more rollout verb you'll use weekly:

```bash
kubectl rollout restart deployment/flask
```

This gracefully recreates every pod (a fresh rolling update with the same image) — the go-to for picking up a changed ConfigMap or Secret, which is exactly how you'll use it in Module 5.

---

## Chunk 10 — Cleanup

Deleting the Deployment cascades down the whole ownership chain — Deployment → ReplicaSets → Pods — in one command:

```bash
kubectl delete -f flask-deployment.yaml
# deployment.apps "flask" deleted
kubectl get all
# only service/kubernetes remains → clean
```

That cascade is the deletion mirror of the creation chain from Chunk 4: you manage the top of the tree, and Kubernetes handles everything beneath it. As always on macOS, toggle Kubernetes off in Docker Desktop if you want the idle control-plane RAM back.

---

## Chunk 11 — Rare-but-real (recognize, don't memorize)

```bash
kubectl rollout pause deployment/flask     # freeze rollouts to batch several edits...
kubectl rollout resume deployment/flask    # ...then unfreeze and roll once
kubectl set resources deployment/flask --limits=memory=256Mi   # quick edits (Module 7)
kubectl set env deployment/flask GREETING="hi"                 # set an env var (Module 5 does this properly)
kubectl autoscale deployment/flask --min=3 --max=10 --cpu-percent=70   # HPA teaser (Module 10)
kubectl get deploy flask -o yaml | grep -A5 strategy           # inspect the rollout strategy
```

Two `strategy` values to recognize in other people's manifests: `RollingUpdate` (the default you've been using) and `Recreate` (kill all old pods, *then* start new ones — accepts downtime, used when two versions must never run at once, e.g. an incompatible schema migration).

---

## Chunk 12 — Command cheat sheet

| Goal | Command |
|---|---|
| Generate a Deployment skeleton | `kubectl create deployment <n> --image=<img> --dry-run=client -o yaml` |
| Apply / update declaratively | `kubectl apply -f deploy.yaml` |
| List deployments / replicasets | `kubectl get deploy` · `kubectl get rs` |
| The whole ownership chain | `kubectl get all` |
| Scale (imperative) | `kubectl scale deployment <n> --replicas=5` |
| Filter pods by label | `kubectl get pods -l app=flask` |
| Show all labels | `kubectl get pods --show-labels` |
| Roll out a new image | `kubectl set image deployment/<n> <container>=<img>:<tag>` |
| Watch a rollout | `kubectl rollout status deployment/<n>` |
| Watch the RS swap | `kubectl get rs -w` |
| Rollout history | `kubectl rollout history deployment/<n>` |
| Roll back | `kubectl rollout undo deployment/<n> [--to-revision=N]` |
| Restart all pods | `kubectl rollout restart deployment/<n>` |
| Detail + events | `kubectl describe deployment <n>` |
| Delete (cascades) | `kubectl delete -f deploy.yaml` |

---

## Chunk 13 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — stand it up, prove it heals**
1. Write (via skeleton) and apply a Deployment named `web` running `flaskapp:1.0`, 3 replicas, label `app: web`. Make sure the selector and template labels match — and explain in one line what breaks if they don't.
2. Show the ownership chain: the Deployment, its ReplicaSet, and its three pods. Explain what each part of a pod's name (`web-xxxx-yyyyy`) means.
3. Delete one pod and prove a replacement appears. Name the controller that did it and the comparison it made.
4. Scale to 6, then back to 3 — using whichever method you'd trust for a permanent change, and say why.

**Challenge B — ship, break, recover**
1. Build `flaskapp:2.0` and roll `web` from `1.0` to `2.0`. Watch the new ReplicaSet scale up as the old scales down, and explain what keeps their pods from getting mixed up.
2. Confirm v2 is serving via a port-forward.
3. Deliberately roll to `flaskapp:7.7` (never built). Describe what happens to (a) the new pod and (b) your *currently running* v2 pods — and explain *why your app stays up* during this failed rollout.
4. Recover in one command, and name the resource Kubernetes kept around that made the recovery possible.

**Bonus question (mental model):** A Deployment, a ReplicaSet, and the kubelet are all running reconciliation loops, but each watches a *different* desired-vs-actual gap. State, in one sentence each, exactly what gap each one is responsible for closing. (Hint: think *pod version/template*, *pod count*, and *container liveness*.)

---

*End of Module 3. Next: Module 4 — Services & Networking, where those three identical pods finally get a single stable address, traffic load-balances across them, and your Flask pods learn to find Postgres and Redis by name — the cluster-scale version of the trick you pulled with container names in the Docker course.*
