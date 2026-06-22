# Module 12 — Observability & Debugging: Where Do I Look?

> **Hands-on rule:** type every command. The capstone of this module is a *manufactured* outage you diagnose by following a method — you learn the playbook by walking it, not reading it.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner.
>
> **Where we're picking up:** across eleven modules you've accumulated a dozen inspection reflexes — `describe`, `logs --previous`, `get endpoints`, `auth can-i`, `top`, the throwaway debug pod. Individually they answered individual questions. This module assembles them into a *method*: a systematic way to go from "it's broken" to "here's exactly why" without flailing. It's the cluster-scale evolution of Docker Module 9's inspect/events debugging — same instinct, far more surface area.

---

## Chunk 1 — The three pillars: logs, metrics, events

Observability is answering "what's happening inside?" from the outside, and Kubernetes gives you three distinct data sources — each answers a *different* question, and knowing which to reach for is half the battle:

- **Logs** — *what did the app say?* The container's stdout/stderr. `kubectl logs`.
- **Metrics** — *how much is it using, how is it performing?* CPU, memory, request rates. `kubectl top`, and Prometheus for the real thing.
- **Events** — *what did Kubernetes itself do and decide?* Scheduling, image pulls, probe failures, evictions, scaling. `kubectl describe` and `kubectl get events`.

(A fourth pillar, **traces**, follows a single request as it hops across services — essential for distributed systems, recognition-level here.) Most debugging dead-ends come from looking in the wrong pillar — checking logs for a scheduling problem, when the answer was in events all along.

---

## Chunk 2 — The mental model: localize before you drill

The cardinal sin of debugging is poking randomly. The skill is *localizing* the failure to one layer first, then drilling. Two complementary directions:

**Trace the request path** — for "I can't reach it" symptoms. A request flows: `client → Ingress → Service → endpoints → pod → container → app`. The break is at exactly one hop. Walk the chain and find where it stops.

**Climb the ownership hierarchy** — for "wrong or missing pods" symptoms. Objects nest: `Deployment → ReplicaSet → Pod → container`. The problem lives at one level — is the Deployment confused, the ReplicaSet unable to create pods, or the container itself crashing?

Pick the direction that fits the symptom, localize to a layer, *then* drill in. That discipline turns a panic into a procedure.

---

## Chunk 3 — The toolkit, assembled

Everything you already know, organized by the question it answers:

| Question | Tool |
|---|---|
| What's the *status*? | `kubectl get` (`-o wide`, the `RESTARTS` column, `-A`) |
| What *happened* to this object? | `kubectl describe <obj>` → the **Events** section |
| What did the *cluster* do, in order? | `kubectl get events --sort-by=.lastTimestamp` |
| What did the *app say*? | `kubectl logs` (`--previous`, `-f`, `--since`, `-l`, `--all-containers`) |
| Inside a *running* container | `kubectl exec` |
| Inside a container with *no shell / crashing* | `kubectl debug` |
| Is the *network* wired up? | `kubectl get endpoints`, `describe svc/ingress`, debug pod |
| Is it a *permissions* problem? | `kubectl auth can-i --as=...` |
| Is it a *resource* problem? | `kubectl top`, `describe` (limits) |

If you remember nothing else: **`describe` (read the Events) and `logs --previous` resolve the majority of real-world failures.** Reach for those two first, almost always.

---

## Chunk 4 — Reading status: each STATUS and where to look next

The pod statuses you've met across the course, now as a diagnostic table — STATUS tells you *which pillar* has the answer:

| STATUS | Likely cause | First move |
|---|---|---|
| `Pending` | Can't be scheduled — no node has room, quota exceeded, PVC unbound, taint/affinity mismatch | `describe pod` → Events |
| `ContainerCreating` (stuck) | Image still pulling, or a volume won't attach | `describe pod` → Events |
| `ImagePullBackOff` | Wrong image/tag, private registry, the `:latest` trap | `describe pod` → Events |
| `CrashLoopBackOff` | App starts then exits, repeatedly | `logs --previous` |
| `OOMKilled` | Exceeded its memory limit | `describe pod` → Last State; fix limit/leak |
| `Error` / non-zero exit | App failed | `logs`; read the exit code |

Exit codes carry meaning worth knowing: **`0`** clean, **`1`** generic app error, **`137`** = 128+9, killed by `SIGKILL` (usually OOM), **`143`** = 128+15, `SIGTERM` (a graceful stop). You'll see these in `describe pod` under *Last State → Terminated*.

---

## Chunk 5 — Events: the cluster's timeline

When you need the chronological story of what the cluster *did*, events are it:

```bash
kubectl get events -n notes --sort-by=.lastTimestamp        # oldest→newest cluster activity
kubectl get events -n notes --field-selector type=Warning   # just the problems
kubectl events -n notes --for pod/<name>                     # events for one object (newer command)
```

