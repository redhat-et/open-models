# EnMaaS goes public: DNS, TLS, and the guardrails that bit back

*October 2026 — the platform gets real hostnames, a managed database, and its
first functional test suite. Along the way a default-deny policy blinds the
monitoring stack from the last post, a security gate forbids the exception it
documents, and an automated deploy refuses to run for an extremely good
reason.*

[Last time](2026-09-enmaas-monitoring.md) we took EnMaaS from "a cluster that
exists" to "a cluster you can trust": Prometheus, Alertmanager, Grafana, SLOs,
and a pile of OpenShift HCP gotchas. That post ended with a list of things we
owed. This one is about paying some of them off, putting the platform in front
of real users, and the specific ways our own safety mechanisms turned around
and bit us.

Phase 1 onboarding starts now — hundreds of engineers — with GA two weeks out.
So the theme of the month was less "build features" and more "make the thing
survive contact with people who didn't build it."

## Paying off last month's debts

Post 1 owed four things. Scorecard:

- **"The `enmaas` workload manifests are in the cluster but not yet in the
  repo — violating our own rule 1."** Paid. Routes, TLS certificates, the
  cert-manager bootstrap and the database exporter all live in git now. This
  took more work than it sounds like, and most of this post is about why.
- **`metering-service` exposes no metrics.** Still owed. Still on the list.
- **Alertmanager is Slack-only; PagerDuty next.** Still owed.
- **The 30-day availability rule needs 30 days of data.** Paid, by the
  calendar. `slo:praxis:availability:30d` returns a number now.

A small confession on that last one. While writing this post we queried
`availability:30d`, got nothing back, and very nearly published a paragraph
about how a month of scrape gaps had starved our own SLO rule. The actual
recording rule is named `slo:praxis:availability:30d`. We had queried a metric
that never existed and briefly believed a story about ourselves that wasn't
true. Check the metric name before you write the postmortem.

Targets went from 8 to 9: a `postgres_exporter` for the new managed database.

## The public surface

EnMaaS now answers on two real hostnames instead of cluster-generated ones:

| Host | Serves |
|---|---|
| `api.enmaas.devshift.net` | the inference API |
| `dashboard.enmaas.devshift.net` | dashboard and partner APIs |

One inference hostname carries every dialect, and the **path** picks the
listener: `/v1/messages` goes to the Anthropic pipeline,
`/v1/chat/completions`, `/v1/responses` and `/v1/conversations` to the
OpenAI-compatible one. Each path is its own Route. That detail matters later.

Certificates come from Let's Encrypt via cert-manager, over **HTTP-01** — not
because HTTP-01 is nicer, but because the DNS zone lives in a different AWS
account than the cluster, so DNS-01 was never an option. The Routes reference
the resulting Secrets through `spec.tls.externalCertificate`, which quietly
requires an RBAC grant letting the ingress router read Secrets in your
namespace. Without it the Route just... doesn't serve, with no obvious
complaint.

Renewal is automatic at 60 days of a 90-day lifetime. The deploy refuses to
create Routes if the TLS Secret isn't there yet, which turns a confusing
half-broken edge into an honest error.

## The database moved out

The in-cluster CloudNativePG Postgres became AWS RDS: Multi-AZ, enforced TLS
(`rds.force_ssl`), on managed backups. Two DSNs now: a read/write one for
ingestion and a **read-only role** used by dashboard reads and the metrics
exporter, so a runaway dashboard query can't touch write capacity.

The interesting consequence is that database access became a *network* concern.
Egress to the database is a specific NetworkPolicy to a specific CIDR on 5432.
Get that wrong and the symptom isn't "permission denied" — it's a pod hanging
at startup. Which brings us to the war stories.

## The war stories

### The default-deny policy that blinded the monitoring stack

We locked the namespace down with a default-deny NetworkPolicy — unambiguously
the right thing to do. It landed at 14:56 UTC. It also dropped every scrape
into the namespace, because the allow-rules for monitoring weren't part of the
same change.

The monitoring stack from last month's post — the one whose entire purpose is
to tell a human before a user notices — went dark for about two hours and
could not tell anyone, because the thing that reports "component down" was
itself behind the policy.

