# Module 1 — The Cluster & kubectl: First Contact

> **Hands-on rule:** type every command. Reading is not learning here; your fingers need to learn the verbs. Every code block shows the command *and* what to expect, so you always know whether you're on track.
>
> **Environment:** macOS + Docker Desktop with Kubernetes enabled (we turn it on in Chunk 3). Where the Mac changes things, it's called out.
>
> **Where we're picking up:** you finished the Docker course running the whole Flask + Redis + Postgres stack with Compose — on *one machine*. This module starts exactly at that ceiling and shows you the machine that breaks through it. Your Docker reflexes (`logs`, `exec`, `ps`) are intact and we'll lean on them constantly; the Kubernetes concepts are what we rebuild from the ground up.

---

## Chunk 1 — Why Kubernetes exists at all

Compose was a triumph. One `docker compose up` and your Flask app, Redis, and Postgres came alive together, talking to each other by name. So why isn't that the end of the story?

Because Compose runs everything on **one machine**, and it does only what you literally told it to. Sit with what that means:

- If that machine dies at 3 a.m., your entire app dies with it. Nothing notices, nothing reacts.
- If your Flask container crashes, Compose restarts it (if you asked) — but only on that same box. It can't move the work elsewhere because there *is* nowhere else.
- Black Friday hits and you need twenty copies of Flask across five servers. Compose can't spread across machines.
- You want to ship a new version with zero downtime — old containers draining while new ones warm up. Compose has no concept of that dance.

Compose is a stagehand following a fixed script. The moment reality deviates from the script — a crash, a traffic spike, a dead server — it has no instinct to fix anything.

**Kubernetes is a different kind of thing.** It runs your containers across a *fleet* of machines, and instead of following a script, it pursues a *goal*. You don't tell it "start this container." You tell it **"I want three copies of this running, always"** — and then a piece of Kubernetes loops forever, comparing what *is* to what you *want*, and quietly fixing any gap. A container dies? It starts another. A whole machine dies? It reschedules that machine's work onto survivors. You ask for twenty copies instead of three? It makes seventeen more appear.

This single idea is the soul of Kubernetes, and it's the one mental model to carry through every module:

> **You declare the desired state. Kubernetes reconciles reality toward it — continuously, forever.** This is called the *reconciliation loop*.

The thermostat is the cleanest analogy. You don't flip the heater on and off all evening (imperative). You set 21°C (declarative — your desired state), and the thermostat runs its own loop: read the room, compare to 21, act, repeat. Open a window and the temperature drops — the thermostat doesn't care *why*, it just notices the gap and responds. Kubernetes is a thermostat for running software.

**The Docker bridge.** Everything you did in Docker was *imperative*: `docker run` means "do this now." Kubernetes is *declarative*: "this is what should exist; keep it true." Same containers underneath — Kubernetes runs the very images you built — but a completely different relationship with them. You'll feel this shift physically by the end of the module.

**Where Kubernetes is the right tool**
- Multi-service apps that must survive crashes and machine failures.
- Anything that needs to scale across more than one machine, or roll out updates without downtime.
- Teams that want a single declarative description of "what should be running" that the cluster enforces.

**Where it's overkill**
- A single container on a single box — Compose or plain `docker run` is simpler and you should keep using it.
- Static sites, one-off scripts, personal experiments.
- Anywhere the operational weight of a cluster costs more than the resilience it buys. Kubernetes is powerful *and* heavy; reach for it when the power is worth the weight.

---

## Chunk 2 — What a cluster is actually made of

A **cluster** is the whole Kubernetes system: a set of machines working together to run your containers. Each machine in it is a **node** — just a computer (physical or virtual) that Kubernetes can place work on. Nodes come in two flavors, and the split mirrors something you already know.

**The control plane — the brain.** This is the part that *thinks*. It decides what should run where and keeps the reconciliation loops turning. It has four pieces worth naming:

- **API server** — the front door. *Everything* talks to the cluster through it, including you via `kubectl`. It's the only component that reads and writes the cluster's state, so it's the hub everything else spokes off.
- **etcd** — the cluster's memory. A database that stores both your desired state ("I want 3 Flask pods") and the recorded reality. If etcd is the source of truth, the API server is the only one allowed to touch it.
- **scheduler** — the placement clerk. When a new container needs a home, the scheduler picks which node it lands on, based on available CPU, memory, and rules you'll learn later.
- **controller manager** — the engine room of reconciliation. It runs the loops: "desired says 3, reality says 2, start one more." Most of the magic from Chunk 1 lives here.

