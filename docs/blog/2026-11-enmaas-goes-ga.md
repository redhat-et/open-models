# EnMaaS goes GA: the release, the numbers, and the load tests that settled it

*October 2026 — the platform is in front of 1,400+ users with a dollar cap
that actually enforces itself. We prove the accounting holds under 8,000-user
load, watch our own dashboards blame traffic for a metering artifact, lose an
hour to one wrong character in a model ID, and hand the whole load-testing job
to people who do it for a living.*

[Last time](2026-10-enmaas-goes-public.md) we put the platform on public
hostnames, moved the database to managed RDS, and got bit by our own guardrails.
That post ended with a debt list. This one covers the thing the debts were
pointing at: the October 15 GA. Phase 1 onboarding, the phase-2 style load
that had to prove it, the accounting model meeting real traffic, and — because
this series exists to be honest — the two hours we spent investigating a CPU graph
that was lying.

## Paying off last month's debts

- **"`metering-service` still exposes no metrics."** Still owed. Third month
  calling it out. On purpose: it is the one service with no scrape target and no
  component SLO, and it is the service that decides whether the bills are right.
- **"PagerDuty for `page` severity."** Still owed. Alertmanager remains
  Slack-only. The interim human pager rotation exists; the escalation receiver does
  not.
- **"Nothing tests the dashboards."** Still owed. We still assert targets are up
  and rules load; we still don't assert a panel can resolve its datasource.
- **"The catalog defect is filed, not fixed."** Paid. The OpenAI dialect now
  advertises the models that actually answer on it — the GPTs and GLM — instead
  of a menu of Claude models that 404. `GET /v1/models` reflects what you can
  actually call.
- **"The metrics exception is still an exception."** Paid as a decision: we keep
  the `allow_public_admin` flag behind default-deny + namespace-restricted
  ingress, and stop there. "We already built the sidecar" is a bad reason to
  land a change nobody needs, and the proxy's own roadmap may make it moot.

## The thing on the calendar: October 15

The number that matters at GA is not "how many users." It's "how much did each
one cost, and is that number right." So this month's story is the accounting
boundary and the load we put it under.

At release the prod database holds **1,459 active users and 1,461 active keys**.
The per-user cap is **$300/month, enforced through AI Budget**, with metering's
own `quota_policy` sitting at $600 as the fallback that takes over enforcement in
the future — GLM is exempted from the cap so the hosted free-tier model stays
available when frontier quota runs dry. Enforcement is
pre-request: the gateway checks the balance before it forwards and metering rejects
over-cap calls except for the allow-listed model.

The interesting design property is that quota is enforced by **two independently
deployed services that don't share code** — the praxis proxy checks an entitlement
endpoint, metering decides. That split is exactly where an accounting defect would
hide, which is why we load-tested the ledger rather than the gateway.

## The load test that measured the wrong thing first

Phase 2 assumes 8,000 users. We had the perf harness from the stage
characterization work, so we pointed it at a more realistic problem than requests per
second: **does the accounting survive sustained, realistic traffic?**

The second campaign used a mocked 10–30 second-inference upstream — the thing
that turns a benchmark into "what a platform of slow reasoning models actually
feels like." Steady 100 RPS with bursts to ~180–200 RPS, at an average
inference of ~20 seconds, which means about **2,000 in-flight requests at all
times**. Over an overnight run: **2.48 million usage events through the ledger,
all `200`, no metering errors, no rollup failures.**

The KPI we treated as the go/no-go gate wasn't throughput. It was **the
reconciliation delta: ledger events emitted by the gateway versus usage events
written by metering**. It was zero. 2.48 million events in, none missing.
For a metered platform, throughput you can't bill is not throughput; it's a leak
you haven't found yet.

We handed the harness, the KPIs (requests, concurrency, latency, error rate,
accounting delta, metering health, resource headroom), and the whole job to the
perf team after. This is the last load post you'll get from us.

## The war stories

### The CPU graph that blamed traffic for a metering artifact

