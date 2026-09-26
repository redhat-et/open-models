# EnMaaS goes prod: standing up a monitoring stack the boring way

*September 2026 — the EnMaaS platform moves from "a cluster that exists" to
"a cluster you can trust." Prometheus, Alertmanager, Grafana, SLOs, and a
bunch of OpenShift gotchas that only show up when you do it for real.*

We've been running an LLM serving platform (Open-Models) on the "emerg" cluster
for a while — a litellm gateway in front of vLLM workers on an L40S core and
a 5090 box, serving both our own users and PriceTag. It works, but it's a
pet: single cluster, hand-deployed, no real observability story beyond a raw
Prometheus.

So when we decided to build the real platform (praxis-ai, Turnstile, MaaS,
metering — EnMaaS), we stood up a fresh ROSA HCP cluster, wrote down the rules
first, and then built a monitoring stack the way we'd have to defend it in a
postmortem.

This is the story of that stack: what we built, the three rules that kept us
sane, and the half-dozen ways OpenShift on HCP bites back.

## The rules, in one sentence each

1. **Clusters are cattle, not pets.** Everything declarative, no state on the
   cluster, one-command bootstrap, recovery drilled quarterly. If it wasn't
   rehearsed, the "cattle" claim is fiction.
2. **Images: podman build → quay, no in-cluster builds.** The cluster pulls;
   it never compiles.
3. **OCP Templates, not Helm.** One template per service, per-env params
   files, `oc process | oc apply` is the whole deploy story.

The full rules are in the repo (`docs/enmaas-best-practices.md`). They read
like common sense. They were written by someone who'd already been burned.

## The stack

Three services, one namespace (`enmaas-monitoring`), one intent: when
something breaks, a human with a phone and a Slack channel finds out before a
user does.

- **Prometheus** (quay.io/prometheus/prometheus, pinned LTS) — scrapes
  itself, Alertmanager, Grafana, and the EnMaaS workloads: the praxis proxy,
  the MaaS control-plane API, the CNPG Postgres operator, and the Postgres
  instances themselves.
- **Alertmanager** — routes `page`/`critical` alerts to Slack
  `#enmaas-alerts` with a webhook stored in a Secret (never a template).
- **Grafana** — basic-auth fronted by the same nginx sidecar, Prometheus
  pre-provisioned as the datasource.

