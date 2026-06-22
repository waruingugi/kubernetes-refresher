# Module 11 — Security & RBAC: Who Can Do What

> **Hands-on rule:** type every command. RBAC is invisible until you watch the *same* pod be allowed to do one thing and flatly denied another — that "Forbidden" is the whole lesson made concrete.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner.
>
> **Where we're picking up:** here's something you haven't questioned in eleven modules — *every* command you've run has succeeded, because Docker Desktop hands you a context authenticated as a full **cluster admin**. You've never been told "no." Meanwhile, every pod has been running with a default identity you never chose. This module asks the two questions a real cluster must answer about every single request: **who are you, and what are you allowed to do?** (Unlike Docker, where access to the daemon was effectively all-or-nothing root, Kubernetes has fine-grained authorization — this is new ground.)

---

## Chunk 1 — Every request: authentication, then authorization

Every call to the API server — yours via `kubectl`, or a pod's — passes through two gates in order:

1. **Authentication** — *who are you?* Kubernetes recognizes two kinds of identity. **Users** are humans (you), authenticated by certificates or tokens that live *outside* the cluster — there's no `User` object. **ServiceAccounts** are identities for *workloads* (pods), and these *are* Kubernetes objects, namespaced like everything else.
2. **Authorization** — *are you allowed to do this?* This is **RBAC** (Role-Based Access Control), and it's **default-deny**: unless a rule explicitly grants a permission, the answer is no.

You've sailed through gate 1 as an admin and gate 2 has waved you past everything. Real clusters give humans and pods narrow, specific permissions — the principle of **least privilege** — so a mistake or a compromise can only touch what that identity was explicitly allowed to touch.

---

## Chunk 2 — ServiceAccounts: a pod's identity

Every pod runs *as* a ServiceAccount. If you don't assign one, it silently uses the namespace's `default` SA — which is what all your pods have been doing:

```bash
kubectl get serviceaccounts -n notes        # there's one called 'default'
kubectl get pod -l app=flask -n notes -o jsonpath='{.items[0].spec.serviceAccountName}'
# default
```

And here's the part with security weight: a **token** for that ServiceAccount is mounted into the pod (under `/var/run/secrets/kubernetes.io/serviceaccount/`), so the application *could* authenticate to the API server. Your Flask app never does — but the credential is sitting right there in the container, which is exactly the kind of thing an attacker who compromises the app would look for. Hold that thought; we harden it in Chunk 7.

---

## Chunk 3 — The RBAC model

RBAC is four object types arranged on two axes — *what* permissions, and *who* gets them, scoped either to a namespace or the whole cluster:

| | Defines permissions | Grants them to subjects |
|---|---|---|
| **Namespace-scoped** | `Role` | `RoleBinding` |
| **Cluster-scoped** | `ClusterRole` | `ClusterRoleBinding` |

The formula to memorize:

> **A subject (who) + a Role (what permissions), joined by a Binding = authorization.**

A Role (or ClusterRole) is a list of *rules*, each granting **verbs** (`get`, `list`, `watch`, `create`, `update`, `patch`, `delete`) on **resources** (`pods`, `services`, `secrets`, …) within an **API group**. A Binding then attaches that Role to one or more subjects (a ServiceAccount, a user, a group). No Binding, no access — and with default-deny, an identity with no bindings can do nothing at all.

---

## Chunk 4 — Build a least-privilege identity

Create a ServiceAccount that may *read pods and nothing else*. Put all three objects in `rbac.yaml`:

```yaml
apiVersion: v1
kind: ServiceAccount
metadata:
  name: pod-reader
  namespace: notes
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata:
  name: pod-reader
  namespace: notes
rules:
- apiGroups: [""]              # "" is the CORE group, where pods live
  resources: ["pods"]
  verbs: ["get", "list", "watch"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata:
  name: pod-reader
  namespace: notes
subjects:
- kind: ServiceAccount
  name: pod-reader
  namespace: notes
roleRef:
  kind: Role
  name: pod-reader
  apiGroup: rbac.authorization.k8s.io
```

```bash
kubectl apply -f rbac.yaml
```

Read it as the formula: the **Role** says "get/list/watch on pods"; the **RoleBinding** says "the `pod-reader` ServiceAccount gets the `pod-reader` Role." (That empty `apiGroups: [""]` trips people up — the *core* API group, home to pods, services, configmaps, and secrets, is the empty string. Deployments would be `apiGroups: ["apps"]`.)

