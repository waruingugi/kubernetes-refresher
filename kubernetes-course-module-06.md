# Module 6 — Storage: Data That Outlives the Pod

> **Hands-on rule:** type every command. The whole point of this module is a single, visceral moment — you delete the database pod and your data is *still there*. You have to watch it survive to believe it.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner. Docker Desktop ships a default StorageClass, so the storage we ask for gets provisioned automatically — no cloud account needed.
>
> **Where we're picking up:** you've now felt the same wound twice. In Module 4 the Redis visit counter reset to 1 when its pod was recreated; in Module 5 your Postgres notes vanished the same way. Both times the diagnosis was identical: Kubernetes gave you stable *names* and externalized *config*, but did nothing for stable *data*. This module is the cure.

---

## Chunk 1 — The problem, named: ephemeral by design

A container's filesystem is **ephemeral**. The thin writable layer on top of the image is born with the container and dies with it — exactly the "containers are disposable" principle you internalized in Docker Module 1. Kubernetes inherits this and makes it sharper: when a pod is deleted and a controller recreates it, the new pod starts from a *pristine* image filesystem with no memory of the old one. Prove it in ten seconds:

```bash
kubectl run scratch --image=busybox --restart=Never -- sh -c 'sleep 3600'
kubectl exec scratch -- sh -c 'echo "remember me" > /data.txt; cat /data.txt'
# remember me
kubectl delete pod scratch
kubectl run scratch --image=busybox --restart=Never -- sh -c 'sleep 3600'
kubectl exec scratch -- cat /data.txt
# cat: can't open '/data.txt': No such file or directory
kubectl delete pod scratch
```

Gone — same as the bare pod in Module 1, same as your notes in Module 5. This is not a bug; it's the design. The fix is to attach storage whose lifetime is *decoupled* from the pod's. In Docker you reached for a volume (Module 4); Kubernetes has the same idea, with more machinery, because in a cluster the storage and the pod that uses it might not even live on the same machine.

---

## Chunk 2 — Volumes and the persistence ladder

A **volume** attaches storage to a pod using the exact `volumes` + `volumeMounts` shape you just learned for ConfigMaps — only the *source* changes. There isn't one kind of volume; there's a ladder of lifetimes, and knowing which rung you're on prevents nasty surprises:

- **Container filesystem** — dies when the *container* restarts. (The bottom rung, what you just demoed.)
- **`emptyDir`** — scratch space created when the pod is scheduled, shared by all containers in the pod, deleted when the *pod* is removed. It survives a container *crash and restart*, but not pod deletion. Great for a sidecar and main container to swap files, or for temporary cache — useless for data you actually want to keep.
- **PersistentVolume** — storage that lives *outside* any pod's lifecycle and survives pod deletion, rescheduling, even the workload being torn down. This is the rung you need.

`emptyDir` looks like this, just so you recognize the shape (same pattern, source `emptyDir: {}`):

```yaml
      volumes:
      - name: cache
        emptyDir: {}
```

But for a database, only the top rung will do. That's PersistentVolumes — and the elegant way Kubernetes lets a pod ask for one.

---

## Chunk 3 — PersistentVolumes and PersistentVolumeClaims: the claim model

Persistent storage in Kubernetes is split across **two** objects, and the split is the whole idea:

- A **PersistentVolume (PV)** is a real piece of storage in the cluster — an actual disk or directory — that exists independently of any pod.
- A **PersistentVolumeClaim (PVC)** is a pod's *request* for storage: "I need 1Gi, read-write." The pod references the PVC; the PVC binds to a PV that satisfies it.

So the chain is **Pod → PVC → PV**. Why the indirection? Separation of concerns. The application declares *what it needs* (a PVC: size, access mode) without knowing or caring *what backs it* (the PV: a local disk, an AWS EBS volume, an NFS share). The same Postgres manifest with the same PVC runs unchanged on your Mac and in a cloud — only the PV underneath differs.

The cleanest mental picture: the **PVC is a coat-check ticket**; the **PV is the coat**. The pod holds the ticket, the cluster keeps the coat, and the binding matches one to the other. Lose interest in the pod, keep the ticket, and your coat is still there when a new pod presents it.

Two fields you'll set on every PVC:

- **accessModes** — `ReadWriteOnce` (RWO: mounted read-write by *one node* — the common case for block storage), `ReadOnlyMany` (ROX), or `ReadWriteMany` (RWX: shared read-write, needs special storage like NFS).
- **resources.requests.storage** — how much you want, e.g. `1Gi`.

---

## Chunk 4 — StorageClass and dynamic provisioning

