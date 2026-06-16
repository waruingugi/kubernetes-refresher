# Module 5 — ConfigMaps & Secrets: Config Out of the Image

> **Hands-on rule:** type every command. The subtle, must-feel lesson of this module — that changed config does *not* reach running pods on its own — only sticks if you watch a `curl` keep returning the old value until you act.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner.
>
> **Where we're picking up:** the stack runs, but its configuration is a mess. There's a hardcoded greeting in the code, a `REDIS_HOST`/`REDIS_PORT` env block you bolted onto the Deployment to dodge a bug, and — most embarrassingly — a plaintext Postgres password sitting in `postgres.yaml`, the kind of thing that ends up committed to git and then on the news. This module cleans all of it up, and finally wires Flask to Postgres for real.

---

## Chunk 1 — The problem: config baked in and scattered

Tally the sins currently in your stack:

- A default greeting hardcoded *inside* `app.py` — changing it means rebuilding the image.
- `REDIS_HOST` and `REDIS_PORT` hand-typed into the Deployment's `env:` block.
- `POSTGRES_PASSWORD: "devpassword"` sitting in plaintext in `postgres.yaml`.

Each is a different flavor of the same disease: **configuration tangled up with code and workload definitions.** Why it hurts: you can't change a setting without editing manifests or rebuilding images; the *same* image can't serve dev and prod because their config differs; and a secret in a manifest is a secret in your git history forever.

The principle — straight from the "config in the environment" idea you met with Docker's `-e` flag and `--env-file` — is to keep the **image generic** and inject what makes each run **specific** from the outside. Kubernetes gives you two purpose-built objects for that injection: **ConfigMap** for ordinary config, and **Secret** for sensitive config. The job of this module is to move all three sins into them.

---

## Chunk 2 — ConfigMap: configuration as a first-class object

A **ConfigMap** is just a named bag of key/value pairs, stored in the cluster, that you can feed into pods. Create one for Flask's non-secret settings — `configmap.yaml`:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: flask-config
data:
  GREETING: "Hello from a ConfigMap!"
  REDIS_HOST: "redis"
  REDIS_PORT: "6379"
  DB_HOST: "postgres"
  DB_NAME: "postgres"
  DB_USER: "postgres"
```

```bash
kubectl apply -f configmap.yaml
kubectl get configmap                 # or: kubectl get cm
kubectl describe configmap flask-config
```

Notice `describe` prints every value in full — because a ConfigMap is *not* for secrets. That's the entire reason Secrets exist as a separate thing (Chunk 4). (You can also create one imperatively with `kubectl create configmap flask-config --from-literal=GREETING=hi ...`, but as always, the file is the version-controllable source of truth.)

---

## Chunk 3 — Consuming a ConfigMap as environment variables

A ConfigMap does nothing until a pod *consumes* it. The most common way is to turn its keys into environment variables, and there are two styles.

**All keys at once, with `envFrom`** — clean when every key should become an env var:

```yaml
        envFrom:
        - configMapRef:
            name: flask-config
```

**One specific key, with `valueFrom`** — when you want a particular key under a particular env var name:

```yaml
        env:
        - name: GREETING
          valueFrom:
            configMapKeyRef:
              name: flask-config
              key: GREETING
```

Use `envFrom` here. Open `flask-deployment.yaml` and **replace** that hand-typed `env:` block (the `REDIS_HOST`/`REDIS_PORT` lines from the bug fix) with `envFrom`, so the container reads:

```yaml
      containers:
      - name: flaskapp
        image: flaskapp:3.0
        imagePullPolicy: IfNotPresent
        envFrom:
        - configMapRef:
            name: flask-config
        ports:
        - containerPort: 5000