**Worker nodes — the muscle.** These actually run your containers. Each one runs:

- **kubelet** — the on-node agent. It takes orders from the API server ("run this container here"), tells the local container runtime to do it, and continuously reports health back up. Think of it as the control plane's hands on each machine.
- **container runtime** — the thing that genuinely starts and stops containers. On Docker Desktop this is `containerd`, the same engine that ran your `docker run` containers. Your images run *unchanged* here.
- **kube-proxy** — wires up networking so containers can reach each other (we go deep on this in Module 4).

**The Docker bridge — same shape, more moving parts.** In Docker, the `docker` CLI (client) talked to the Docker daemon (server) which did the work. Kubernetes is the same client/server split, just scaled up for a bigger job: `kubectl` (client) talks to the **API server** (server). The difference is what happens after. In Docker, the daemon just *did the thing*. In Kubernetes, your request is *recorded as a wish* in etcd; controllers notice the wish, the scheduler picks a node, and that node's kubelet makes it real. You'll see this whole relay happen in Chunk 5.

**The macOS detail.** Remember Docker Desktop's hidden Linux VM? Kubernetes runs *inside that same VM*. Your single node — the entire cluster — *is* that VM. So "the cluster," "the node," and "the Docker Desktop Linux VM" are, for us, three names for one thing. That's why local Kubernetes feels so close to your Docker setup: it's living in the same place.

---

## Chunk 3 — Turning it on and checking the machinery

Open Docker Desktop → **Settings** → **Kubernetes** → tick **Enable Kubernetes** → **Apply & Restart**. The first time, it downloads control-plane images and starts them; give it a minute or two until the indicator (bottom-left of Docker Desktop, or the menu-bar whale) goes green / "Kubernetes running."

Now your first command of the course — and notice how it rhymes with `docker version`:

```bash
kubectl version
```

Expect a client block and a server block:

```
Client Version: v1.3x.x
Kustomize Version: v5.x.x
Server Version: v1.3x.x
```

If you see only the client and an error like `couldn't connect to a server`, Kubernetes isn't up yet — same situation as "the Docker daemon isn't running." Wait for the green light and re-run.

The wider view, mirroring `docker info`:

```bash
kubectl cluster-info
```

```
Kubernetes control plane is running at https://127.0.0.1:6443
CoreDNS is running at https://127.0.0.1:6443/api/v1/namespaces/kube-system/services/kube-dns:dns/proxy
```

That first line is the API server's address — the front door from Chunk 2. (The exact host may read `kubernetes.docker.internal`; that's fine.)

Now meet your cluster's one and only machine:

```bash
kubectl get nodes
```

```
NAME             STATUS   ROLES           AGE   VERSION
docker-desktop   Ready    control-plane   5m    v1.3x.x
```

One node, named `docker-desktop`, `Ready`. Notice its role is `control-plane` — on a single-node cluster the brain and the muscle are the same machine. In production these are separate, but everything you learn here transfers unchanged. Want more detail (internal IP, OS, runtime)?

```bash
kubectl get nodes -o wide
```

The `-o wide` flag — "give me extra columns" — works on almost every `get`, and you'll reach for it constantly.

One last orientation command. `kubectl` can talk to many clusters (a local one, a work one, a cloud one); the active one is your **context**. Confirm you're pointed at the local cluster, not something else:

```bash
kubectl config current-context
# docker-desktop
```

If that ever says something unexpected, you're aiming `kubectl` at the wrong cluster — a real and confusing mistake we'll defuse properly in Module 8.

---

## Chunk 4 — kubectl's grammar: the pattern that unlocks everything

Here's the single most useful thing for getting un-rusty fast. `kubectl` is not a pile of commands to memorize — it's a **grammar**, almost always:

```
kubectl  VERB  RESOURCE  [NAME]  [FLAGS]
```

A *verb* (what to do), a *resource* (what kind of thing), optionally a *name* (which one), and flags. Learn the handful of verbs and they compose against *every* resource type:

- `get` — list things
- `describe` — show one thing in full, human-readable detail
- `create` / `apply` — make things exist
- `delete` — remove things
- `logs` — read a container's output
- `exec` — run a command inside a container
- `edit`, `scale`, `rollout` — modify things (later modules)