In the old days an admin hand-created PVs ahead of time and you hoped one matched your claim. Today a **StorageClass** does it automatically: when a PVC asks for storage, the StorageClass *dynamically provisions* a brand-new PV to satisfy it. Docker Desktop ships one out of the box — check it:

```bash
kubectl get storageclass
# NAME                 PROVISIONER          ...
# hostpath (default)   docker.io/hostpath   ...
```

That `(default)` means any PVC that doesn't name a class gets storage from it. So you never touch PVs directly — you just create a PVC and watch a PV appear. Create `postgres-pvc.yaml`:

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: postgres-pvc
spec:
  accessModes: ["ReadWriteOnce"]
  resources:
    requests:
      storage: 1Gi
```

```bash
kubectl apply -f postgres-pvc.yaml
kubectl get pvc
# NAME           STATUS   VOLUME        CAPACITY   ACCESS MODES   STORAGECLASS   AGE
# postgres-pvc   Bound    pvc-9f3a...   1Gi        RWO            hostpath       4s
kubectl get pv
# a PV named pvc-9f3a... was created automatically and is Bound to your claim
```

You asked for storage; the cluster manufactured a PV and bound it to your claim, all from that one small file. That's dynamic provisioning — the reason you'll rarely write a PV by hand.

---

## Chunk 5 — Give Postgres real storage: the wound closes

Now mount that claim into Postgres at the directory where it keeps its data, `/var/lib/postgresql/data`. Edit `postgres.yaml` — add the volume (same `volumes` + `volumeMounts` shape, source `persistentVolumeClaim`) and one important strategy line:

```yaml
spec:
  replicas: 1
  strategy:
    type: Recreate          # see note below — critical for a single stateful pod
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
          valueFrom:
            secretKeyRef:
              name: db-secret
              key: password
        ports:
        - containerPort: 5432
        volumeMounts:
        - name: pgdata
          mountPath: /var/lib/postgresql/data
      volumes:
      - name: pgdata
        persistentVolumeClaim:
          claimName: postgres-pvc
```

Why `strategy: Recreate`? With the default `RollingUpdate`, a roll would try to start a *new* Postgres pod before killing the old one — but the new pod can't mount a `ReadWriteOnce` volume that the old pod still holds, so the rollout deadlocks. `Recreate` says "kill the old pod first, *then* start the new one," which is exactly right for a single stateful pod. (It accepts brief downtime — fine for one database; the real fix for zero-downtime stateful workloads is the StatefulSet, coming next.)

Apply it, then **predict**: after you add a note and delete the Postgres pod, will the note survive this time?

```bash
kubectl apply -f postgres.yaml
kubectl rollout status deployment/postgres

curl localhost:8080/notes/add/i-will-survive
curl localhost:8080/notes                  # - i-will-survive

kubectl delete pod -l app=postgres         # kill the database pod
kubectl get pods -l app=postgres           # a fresh pod comes up
curl localhost:8080/notes                  # - i-will-survive   ← STILL THERE
```

That's the moment. The pod died, a new one took its place, and the data was *waiting for it* — because the data lives on the PersistentVolume, which outlived the pod entirely. The exact scenario that erased your notes in Module 5 now leaves them untouched. The wound is closed.

> **Note for the cloud:** on real block storage (formatted ext4) the data directory may contain a `lost+found` entry that makes Postgres' `initdb` refuse to start. The standard fix is to point Postgres at a *subdirectory* — set `PGDATA=/var/lib/postgresql/data/pgdata`. On Docker Desktop's hostpath storage it isn't needed, but recognize the pattern when you see it.

---

## Chunk 6 — Why a database really wants a StatefulSet

A Deployment + PVC closed the wound, and for a single dev database it genuinely works. But it's the wrong *shape* for stateful software, and two limits show why:

**It can't scale.** Set `replicas: 2` and the second pod can't mount the same `ReadWriteOnce` volume — and even if it could, two Postgres processes writing the same files would corrupt the database instantly. A Deployment is built for **interchangeable, stateless replicas** ("cattle"): any pod is as good as any other, they share nothing, you can swap them freely. A database is the polar opposite — each instance has its *own* data and a distinct role ("pets").

**It has no stable identity.** Deployment pods get random names (`postgres-7d9f8-x2k4p`) and are treated as disposable equals. A clustered database needs each member to keep a *stable name* and reattach to *its own storage* every time, so replicas can find each other and know who holds what.

The **StatefulSet** is the controller built for exactly this. It makes three guarantees a Deployment can't:

1. **Stable network identity.** Pods are named with ordinals — `postgres-0`, `postgres-1` — and keep those names across restarts and rescheduling, each addressable by stable DNS (via a headless Service).
2. **Stable per-pod storage.** A `volumeClaimTemplate` mints a *dedicated* PVC for every pod (`data-postgres-0`, `data-postgres-1`, …), and a given pod always reattaches to *its own* volume — never a sibling's.
3. **Ordered operations.** Pods are created, scaled, and deleted one at a time in order (0, then 1, …), which clustered systems need to bootstrap and update safely.

To be honest about it: a single Postgres on a Deployment+PVC is a defensible dev shortcut. But StatefulSet is the *correct* tool, it's what you'll see in every real cluster and Helm chart, and it's the only safe path the moment you have more than one stateful replica. So let's convert.

---

## Chunk 7 — Convert Postgres to a StatefulSet

First, a StatefulSet needs a **headless Service** to give its pods stable DNS. "Headless" means `clusterIP: None` — instead of one virtual IP that load-balances, DNS returns the pod IPs directly. Edit your `postgres` Service to add that one line:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: postgres
spec:
  clusterIP: None        # headless — required for StatefulSet stable per-pod DNS
  selector:
    app: postgres
  ports:
  - port: 5432
    targetPort: 5432
```

