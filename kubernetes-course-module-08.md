# Module 8 — Namespaces & the Declarative Workflow

> **Hands-on rule:** type every command. You'll move the entire running stack into a new home this module — the kind of operation that feels scary until you've done it once and seen the files make it routine.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner.
>
> **Where we're picking up:** every object you've created across seven modules — Flask, Redis, Postgres, the ConfigMap, the Secret, the PVCs — has landed in one place: the `default` namespace. That's been fine for learning, but it's a junk drawer, and real clusters never run that way. This module is about *organizing and operating* a cluster: partitioning it into namespaces, putting guardrails on resource use, knowing exactly which cluster and namespace you're aimed at, and tightening the declarative workflow that's been holding everything together. It's the capstone of the core spine.

---

## Chunk 1 — The `default` junk drawer

Run `kubectl get all` and look at the pile: your whole stack, plus the cluster's own `kubernetes` service, all jumbled into `default`. Now imagine a real cluster — three teams, a dev and a prod copy of each app, shared infrastructure — all in one flat space. The problems are immediate:

- **No isolation.** Every app can see and stumble over every other. One team's `kubectl delete -l app=worker` might catch someone else's worker.
- **Name collisions.** You can't run a `flask` service for dev *and* a `flask` service for prod — names must be unique, and they'd clash.
- **No boundaries to govern.** There's no unit to say "this team gets at most 4 CPUs" or "these credentials can only touch *that* app."

The fix is to partition the cluster into **namespaces** — and it's the foundation for resource quotas (this module) and access control (Module 11).

---

## Chunk 2 — Namespaces: virtual clusters within the cluster

A **namespace** is a scope that partitions cluster resources into independent groups. It gives you three things at once:

1. **Name scoping.** Names only need to be unique *within* a namespace, so a `flask` Service can exist in `dev` and a `flask` Service in `prod` simultaneously, no conflict.
2. **A unit to govern.** ResourceQuotas, LimitRanges, and RBAC roles all apply *per namespace* — the natural boundary for "this team's slice."
3. **Organization and isolation.** A clean place to group everything that belongs to one app or environment.

Not everything is namespaced, and the split is worth knowing. **Namespaced**: pods, deployments, services, configmaps, secrets, PVCs — the things you create constantly. **Cluster-scoped** (they belong to the whole cluster, not any namespace): nodes, PersistentVolumes, StorageClasses, and namespaces themselves. Check any resource:

```bash
kubectl api-resources --namespaced=true | head      # things that live in a namespace
kubectl api-resources --namespaced=false            # cluster-wide things (nodes, pv, storageclass...)
```

Your cluster already has a few built-in namespaces — you met `kube-system` (where CoreDNS and the control plane run) back in Module 1:

```bash
kubectl get namespaces        # default, kube-system, kube-public, kube-node-lease
```

---

## Chunk 3 — Working with namespaces

Three habits cover almost everything. Create one for our stack:

```bash
kubectl create namespace notes
kubectl get ns                 # 'notes' now appears
kubectl get pods -n notes      # empty — nothing lives here yet
kubectl get pods -A            # the -A flag spans EVERY namespace at once
```

The grammar: `-n <namespace>` scopes a single command; `-A` (or `--all-namespaces`) spans all of them; no flag means the `default` namespace — or, after Chunk 4, whatever your context points at.

---

## Chunk 4 — Move the stack into its own namespace

A design decision first: **don't hardcode `namespace:` into your manifests.** Leaving it out keeps the files portable — the *same* YAML can deploy to `dev`, `staging`, or `prod` just by choosing where you apply it. You pick the namespace at apply time instead.