```

Apply and check the result — the greeting should now come from the ConfigMap, not the code:

```bash
kubectl apply -f flask-deployment.yaml
kubectl rollout status deployment/flask
curl localhost:8080
# Hello from a ConfigMap! (v3.0)
# Served by pod: ...
```

The greeting changed without touching `app.py` or rebuilding the image. That's config externalized.

---

## Chunk 4 — Secret: like a ConfigMap, but handled with care

A **Secret** is structurally almost identical to a ConfigMap — a bag of key/values — but it's the object for sensitive data: passwords, API keys, TLS certs. Before you use one, internalize the single most misunderstood fact about Secrets:

> **A Kubernetes Secret is base64-*encoded*, not *encrypted*.** Base64 is not security — anyone who can read the Secret can decode it in one command.

So what *do* you gain over a ConfigMap? Secrets are kept out of normal output (`describe` and logs won't print their values), they *can* be encrypted at rest in etcd (a cluster setting), access to them is restricted separately via RBAC (Module 11), and using one signals "this is sensitive" to every tool and human. It's about reduced exposure and control, not magic encryption. Treat the contents as real credentials regardless.

Create the database password as a Secret. The cleanest way avoids ever writing base64 by hand — use `kubectl create` with a literal:

```bash
kubectl create secret generic db-secret --from-literal=password=devpassword
```

Or declaratively, using `stringData` so Kubernetes does the base64 encoding for you — `secret.yaml`:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: db-secret
type: Opaque
stringData:
  password: "devpassword"
```

Now prove the "encoded, not encrypted" point to yourself:

```bash
kubectl get secret db-secret -o jsonpath='{.data.password}' | base64 -d
# devpassword           (if -d errors on macOS, use -D)
```

The value came back in cleartext with a single decode. *That's* why you never commit a Secret manifest with real production credentials to git — see Chunk 11 for what real-world teams do instead.

---

## Chunk 5 — One Secret, two consumers: Postgres and Flask

The power move is making a *single* Secret the one source of truth, consumed by both the database that *sets* the password and the app that *uses* it.

**Postgres** — edit `postgres.yaml`, replacing the plaintext value with a reference into the Secret:

```yaml
        env:
        - name: POSTGRES_PASSWORD
          valueFrom:
            secretKeyRef:
              name: db-secret
              key: password
```

**Flask** — add the password to its container as `DB_PASSWORD`, pulled from the *same* Secret (the rest of its config still comes from the ConfigMap via `envFrom`):

```yaml
        envFrom:
        - configMapRef:
            name: flask-config
        env:
        - name: DB_PASSWORD
          valueFrom:
            secretKeyRef:
              name: db-secret
              key: password
```

```bash
kubectl apply -f secret.yaml
kubectl apply -f postgres.yaml
kubectl apply -f flask-deployment.yaml
```