For a single replica this still serves Flask perfectly — `postgres` resolves to `postgres-0`'s IP. Now replace the Postgres **Deployment** with a **StatefulSet**. Delete the old Deployment first (the StatefulSet provisions its own fresh PVC, so the old `postgres-pvc` is left behind — we'll clean it up in Chunk 9):

```bash
kubectl delete deployment postgres
```

Write the StatefulSet into `postgres.yaml`:

```yaml
apiVersion: apps/v1
kind: StatefulSet
metadata:
  name: postgres
spec:
  serviceName: postgres        # must reference the headless Service
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
          valueFrom:
            secretKeyRef:
              name: db-secret
              key: password
        ports:
        - containerPort: 5432
        volumeMounts:
        - name: data
          mountPath: /var/lib/postgresql/data
  volumeClaimTemplates:        # ← the StatefulSet superpower: a PVC per pod
  - metadata:
      name: data
    spec:
      accessModes: ["ReadWriteOnce"]
      resources:
        requests:
          storage: 1Gi
```

```bash
kubectl apply -f postgres.yaml
kubectl get statefulset           # or: kubectl get sts
kubectl get pods                  # postgres-0  ← stable, ordinal name
kubectl get pvc                   # data-postgres-0  ← auto-created from the template
```

The pod is `postgres-0`, not a random string — and a PVC named `data-postgres-0` was minted just for it. Now prove the two headline guarantees. (Your notes reset here, because the StatefulSet got a *fresh* volume — moving data between PVCs is a migration task; we're starting clean.) Add a note, then delete the pod and **predict its new name**:

```bash
curl localhost:8080/notes/add/stateful-and-stable
kubectl delete pod postgres-0
kubectl get pods                  # it comes back as postgres-0 — SAME name, not a new random one
curl localhost:8080/notes         # - stateful-and-stable   ← data survived, on its own PVC
```

Same stable identity, same dedicated storage, every time. And if you ever scaled this to a clustered setup, `postgres-1` would automatically get its *own* `data-postgres-1` volume — each pod a pet with its own data. That's the StatefulSet doing the job a Deployment fundamentally can't.

---

## Chunk 8 — Inspecting storage

```bash
kubectl get pvc                          # claims and what they're Bound to
kubectl get pv                           # the actual volumes (and their reclaim policy)
kubectl get storageclass                 # available classes; which is default
kubectl describe pvc data-postgres-0     # Events: provisioning and binding history
kubectl get sts postgres                 # the StatefulSet and its ready replicas
kubectl describe pod postgres-0          # the Volumes section shows the attached claim
```

The classic storage failure to recognize: a pod stuck in `Pending` with a `describe` event like `FailedScheduling: pod has unbound immediate PersistentVolumeClaims`, or a PVC stuck `Pending` with no StorageClass to satisfy it. The fix is almost always "no default StorageClass" or "asked for an access mode the storage can't provide" — `describe pvc` tells you which.

---

## Chunk 9 — Keep it running — and the data-deletion gotcha

This one matters, and it should feel familiar. Remember Docker Module 1's loud warning about `prune --volumes` erasing your database? Kubernetes has the same trap, and it errs the *safe* way:

> **Deleting a StatefulSet (or a Deployment) does NOT delete its PVCs.** Your data is deliberately preserved, even after the workload is gone.

So if you `kubectl delete -f .`, your Deployments, Services, and StatefulSet vanish — but the PVCs (and the data on them) remain:

```bash
kubectl delete -f .
kubectl get pvc
# postgres-pvc      Bound   ...   ← the orphan from Chunk 5
# data-postgres-0   Bound   ...   ← from the StatefulSet, still here
```

That's a feature, not a leak — it's what lets you tear down and rebuild the stack while keeping the database intact. But it means storage accumulates until you *explicitly* remove it. To truly reclaim (and lose the data), delete the claims by hand:

```bash
kubectl delete pvc postgres-pvc data-postgres-0      # ⚠️ this destroys the data
```

Clean up that orphaned `postgres-pvc` from the Deployment era now; keep `data-postgres-0` if you want your notes to persist into the next module. Your stack directory has grown — `postgres-pvc.yaml` joins the set — and `kubectl apply -f .` still brings everything back.

---

## Chunk 10 — Rare-but-real (recognize, don't memorize)

```bash
kubectl edit pvc data-postgres-0      # grow storage if the StorageClass allows it (allowVolumeExpansion)
kubectl get pv -o wide                # see each PV's RECLAIM POLICY (Delete vs Retain)
```

Worth recognizing in real clusters and manifests:

- **`hostPath`** volume — binds a directory on a specific *node* into the pod. Tempting on a single-node setup, but it pins the pod to one machine and isn't portable; avoid outside dev.
- **Statically provisioned PV** — an admin creates the PV by hand and PVCs bind to it, instead of dynamic provisioning. You'll see it where storage is pre-allocated.
- **`reclaimPolicy: Retain`** vs `Delete` — whether deleting a PVC also destroys the underlying PV/data. Production data volumes often use `Retain` as a safety net.
- **`ReadWriteMany`** — shared read-write storage (NFS, CephFS, cloud file shares) for the rarer case where many pods write the same files.
- **`subPath`** — mount a single file or subdirectory of a volume rather than the whole thing (the `PGDATA` trick from Chunk 5).

---

## Chunk 11 — Command cheat sheet

| Goal | Command |
|---|---|
| List storage classes | `kubectl get storageclass` |
| Create a claim | `kubectl apply -f postgres-pvc.yaml` |
| List claims / volumes | `kubectl get pvc` · `kubectl get pv` |
| Why is a claim stuck? | `kubectl describe pvc <n>` |
| Mount a PVC in a pod | `volumes: [persistentVolumeClaim: {claimName: ...}]` |
| Single stateful Deployment | add `strategy: {type: Recreate}` |
| List StatefulSets | `kubectl get sts` |
| Per-pod storage | `volumeClaimTemplates:` in a StatefulSet |
| Headless Service | `spec.clusterIP: None` |
| Reclaim storage (destroys data) | `kubectl delete pvc <n>` |

---

## Chunk 12 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — close the wound**
1. Show that a file written into a fresh `busybox` pod disappears after the pod is deleted and recreated. State in one sentence why this happens by design.
2. Create a 1Gi `ReadWriteOnce` PVC and show that a PV was provisioned and `Bound` automatically. Name the object that did the provisioning.
3. Mount that PVC into Postgres at its data directory, and explain why you'd set `strategy: Recreate` on a single-replica stateful Deployment.
4. Add a note, delete the Postgres pod, and prove the note survives. Explain precisely *where* the data lived such that it outlasted the pod.

**Challenge B — make it stateful**
1. Convert Postgres to a StatefulSet with a `volumeClaimTemplate` and a headless Service. Name the two things that change about the *Service* and what each enables.
2. After applying, show the pod's name and the auto-created PVC's name, and explain how those names are derived.
3. Delete `postgres-0` and predict its new name before checking. Explain why a StatefulSet pod's identity is stable while a Deployment pod's is not.
4. Run `kubectl delete -f .`, then `kubectl get pvc`. Explain why the claim is still there and what you must do to actually reclaim the storage — and which Docker command this echoes.

**Bonus question (mental model):** Walk the full chain that lets your notes survive a pod deletion: name the four objects involved (the workload, the per-pod claim, the provisioned volume, the storage class) and state, in one sentence each, what role each plays. Then explain why a Deployment with three replicas all sharing *one* PVC would be a disaster, and how a StatefulSet's `volumeClaimTemplate` avoids it.

---

*End of Module 6. Next: Module 7 — Health & Resources, where we make the stack genuinely self-healing: liveness, readiness, and startup probes so Kubernetes knows the difference between "running" and "actually working," plus CPU/memory requests and limits so one greedy pod can't starve the rest — finally wiring up the readiness gate that Module 4's Service has been waiting for.*
