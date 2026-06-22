# Module 13 — Helm: Packaging the Whole Thing

> **Hands-on rule:** type every command. Helm's "aha" is watching one `helm install` stand up what used to take fifteen `kubectl apply`s — and then upgrading and rolling back the *entire app* atomically. You have to run the lifecycle to feel it.
>
> **Environment:** macOS + Docker Desktop, Kubeadm provisioner.
>
> **Where we're picking up:** this is the finale. You've spent twelve modules hand-building a real system, and it now lives in roughly fifteen YAML files you deploy with `kubectl apply -f .`. That's "the files are the system" — durable, but not *packaged*. You can't version the app as a unit, can't upgrade or roll it all back atomically, and can't redeploy it to a second environment without copy-pasting and hand-editing a dozen values. Helm is the package manager that fixes all of that. (It's the natural extension of how Docker Compose packaged a multi-container app into one parameterizable unit — now for a multi-resource Kubernetes app.)

---

## Chunk 1 — The problem: a folder of YAML isn't a package

`kubectl apply -f .` works, but look at what it *can't* do. There's no single version stamped on "the whole app." You can't upgrade everything and roll it all back as one atomic unit if something breaks. And to deploy a `dev` and a `prod` copy, you'd copy the entire folder and hand-edit the namespace, image tags, replica counts, the greeting, the database size — error-prone, and unreviewable. Sharing the app with someone means "here's a zip, now edit these eight places."

A real application needs to be a **package**: versioned, parameterized, and installable in one command. That's Helm — `apt`/`brew`/`npm`, but for Kubernetes applications.

---

## Chunk 2 — Helm's five concepts

Everything in Helm is one of five things:

- **Chart** — the package itself: a directory of *templated* manifests plus metadata and default values.
- **Template** — one of your manifests with `{{ }}` placeholders where values get filled in.
- **Values** — the parameters that fill those placeholders (defaults in the chart, overridable at install time).
- **Release** — a named, *installed instance* of a chart. Install the same chart twice with different values → two independent releases.
- **Repository** — where charts are published and shared, like a registry for images.

The mental model in one line: **the Chart is a recipe with blanks, the Values are how you fill the blanks, the Release is the dish you actually cooked, and the Repository is the cookbook others publish to.**

---

## Chunk 3 — Install Helm and scaffold a chart

```bash
brew install helm
helm create notes-app
```

`helm create` generates a complete sample chart so you can see the structure:

```
notes-app/
  Chart.yaml          # metadata: name, version, the app version
  values.yaml         # default parameters (the knobs)
  templates/          # templated manifests
    deployment.yaml
    service.yaml
    _helpers.tpl       # reusable template snippets
    NOTES.txt          # message printed after install
  charts/             # subcharts (dependencies)
```

The generated templates deploy a sample nginx. To learn templating from first principles rather than wade through boilerplate, clear them out and we'll write our own:

```bash
rm notes-app/templates/*.yaml notes-app/templates/*.txt
```

---

## Chunk 4 — `Chart.yaml` and `values.yaml`

`Chart.yaml` is the package's identity:

```yaml
apiVersion: v2
name: notes-app
description: The Flask stack, as a Helm chart
version: 0.1.0          # the CHART's version (the packaging)
appVersion: "6.0"       # the APP's version (your flaskapp image tag)
```

Note the two versions: `version` is how the *chart* is packaged (bump it when you change templates), `appVersion` is the *software* it deploys. They move independently.

`values.yaml` holds the defaults — every knob someone can turn:

```yaml
replicaCount: 3
image:
  repository: flaskapp
  tag: "6.0"
greeting: "Hello from a Helm chart!"
service:
  port: 8080
```

---

## Chunk 5 — Templating a manifest

Now the heart of it. Create `templates/deployment.yaml` — your familiar Deployment, with values punched in where the hardcoded bits used to be:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: {{ .Release.Name }}-flask
spec:
  replicas: {{ .Values.replicaCount }}
  selector:
    matchLabels:
      app: {{ .Release.Name }}-flask
  template:
    metadata:
      labels:
        app: {{ .Release.Name }}-flask
    spec:
      containers:
      - name: flask
        image: "{{ .Values.image.repository }}:{{ .Values.image.tag }}"
        imagePullPolicy: IfNotPresent
        env:
        - name: GREETING
          value: {{ .Values.greeting | quote }}
        ports:
        - containerPort: 5000
        readinessProbe:
          httpGet: { path: /healthz, port: 5000 }
```

Three template ideas, which are 80% of what you'll use:

- **`{{ .Values.x }}`** pulls a value from `values.yaml` (or an override) — `.Values.replicaCount` becomes `3`.
- **`{{ .Release.Name }}`** is the name you give the release at install time. Prefixing every object with it is *why* you can install the same chart twice without collisions — `demo-flask` and `staging-flask` coexist.
- **`| quote`** is a pipeline function (wraps the value in quotes); Helm ships dozens like `default`, `upper`, `b64enc`.

And a matching `templates/service.yaml`:

```yaml
apiVersion: v1
kind: Service
metadata:
  name: {{ .Release.Name }}-flask
spec:
  selector:
    app: {{ .Release.Name }}-flask
  ports:
  - port: {{ .Values.service.port }}
    targetPort: 5000
```

(For the demo we're charting just Flask — a representative slice. Redis, Postgres, the Ingress, and the rest would be additional templates in this same `templates/` folder, following the identical pattern. Building out the *full* multi-service chart is exactly what the dedicated Helm course does.)

---

## Chunk 6 — Render before you install: `helm template` and `lint`

The single best way to demystify templating is to see what your `{{ }}` actually becomes — without touching the cluster:

```bash
helm template demo ./notes-app
```

Helm prints the fully-rendered manifests, every placeholder substituted. This is your debugging and learning tool — if an install behaves oddly, render it and read the real YAML. Catch structural mistakes first with:

```bash
helm lint ./notes-app
```

---

## Chunk 7 — `helm install`: your first release

```bash
kubectl create namespace helm-demo
helm install demo ./notes-app -n helm-demo
helm list -n helm-demo
# NAME   NAMESPACE   REVISION   STATUS     CHART             APP VERSION
# demo   helm-demo   1          deployed   notes-app-0.1.0   6.0
kubectl get all -n helm-demo
# deployment/demo-flask, service/demo-flask — note the "demo-" release-name prefix
```

One command deployed the slice as a named **release**, `demo`, at revision 1. The resources carry the `demo-` prefix from `{{ .Release.Name }}` — install again as `staging` and you'd get a parallel `staging-flask` with zero conflict. (The Flask pod runs fine here even without Redis/Postgres in this mini-chart — its home page just reports the counter as unavailable, the graceful degradation you built in Module 4.)

---

## Chunk 8 — `helm upgrade` and value overrides

Change a setting *without editing any file* — override a value on the command line:

```bash
helm upgrade demo ./notes-app -n helm-demo --set greeting="Upgraded via Helm!"
helm list -n helm-demo        # REVISION is now 2
```

Helm diffed the new render against the old and rolled the Deployment — the very Module 3 rolling update, now driven by a value change. This is the parameterization payoff: the *same chart* serves every environment, differing only by values. In practice you keep per-environment values files —

```bash
helm upgrade demo ./notes-app -n helm-demo -f values-prod.yaml
```

— so `dev` and `prod` are one chart plus two small values files, not two copies of fifteen manifests.

---

## Chunk 9 — `helm rollback`: undo the whole app at once

Every install and upgrade is a numbered revision, and any of them is one command away:

```bash
helm history demo -n helm-demo
# REVISION   STATUS       DESCRIPTION
# 1          superseded   Install complete
# 2          deployed     Upgrade complete
helm rollback demo 1 -n helm-demo      # back to revision 1's exact state
helm history demo -n helm-demo         # revision 3 = "Rollback to 1"
```

This is Module 3's `rollout undo` grown up: it doesn't revert one Deployment, it reverts *the entire release* — every resource the chart manages — atomically, to a known-good revision. A bad release of any complexity becomes a one-line recovery.

---

## Chunk 10 — The other half: consuming public charts

You don't author every chart — Helm is a *package manager*, so most of the time you **install other people's**. Add a repository, search it, install from it:

```bash
helm repo add ingress-nginx https://kubernetes.github.io/ingress-nginx
helm repo update
helm search repo ingress-nginx
helm install ingress-nginx ingress-nginx/ingress-nginx \
  -n ingress-nginx --create-namespace
```

That's the same ingress controller you installed from raw YAML in Module 9 — the Helm chart is the configurable, upgradable way to do it. The whole ecosystem works like this: Postgres, Redis, Prometheus, cert-manager, Grafana — all one `helm install` away, each parameterized through its `values.yaml`. (Artifact Hub is the public index.) Don't re-install ingress-nginx if you already have it running; this is illustration of the workflow.

---

## Chunk 11 — Cleanup and uninstall

```bash
helm uninstall demo -n helm-demo       # removes everything the release created, in one shot
kubectl delete namespace helm-demo
```

One command tears down the whole release — the inverse of `helm install`, and a world away from hunting down fifteen files to delete.

---

## Chunk 12 — Where the dedicated Helm course goes from here

You now have the whole mental model — **chart, template, values, release, repository** — and the full lifecycle: `install` → `upgrade` → `rollback` → `uninstall`. That's the foundation. The dedicated course builds depth on these same bones:

- The **template language** in full — named templates and `_helpers.tpl`, pipelines, conditionals and loops, `lookup`, `tpl`.
- **`values.schema.json`** to validate and document a chart's inputs.
- **Subcharts and dependencies** — managing Redis and Postgres as dependency charts rather than hand-written templates.
- **Hooks** — pre/post-install Jobs, e.g. running a database migration before the app rolls.
- **Packaging and publishing** to a chart repository or OCI registry, and chart testing.
- Charting a real *multi-service* app — turning the entire stack you built into one production-grade chart.

The concepts don't change; you just go deeper on each.

---

## Chunk 13 — Rare-but-real (recognize, don't memorize)

```bash
helm get values demo -n helm-demo        # what values a release was installed with
helm get manifest demo -n helm-demo       # the exact YAML a release applied
helm status demo -n helm-demo             # release status + the NOTES.txt output
helm diff upgrade demo ./notes-app        # preview an upgrade's changes (helm-diff plugin)
```

- **`helm-diff`** (plugin) — see exactly what an upgrade would change before running it; pairs with the `kubectl diff` habit.
- **`helmfile`** — declaratively manage *many* releases across environments.
- **Helm + GitOps** — Argo CD and Flux render Helm charts as part of the reconcile loop you learned in Module 1, so a git push becomes a `helm upgrade`.
- **OCI registries** — charts can now live in the same registries as your container images.

---

## Chunk 14 — Command cheat sheet

| Goal | Command |
|---|---|
| Install Helm | `brew install helm` |
| Scaffold a chart | `helm create <name>` |
| Render templates locally | `helm template <release> ./<chart>` |
| Lint a chart | `helm lint ./<chart>` |
| Install a release | `helm install <release> ./<chart> -n <ns>` |
| Override a value | `helm install/upgrade ... --set key=val` or `-f values.yaml` |
| List releases | `helm list -n <ns>` |
| Upgrade | `helm upgrade <release> ./<chart> -n <ns>` |
| Revision history | `helm history <release> -n <ns>` |
| Roll back the whole app | `helm rollback <release> <revision> -n <ns>` |
| Uninstall | `helm uninstall <release> -n <ns>` |
| Add a chart repo | `helm repo add <name> <url>` |
| Install a public chart | `helm install <release> <repo>/<chart>` |

---

## Chunk 15 — Checkpoint challenges

Do these from memory — no scrolling up.

**Challenge A — chart your app**
1. Define the five Helm concepts in your own words, and explain the difference between a *chart* and a *release*.
2. Scaffold a chart, then template a Deployment so its replica count, image tag, and greeting all come from `values.yaml`. Explain why prefixing resource names with `{{ .Release.Name }}` matters.
3. Render the chart with `helm template` *without* installing it, and explain why you'd do that before an install.

**Challenge B — the lifecycle**
1. Install your chart as a release named `demo`. Change the greeting via `helm upgrade --set` and confirm the revision number incremented. Explain what Helm did to the running pods under the hood.
2. Roll back to revision 1 in one command, and explain how this differs from `kubectl rollout undo` in scope.
3. Add a public chart repository and show how you'd install a community chart from it. Explain what makes this the "package manager" half of Helm.

**Bonus question (mental model):** You have one chart and need to run it in `dev` (1 replica, image tag `dev`, debug greeting) and `prod` (5 replicas, tag `6.0`, real greeting). Explain how you'd achieve both from the *same* chart without copying any YAML — naming what stays fixed (the chart/templates) and what varies (values), and how each becomes its own isolated release.

---

## Course wrap-up

That's the whole course. Look at the distance traveled.

You started at the ceiling of the Docker course — a Flask + Redis + Postgres stack that ran on one machine with Compose, with no way to heal, scale, or update without downtime. Module by module, you taught a cluster to run it properly: pods as the unit of work, Deployments that self-heal and roll out without dropping a request, Services and DNS so the pieces find each other by name, ConfigMaps and Secrets so configuration lives outside the image, PersistentVolumes and a StatefulSet so data survives anything, probes and resource limits so "running" actually means "working," namespaces and quotas so it's organized and bounded, an Ingress as the single front door, autoscaling and scheduled jobs so the cluster reacts on its own, RBAC so nothing runs with more power than it needs, a debugging playbook for when it all goes sideways at 3 a.m. — and finally Helm, folding the whole thing into one installable package.

The thread running through every module was the same idea you met in Module 1: you declare the desired state, and the cluster reconciles reality toward it, forever. Deployments, Services, the autoscaler, Helm — they're all that one loop, applied to a different gap between what-you-want and what-is. Once that clicks, Kubernetes stops being a pile of commands and becomes a single coherent idea with many faces.

From here, the natural next steps are the courses already on the horizon — Helm in depth, and Prometheus for the observability layer this course only pointed at. But you've got the spine now: a complete, production-shaped Kubernetes system you built with your own hands, one command at a time.

*End of Module 13 — and the end of the course.*