The embarrassing plaintext password is gone from your manifests, and the credential now lives in exactly one place. Change it there and both sides draw from the same well. (One nuance you'll meet in Module 6: Postgres only *sets* its password on first initialization with an empty data directory — so changing the Secret later won't repassword an already-initialized database. We keep the value the same here, so it's moot for now.)

---

## Chunk 6 — Flask finally uses Postgres

Time to give Flask a real reason to hold that credential. Evolve `app.py` to v4.0, adding a Postgres-backed notes feature alongside the Redis counter:

```python
import os, socket
from flask import Flask
import redis, psycopg2

app = Flask(__name__)
POD_NAME = socket.gethostname()
APP_VERSION = "4.0"

r = redis.Redis(host=os.environ.get("REDIS_HOST", "redis"),
                port=int(os.environ.get("REDIS_PORT", "6379")),
                socket_connect_timeout=2)

def db():
    return psycopg2.connect(
        host=os.environ.get("DB_HOST", "postgres"),
        dbname=os.environ.get("DB_NAME", "postgres"),
        user=os.environ.get("DB_USER", "postgres"),
        password=os.environ.get("DB_PASSWORD", ""),
        connect_timeout=2,
    )

@app.route("/")
def home():
    greeting = os.environ.get("GREETING", "Hello from Flask on Kubernetes")
    try:
        counter = f"Visit #{r.incr('visits')} (shared via Redis)"
    except redis.exceptions.RedisError:
        counter = "Visit counter unavailable"
    return f"{greeting} (v{APP_VERSION})\nServed by pod: {POD_NAME}\n{counter}\n"

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

@app.route("/healthz")
def healthz():
    return "ok\n", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
```

Add the Postgres driver to the image — update the `Dockerfile` pip line to `pip install --no-cache-dir flask==3.0.3 redis==5.0.8 psycopg2-binary==2.9.9` — then build and roll:

```bash
docker build -t flaskapp:4.0 .
# edit image: flaskapp:4.0 in flask-deployment.yaml, then:
kubectl apply -f flask-deployment.yaml
kubectl rollout status deployment/flask
```

Try the new feature end to end — Flask connecting to Postgres with a password it got from a Secret:

```bash
curl localhost:8080/notes                       # No notes yet
curl localhost:8080/notes/add/hello-kubernetes   # Saved note: hello-kubernetes
curl localhost:8080/notes/add/secrets-work        # Saved note: secrets-work
curl localhost:8080/notes                        # lists both
```

Now the **predict-then-try** that sets up the next module. Delete the Postgres pod, let it come back, and check your notes:

```bash
kubectl delete pod -l app=postgres
kubectl get pods -l app=postgres      # new pod, freshly initialized
curl localhost:8080/notes             # No notes yet  ← your notes are GONE
```

The Secret did its job perfectly — Flask still authenticates, Postgres still starts. But the *data* vanished, because the new Postgres pod started with an empty filesystem. Same lesson as the Redis counter reset in Module 4: Kubernetes has given you stable *names* and externalized *config*, but it has done nothing for stable *data*. That is exactly the hole Module 6 fills.

---

## Chunk 7 — The gotcha that gets everyone: config changes don't auto-apply

This is the operational lesson of the module, and it surprises nearly everyone. Change a value in your ConfigMap — edit `GREETING` to something new — and apply it:

```bash
# edit GREETING in configmap.yaml to "Greetings, version two!"
kubectl apply -f configmap.yaml
curl localhost:8080
# Hello from a ConfigMap! (v3.0)   ← STILL THE OLD VALUE
```

The ConfigMap updated, but your running pods didn't notice. Why? **Environment variables are set once, when the container starts.** Injecting a ConfigMap via `envFrom` copies the values into the container's environment at launch; later edits to the ConfigMap never reach a process that's already running. To pick up the change, you must restart the pods — and here's the `rollout restart` from Module 3, doing exactly the job I previewed back then:

```bash
kubectl rollout restart deployment/flask
kubectl rollout status deployment/flask
curl localhost:8080
# Greetings, version two! (v3.0)   ← now it's live
```

Burn this in: **change a ConfigMap or Secret consumed as env vars → `kubectl rollout restart` the consumers**, or nothing happens. (The file-mount method in the next chunk behaves differently — it *does* update in place — which is one reason to prefer it for config that changes often.)

---

## Chunk 8 — The other way to consume: mounting as files

Sometimes config isn't a handful of env vars — it's a *file*: an `nginx.conf`, a TLS certificate, an app config in JSON. For that, mount the ConfigMap (or Secret) as a **volume**, and each key becomes a file. Add to the Flask container, just to see it work:

```yaml
      containers:
      - name: flaskapp
        # ...existing fields...
        volumeMounts:
        - name: config-files
          mountPath: /etc/flask-config
      volumes:
      - name: config-files
        configMap:
          name: flask-config
```

```bash
kubectl apply -f flask-deployment.yaml
kubectl exec -it deployment/flask -- ls /etc/flask-config
# DB_HOST  DB_NAME  DB_USER  GREETING  REDIS_HOST  REDIS_PORT
kubectl exec -it deployment/flask -- cat /etc/flask-config/GREETING
```

Each ConfigMap key is now a file whose contents are the value. Secrets mount the same way (commonly for certs and key files). Which method to choose:

- **Env vars** (`envFrom`/`valueFrom`) — simplest, for apps that read settings from the environment. Frozen at container start; needs `rollout restart` to change.
- **File mounts** (volumes) — for config files and certificates, and when you want updates without a full restart: a mounted ConfigMap's files are refreshed in place by the kubelet within about a minute (the app still has to re-read them). 

(You can remove that demo `volumeMounts`/`volumes` block again afterward if you like — it was to feel the mechanism, not something the Flask app needs.)

---

## Chunk 9 — Inspecting config and secrets

```bash
kubectl get cm,secret                                  # all config objects at a glance
kubectl describe configmap flask-config                # values shown (not sensitive)
kubectl describe secret db-secret                      # keys and sizes only — values hidden
kubectl get secret db-secret -o jsonpath='{.data.password}' | base64 -d   # decode to debug
kubectl describe pod <flask-pod>                        # see referenced configmaps/secrets under Environment & Mounts
```

When an app behaves as if it has the wrong config, `describe pod` is the fast check: its `Environment` and `Mounts` sections show exactly which ConfigMaps and Secrets the container actually pulled in — which usually reveals a stale pod (forgot the `rollout restart`) or a key-name typo.

---

## Chunk 10 — Keep it running

The stack now spans more files. Your directory should hold:

```
flask-deployment.yaml   flask-service.yaml
redis.yaml              postgres.yaml
configmap.yaml          secret.yaml
```

`kubectl apply -f .` brings the whole thing up. One real-world caution: **don't commit `secret.yaml` with a live credential to a public repo.** For this course's `devpassword` it's harmless, but in real work teams either create Secrets imperatively (so the value never lands in a file), or use tooling that encrypts secrets safely for git — see the next chunk.

---

## Chunk 11 — Rare-but-real (recognize, don't memorize)

```bash
kubectl create configmap nginx-conf --from-file=./nginx.conf      # a whole file becomes a key
kubectl create configmap app-env --from-env-file=./.env           # a .env file → many keys
kubectl create secret tls my-tls --cert=tls.crt --key=tls.key     # a TLS Secret (Ingress, Module 9)
kubectl create secret docker-registry regcred ...                 # pull from a private registry
```

Worth recognizing in real manifests and conversations:

- **`immutable: true`** on a ConfigMap/Secret — locks it so it can't be changed (you replace it instead), which improves performance and prevents accidental edits.
- **`optional: true`** on a key reference — the pod still starts if the ConfigMap/Secret or key is missing, instead of failing.
- **Secret types** beyond `Opaque`: `kubernetes.io/tls`, `kubernetes.io/dockerconfigjson`, `kubernetes.io/basic-auth`.
- **Real secret management** — because base64 isn't security, production teams reach for the *Sealed Secrets* controller (safe to commit encrypted secrets to git), the *External Secrets Operator*, or cloud managers (AWS Secrets Manager, GCP Secret Manager, HashiCorp Vault). You don't need them here; just know plain Secrets are the floor, not the ceiling.

---

## Chunk 12 — Command cheat sheet

| Goal | Command |
|---|---|
| Create a ConfigMap (declarative) | `kubectl apply -f configmap.yaml` |
| Create a ConfigMap (imperative) | `kubectl create configmap <n> --from-literal=K=V` |
| Create a Secret (imperative) | `kubectl create secret generic <n> --from-literal=K=V` |
| List config objects | `kubectl get cm,secret` |
| Inspect a ConfigMap (values shown) | `kubectl describe configmap <n>` |
| Inspect a Secret (values hidden) | `kubectl describe secret <n>` |
| Decode a Secret value | `kubectl get secret <n> -o jsonpath='{.data.K}' \| base64 -d` |
| Inject all keys as env vars | `envFrom: [configMapRef: {name: ...}]` |
| Inject one key as an env var | `valueFrom: {configMapKeyRef \| secretKeyRef}` |
| Mount config as files | `volumes:` + `volumeMounts:` (configMap / secret) |
| Apply a config change to pods | `kubectl rollout restart deployment/<n>` |

---

## Chunk 13 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — externalize the config**
1. Create a ConfigMap named `flask-config` holding `GREETING`, `REDIS_HOST`, `REDIS_PORT`, `DB_HOST`, `DB_NAME`, `DB_USER`.
2. Change the Flask Deployment to read all of them via `envFrom`, replacing any hand-typed `env:` block. Apply, and confirm the greeting on `curl` now comes from the ConfigMap.
3. Edit `GREETING` to a new value and apply the ConfigMap. `curl` and explain why the value *didn't* change. Then make it change with one command — and name the reason env-injected config behaves this way.

**Challenge B — one Secret, two consumers**
1. Create a Secret `db-secret` with key `password`. Show that you can decode its value in one command, and state what that proves about Secret "security."
2. Wire the *same* Secret into both Postgres (as `POSTGRES_PASSWORD`) and Flask (as `DB_PASSWORD`). Explain the benefit of one Secret over hardcoding the password in two places.
3. Build/roll `flaskapp:4.0`, add two notes via the `/notes/add/...` route, and list them.
4. Delete the Postgres pod, then `curl /notes` again. Explain precisely why the notes are gone even though authentication still worked — and name what Module 6 will add to fix it.

**Bonus question (mental model):** You have config in three forms now — baked into the image, in a ConfigMap consumed as env vars, and in a ConfigMap mounted as files. A teammate edits a value and is confused that "Kubernetes didn't apply it." For each of the three forms, say what it takes (if anything) for a *running* pod to actually see the new value, and explain *why* the env-var case and the file-mount case differ.

---

*End of Module 5. Next: Module 6 — Storage, where we close the wound you've now felt twice (the Redis counter reset, the vanished notes): pods get persistent volumes that outlive them, you meet PersistentVolumeClaims and StorageClasses, and Postgres graduates from a fragile Deployment to a StatefulSet with storage that survives anything.*