So `kubectl get nodes`, `kubectl get pods`, `kubectl get services` are not three commands you memorized — they're one verb (`get`) against three nouns. Once the pattern clicks, you can guess your way around. Want to see every noun the cluster understands?

```bash
kubectl api-resources
```

A long table — pods, services, deployments, configmaps, secrets, nodes, and dozens more. You're not memorizing this; you're recognizing that *this* is the vocabulary, and `get`/`describe`/`delete` work against all of it.

And when you inevitably forget what fields a resource has (you will, everyone does), Kubernetes documents *itself*:

```bash
kubectl explain pod
kubectl explain pod.spec.containers
```

`explain` walks the structure of any resource, field by field. This becomes a lifeline in Module 2 when you start writing YAML and can't remember whether it's `image` or `images`. Don't read it now — just file away that it exists. For a rusty learner, `kubectl explain` and "let kubectl write the YAML for me" (Chunk 9) are the two crutches that make everything else easier.

---

## Chunk 5 — First contact: running a pod

Time to put something on the cluster. But first, one new noun.

A **pod** is the smallest thing Kubernetes runs. Kubernetes doesn't schedule containers directly — it wraps them in a pod. For now, the simplest accurate picture is: **a pod is one container plus a little Kubernetes paperwork around it** (a pod *can* hold several tightly-coupled containers sharing a network and disk, but that's a Module 2 nuance). When the docs say "Kubernetes runs pods," read it as "Kubernetes runs your containers, each in a thin wrapper."

We'll run something you already know intimately — the `nginx:1.27-alpine` image from Docker Module 1. Maximum familiarity, so all your attention goes to the *Kubernetes* parts:

```bash
kubectl run web --image=nginx:1.27-alpine
```

```
pod/web created
```

Read that carefully. It says *created*, not *started* — because you didn't start anything directly. You filed a wish with the API server: "a pod named `web` should exist, running this image." What happens next is the Chunk 2 relay, in real time: the wish lands in etcd → the scheduler assigns the pod to your one node → that node's kubelet tells `containerd` to pull the image and start the container.

**Predict before you run:** the next command lists your pods. The very first instant, what `STATUS` do you expect? (Hint: the image may still be pulling.)

```bash
kubectl get pods
```

```
NAME   READY   STATUS              RESTARTS   AGE
web    0/1     ContainerCreating   0           3s
```

`ContainerCreating` — caught it mid-relay. Run it again a few seconds later:

```bash
kubectl get pods
```

```
NAME   READY   STATUS    RESTARTS   AGE
web    1/1     Running   0           20s
```

Now `Running`, and `READY` shows `1/1` (one of one container ready). To watch that transition live instead of re-running, use `-w` (watch) — predict the sequence first, then confirm:

```bash
kubectl get pods -w
# stream of status changes; Ctrl+C to stop watching (the pod keeps running)
```

You just declared a desired state and watched the cluster reconcile reality to match it. That `Ctrl+C` only stops your *watching* — exactly like `docker logs -f`, where Ctrl+C left the container running. The instinct transfers.

---

## Chunk 6 — The daily-driver four: get, describe, logs, exec

This is the muscle-memory chunk — the Kubernetes equivalent of `docker ps / logs / exec`. These four verbs are 80% of your day, and each one bridges straight off a Docker reflex you already own.

### `kubectl get` — the inventory

You've met it. A few variations you'll live in:

```bash
kubectl get pods                 # the default-namespace inventory
kubectl get pods -o wide         # + node, IP, and more columns
kubectl get pods -o yaml         # the pod's FULL state, as Kubernetes sees it
kubectl get pods -A              # pods in ALL namespaces (you'll see the cluster's own)
```

That `-A` will reveal a `kube-system` namespace full of pods — CoreDNS, the API server, and friends. The cluster runs itself *as pods on itself*. Namespaces are just folders for organizing all this; full treatment in Module 8. For now, no flag = the `default` namespace, where your `web` pod lives.

### `kubectl describe` — one thing, in full, with a *story*

```bash
kubectl describe pod web
```

A rich human-readable report: which node it landed on, its IP, the container's image and state, and — scroll to the bottom — an **Events** section:

```
Events:
  Type    Reason     Age   From               Message
  ----    ------     ----  ----               -------
  Normal  Scheduled  45s   default-scheduler  Successfully assigned default/web to docker-desktop
  Normal  Pulling    44s   kubelet            Pulling image "nginx:1.27-alpine"
  Normal  Pulled     42s   kubelet            Successfully pulled image
  Normal  Created    42s   kubelet            Created container web
  Normal  Started    42s   kubelet            Started container web
```

That timeline *is the Chunk 2 relay, recorded* — scheduler assigned it, kubelet pulled and started it. This is the single most valuable debugging surface in all of Kubernetes. When a pod won't start, `describe` and its Events almost always tell you why ("ImagePullBackOff: image not found", "Insufficient memory"). It's like `docker inspect`, but with a chronological diary of what the cluster *did*. Make `kubectl describe pod <name>` your reflex the instant anything looks wrong.

### `kubectl logs` — read the container's output

Pure muscle memory from `docker logs` — same idea, same flags:

```bash
kubectl logs web                 # everything so far
kubectl logs -f web              # follow live (Ctrl+C stops following, not the pod)
kubectl logs --tail 20 web       # last 20 lines
kubectl logs --since 5m web      # only the last 5 minutes
kubectl logs --previous web      # logs from the PREVIOUS crashed container (gold for debugging)
```

That last one has no clean Docker equivalent and it's a lifesaver: when a container crashes and restarts, `--previous` shows you the dead one's final words — usually the actual error. Tuck it away for Chunk 8.

### `kubectl exec` — step inside a running container

The `docker exec` instinct, with one new wrinkle:

```bash
kubectl exec -it web -- sh
```

Your prompt changes to the in-container shell:

```
/ #
```

You're inside the nginx container, on the cluster. Prove it, then leave:

```bash
ls /usr/share/nginx/html     # there's the welcome page nginx serves
exit
```

The wrinkle is the `--`. Everything *after* it is the command for the container; everything before it is for `kubectl`. It separates "kubectl's flags" from "the container's command" so they don't collide. One-off commands work too — note the `--`:

```bash
kubectl exec web -- nginx -v
kubectl exec web -- cat /etc/nginx/nginx.conf
```

**Burn in the four reflexes** (they're the same shape as Docker's): `get` = inventory, `describe` = full state + event timeline, `logs` = output, `exec` = step inside. When something's wrong, the order is almost always `get` (what's the status?) → `describe` (what happened?) → `logs` (what did it say?).

---

## Chunk 7 — Reaching the pod from your Mac: `port-forward`

Your pod is `Running`, but open <http://localhost> and there's nothing. In Docker, `-p 8080:80` published the port. Here the pod has an IP, but it's *inside the cluster's network* — unreachable from your Mac directly. (Properly exposing services to the outside world is what Module 4 is entirely about.)

For now, to poke at a single pod during development, Kubernetes gives you a debug tunnel:

```bash
kubectl port-forward pod/web 8080:80
```

```
Forwarding from 127.0.0.1:8080 -> 80
Forwarding from [::1]:8080 -> 80
```

Same `host:container` port order you drilled with Docker's `-p`. This holds your terminal open (it's the live tunnel), so in **another terminal**:

```bash
curl localhost:8080
```

Expect the nginx welcome HTML — the same payoff as Docker Module 1, now served through Kubernetes. `Ctrl+C` in the first terminal closes the tunnel; the pod keeps running (same instinct as before).

**Don't mistake this for "how you expose apps."** `port-forward` is a temporary, single-pod, developer-only tunnel — perfect for "let me check if this one pod works," useless for real traffic. The real mechanism (Services) is Module 4. Recognize `port-forward` as your debugging peephole, nothing more.

---

## Chunk 8 — The reconciliation loop, made visible

Now the conceptual payoff — and a result that surprises almost everyone, which is exactly why it's worth doing by hand.

First, look at a column you've been ignoring:

```bash
kubectl get pods
```

```
NAME   READY   STATUS    RESTARTS   AGE
web    1/1     Running   0          6m
```