---

## Chunk 5 — Test permissions with `kubectl auth can-i`

The fastest way to check what an identity may do — without running anything — is `kubectl auth can-i`, impersonating the ServiceAccount from your admin context. The SA's full identity name is `system:serviceaccount:<namespace>:<name>`:

```bash
kubectl auth can-i list pods    --as=system:serviceaccount:notes:pod-reader -n notes   # yes
kubectl auth can-i list secrets --as=system:serviceaccount:notes:pod-reader -n notes   # no
kubectl auth can-i delete pods  --as=system:serviceaccount:notes:pod-reader -n notes   # no
kubectl auth can-i '*' '*'      --as=system:serviceaccount:notes:pod-reader -n notes   # no
```

`yes` only to reading pods; `no` to everything else — least privilege, enforced by default-deny. To dump the complete picture of what an identity can do:

```bash
kubectl auth can-i --list --as=system:serviceaccount:notes:pod-reader -n notes
```

`auth can-i` is also how you sanity-check *your own* access (`kubectl auth can-i create deployments`) — invaluable the moment you're on a locked-down cluster instead of as admin.

---

## Chunk 6 — Prove it from inside a real pod

Impersonation is convincing; watching a *real workload* get refused is more so. Run a pod *as* the `pod-reader` SA, with `kubectl` inside, and try both:

```bash
kubectl run reader -n notes --rm -it --image=bitnami/kubectl \
  --overrides='{"spec":{"serviceAccountName":"pod-reader"}}' -- sh
# inside the pod:
kubectl get pods            # works — the Role allows it
kubectl get secrets         # Error from server (Forbidden): ... cannot list resource "secrets"
exit
```