The lesson isn't "don't use default-deny." It's that a default-deny change and
its allow-rules are **one change**, never two. We now have the monitoring
ingress rules in git next to the deny rule, and `tools/functional-test.sh`
asserts every target is up.

A related tell worth internalizing: *one* target down is a workload problem,
*all* targets down at once is a policy problem. Those two symptoms deserve
completely different first moves.

### DNS egress that looked like a database outage

Shortly after the lockdown, `maas-api` started crashlooping — six restarts,
exit code 2, last log line `Connecting to PostgreSQL`. Everyone's first
instinct, including ours, was "the RDS migration broke something."

It hadn't. The DNS egress rule selected `kube-system`, but on this platform
DNS lives in `openshift-dns`. The pod couldn't resolve *anything*; the database
hostname was simply the first name it tried to look up. The error message
pointed at the last thing that worked, not the thing that was broken.

If a pod dies at startup right after a policy change, check name resolution
before you check the thing it says it was connecting to.

### Two route sets, one claim

OpenShift admits **one Route per host+path**. A duplicate claim isn't an error
you see — the second Route is silently rejected and the path keeps being served
by whichever one won.

We'd ended up with two overlapping sets: Routes created by hand during the TLS
work, and Routes declared in git. Both pointed at the same Services with the
same certificates, so everything looked fine. Underneath, 23 Routes were sitting
in `Admitted: False`, and which copy was actually serving any given path was
essentially historical accident.

Converging it was anticlimactic in the best way: delete the hand-made set, watch
the git-declared Routes pick up the freed claims within seconds, verify traffic
never moved. 23 not-admitted went to 0, and now git owns every claim in the
namespace.

The check is cheap and worth wiring into your tests:

```bash
oc -n enmaas get routes -o json | \
  python3 -c 'import json,sys; [print(r["metadata"]["name"], c["status"]) \
    for r in json.load(sys.stdin)["items"] \
    for i in r.get("status",{}).get("ingress",[]) \
    for c in i.get("conditions",[]) if c["type"]=="Admitted" and c["status"]!="True"]'
```

Also deleted: a Route someone had created in the web console for testing, which
forwarded a `X-MaaS-Username` header straight through. It served 503s and had
been sitting there for days. Console-created resources are invisible to git and
to review.

### The gate that forbade its own exception

The gateway exposes its metrics on an admin listener that binds to loopback by
default. To let Prometheus scrape it, the proxy requires you to explicitly set
an `allow_public_admin` flag — a deliberate, documented decision, with
compensating controls written into the security baseline: default-deny,
ingress restricted to monitoring namespaces, never exposed through a Route.

Flipping the flag is therefore sanctioned. Except the deploy wouldn't go
through, and neither would the validators, which failed with:

```
Praxis enables insecure_options.allow_public_admin by default
```

The flag is passed as an environment variable. Both validators built a
"default render" to prove the repo's default is loopback — but neither
*pinned* the variable, so the render inherited it from the operator's shell.
The moment you exported the flag in order to deploy, the gate decided your
intentional, documented exception was an accidental insecure default.

The fix is two words (`PRAXIS_PUBLIC_ADMIN=false` on the baseline render). The
lesson is bigger: **a gate that can't distinguish "the repo's default" from
"this operator's intent" will eventually block the exact thing it was written
to permit.** For hours, the correct behaviour — run the gate, refuse on
failure — looked like an automation being stubborn.

### The authentication that was documented but never existed

A set of partner APIs was being prepared for other teams. The repo was explicit
about the design:

> *"The Metering listener intentionally does not authenticate these partner
> paths. The OpenShift Route/AuthPolicy must authenticate the caller and must
> prevent direct Service or port-forward bypass."*

Clear, deliberate, and written down. The problem: no AuthPolicy existed. The
CRD was installed with zero instances, and the policy engine that would enforce
the alternative wasn't on the cluster at all. The design had moved
authentication out of the application and the replacement had never been built.

An automated deploy stopped at preflight and refused to continue, on the
grounds that shipping those endpoints would expose them with no credential
anywhere in the path. It was right. The resolution was to restore
application-level bearer authentication on those endpoints — the opposite
direction from the documented design, and the correct call for a service about
to meet real users.