This is where you see "Scheduled," "Pulling," "Liveness probe failed," "OOMKilling," "scaled up replica set," "FailedScheduling: Insufficient cpu" — the decisions and reactions that `get` and `logs` don't show. One catch: **events are short-lived** (retained roughly an hour by default). For an incident that happened overnight, `kubectl get events` will already have forgotten it — which is exactly why production needs a persistent store (Chunk 10).

---

## Chunk 6 — Logs at scale

Single-pod `kubectl logs` you know cold. With an HPA running many Flask replicas, you need to read them *together*:

```bash
kubectl logs -l app=flask -n notes --all-containers=true --tail=50   # every matching pod at once
kubectl logs -l app=flask -n notes --since=10m --timestamps          # last 10 min, time-stamped
kubectl logs <pod> -n notes --previous                               # the dead container's last words
```

The `-l` selector turns "which of the eight pods logged the error?" into one command. The hard limit is that **pod logs die with the pod** — delete a pod (or let it crash and get replaced) and its logs are gone unless you grabbed `--previous` in time. That ephemerality is the whole reason centralized logging exists (Chunk 10). (The community tool `stern` tails multiple pods with color-coding and is worth installing for live work.)

---

## Chunk 7 — Getting inside: `exec` and `kubectl debug`

`kubectl exec -it <pod> -- sh` works when the image *has* a shell. But modern images are often minimal or *distroless* — no shell, no tools, sometimes the container is crash-looping and never stays up long enough to exec into. That's what `kubectl debug` is for: it attaches an **ephemeral debug container** that shares the target pod's namespaces (network, and optionally processes), bringing its own toolset:

```bash
kubectl debug -it <pod> -n notes --image=busybox --target=<container> -- sh
# now you're "inside" the pod's network, with busybox's tools, even if the app image has none
```

It can also clone a crashing pod so you can poke at a working copy (`--copy-to`), or debug a node directly (`kubectl debug node/<node>`). When `exec` fails because there's nothing to exec *into*, `debug` is the answer.

---

## Chunk 8 — The capstone: diagnose a manufactured outage

Now apply the method. Break something subtle — introduce a typo into the Flask Service's selector so it points at a label no pod carries:

```bash
# edit flask-service.yaml: change selector app: flask  →  app: flaskk
kubectl apply -f flask-service.yaml -n notes
curl -H "Host: flask.local" http://localhost/
# 503 Service Temporarily Unavailable
```

Don't guess — **trace the request path** (Chunk 2):

```bash
# 1. The symptom is a 503 from the ingress. From Module 9: 503 = the backend has no READY endpoints.
kubectl describe ingress -n notes | grep -A3 flask.local      # backend → service "flask"

# 2. So check that service's endpoints — the real pod IPs behind it:
kubectl get endpoints flask -n notes
# ENDPOINTS   <none>          ← EMPTY. The service fronts nothing.

# 3. Why empty? Either no pods match its selector, or none are ready. Check the selector:
kubectl describe svc flask -n notes | grep Selector
# Selector:  app=flaskk                                       ← there's the typo
kubectl get pods -l app=flaskk -n notes        # No resources found  → matches nothing
kubectl get pods -l app=flask  -n notes        # your 3 pods, all healthy, labelled app=flask
```

Localized in three steps: the pods are fine, the Ingress is fine — the *Service selector* matches nothing, so it has no endpoints, so the Ingress has nowhere to route. Fix and confirm:

```bash
# restore selector to app: flask, then:
kubectl apply -f flask-service.yaml -n notes
kubectl get endpoints flask -n notes           # 3 IPs reappear
curl -H "Host: flask.local" http://localhost/  # 200 OK
```

The general lesson, which you'll use constantly: **empty endpoints means either a selector mismatch or no *ready* pods** — and that single check (`get endpoints`) collapses most "I can't reach my service" mysteries. (You've manufactured the other big failure classes earlier — `ImagePullBackOff` and `CrashLoopBackOff` in Module 2, `OOMKilled` in Module 7 — the playbook in the next chunk routes all of them.)

---

## Chunk 9 — The playbook

The whole method on one card. Localize by symptom, then drill:

- **Pod not `Running`?** → `describe pod` and read Events.
  - `Pending` → scheduling: insufficient resources, quota, unbound PVC, taint/affinity.
  - `ImagePullBackOff` → image name/tag/registry/auth.
  - `CrashLoopBackOff` → `logs --previous`.
  - `OOMKilled` → memory limit too low or a leak.
- **Pod `Running` but the app misbehaves?** → `logs` first; then `exec` / `debug` to inspect from inside.
- **Can't reach the app?** → trace the path: `describe ingress` → `get endpoints` (empty? → selector or readiness) → pod `logs`.
- **`Forbidden`?** → RBAC: `auth can-i --as=<identity>`, then add the missing verb/resource to a Role (Module 11).
- **Slow, throttled, or evicted?** → `top`, `describe` (limits/QoS), check the HPA.