The pod authenticated using its mounted token (Chunk 2), and RBAC checked the `pod-reader` Role for each request — allowing the pod read, denying the secret read. Same pod, same namespace, two outcomes, decided entirely by identity. That `Forbidden` is RBAC doing its job. (If `bitnami/kubectl` isn't available, any image carrying `kubectl` works — or just rely on the `auth can-i --as=` method from Chunk 5.)

---

## Chunk 7 — The default-token risk, and hardening Flask

Back to that thought from Chunk 2. Your Flask app never talks to the API server, yet it's been getting a ServiceAccount token mounted into every pod — a live credential an attacker could grab if they popped the app. The least-privilege move is to simply *not mount it* when it isn't needed. Add one line to the Flask pod template's `spec` in `flask-deployment.yaml`:

```yaml
    spec:
      automountServiceAccountToken: false
      containers:
      - name: flaskapp
        # ...everything else unchanged...
```

```bash
kubectl apply -f flask-deployment.yaml -n notes
kubectl exec deploy/flask -n notes -- ls /var/run/secrets/kubernetes.io/serviceaccount/
# ls: ...: No such file or directory   ← the token is gone
```

Flask works exactly as before (it never used the token), but a compromised pod now has *no* cluster credential to abuse. The broader lesson: least privilege isn't only about writing tight Roles — it's also about *not handing out credentials that nobody uses*.

---

## Chunk 8 — ClusterRoles and the built-in roles

When permissions must span the whole cluster — or cover *cluster-scoped* resources like nodes, PVs, and StorageClasses, which don't belong to any namespace — you use a **ClusterRole** with a **ClusterRoleBinding**. You don't always have to write your own: Kubernetes ships ready-made ClusterRoles you can bind directly:

```bash
kubectl get clusterroles | grep -E '^(view|edit|admin|cluster-admin)'
```

- **`view`** — read-only on most resources (but not secrets).
- **`edit`** — read/write on most resources.
- **`admin`** — full control within a namespace.
- **`cluster-admin`** — god mode (what *you've* had all along).

A neat, common pattern: bind a *ClusterRole* with a namespace-scoped *RoleBinding* to grant its permissions in **one** namespace only — e.g., give a teammate the built-in `view` role, but only in `notes`. You reuse the well-tested built-in definition without granting it cluster-wide.

---

## Chunk 9 — Inspecting and debugging RBAC

```bash
kubectl get role,rolebinding -n notes              # namespace-scoped grants
kubectl get clusterrole,clusterrolebinding         # cluster-wide grants
kubectl describe rolebinding pod-reader -n notes    # who is bound to what
kubectl auth can-i --list --as=<identity> -n notes  # everything an identity can do
```

The good news about RBAC debugging: the error tells you exactly what to fix. A `Forbidden` message names the precise verb, resource, API group, and identity that was denied — for example *"cannot create resource 'deployments' in API group 'apps'... by ServiceAccount 'notes:ci-bot'."* You read it literally and add that verb/resource to the Role. No guesswork.

---

## Chunk 10 — Keep it running

Your stack gained `rbac.yaml` (the ServiceAccount, Role, and RoleBinding) and the `automountServiceAccountToken: false` hardening on Flask. The `pod-reader` identity isn't *used* by your app — it's there as a worked example — but the Flask hardening is a genuine improvement you'd keep. `kubectl apply -f . -n notes` brings it all back as always.

---

## Chunk 11 — Rare-but-real (recognize, don't memorize)

RBAC controls the *API*. Real security has more layers, all worth recognizing:

- **`securityContext`** — pod/container hardening: `runAsNonRoot: true`, `readOnlyRootFilesystem: true`, dropping Linux capabilities. Stops a container from running as root or writing where it shouldn't.
- **Pod Security Admission** — namespace-level policy enforcing baselines (`privileged` / `baseline` / `restricted`), e.g. "no privileged pods in this namespace."
- **NetworkPolicy** — least privilege for the *network*: rules like "only pods in `frontend` may reach `backend` on port 8080." It's the network counterpart to RBAC, and uses the namespace/pod labels from Module 8. (Note: it needs a CNI that enforces it — Docker Desktop's default doesn't, so policies there are accepted but not enforced.)
- **Aggregated ClusterRoles** — ClusterRoles that automatically absorb others via label selectors (how `view`/`edit`/`admin` stay current).
- **External identity** — real clusters wire authentication to OIDC or cloud IAM, not static certs; ServiceAccount tokens are now short-lived, audience-bound projected tokens rather than long-lived secrets.

---

## Chunk 12 — Command cheat sheet

| Goal | Command / field |
|---|---|
| List service accounts | `kubectl get serviceaccounts -n <ns>` |
| A pod's identity | `...-o jsonpath='{.spec.serviceAccountName}'` |
| Define namespace permissions | `Role` (verbs × resources) |
| Grant a Role to a subject | `RoleBinding` |
| Cluster-wide permissions | `ClusterRole` / `ClusterRoleBinding` |
| Can this identity do X? | `kubectl auth can-i <verb> <resource> --as=<identity> -n <ns>` |
| Everything an identity can do | `kubectl auth can-i --list --as=<identity> -n <ns>` |
| SA identity string | `system:serviceaccount:<namespace>:<name>` |
| Don't mount the API token | `spec.automountServiceAccountToken: false` |
| Built-in roles | `view` · `edit` · `admin` · `cluster-admin` |
| Who's bound to what | `kubectl describe rolebinding <n> -n <ns>` |

---

## Chunk 13 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — grant exactly enough**
1. Name the two gates every API request passes through, and the two kinds of identity Kubernetes authenticates. Which kind is a Kubernetes object, and which isn't?
2. Create a ServiceAccount, a Role allowing only `get`/`list`/`watch` on `pods`, and a RoleBinding tying them together. Explain the formula (subject + role + binding) in your own words.
3. Using `kubectl auth can-i` with impersonation, show the SA *can* list pods but *cannot* list secrets or delete pods. Write out the SA's full identity string.

**Challenge B — least privilege in practice**
1. Run a pod *as* that ServiceAccount and show, from inside it, that `kubectl get pods` succeeds while `kubectl get secrets` is `Forbidden`. Explain what authenticated the pod and what authorized (or denied) each call.
2. Your Flask app never calls the API, yet it has a credential mounted. Make that credential disappear with a single field, and explain the security principle behind doing so.
3. A CI bot's pod fails with *"Forbidden: cannot create resource 'deployments' in API group 'apps'."* Explain exactly what to add, and where, to fix it — and why this error is easy to debug.

**Bonus question (mental model):** RBAC is "default-deny," and you've operated as `cluster-admin` the whole course. Explain why you've never seen a `Forbidden` error, what would change if your context were bound to only the built-in `view` ClusterRole in `notes`, and how a *pod's* permissions are determined differently from *your* permissions even though both go through the same two gates.

---

*End of Module 11. Next: Module 12 — Observability & Debugging, where we pull every inspection reflex you've built — `describe`, `logs`, events, endpoints, `auth can-i` — into a single systematic playbook for the only question that matters at 3 a.m.: "it's broken; where do I look?"*
