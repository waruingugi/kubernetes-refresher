# Module 9 — Ingress: One Front Door for HTTP

> **Hands-on rule:** type every command. Ingress has a famous trap — the rules do nothing without a separate piece you have to install first — and you'll only internalize it by watching an Ingress resource sit there inert until the controller is running.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner. Docker Desktop's `LoadBalancer`→`localhost` mapping is what lets the ingress controller answer on `localhost`.
>
> **Where we're picking up:** this is the start of the advanced track. The core spine left you with a complete stack — but with one crude edge: Flask is exposed to the world through its own `LoadBalancer` on port 8080. That works for one service. It falls apart the moment a real application has several services that all need to face the internet. Ingress is the fix: a single, HTTP-aware entry point that routes to the right service by hostname and path.

---

## Chunk 1 — The problem: a LoadBalancer per service doesn't scale

Right now, reaching Flask from outside means a dedicated `LoadBalancer` Service. Fine for one. But picture a real app — a web frontend, a JSON API, an admin panel — each needing external access. Give each its own `LoadBalancer` and you get a separate external IP per service (a separate *cloud* load balancer, with a separate *bill*, in production), no shared TLS, and no way to express "send `/api` here and `/` there." It's wasteful and dumb.

What you want is **one** entry point that's *smart about HTTP*: it can look at the request's hostname, path, and headers and route accordingly. That's the leap from Layer 4 to Layer 7. A `Service`/`LoadBalancer` works at **L4** — it forwards TCP by port and understands nothing about HTTP. **Ingress** works at **L7** — it reads the HTTP request and routes on its contents. One IP, many backends, intelligent routing. (It's the `-p` port-publish idea you knew in Docker, evolved into a reverse proxy that actually understands the traffic.)

---

## Chunk 2 — Ingress vs Ingress Controller (the trap)

This is the one thing that confuses everyone, so get it straight before touching any YAML:

> An **Ingress resource** is *just routing rules* — declarative config, like a routing table written on paper. **It does nothing on its own.** To enforce those rules you need an **Ingress controller**: an actual running pod (an nginx or Traefik reverse proxy) that *watches* Ingress resources and implements the routing.

The analogy: the Ingress resource is the **delivery instructions**; the controller is the **delivery driver** who reads them and actually moves the packages. Write the most perfect instructions you like — with no driver, nothing gets delivered.

This is different from everything you've built so far. Deployments, Services, and the rest are acted on by controllers *built into* the cluster's control plane (Module 2). Ingress is not — you **install a controller yourself**, and until you do, every Ingress resource you create just sits there, ignored. So that's the order: controller first, rules second.

---

## Chunk 3 — Install an ingress controller

We'll use **ingress-nginx**, the most common one. Its "cloud" deployment creates a `LoadBalancer` Service, which on Docker Desktop maps to `localhost` — exactly what we want. (Version tags move; grab the current one from kubernetes.github.io/ingress-nginx. At time of writing:)

```bash
kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/controller-v1.11.3/deploy/static/provider/cloud/deploy.yaml
```