GA-adjacent: stage RDS started showing a suspicious CPU pattern — a small bump
every 15 minutes and a much larger one hourly, peaking around 40%. Every
instinct said "load." Checkpoint activity? We sampled live `pg_stat_activity`
during a spike and caught the actual query: not checkpoints, **metering rollup
parity**. Two replicas, about a minute apart, each running the parity queries —
and the hourly bigger spike is the 168-hour (7-day) parity window that runs every
fourth maintenance cycle.

The thing that made it a story is that it runs **even though rollup-based reads
are disabled** (`DASHBOARD_USE_ROLLUPS=false`). The maintenance never stopped
when we stopped using its output. And our own Grafana was sampling in 300-second
buckets — wide enough that a 15-minute cadence pattern was easy to misread as
something noisier.

The fixes are boring and correct: skip parity while rollup reads are off, elect a
single maintenance owner, and give the dashboards a 1-minute resolution so the
cadence is visible instead of mysterious.

### The one character that cost an hour (and told the truth the whole time)

A phase-1 user came to us with:

```
model 'rits/zai.org/glm-5-3' is not permitted by access policy
```

Entitlement said `hasAccess: true`. Metering logs said nothing — because metering
never saw it. The model is `rits/zai-org/glm-5-3`, with a **hyphen**.
The user sent `zai.org`, with a **dot**. Model access is an exact,
case-sensitive match, fail-closed by design. The error message was correct from the
first line; the model ID was wrong, and both the catalog and the user had a
reason to believe otherwise.

Two things to keep: fail-closed model access means a typo looks like a denial,
so the error text should show the allowed spelling, and "not permitted" deserves a
sub-check of "does that model ID even exist?" before anyone goes read entitlement
code.

### The mock that OOM'd before the gateway did

Setting up the slow-inference upstream, we found the load generator was the
bottleneck: the mock crashed at 2,000 concurrent with `exit 137` on 256Mi.
Not a gateway limit — a test artifact. The fix (real limits, a request queue,
untainted nodes) is the whole lesson in miniature: **instrument the thing under
test, but don't let the test instrument be the first thing to fall over.** This
is also why our dropped-iteration numbers were a client-queueing signal and not a
server limit.

## What it looks like now

```
1,459 users, 1,461 active keys. Cap $300 via AI Budget; metering fallback
$600. GLM over-limit.
2.48M-event load run, 0 accounting deficit, 0 metering errors.
Stage: load harness + synthetic users removed, 36.8M rows deleted, DB compacted.
Rollup parity green. Canary green. 9/9 scrape targets up.
```

The accounting boundary held, the catalog tells the truth, the stage environment
is back to being a clean staging environment, and the load-testing job has a new
owner. The October 15 gate passed on evidence rather than hope.

## The parts we still owe

- **`metering-service` still exposes no metrics.** Four months if we count the
  original list. It is the last service without a scrape target, it is the
  accounting source of truth, and the parity maintenance we can only see sideways in
  the database will stay invisible until it has a `/metrics`.
- **PagerDuty for `page` severity.** The `page` bucket still lands in a Slack
  channel. This is the same item every month until it isn't.
- **Nothing tests the dashboards.** The datasource-uid class of failure would still
  ship silent. One request against `/api/datasources/proxy/uid/prometheus`
  remains the fix.
- **The rollup-parity CPU behavior on stage is diagnosed, not fixed.** The 15-
  minute/hourly maintenance cadence, the redundant second replica, and the
  "runs even when unused" behavior are all understood and written down; none of the
  three boring fixes have shipped.
- **AppSRE onboarding continues** — team and stage namespaces are in, the
  production namespace MR is still queued behind the cluster provisioning. The
  monitoring stack remains ours, as decided.

---

*Credentials, internal endpoints and account identifiers are redacted. The
deployment manifests live in the `pricetag` repository; the monitoring stack and
its runbook remain in `deployments/enmaas/monitoring/`. The load harness,
KPIs and findings are captured in the handoff handed to the perf team; the stage
load tooling it refers to was removed from the repo by PR and is recoverable from
git history if a future campaign needs it.*