Everything is an OpenShift Template with a `params/` file per environment. The
routes have pinned hosts (never let the router guess — that lesson is already in
our history as a 503'd production URL).

## Annotation-driven service discovery

We didn't hardcode scrape targets. Prometheus uses `role: service` discovery
against the `enmaas` and `cnpg-system` namespaces and keeps any Service
tagged:

```yaml
annotations:
  prometheus.io/scrape: "true"
  prometheus.io/port: "9901"
```

Adding a new component to monitoring is one `oc annotate`. The prometheus
ServiceAccount got a least-privilege Role (`get/list/watch services` only) in
each namespace — no cluster-admin, per our own rule 8.

Current targets: `praxis` (the proxy — request counters, errors, latency),
`maas-api` (Go runtime), `cnpg-controller-metrics` (operator), and
`aigateway-pg-metrics` (pg_exporter on 9187, selected by `cnpg.io/cluster`).

## SLOs before they were asked for

The praxis proxy is the user-facing door, so it's the SLO target:

- **Availability: 99.9% over 30 days.** Errors = `praxis_errors_total`
  divided by `praxis_upstream_requests_total`.
- **Latency: p99 < 4s** on `praxis_http_request_duration_seconds`.

We record the SLIs (`slo:praxis:error_ratio:5m`, `availability:30d`,
`error_budget_remaining:30d`) so Grafana dashboards can show budget burn, and
alert on them with the SRE workbook's multi-window multi-burn pattern:

- 14.4x burn rate sustained for 1h → **page** (~2% of the 30d budget)
- 3x burn rate for 6h → **warning**

Plus component-down alerts across all seven scrape targets and a config-reload
watchdog on Prometheus itself.

## The war stories

None of this was smooth. That's the point — here's what OpenShift HCP actually
did to us, so your next cluster doesn't have to.

### HCP has no `Template` API kind

`oc process -f` against a ROSA HCP cluster fails: `no matches for kind
"Template"`. The Template API just isn't served. Client-side rendering
(`oc process --local`) works — but `--local` **drops object namespaces** and
stamps the current kubeconfig namespace instead. We found our entire stack silently
landing in `default` with `oc -n enmaas-monitoring get pods` returning empty,
which reads exactly like a ghost. Fix: `oc process --local ... | oc apply -n <ns> -f -`.

Also: `oc apply | head` dies from SIGPIPE and silently stops applying halfway
through the file. We lost a ServiceAccount that way and spent an hour on a
"pod can't find its SA" error.

### `restricted-v2` SCC rejects pinned uids

Setting `runAsUser: 65534` / `fsGroup: 65534` (the "standard" Prometheus
recipe) is rejected — the namespace is assigned a uid range like
`[1000850000, 1000859999]` and fixed values are forbidden. The OpenShift
way: don't pin uids, let SCC assign. Prometheus, Alertmanager, and Grafana
all run fine as arbitrary uids.

### Every password provider disappeared

First try: oauth-proxy with the openshift provider for SSO. Then: oauth2-proxy
v7 — `htpasswd` provider **removed**. Then origin-oauth-proxy 4.22 — no
htpasswd provider either. Then the `openshift` provider... which worked, right up
until `rosa create idp` wedged the hosted control plane and the OAuth server
stopped being able to create login sessions at all.

That's when we stopped depending on cluster OAuth for observability and put an
**nginx basic-auth sidecar** in front of every route. One secret, bcrypt
htpasswd, zero dependency on a broken control plane. SSO is still the right
long-term answer; the nginx sidecar is the answer that works at 2am.

### The NetworkPolicy that made healthy pods unreachable

After the nginx swap, every route returned 503 with perfectly healthy pods. The
sidecar listened on 8080; the NetworkPolicy (and the Service) admitted 8443.
Router traffic was being silently dropped. Sidecars must listen on the port the
policy allows. This one took embarrassingly long to find because everything *looked*
up.

### Rules that never loaded

Our alert rules were mounted at `/etc/prometheus/rules-alerts.yml` but the
config globbed `/etc/prometheus/rules/*.yml`. Prometheus started fine, targets
scraped fine, and **no rules were ever evaluated** — until the SLO work made us
read the `/api/v1/rules` output and see an empty list. The watchdog rule for
config-reload failures was itself silently dead. There is no worse bug than the one
that disables your alarms without telling you.

### Grafana's password is only read once

Grafana reads `GF_SECURITY_ADMIN_PASSWORD` when it *initializes* its database.
Change the Secret later and nothing happens — the UI still takes the old password.
Fix: `grafana cli admin reset-admin-password`. We also learned this the hard way
when a `sed`-style awk parse turned the admin password into `/`.

## What it looks like now

```
7/7 targets up, rules loaded, SLOs recording, Slack wired.
```

A test alert posted to Alertmanager's API showed up in `#enmaas-alerts`
before we'd even finished the coffee. The whole thing is `oc process --local |
oc apply -n` + a `params/` file — the namespace can be deleted and rebuilt
boringly, on purpose, any time.

## The parts we still owe

- The `enmaas` workload manifests (praxis, MaaS, metering) are in the
  cluster but not yet in the repo — violating our own rule 1 until they land.
- `metering-service` exposes no metrics endpoint at all. It's on the list.
- Grafana dashboards are now provisioned from git (`07-grafana-dashboards.yaml`)
  — EnMaaS Overview with 9 panels (request/error rates, error ratio, p99
  latency, 30d budget + availability stats, postgres/CNPG/MaaS backends),
  referenced via the deterministic `$DS_PROMETHEUS` datasource variable so they
  survive a namespace rebuild. A generator bug taught us to count `len(panels)`
  before believing a provisioned dashboard is non-empty.
- Alertmanager is Slack-only. PagerDuty for the `page` severity is next.
- The `30d` availability recording rule needs 30 days of data before it means
  anything. The burn-rate alerts are the ones that matter today.

The boring part — the templates, the params, the RBAC, the README with the war
stories — is in `deployments/enmaas/monitoring/` and `docs/` in the
open-models repo. If the cluster died right now, it'd take one command and a
params file to rebuild, and that's the whole point.

---

*All credentials, webhook URLs, and cluster-admin tokens have been redacted. The
actual deployment lives in `deployments/enmaas/monitoring/` with a README
that is simultaneously the runbook and the next agent's context file.*