It installs into its own `ingress-nginx` namespace (it's shared infrastructure, not part of your app). Wait for the controller to come up:

```bash
kubectl get pods -n ingress-nginx          # wait for the controller pod to be Running/Ready
kubectl get svc -n ingress-nginx
# ingress-nginx-controller   LoadBalancer   10.x.x.x   localhost   80:3xxxx/TCP,443:3xxxx/TCP
```

Once that Service shows `EXTERNAL-IP: localhost`, your cluster has a front door listening on `localhost:80` and `:443`. The install also registers an **IngressClass** — the name your Ingress rules will point at:

```bash
kubectl get ingressclass        # nginx
```

---

## Chunk 4 — Add a second backend so routing means something

Routing to a single service isn't much of a demo. Add a tiny second backend — plain nginx, the image you've known since Module 1 — so we can route to *two* different services. Create `web.yaml`:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: web
spec:
  replicas: 1
  selector:
    matchLabels:
      app: web
  template:
    metadata:
      labels:
        app: web
    spec:
      containers:
      - name: web
        image: nginx:1.27-alpine
        ports:
        - containerPort: 80
---
apiVersion: v1
kind: Service
metadata:
  name: web
spec:
  selector:
    app: web
  ports:
  - port: 80
    targetPort: 80
```

```bash
kubectl apply -f web.yaml -n notes
```

(The LimitRange from Module 8 hands this pod default requests/limits automatically, so it slots under the quota without you specifying resources.)

---

## Chunk 5 — Take Flask off its LoadBalancer

Now that an Ingress will front everything, your app's Services go back to being *internal* — the whole point is that you no longer need a `LoadBalancer` per service. Edit `flask-service.yaml` and remove the `type: LoadBalancer` line so it returns to a plain `ClusterIP`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: flask
spec:
  selector:
    app: flask
  ports:
  - port: 8080
    targetPort: 5000
```

```bash
kubectl apply -f flask-service.yaml -n notes
```

`curl localhost:8080` will stop working now — and that's correct. Flask is internal again; the new way in is the Ingress.

---

## Chunk 6 — Write the Ingress: path-based routing

Here's the headline feature — one hostname, routed to two different services by path. Create `ingress.yaml`:

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: myapp
spec:
  ingressClassName: nginx          # which controller enforces these rules
  rules:
  - host: myapp.local
    http:
      paths:
      - path: /notes               # requests to /notes...
        pathType: Prefix
        backend:
          service:
            name: flask            # ...go to the flask Service
            port:
              number: 8080
      - path: /                    # everything else...
        pathType: Prefix
        backend:
          service:
            name: web              # ...goes to the web (nginx) Service
            port:
              number: 80
```

Read it top-down: `ingressClassName` binds these rules to the nginx controller; each `rule` is keyed on a `host`; within it, `paths` map URL prefixes to a backend `service` and `port`. Apply it:

```bash
kubectl apply -f ingress.yaml -n notes
kubectl get ingress -n notes        # ADDRESS shows localhost once the controller programs it
```

Test it — no `/etc/hosts` editing needed, just send the `Host` header the rules match on:

```bash
curl -H "Host: myapp.local" http://localhost/          # nginx welcome page  → web
curl -H "Host: myapp.local" http://localhost/notes     # your notes list     → flask
```

One front door at `localhost:80`, two backend services, routed by path. *That's* Ingress earning its name — and it's why you'd never want a `LoadBalancer` per service again.

---

## Chunk 7 — Host-based routing (virtual hosting)

Ingress can route by *hostname* too — the mechanism that lets one IP serve many sites. Add a second rule to `ingress.yaml`, keyed on a different host:

```yaml
  - host: flask.local
    http:
      paths:
      - path: /
        pathType: Prefix
        backend:
          service:
            name: flask
            port:
              number: 8080
```

```bash
kubectl apply -f ingress.yaml -n notes
curl -H "Host: flask.local" http://localhost/          # the whole Flask app
curl -H "Host: myapp.local"  http://localhost/         # still the nginx page
```

Same entry point, same IP — the controller decides where to send each request purely from the `Host` header. This is how a single cluster hosts `app.example.com`, `api.example.com`, and `admin.example.com` behind one address. To test in a *browser* (which sends the real Host header), add a line to `/etc/hosts`:

```
127.0.0.1   myapp.local flask.local
```

Then `http://myapp.local/notes` works in the browser directly.

---

## Chunk 8 — `pathType` and the rewrite gotcha

`pathType` controls how paths match: **`Prefix`** (the common one — `/notes` matches `/notes`, `/notes/add/x`, etc.), **`Exact`** (only that precise path), and `ImplementationSpecific` (controller-defined).

Now the gotcha that wastes hours. Our `/notes` rule worked cleanly because Flask *actually has* a `/notes` route — the path the user requested is the path the backend expects. But often that isn't true: you want `myapp.local/api/...` to reach a backend that serves at `/...` with no `/api` prefix. By default the *full* path is forwarded, so the backend gets `/api/...`, doesn't recognize it, and 404s. The fix is a controller-specific **rewrite annotation**:

```yaml
metadata:
  annotations:
    nginx.ingress.kubernetes.io/rewrite-target: /$1
```

You don't need it for our stack, but recognize the symptom — "my Ingress paths 404 even though the rule matches" almost always means the backend isn't getting the path it expects, and a rewrite is the answer.

---

## Chunk 9 — TLS termination

Ingress is also where HTTPS usually lives: the controller terminates TLS at the front door (handles the certificate) and talks plain HTTP to your backends, so your apps never deal with certs. You reference a **TLS Secret** — the `kubernetes.io/tls` type you met in Module 5's rare-but-real:

```yaml
spec:
  tls:
  - hosts:
    - myapp.local
    secretName: myapp-tls        # a Secret holding tls.crt and tls.key
  rules:
  - host: myapp.local
    # ...same rules as before...
```

For local experimentation you'd make a self-signed cert and load it (`openssl req -x509 ...` then `kubectl create secret tls myapp-tls --cert=... --key=...`), and your browser would warn about the self-signed cert — expected. In real clusters nobody hand-manages certificates: **cert-manager** watches your Ingresses and auto-provisions and renews free Let's Encrypt certs. Just know TLS lives here, at the edge, and is a Secret reference away.

---

## Chunk 10 — Inspecting and debugging Ingress

```bash
kubectl get ingress -n notes               # hosts and the assigned ADDRESS
kubectl describe ingress myapp -n notes     # the full rule→backend mapping + events
kubectl get ingressclass                    # which controllers are available
kubectl logs -n ingress-nginx deploy/ingress-nginx-controller   # the controller's request log — gold
```

The three failures you'll actually hit, and what each means:

- **404 from the controller** — no rule matched. Check the `Host` header and the path; a host typo or missing rule is the usual cause.
- **503 Service Temporarily Unavailable** — the rule matched, but the backend Service has *no ready endpoints*. Straight back to Module 7: if the pods aren't passing readiness, they're not endpoints, so the Ingress has nowhere to send the request.
- **Nothing happens / rule ignored** — wrong or missing `ingressClassName`, so no controller claimed the Ingress.

That controller log is your best friend here — it shows every request, the matched rule, and the upstream it chose.

---

## Chunk 11 — Keep it running

Your stack grew by two files — `web.yaml` and `ingress.yaml` — both deployed into `notes`. The **ingress controller**, though, lives in its own `ingress-nginx` namespace and is shared infrastructure: it stays put even if you tear down `notes`, and you install it once per cluster. So:

```bash
kubectl apply -f . -n notes        # your app + web + ingress (the controller is separate)
```

And remember from Module 8 — deleting the `notes` namespace removes your Ingress *resource* but leaves the controller untouched in its own namespace, ready for the next thing you deploy.

---

## Chunk 12 — Rare-but-real (recognize, don't memorize)

- **Annotations** — most real Ingress power is controller-specific annotations: `rewrite-target`, `ssl-redirect`, `proxy-body-size` (upload limits), rate limiting, auth. They live under `metadata.annotations` and vary by controller.
- **Default backend** — where unmatched requests go (a catch-all 404 page).
- **cert-manager** — automatic TLS certificate provisioning and renewal from Let's Encrypt.
- **ExternalDNS** — watches Ingresses and auto-creates the matching DNS records at your provider.
- **The Gateway API** — the newer, more expressive successor to Ingress (separate `Gateway` and `HTTPRoute` objects, better multi-team support). Ingress isn't going away, but you'll increasingly see Gateway API in new setups — recognize the name.

---

## Chunk 13 — Command cheat sheet

| Goal | Command |
|---|---|
| Install ingress-nginx | `kubectl apply -f <ingress-nginx cloud deploy URL>` |
| Check the controller | `kubectl get pods,svc -n ingress-nginx` |
| List ingress classes | `kubectl get ingressclass` |
| Apply Ingress rules | `kubectl apply -f ingress.yaml -n notes` |
| List ingresses | `kubectl get ingress -n notes` |
| Rule → backend mapping | `kubectl describe ingress <n> -n notes` |
| Test by Host header | `curl -H "Host: myapp.local" http://localhost/notes` |
| Controller request log | `kubectl logs -n ingress-nginx deploy/ingress-nginx-controller` |
| Browser testing | add hostnames to `/etc/hosts` → `127.0.0.1 myapp.local` |

---

## Chunk 14 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — stand up the front door**
1. In one or two sentences, explain why an Ingress *resource* alone does nothing, and what you must install for it to work. Use the delivery analogy.
2. Install an ingress controller and confirm both that its pod is ready and that its Service has an external address on `localhost`.
3. Deploy the `web` (nginx) backend, and switch the Flask Service from `LoadBalancer` back to `ClusterIP`. Explain why moving Flask off its own LoadBalancer is the *point* of adding Ingress.

**Challenge B — route the traffic**
1. Write one Ingress that sends `myapp.local/notes` to Flask and `myapp.local/` to the web backend. Test both with `curl` and the `Host` header. Explain why `/notes` worked without any path rewriting.
2. Add a second host, `flask.local`, that routes everything to Flask. Test it, and explain what the controller inspects to decide between the two hosts.
3. You get a `503` from the controller for `myapp.local/notes`. Walk through what that specifically means and trace it back to a concept from Module 7.

**Bonus question (mental model):** Contrast the path a request takes to reach Flask *before* this module (browser → `LoadBalancer` Service → pod) versus *after* (browser → ingress controller → `ClusterIP` Service → pod). At which step does HTTP-aware routing now happen, why couldn't the old `LoadBalancer` do it, and what does this let you do with a single external IP that you couldn't before?

---

*End of Module 9. Next: Module 10 — Scaling & Scheduled Work, where the cluster starts making decisions for you: autoscaling Flask up and down based on load (HPA), running one-off and recurring jobs (Jobs and CronJobs), and — with a brief `kind` multi-node cameo — watching the scheduler actually place pods across machines using affinity and taints.*
