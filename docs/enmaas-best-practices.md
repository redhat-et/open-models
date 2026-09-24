# EnMaaS Deployment Best Practices

Rules of engagement for deploying applications on the EnMaaS platform
(praxis-ai, Turnstile, MaaS, metering — and whatever joins them).
Written for humans and for models: every rule here is mechanically
checkable or has a linked artifact.

## The three founding rules

### 1. Clusters are cattle, not pets

We have already moved once (us-east-2 quota block → us-west-2) and we
will move again when the east-2 quota lands. Consequences:

- **Declarative or it doesn't exist.** Every object in a cluster must be
  reproducible from git (`oc process -f template | oc apply -f -`). If
  you `oc edit` something in the cluster, you have created a pet. Drift
  is measured by `oc diff` against the rendered template — make it
  part of CI or at least the weekly sweep.
- **No state on cluster, ever.** Databases run under an operator
  (CNPG for Postgres — see `ai-gateway-metering-service/deploy/cnpg/`)
  with continuous WAL archiving + base backups to S3/COS, so a dead
  cluster costs minutes, not data. The metering repo's
  `docs/db-design.md` (2026-09-16 cutover record) is the reference
  procedure; copy it, don't reinvent it.
- **Never hardcode a route hostname.** Routes are cluster-scoped
  facts; hosts change on every move. Route hosts are template
  *parameters* (`ROUTE_HOST`), consumed from config by callers, never
  baked into images, docs, or another app's env. Cautionary tale: our
  litellm-era `deploy_model.py` once re-applied the core route without
  a host and silently 503'd the production URL — same class of bug as
  every hardcoded URL.
- **Bootstrap must be one command.** `make deploy ENV=<env>` processes
  the params file, applies secrets, rolls the smoke test. Target:
  cold cluster to green smoke test < 15 minutes.
- **Recovery is drilled, not promised.** Quarterly: delete a namespace,
  reapply from templates, restore the DB from backup, verify. If it
  wasn't rehearsed, the cattle claim is fiction.
- **PDBs + honest probes** on everything user-facing so node/cluster
  eviction is boring.

### 2. Images: podman build → quay.io. No in-cluster builds.

- No BuildConfig, no s2i, no `oc import-image` from source. The cluster
  pulls; it never compiles.
- **Build explicitly:** `podman build --platform linux/amd64` (pin the
  arch; don't inherit your laptop's), multi-stage (toolchain out,
  runtime in), base on `registry.access.redhat.com/ubi9/*` or distroless,
  non-root `USER`, run as `restricted-v2`-compatible (4.22 enforces
  restricted SCC by default anyway — build for it, don't fight it).
- **Quay conventions:** `quay.io/<org>/enmaas-<app>`, tags = immutable
  (`git describe` / semver / sha). `latest` is for humans, never for
  template params. Templates pin digests (`@sha256:`) or at minimum an
  immutable tag; the *same* image tag promotes staging → prod (never
  rebuild per environment).
- Prefer **public** quay repos: the whole stack is Apache-2.0, and
  public pull kills an entire class of imagepullsecret incidents.
- Provenance labels in the image (`org.opencontainers.image.revision`,
  `.source`) so `podman inspect` answers "what git sha is running?".
- Once CI exists: builds run in CI, not on laptops; quay's scanner gates
  `Critical` CVEs on the promote step.

### 3. OCP Templates, not Helm (App SRE tooling constraint)

- One Template per service, versioned in git; parameters cover **every**
  environment-varying value (route host, replicas, image tag, secret
  names). No values inlined for one environment.
- Per-environment params files in the repo (`enmaas-prod.params`), so
  `oc process -f template.yaml --param-file=enmaas-prod.params | oc apply -f -`
  is the entire deploy story.
- `oc process --local -o yaml` in CI to prove templates render;
  `oc diff` to prove clusters match.
- Keep conditionals in templates near zero — if a template needs
  branching logic, split it into two templates.
- Standard labels on every object: `app.kubernetes.io/name`,
  `app.kubernetes.io/part-of=enmaas`, `app.kubernetes.io/managed-by=template`.
  Pruning and diffing depend on them.
- Secret *references* only — never put secret *values* in params. If
  a value must be generated, mark the param `required: true,
  generate: expression` **and `protected: true`**, understanding it
  still lands in the Template resource — prefer pre-created Secrets.
- Because this is a tooling constraint and not a theology, hide the
  mechanism behind `make deploy-<service> ENV=x` — if App SRE ever
  blesses Helm, we swap the Makefile internals, not the culture.

## Additions we should adopt from day one

4. **Pin the platform too.** OpenShift minor (4.22.x), praxis-ai image
   tag (it is *alpha* — pin deliberately and bump via PR with the
   changelog read; never track `:latest` in prod), operator versions.
   Upgrades are pull requests, not `oc adm upgrade` vibes.
5. **Observability is part of the template.** Every service ships its
   ServiceMonitor/PrometheusRule alongside its Deployment (praxis
   exposes `praxis_ai_ttft_seconds`; metering has its own dashboards).
   Logs go to stdout structured — no log files, no sidecars.
6. **Resource honesty.** Requests+limits on every container, sized by
   the load tests, not guesses. (litellm OOM-killed itself in a 2Gi
   limit under real traffic until we measured; see open-models commit
   22fa828.) Default-deny `NetworkPolicy` per namespace with explicit
   allows.
7. **External endpoints are config.** Backends (vLLM boxes, praxis
   upstreams, `TURNSTILE_UPSTREAMS`) come from params/Secrets. Several
   of our model hosts are on dynamic WAN IPs — anything that hardcodes
   them dies the first time the DSL reconnects.
8. **RBAC least privilege.** Apps run with dedicated ServiceAccounts
   and the narrowest Role they can work with; nothing requests
   cluster-admin; document any exemption in the app's README.
9. **The app README is the interface.** Each service directory states:
   what it does, ports, env vars, template path, params table, health
   endpoints, backup/restore procedure. Keep it current in the PR —
   this file is simultaneously the onboarding doc for the next human
   and the context file the next agent session loads.
10. **Migration runbook is a first-class artifact.** Cluster move =
    inventory (namespaces + templates + backup URLs) → rebuild cluster
    (VPC CFN + rosa, ~15 min as proven) → restore → re-point clients →
    old cluster idle 24h → destroy. The move should be boring because
    it is documented, rehearsed, and scripted — not because we're lucky.

## Quick reference

```
# deploy / re-deploy a service
make deploy-turnstile ENV=enmaas-prod        # wraps: oc process --param-file | oc apply

# prove a cluster matches git
oc process -f turnstile/template.yaml --param-file=enmaas-prod.params -o yaml \
  | oc diff -f - -n enmaas-prod

# build + publish an image
podman build --platform linux/amd64 -t quay.io/<org>/enmaas-turnstile:$(git describe --tags) .
podman push quay.io/<org>/enmaas-turnstile:$(git describe --tags)
```