Internalize the *shape*, not the lines: identify the layer the symptom points to, go straight there, and let `describe` Events and `logs` tell you the rest.

---

## Chunk 10 — The bigger observability picture

`kubectl` is for *interactive, right-now* debugging — it is **not** a monitoring system, and it can't tell you what happened last night. Production layers three things on top, all of which run *inside* the cluster:

- **Metrics** — **Prometheus** scrapes metrics from your pods, stores them as time series, and **Grafana** dashboards them; **Alertmanager** pages you when something's wrong. (The `metrics-server` you installed only feeds HPA and `kubectl top` — it is *not* this.)
- **Logs** — because pod logs are ephemeral, a collector (Fluent Bit / Fluentd, typically running as a **DaemonSet** — one per node, Module 10) ships every pod's logs to a central store (Loki, Elasticsearch) you query through Grafana or Kibana long after the pod is gone.
- **Traces** — OpenTelemetry instrumentation plus a backend (Jaeger, Tempo) reconstructs a single request's journey across services.

You don't build these here, but recognize the division of labor: `kubectl` answers "what's wrong *now*," and the observability stack answers "what happened, and is anything trending toward wrong." (Prometheus is the natural next thing to learn after this course.)

---

## Chunk 11 — Rare-but-real (recognize, don't memorize)

```bash
kubectl cp notes/<pod>:/path/to/file ./file       # copy a file OUT of a pod (like docker cp)
kubectl get --raw /healthz                          # hit an API server endpoint directly
kubectl debug node/<node> -it --image=busybox       # a shell with the node's filesystem mounted
kubectl get events -A --watch                        # live cluster-wide event stream
```

- **`k9s`** — a terminal UI for the whole cluster; many people live in it for day-to-day operations and debugging.
- **`stern`** — multi-pod log tailing with color-coding.
- **`crictl`** — talk to the container runtime directly on a node, below Kubernetes, when the kubelet itself is suspect.
- **Audit logs** — the API server's record of *who did what*, the forensic counterpart to events.

---

## Chunk 12 — Command cheat sheet

| Goal | Command |
|---|---|
| Status, fast | `kubectl get pods -o wide` (watch `RESTARTS`) |
| What happened to X | `kubectl describe <obj> <n>` → **Events** |
| Cluster timeline | `kubectl get events --sort-by=.lastTimestamp` |
| App output | `kubectl logs <pod>` (`-f`, `--since`, `--tail`) |
| Crashed container's logs | `kubectl logs <pod> --previous` |
| Logs across all replicas | `kubectl logs -l app=<x> --all-containers --tail=50` |
| Inside a running pod | `kubectl exec -it <pod> -- sh` |
| Inside a shell-less/crashing pod | `kubectl debug -it <pod> --image=busybox --target=<c> -- sh` |
| Service has backends? | `kubectl get endpoints <svc>` |
| Permission check | `kubectl auth can-i <verb> <res> --as=<identity>` |
| Resource usage | `kubectl top pods` / `kubectl top nodes` |
| Copy a file out | `kubectl cp <ns>/<pod>:<path> ./local` |

---

## Chunk 13 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — the pillars and the method**
1. Name the three observability pillars and the question each answers. For a pod stuck in `Pending`, which pillar holds the answer, and which command surfaces it?
2. Map four statuses — `Pending`, `ImagePullBackOff`, `CrashLoopBackOff`, `OOMKilled` — each to its most likely cause and the single first command you'd run.
3. A container's *Last State* shows exit code `137`. Explain what signal that is and the most common reason a pod gets it.

**Challenge B — walk a real break**
1. Break the Flask Service by giving it a selector that matches no pods, and confirm `curl` returns a 503. Then, *tracing the request path*, find the break in three commands. State what each command ruled in or out.
2. Explain in one sentence what "empty endpoints" tells you, and the two distinct conditions that produce it.
3. You need a shell inside a crash-looping pod whose image has no shell. Name the command, and explain why plain `kubectl exec` fails here.

**Bonus question (mental model):** It's 3 a.m. and `curl` to your app returns a 503. Walk the *entire* request path from the outside in, naming at each hop the one command you'd run and what a failure there would look like — ingress, service, endpoints, readiness, pod, container, app. Then explain why `kubectl get events` might show *nothing* about an incident that happened two hours ago, and what production component would have the answer instead.

---

*End of Module 12. Next: Module 13 — Helm, the finale. Everything you've hand-assembled across twelve modules — Deployments, Services, ConfigMaps, Secrets, the StatefulSet, the Ingress, the HPA — gets packaged into a single installable, versioned, parameterized chart. It's the bow on the whole course, and the doorway to Helm's own.*