`RESTARTS  0`. The kubelet watches the container; if it *dies*, the kubelet restarts it (a pod's default `restartPolicy` is `Always`). Let's prove it by killing the process inside — predict what `RESTARTS` becomes:

```bash
kubectl exec web -- kill 1      # signal nginx's main process (PID 1)
sleep 3
kubectl get pods
```

```
NAME   READY   STATUS    RESTARTS   AGE
web    1/1     Running   1          7m
```

`RESTARTS  1`. The container died and the kubelet brought it back, all on its own. That's reconciliation at the *container* level — desired state "this container should be running" enforced by the node's agent. (And `kubectl logs --previous web` would now show that dead container's output — there's the use for it.)

Now the surprise. Delete the **pod** itself and predict: does Kubernetes recreate it?

```bash
kubectl delete pod web
# pod "web" deleted
kubectl get pods
# No resources found in default namespace.
```

**Gone. Nothing brings it back.** Why does a killed *container* heal but a deleted *pod* doesn't? Because the desired state was only ever "this *container* inside this pod should run" — the kubelet honored that. But *nothing in the cluster ever declared "a pod named `web` should exist."* You created it directly with `kubectl run`, by hand. There's no controller holding that wish, so when the pod's gone, the loop has nothing to restore it to.

This is the whole reason you **never run bare pods in real life** — and it's the precise problem **Module 3's Deployment** solves. A Deployment is the controller that says "I always want N copies of this pod to exist," and reconciles toward it forever — heal a crash, replace a deleted pod, survive a dead node. You just felt the gap that Deployments fill. That's the cliffhanger into Module 3.

---

## Chunk 9 — Imperative vs declarative, and a first look at YAML

Everything you've typed — `kubectl run`, `kubectl delete` — has been **imperative**: "do this now." It's fast and great for learning, which is exactly why we started here. But it's *not* how real Kubernetes is operated, for the same reason the surprise in Chunk 8 happened: imperative commands leave no durable record of intent.

The real way is **declarative**: you write your desired state as a YAML file and hand it to the cluster with `kubectl apply -f file.yaml`. The file *is* the wish, version-controlled and reviewable, and the cluster's job is to make reality match it. Module 2 goes deep; here's just enough of a peek to make the idea concrete.

You don't have to write YAML from memory — let `kubectl` generate it. This trick is a rusty-learner's best friend:

```bash
kubectl run web --image=nginx:1.27-alpine --dry-run=client -o yaml
```

`--dry-run=client` means "don't actually create anything, just show me what you *would* send." Combined with `-o yaml`, it prints the manifest:

```yaml
apiVersion: v1
kind: Pod
metadata:
  name: web
spec:
  containers:
  - name: web
    image: nginx:1.27-alpine
```

Four fields anchor *every* Kubernetes manifest, so meet them now:
- **apiVersion** — which version of the API this object speaks.
- **kind** — what kind of thing it is (`Pod`, `Deployment`, `Service`…).
- **metadata** — its name, labels, and identity.
- **spec** — the desired state: *what you want to be true.*

The mental shift to hold onto: imperative says *"do this once"*; declarative says *"this should be true — keep it that way."* That second sentence is the reconciliation loop from Chunk 1, now in a file you can commit to git. From Module 2 onward we live in YAML, and our Flask + Redis + Postgres stack gets rebuilt this way, manifest by manifest.

---

## Chunk 10 — Cleanup

Kubernetes accumulates less clutter than Docker did at this stage, but tidy habits start now. You already deleted `web` in Chunk 8; if you generated others while experimenting:

```bash
kubectl get pods                 # see what's left
kubectl delete pod <name>        # remove one
```

To survey everything you've created in your namespace at once:

```bash
kubectl get all
```

```
NAME                 TYPE        CLUSTER-IP   EXTERNAL-IP   PORT(S)   AGE
service/kubernetes   ClusterIP   10.96.0.1    <none>        443/TCP   1h
```

If only the `kubernetes` service remains, your namespace is clean — that one is the cluster's own API endpoint and is *always* there; leave it alone. (Note: `get all` shows the common resource types, not literally everything — a quirk worth knowing.)

**The macOS note.** Unlike Docker, where you ran `docker system prune` to reclaim disk, your main resource concern here is that the Kubernetes control plane *idles in the background*, consuming RAM and CPU inside the Docker Desktop Linux VM even when you're running nothing. Check it with the Docker reflex you already have — `docker stats` — and you'll see `kube-system` containers ticking along. When you're done for the day and want that RAM back, toggle Kubernetes off in Docker Desktop → Settings, or quit Docker Desktop entirely. Turning it back on later restores your cluster.

---

## Chunk 11 — Rare-but-real commands (recognize, don't memorize)

You'll meet these in tutorials and other people's setups. The goal is recognition, not fluency.

```bash
kubectl api-resources                 # the full vocabulary of resource types (Chunk 4)
kubectl explain <resource>            # built-in field docs for any resource (Chunk 4)
kubectl get events                    # the cluster's live event stream — like `docker events`
kubectl get events --sort-by=.lastTimestamp   # events in time order (debugging)
kubectl config get-contexts           # every cluster kubectl can talk to
kubectl config use-context <name>     # switch which cluster you're aiming at (Module 8)
kubectl top nodes                     # node CPU/memory — like `docker stats`, see note
kubectl top pods                      # per-pod CPU/memory
kubectl cluster-info dump             # exhaustive cluster state dump (deep debugging)
```

A note on `kubectl top`: it needs a component called **metrics-server**, which Docker Desktop may not install by default — so `top` might error with "Metrics API not available." That's expected; resource usage and limits get full treatment in Module 7, where we'll sort this out. For now, just recognize `top` as the cluster's `docker stats`.

---

## Chunk 12 — Command cheat sheet

| Goal | Command |
|---|---|
| Check client + server versions | `kubectl version` |
| Where is the control plane? | `kubectl cluster-info` |
| List cluster nodes | `kubectl get nodes` |
| Which cluster am I aimed at? | `kubectl config current-context` |
| The grammar | `kubectl VERB RESOURCE [NAME] [FLAGS]` |
| List the resource vocabulary | `kubectl api-resources` |
| Built-in field docs | `kubectl explain <resource>` |
| Run a pod (imperative) | `kubectl run <name> --image=<img>` |
| List pods | `kubectl get pods` |
| More columns | `kubectl get pods -o wide` |
| Full state as YAML | `kubectl get pods -o yaml` |
| All namespaces | `kubectl get pods -A` |
| Watch live | `kubectl get pods -w` |
| Full detail + event timeline | `kubectl describe pod <name>` |
| Read logs (follow/tail/since) | `kubectl logs -f --tail 20 --since 5m <name>` |
| Logs of crashed previous container | `kubectl logs --previous <name>` |
| Shell into a pod | `kubectl exec -it <name> -- sh` |
| One-off command in a pod | `kubectl exec <name> -- <cmd>` |
| Dev tunnel to a pod | `kubectl port-forward pod/<name> 8080:80` |
| Generate a manifest (don't apply) | `kubectl run <name> --image=<img> --dry-run=client -o yaml` |
| Apply a manifest (declarative) | `kubectl apply -f <file>.yaml` |
| Delete a pod | `kubectl delete pod <name>` |
| Survey your namespace | `kubectl get all` |
| Cluster event stream | `kubectl get events` |

---

## Chunk 13 — Checkpoint challenges

Do these from memory — no scrolling up. They cover the whole module.

**Challenge A — first contact, end to end**
1. Confirm Kubernetes is up and you're aimed at the `docker-desktop` cluster.
2. List your cluster's nodes with the extra-detail columns.
3. Run a pod named `site` from `nginx:1.27-alpine`.
4. Watch it go from creating to running, live.
5. Read its event timeline and find the line where the scheduler assigned it to a node.
6. Open a tunnel and `curl` it from your Mac to see the nginx welcome page.
7. Exec in and confirm the file at `/usr/share/nginx/html/index.html` exists, then exit.

**Challenge B — the reconciliation surprise**
1. With `site` running, note its `RESTARTS` count.
2. Kill its main process and, a few seconds later, show that the count went up by one. Explain *who* restarted it.
3. Now delete the pod entirely. Confirm nothing recreates it.
4. In one sentence, explain why the killed *container* came back but the deleted *pod* did not.
5. Generate (but do not apply) the YAML manifest for `site`, and name the four top-level fields every manifest has.

**Bonus question (mental model):** You set Black Friday traffic loose and want twenty copies of your app, self-healing across machines, with zero-downtime updates. Based on Chunk 8, explain in two sentences why `kubectl run` cannot give you this — and name the resource (coming in Module 3) that can. Frame your answer in terms of *who is holding the desired state.*

---

*End of Module 1. Next: Module 2 — Pods, where we stop borrowing nginx and run our own Flask app, meet the pod properly, and make the leap from typing commands to declaring state in YAML.*