So, migrate. First tear down the `default` copy (this also frees the LoadBalancer's hold on host port 8080, which the new copy will want):

```bash
kubectl delete -f .                              # removes the stack from default
kubectl delete pvc postgres-pvc data-postgres-0  # the leftover PVCs (delete -f . won't touch these)
```

Now deploy the whole stack into `notes` with a single flag:

```bash
kubectl apply -f . -n notes
kubectl get all -n notes
```

The entire stack reassembled in its new home from the exact same files — that's the portability payoff. Two things to expect. Your `curl localhost:8080` still works, because a `LoadBalancer` maps to `localhost` no matter which namespace it's in. And your notes are empty again — because **PVCs are namespaced**, so the StatefulSet in `notes` provisioned a brand-new `data-postgres-0` (the one in `default` was a different object entirely). That reset isn't a bug; it's a vivid reminder that storage, like everything else, lives inside a namespace.

Finally, stop typing `-n notes` on every command by pointing your context's default namespace at it:

```bash
kubectl config set-context --current --namespace=notes
kubectl get pods               # now defaults to the 'notes' namespace
curl localhost:8080/notes/add/new-home && curl localhost:8080/notes
```

---

## Chunk 5 — Cross-namespace DNS (the Module 4 payoff)

Back in Module 4 I showed `redis` resolving via DNS and mentioned that across namespaces you'd need a fully-qualified name — then deferred it to here. Now you can see exactly why. Spin up a probe pod in a *different* namespace and try both forms:

```bash
kubectl create namespace scratch
kubectl run probe -n scratch --rm -it --image=busybox --restart=Never -- sh
# inside the pod:
nslookup redis              # FAILS — there's no 'redis' service in the 'scratch' namespace
nslookup redis.notes        # RESOLVES — short for redis.notes.svc.cluster.local
# exit
kubectl delete namespace scratch
```

There's the rule made concrete: the DNS name is `<service>.<namespace>.svc.cluster.local`, and the bare short form (`redis`) only works *within the same namespace*. Your Flask pods reach `redis` and `postgres` by short name precisely because they're co-located in `notes`. A pod anywhere else must say `redis.notes`. Namespaces scope names — in DNS as everywhere else.

---

## Chunk 6 — Guardrails: ResourceQuota and LimitRange

Module 7 governed resources *per pod*; namespaces let you govern them *per slice of the cluster*. Two objects work together:

- **LimitRange** sets per-container *defaults* (and min/max) — so pods that forget to declare resources still get sane values.
- **ResourceQuota** caps the namespace's *totals* — the sum of all requests/limits, and object counts.

They pair up for a reason you'll hit immediately: once a ResourceQuota constrains `requests.cpu`, *every* pod in the namespace must declare CPU requests or its creation is rejected — and your Redis and Postgres manifests never set resources. The LimitRange rescues them by supplying defaults. Apply the LimitRange first — `limitrange.yaml`:

```yaml
apiVersion: v1
kind: LimitRange
metadata:
  name: notes-limits
spec:
  limits:
  - type: Container
    default:                 # used as the LIMIT if a container sets none
      cpu: 500m
      memory: 128Mi
    defaultRequest:          # used as the REQUEST if a container sets none
      cpu: 100m
      memory: 64Mi
```

Then the quota — `quota.yaml`:

```yaml
apiVersion: v1
kind: ResourceQuota
metadata:
  name: notes-quota
spec:
  hard:
    requests.cpu: "1"
    requests.memory: 1Gi
    limits.cpu: "2"
    limits.memory: 2Gi
    pods: "10"
```

```bash
kubectl apply -f limitrange.yaml -f quota.yaml
kubectl describe quota notes-quota         # shows Used vs Hard for each line
```

Now feel the cap. **Predict** what happens when you ask for far more than the budget allows:

```bash
kubectl scale deployment flask --replicas=15
kubectl get pods                            # only as many as fit; the rest never appear
kubectl describe replicaset -l app=flask | grep -i quota
# Error creating: pods "flask-..." is forbidden: exceeded quota: notes-quota,
#   requested: requests.cpu=..., used: ..., limited: requests.cpu=1
```

The ReplicaSet *tries* to create all 15, but the quota refuses every pod past the namespace's CPU budget — they're never created, and the events say exactly why. Scale back down:

```bash
kubectl scale deployment flask --replicas=3
```

This is how a platform team hands a namespace to a product team and sleeps soundly: the LimitRange makes lazy manifests safe, the ResourceQuota makes runaway scaling impossible.

---

## Chunk 7 — Contexts: knowing exactly where you're aimed

You quietly used a context in Chunk 4. A **context** bundles three things — a *cluster*, a *user* (credentials), and a default *namespace* — and it all lives in your kubeconfig file (`~/.kube/config`). It answers the question from Module 1: *which cluster, as whom, in which namespace, am I talking to right now?*

```bash
kubectl config get-contexts        # list all; the * marks the active one
kubectl config current-context     # docker-desktop
kubectl config use-context <name>  # switch to a different cluster entirely
kubectl config set-context --current --namespace=notes   # change just the namespace (Chunk 4)
```

This is not bookkeeping — it's the single most important safety habit in operating Kubernetes. The classic disaster is running a destructive command against the *wrong* context: you think you're in `dev` and you're in `prod`, you `delete` something, and it's gone for real. Before anything destructive, glance at your context. (The community tools `kubectx` and `kubens` make switching fast and make the current target visible in your prompt — well worth installing once you juggle more than one cluster or namespace.)

---

## Chunk 8 — The declarative workflow, matured

You've been living the "files are the system" philosophy since Module 4. At scale, a few more moves make it solid:

```bash
kubectl diff -f .                  # PREVIEW every change before applying — build this habit
kubectl apply -f . -R              # apply a whole tree of manifests, recursively
kubectl get all -l app=flask       # operate in bulk by label, not by name
kubectl apply --dry-run=server -f flask-deployment.yaml   # validate against the live API, change nothing
```

Two power tools to *recognize*, both about keeping a namespace exactly in sync with a directory of files:

- **`kubectl apply --prune`** reconciles a namespace to match a set of files *and deletes anything not in them*. It's how you make "the directory is the truth" literally enforced — and it's genuinely dangerous, because a mistyped label selector can prune things you never meant to. Use it deliberately, never casually.
- **Kustomize** (`kubectl apply -k`) layers environment-specific *overlays* on a shared base — the clean way to deploy the same manifests to dev and prod with small differences (replica counts, image tags), without copy-pasting YAML.

In real organizations this matures into **GitOps**: a git repository *is* the desired state, and a controller (Argo CD, Flux) continuously applies it — the reconciliation loop you've known since Module 1, scaled up to the whole cluster. You don't need it here; just recognize that everything you've practiced is the foundation it's built on.

---

## Chunk 9 — Cleanup hygiene: the namespace broom

Here's the cleanest teardown in Kubernetes, and the echo of Docker Module 11's `docker system prune`: delete the namespace, and *everything inside it cascades away* — pods, deployments, services, configmaps, secrets, and crucially the PVCs too.

```bash
kubectl delete namespace notes      # ⚠️ removes EVERYTHING in it, data volumes included
```

Note the contrast you learned in Module 6: `kubectl delete -f .` deliberately *leaves* PVCs behind, but deleting the whole **namespace** takes them with it. It's the big broom — fast, total, and unforgiving, exactly like `system prune --volumes`. Respect it accordingly.

We're not done with the stack, though, so recreate it — and notice how trivially the portability pays off:

```bash
kubectl create namespace notes
kubectl apply -f . -n notes
kubectl config set-context --current --namespace=notes
```

Same files, fresh namespace, whole system back. (You'll also want to re-apply `limitrange.yaml` and `quota.yaml` if you want the guardrails in the recreated namespace.)

---

## Chunk 10 — Rare-but-real (recognize, don't memorize)

```bash
kubectl config view                          # the whole kubeconfig (contexts, clusters, users)
export KUBECONFIG=~/.kube/config:~/work/config   # merge multiple kubeconfig files
kubectl label namespace notes team=backend   # namespaces carry labels too (used by NetworkPolicy)
kubectl get ns notes -o jsonpath='{.metadata.labels}'   # every ns auto-gets kubernetes.io/metadata.name
```

Worth recognizing:

- **`kubectx` / `kubens`** — community CLIs for fast, safe context and namespace switching.
- **Namespace labels + NetworkPolicy** — selectors over namespace labels let you write rules like "only the `frontend` namespace may talk to `backend`" (a Module 11/12 topic).
- **ServiceAccounts are namespaced** — the identity a pod uses to talk to the API lives in its namespace; the basis for RBAC in Module 11.
- **`terminating` namespaces** — a namespace stuck in `Terminating` usually means a resource with a finalizer won't release; you'll meet this debugging in Module 12.

---

## Chunk 11 — Command cheat sheet

| Goal | Command |
|---|---|
| Create a namespace | `kubectl create namespace <n>` |
| List namespaces | `kubectl get ns` |
| Scope one command | `kubectl get pods -n <n>` |
| Span all namespaces | `kubectl get pods -A` |
| Deploy a stack to a namespace | `kubectl apply -f . -n <n>` |
| Set the default namespace | `kubectl config set-context --current --namespace=<n>` |
| Cross-namespace DNS | `<service>.<namespace>.svc.cluster.local` |
| Cap a namespace's totals | `ResourceQuota` |
| Default per-container resources | `LimitRange` |
| Inspect quota usage | `kubectl describe quota <n>` |
| List contexts | `kubectl config get-contexts` |
| Switch cluster | `kubectl config use-context <n>` |
| Preview changes | `kubectl diff -f .` |
| Delete everything in a namespace | `kubectl delete namespace <n>` |

---

## Chunk 12 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — give the stack a home**
1. Create a namespace and deploy the whole stack into it from your existing files *without* editing a `namespace:` field into any manifest. Explain why keeping namespaces out of the manifests is the better default.
2. Set your context so you no longer need `-n` on every command. Confirm `kubectl get pods` now targets the new namespace.
3. From a throwaway pod in a *different* namespace, show that `redis` fails to resolve but `redis.<your-namespace>` succeeds. Write out the full DNS name the short form expands to.

**Challenge B — guardrails and hygiene**
1. Apply a LimitRange with default requests/limits, then a ResourceQuota capping the namespace to 1 CPU of requests. Scale Flask high enough to hit the cap, and show the exact error that prevents the extra pods from being created.
2. Explain *why* the LimitRange is necessary for your Redis and Postgres pods to survive once that quota exists.
3. Tear the whole stack down with a *single* command, then bring it back. Explain what that single delete removes that `kubectl delete -f .` would have left behind — and which Docker command it's analogous to.

**Bonus question (mental model):** You have one cluster and want a `dev` and a `prod` copy of the entire stack, each with a `flask` Service, isolated resource budgets, and pods that reach their *own* `redis` by the short name `redis`. Explain how namespaces make all of that possible at once — name scoping, the governance unit, and DNS resolution — and explain why your manifests need *no* changes to deploy into either one.

---

*End of Module 8 — and the end of the core spine. Your stack is now containerized, orchestrated, networked, configured, persistent, self-healing, resource-governed, and organized into its own namespace. Next we move into the advanced track: Module 9 — Ingress, where instead of a separate `LoadBalancer` per service, a single entry point routes HTTP traffic to the right service by hostname and path — the front door a real application presents to the world.*
