# Module 10 — Scaling & Scheduled Work

> **Hands-on rule:** type every command. The autoscaler and the scheduler both make decisions *on their own* — you only trust them after you've generated load and watched replicas climb, or spun up a multi-node cluster and watched pods land on different machines.
>
> **Environment:** macOS + Docker Desktop (Kubeadm) for the first half; a temporary **`kind`** multi-node cluster for the scheduling cameo. Your main stack stays untouched throughout.
>
> **Where we're picking up:** you've scaled by hand since Module 3 (`kubectl scale --replicas=N`) — fine, but it means *you* have to watch load and react. This module hands three jobs to the cluster: scale automatically with demand, run work that finishes and stops (Jobs and CronJobs), and decide *which machine* each pod runs on. That last one only makes sense with more than one node, so it's the `kind` cameo we planned.

---

## Chunk 1 — From manual to automatic scaling

Manual scaling has an obvious flaw: it requires a human in the loop. Traffic spikes at 2 a.m., and unless someone's watching the dashboards and types `kubectl scale`, your three pods drown. The **HorizontalPodAutoscaler (HPA)** removes the human: it watches a metric (usually CPU), compares it to a target you set, and adjusts the replica count to keep the metric near that target — the same reconciliation loop you've known since Module 1, now applied to *replica count* driven by *live load*.

---

## Chunk 2 — Prerequisites: metrics, a CPU-heavy endpoint, and quota headroom

HPA needs three things in place first.

**Metrics.** HPA computes utilization as *usage ÷ request*, so it needs the metrics pipeline. That's **metrics-server** from Module 7 — confirm it works:

```bash
kubectl top pods -n notes
```