Two things worth taking from this. First, "authentication is enforced at a
different layer" is only true if someone has *built* that layer; otherwise it's
a comment describing a hole. Second, when your tooling refuses to deploy, the
useful question is "what does it know that I don't?" rather than "how do I
override it?"

### The catalog that advertised dead models

We wrote a functional test suite this week (more below). On its first
authenticated run against production it failed two assertions we'd expected to
pass trivially: the models we were testing with weren't in the catalog.

They weren't. `GET /v1/models` with an OpenAI-style Bearer token returned
exactly ten Claude models — and every one of them returns 404 on
`/v1/chat/completions`, because Claude models are routed only through the
Messages dialect. Request a Claude model on the OpenAI path and it falls
through to the OpenAI upstream, which quite reasonably reports:

```
404  The model `claude-haiku-4-5` does not exist or you do not have access to it.
```

That error text is OpenAI's, not ours. Meanwhile the models that *do* work on
that endpoint — three GPTs and our free hosted GLM — weren't advertised at all.

Cause: four `model_catalog` filters in the same chain, all matching
`/v1/models`. First match wins, and the two Claude-only filters were shadowing
the two that listed everything else. Net effect for any client doing model
discovery: a menu where every item is broken, and the working items are
invisible.

This is the single most user-visible defect we've found, and it existed because
the catalog and the router are configured independently. Nothing validated that
a model you *advertise* is a model you can *call*. Now something does.

### Reasoning models bill you for thinking

Smaller, but it cost us a confused twenty minutes. Our first inference test
capped output at 16 tokens to keep the suite cheap. Against GLM 5.3 it came
back `200 OK` with:

```json
"finish_reason": "length",
"message": { "content": null, "reasoning_content": "The user is asking me to..." }
```

GLM is a reasoning model. It spent the entire 16-token budget thinking and had
nothing left to say out loud. The call succeeded, was routed correctly, and was
billed — it just produced no visible output.

A test asserting `content` is non-empty calls that a failure. The honest
assertion is "the model produced output *and* tokens were billed", accepting
either visible content or reasoning content. Worth knowing before you set a
tight `max_tokens` in production code, too.

And one more parameter trap: `gpt-5.x` rejects `max_tokens` outright and
requires `max_completion_tokens`. A client that hardcodes one spelling works
with some models and fails with others.

### The dashboards were blank for impeccable reasons

Last month's post was quietly proud of provisioning Grafana dashboards from
git, "referenced via the deterministic `$DS_PROMETHEUS` datasource variable so
they survive a namespace rebuild." Someone mentioned this week that the
dashboards weren't working.

They weren't. Every panel showed no data. So we extracted all 24 panel queries
from the ConfigMap and ran them straight against Prometheus. All 24 returned
data. The metrics were healthy, the recording rules were healthy, the queries
were correct, and the dashboards were blank.

The panels bind to datasource `uid: ${DS_PROMETHEUS}`, and that dashboard
variable pins `uid: "prometheus"`. The datasource provisioning file declared
no `uid` at all — so Grafana generated a random one at startup, and every
panel resolved to a datasource that did not exist. The dashboard half of
"deterministic" was deterministic. The datasource half had never been pinned.
And because the variable is `hide: 2`, there wasn't even a picker in the UI to
notice the mismatch.

Then the fix took Grafana down.

Pinning `uid: prometheus` and applying it looks like a one-line change. But
Grafana keeps datasources in its PVC-backed database, and the existing
`Prometheus` record had the old generated uid. Provisioning tried to update a
record it couldn't find:

```
Failed to provision data sources
error="Datasource provisioning error: data source not found"
```

Provisioning is a **startup module**, so that error isn't a warning — it's
fatal. Grafana crashlooped. And the deployment strategy is `Recreate`, so
there was no old pod still serving. Dashboards were down for about three
minutes while we worked out that the provisioning file needs to *delete* the
datasource by name before recreating it with the pinned uid.

Worth being clear about the blast radius, because it's the part that was
designed correctly: Prometheus and Alertmanager were untouched the whole time.
Alerting never went blind. We lost the ability to *look* at the system, not
the ability to be *told* about it. That separation is the only reason this was
an annoyance instead of an incident.