If that errors, install it (the components manifest plus the Docker-Desktop TLS patch from Module 7's Chunk 11), then retry.

**Something that burns CPU.** Flask serving a counter barely touches the CPU, so there'd be nothing to scale on. Add a deliberately CPU-heavy endpoint to `app.py` and build `flaskapp:6.0`:

```python
@app.route("/compute")
def compute():
    x = 0
    for _ in range(5_000_000):
        x += 1
    return f"done {x}\n"
```

```bash
docker build -t flaskapp:6.0 .
# set image: flaskapp:6.0 in flask-deployment.yaml, then:
kubectl apply -f flask-deployment.yaml -n notes
```

**Quota headroom.** Heads up — your Module 8 `ResourceQuota` caps total CPU *requests* for the namespace, which means it also caps how high HPA can scale. With Flask requesting 100m per pod and the quota at `requests.cpu: "1"`, you'd stall around 7–8 pods. Bump it so HPA has room (a real, worth-knowing interaction between quotas and autoscaling):

```bash
# raise requests.cpu to "2" in quota.yaml, then:
kubectl apply -f quota.yaml -n notes
```

---

## Chunk 3 — Create the HPA

The quickest way is imperative; the durable way is a manifest. Here's the manifest (`hpa.yaml`), using the stable `autoscaling/v2` API:

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: flask
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: flask
  minReplicas: 1
  maxReplicas: 10
  metrics:
  - type: Resource
    resource:
      name: cpu
      target:
        type: Utilization
        averageUtilization: 50      # keep average CPU near 50% of each pod's request
```

```bash
kubectl apply -f hpa.yaml -n notes
kubectl get hpa -n notes
# NAME    REFERENCE          TARGETS       MINPODS   MAXPODS   REPLICAS
# flask   Deployment/flask   cpu: 1%/50%   1         10        3
```

`TARGETS` reads as *current ÷ target*. Utilization is relative to each pod's **request** (Flask requests 100m), so a pod actually using 100m is "100%." At a 50% target, HPA aims to keep the *average* across pods at ~50m, adding or removing replicas to get there.

**The critical rule — and the Module 3 gotcha finally paying off.** Once an HPA owns a Deployment's replica count, you must **remove `replicas:` from the Deployment manifest.** If both exist, every `kubectl apply` resets replicas to the file's value, HPA scales it back, and they fight forever. This is exactly the "something else manages the replica count" case I flagged back in Module 3 — delete the `replicas:` line from `flask-deployment.yaml` now.

---

## Chunk 4 — Watch it autoscale

Generate load by hammering `/compute` from an in-cluster pod:

```bash
kubectl run load -n notes --image=busybox --restart=Never -- \
  /bin/sh -c "while true; do wget -q -O- http://flask:8080/compute; done"
```

Now watch the autoscaler react (give it a minute — metrics are sampled, not instant):

```bash
kubectl get hpa -n notes -w
# TARGETS climbs past 50%... REPLICAS: 3 → 5 → 8 → ...
kubectl get pods -n notes        # new flask pods appearing to share the load
```

The single overloaded pod pushed utilization way over target, so HPA spun up more to spread the work. Now remove the load and watch it scale back down:

```bash
kubectl delete pod load -n notes
kubectl get hpa -n notes -w      # TARGETS drops... but REPLICAS shrinks SLOWLY
```

Note the asymmetry: HPA **scales up fast** (a spike needs immediate help) but **scales down slowly** — by default it waits ~5 minutes of sustained low usage before removing pods, so a brief dip doesn't cause flapping. That patience is deliberate; thrashing replicas up and down would be worse than running a few extra for a while.

---

## Chunk 5 — Jobs: work that finishes

Everything you've deployed so far runs *forever* — Deployments and StatefulSets assume a long-lived service. But plenty of work is the opposite: a database migration, a batch import, a one-off computation. That's a **Job** — it runs pods until they *complete successfully*, then stops. Create `seed-job.yaml`:

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: seed-notes
spec:
  backoffLimit: 4                  # retry up to 4 times on failure, then give up
  template:
    spec:
      restartPolicy: OnFailure     # Jobs use OnFailure or Never — NEVER Always
      containers:
      - name: seed
        image: postgres:16-alpine
        command: ["sh", "-c",
          "psql -h postgres -U postgres -c \"CREATE TABLE IF NOT EXISTS notes (id SERIAL PRIMARY KEY, text TEXT); INSERT INTO notes (text) VALUES ('seeded by a Job')\""]
        env:
        - name: PGPASSWORD
          valueFrom:
            secretKeyRef:
              name: db-secret
              key: password
```

```bash
kubectl apply -f seed-job.yaml -n notes
kubectl get jobs -n notes        # COMPLETIONS goes 0/1 → 1/1
kubectl get pods -n notes        # the seed pod ends as "Completed", not "Running"
kubectl logs job/seed-notes -n notes
curl -H "Host: flask.local" http://localhost/notes   # the seeded note is there
```

The defining contrast: a **Deployment** with `restartPolicy: Always` would treat that completed pod as a crash and restart it endlessly. A **Job** understands that *done means done* — it runs the work, records success, and stops. That single difference (`completion` is a goal, not a failure) is the whole reason Jobs exist. (`completions` and `parallelism` let a Job run many pods to finish a batch; `backoffLimit` caps retries.)

---

## Chunk 6 — CronJobs: work on a schedule

A **CronJob** is a Job on a timer — it creates a new Job on a cron schedule. Backups, nightly reports, cleanup. Create `heartbeat-cron.yaml` (running every minute for demo speed):

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: heartbeat
spec:
  schedule: "* * * * *"            # every minute (min hour day month weekday)
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
          - name: beat
            image: postgres:16-alpine
            command: ["sh", "-c",
              "psql -h postgres -U postgres -c \"INSERT INTO notes (text) VALUES ('heartbeat ' || now())\""]
            env:
            - name: PGPASSWORD
              valueFrom:
                secretKeyRef:
                  name: db-secret
                  key: password
```

```bash
kubectl apply -f heartbeat-cron.yaml -n notes
kubectl get cronjob -n notes
kubectl get jobs -n notes -w     # a new job appears each minute
```

Wait two or three minutes, then check `curl -H "Host: flask.local" http://localhost/notes` — timestamped heartbeats accumulating. **Stop it before it fills your table** by suspending or deleting it:

```bash
kubectl patch cronjob heartbeat -n notes -p '{"spec":{"suspend":true}}'   # pause it
# or: kubectl delete cronjob heartbeat -n notes
```

Useful CronJob fields to know: `concurrencyPolicy` (what to do if the previous run is still going — `Allow`/`Forbid`/`Replace`), and `successfulJobsHistoryLimit`/`failedJobsHistoryLimit` (how many finished Jobs to keep around for inspection).

---

## Chunk 7 — The `kind` cameo: a real multi-node cluster

Everything so far ignored *which* node runs a pod, because you only have one. Scheduling — the cluster deciding pod placement — only becomes visible with several nodes. So we spin up a throwaway multi-node cluster with **`kind`** (Kubernetes-in-Docker), *alongside* your Kubeadm cluster, which stays completely untouched. This is also where Module 8's context-switching earns its keep.

```bash
brew install kind        # if you don't have it
cat > kind-3node.yaml <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
- role: worker
EOF
kind create cluster --name sched --config kind-3node.yaml
```

`kind` automatically adds a context and switches to it — exactly the kubeconfig mechanism from Module 8:

```bash
kubectl config get-contexts        # the active one is now kind-sched
kubectl get nodes                  # THREE nodes: one control-plane, two workers
```

Your Docker Desktop stack is still running, in its own cluster; `kubectl config use-context docker-desktop` returns to it any time.

---

## Chunk 8 — Watch the scheduler spread pods

Deploy something simple with several replicas and look at *where* they land:

```bash
kubectl create deployment spread --image=nginx:1.27-alpine --replicas=6
kubectl get pods -o wide           # the NODE column — pods spread across the two workers
```

The scheduler distributed the six pods across the worker nodes on its own — that's its default behavior, balancing load. Notice they all landed on *workers*, never the control-plane. That's not luck: the control-plane node carries a **taint** that repels ordinary pods (next chunk). Seeing placement actually vary across machines is the whole point of this cameo.

---

## Chunk 9 — Steering and repelling pods

Two opposite mechanisms control placement.

**Attracting — `nodeSelector` and affinity.** Label a node, then tell a pod it wants that label:

```bash
kubectl label node sched-worker disk=ssd
kubectl run pinned --image=nginx:1.27-alpine \
  --overrides='{"spec":{"nodeSelector":{"disk":"ssd"}}}'
kubectl get pod pinned -o wide     # lands only on the node labelled disk=ssd
```

`nodeSelector` is the blunt version; **node affinity** is the expressive one (`required` vs `preferred` rules). Its cousin, **pod anti-affinity**, spreads replicas apart — "never put two of these on the same node" — the standard trick for surviving a node failure.

**Repelling — taints and tolerations.** A **taint** is a node saying "keep off unless you explicitly tolerate me." Taint a worker and watch new pods avoid it:

```bash
kubectl taint node sched-worker2 dedicated=gpu:NoSchedule
# new pods won't schedule there unless they carry a matching toleration:
#   tolerations:
#   - key: dedicated
#     value: gpu
#     effect: NoSchedule
```

This is exactly why your pods avoided the control-plane in Chunk 8 — it's tainted by default. Taints reserve nodes for specific workloads (GPU nodes, dedicated tenants); tolerations are the permission slip that lets a pod onto them.

---

## Chunk 10 — DaemonSets: one pod per node

A **DaemonSet** runs *exactly one pod on every (eligible) node* — and automatically adds one when a node joins. It's the pattern for node-level agents: log collectors (Fluent Bit), metrics exporters (node-exporter), networking plugins. Deploy one and watch:

```bash
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: node-agent
spec:
  selector:
    matchLabels: { app: node-agent }
  template:
    metadata:
      labels: { app: node-agent }
    spec:
      containers:
      - name: agent
        image: busybox
        command: ["sh", "-c", "while true; do sleep 3600; done"]
EOF
kubectl get pods -o wide -l app=node-agent
# one pod on EACH worker (the control-plane is skipped — its taint, again)
```

Two pods on your three-node cluster (the tainted control-plane is skipped, since this DaemonSet has no matching toleration) — and if you added a fourth worker, a third agent pod would appear on it automatically. No replica count to manage; "one per node" *is* the spec.

---

## Chunk 11 — Tear down the cameo, return home

```bash
kind delete cluster --name sched
kubectl config use-context docker-desktop
kubectl get pods -n notes          # your stack, exactly as you left it
```

That painless round-trip — stand up a second cluster, experiment, delete it, switch back, main stack untouched — is contexts (Module 8) doing precisely the job they're for. The scheduling concepts (affinity, taints, DaemonSets) are real and you'll meet them in any production cluster; you just can't *feel* them on a single node, which is why the cameo exists.

---

## Chunk 12 — Rare-but-real (recognize, don't memorize)

- **Cluster Autoscaler** — adds and removes *nodes* (not pods) based on demand; the cloud-side complement to HPA.
- **VPA (VerticalPodAutoscaler)** — right-sizes a pod's requests/limits instead of changing replica count.
- **KEDA** and **custom/external metrics** — scale on requests-per-second, queue depth, or any signal, not just CPU/memory.
- **`topologySpreadConstraints`** — the modern, declarative way to spread pods evenly across nodes/zones (supersedes most anti-affinity uses).
- **PodDisruptionBudget** — "at least N must stay up" during voluntary disruptions like node drains and upgrades.
- **PriorityClass + preemption** — high-priority pods can evict lower-priority ones when a node is full.

---

## Chunk 13 — Command cheat sheet

| Goal | Command |
|---|---|
| Create an autoscaler | `kubectl autoscale deployment <n> --cpu-percent=50 --min=1 --max=10` |
| Watch autoscaling | `kubectl get hpa -w` |
| Live resource usage | `kubectl top pods` (needs metrics-server) |
| Run a one-off task | a `Job` (`restartPolicy: OnFailure`/`Never`) |
| List jobs | `kubectl get jobs` |
| Run on a schedule | a `CronJob` (`schedule: "* * * * *"`) |
| Pause a CronJob | `kubectl patch cronjob <n> -p '{"spec":{"suspend":true}}'` |
| Multi-node cluster | `kind create cluster --config kind-3node.yaml` |
| See pod placement | `kubectl get pods -o wide` (NODE column) |
| Steer pods to a node | `nodeSelector` / node affinity |
| Keep pods off a node | `kubectl taint node <n> key=val:NoSchedule` |
| One pod per node | a `DaemonSet` |

---

## Chunk 14 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — let the cluster scale itself**
1. Confirm `kubectl top pods` works, then create an HPA targeting Flask at 50% CPU, min 1, max 10. Explain what number 50% is measured *against*.
2. Generate load against `/compute` and show the replica count climbing. Then remove the load and explain why the scale-*down* is much slower than the scale-up.
3. State the one edit you must make to the Deployment manifest once an HPA owns it, and explain the loop that breaks if you don't.
4. Explain how your Module 8 ResourceQuota could secretly cap the autoscaler.

**Challenge B — work that finishes, and where it runs**
1. Write a Job that runs a `psql` command against Postgres and exits. Explain why its `restartPolicy` can't be `Always`, and how a Job differs from a Deployment running the same container.
2. Turn it into a CronJob that runs every minute, confirm new Jobs appear on schedule, then suspend it.
3. On a `kind` multi-node cluster, deploy 6 replicas and show they spread across workers but avoid the control-plane. Explain the mechanism that keeps them off it.
4. Explain what a DaemonSet guarantees that a Deployment with `replicas: N` cannot, and give one real use case.

**Bonus question (mental model):** HPA, a Job, and a DaemonSet all decide "how many pods should exist" — but each answers it from a completely different input. State, in one sentence each, what determines the pod count for the three: an HPA-managed Deployment, a Job, and a DaemonSet. (Hint: think *live metric*, *completions goal*, and *number of nodes*.)

---

*End of Module 10. Next: Module 11 — Security & RBAC, where we stop letting everything run with god-mode access: ServiceAccounts give pods their own identity, Roles and RoleBindings grant least-privilege permissions, and you'll see exactly what a pod is and isn't allowed to ask the API server to do.*