While in there we found a second, quieter fault. The nginx sidecar
authenticates with `auth_basic` and then proxied the browser's `Authorization`
header straight through to Grafana, which dutifully tried to authenticate
those credentials as a *Grafana* user and failed on every single request:

```
too many consecutive incorrect login attempts for user - login for user
temporarily blocked
```

Several lines per second, for days, as a direct consequence of people
successfully logging in. nginx is the authentication boundary, so it now
clears the header and Grafana accepts anonymous `Viewer`. Failed-login lines
went from a steady stream to zero.

Two lessons, one old and one new. The old one: **a dashboard that renders is
not a dashboard that works** — last month we learned to count `len(panels)`
before believing a provisioned dashboard, and this month the panels were all
there and still showed nothing. The new one: when you pin an identifier that
was previously generated, something somewhere already stored the old value.

## The functional test suite

All of the above is why we stopped hand-curling endpoints and wrote
`tools/functional-test.sh`. It runs against any environment and has four
cumulative levels:

| Level | What it proves | Credential | Cost |
|---|---|---|---|
| `smoke` | DNS, TLS validity, redirects, health, the unauthenticated deny wall, forged identity headers, Route admission, workload readiness | none | free |
| `auth` | a valid key works, an invalid one doesn't, both catalog dialects, the key is never echoed back | key | free |
| `inference` | one real completion per model and endpoint | key | tokens |
| `full` | streaming, error shapes, concurrency, metering | key | tokens |

Anything missing a credential **skips** rather than fails, so the same script
runs unchanged in CI with no secrets and on a laptop with full cluster access.
Production smoke runs on a schedule four times a day; model traffic requires an
explicit `--confirm-prod`.

Most assertions encode a failure that already happened — Route admission,
forged `X-MaaS-Username`, the partner API deny wall, the certificate renewal
window. That's the main thing we'd recommend copying: **write the test for the
outage you just had, while you still remember what the symptom looked like.**

One design question took a couple of iterations. The first version demanded a
literal `401` everywhere and "failed" nine checks that were actually correct —
browser endpoints legitimately redirect to `/login`, and a bare path prefix
legitimately 404s when the real handler lives on a sub-path. Asserting `401` on
a path with no handler proves nothing at all. The property that actually matters
is **"an unauthenticated request never gets a 2xx"**, with the documented
partner contract held to a strict `401`/`403`.

## What it looks like now

```
9/9 targets up. 32 Routes, all admitted, all declared in git.
TLS valid 87 days, auto-renewing. Smoke: 32 passed, 0 failed.
Full suite: 44 passed, 3 failed — all three the catalog defect, filed.
Dashboards rendering, datasource uid pinned, failed-login spam at zero.
```

The database is managed and Multi-AZ, the namespace is default-deny with
explicit allow-rules in git, certificates renew without anyone watching, and
the hostnames are ones you can tell a user over a video call without spelling
out a cluster ID.

## The parts we still owe

- **`metering-service` still exposes no metrics.** Second month on this list.
  Called out again deliberately.
- **PagerDuty for `page` severity.** Alertmanager is still Slack-only. Which
  means that for three minutes this month our dashboards were down and the
  only thing that would have told us is a Slack channel.
- **Nothing tests the dashboards.** We assert that targets are up and that
  rules load. Nothing asserts that a panel can actually resolve its datasource,
  which is precisely the failure we shipped. A one-request check against
  `/api/datasources/proxy/uid/prometheus` would have caught it.
- **The catalog defect is filed, not fixed.** Model discovery is unreliable for
  OpenAI-dialect clients until the duplicate filters are removed.
- **A deprecated hosted model is still advertised** in the catalog and still
  shown in the welcome page's model picker, after being superseded. Tracked.
- **The metrics exception is still an exception.** A sidecar that republishes
  only `/metrics` would remove the need for the permissive flag entirely. We
  built most of it, then stopped: the compensating controls already constrain
  the risk, the proxy's own roadmap may make it moot, and "we already built it"
  is a bad reason to land a change nobody needs.

---

*Credentials, internal endpoints and account identifiers are redacted. The
deployment manifests live in the `pricetag` repository; the monitoring stack
and its runbook remain in `deployments/enmaas/monitoring/`. As before, the
README is simultaneously the runbook and the next person's context file.*
