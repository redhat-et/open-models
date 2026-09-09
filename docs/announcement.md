# OCTO Open Models — launch notes (2026-09-09)

Draft material for the launch announcement + slide deck. Numbers sourced from
catalog.yaml + measured benchmarks 2026-09-07/08.

## The one-liner

Open models, one key, zero invoice: every OCTO associate gets production-grade
Qwen models served on hardware we already own — metered, budgeted, and
measured against frontier-API prices so "saved vs Claude/GPT" is a real number,
not a vibe.

## The problem

- Token spend is a real line item. Associates hit monthly budgets and stop
  shipping, or spin up shadow GPU spend nobody tracks.
- Open models are finally good enough for daily agentic work — but serving
  them well (quantization, spec-decode, context length, concurrency) is a
  project each team would otherwise repeat badly on its own.

## What we built

- **One gateway**: LiteLLM on OpenShift. OpenAI-compatible, per-user keys,
  per-user budgets, automatic fallback, Prometheus spend + latency metrics.
- **One portal**: GitHub login (redhat-et), self-serve keys, live model cards
  (tier, seats, context, notional price), spend/savings, one-paste setup.
- **One source of truth**: `catalog.yaml` — models, hosts, tiers, prices,
  fallbacks. Gateway config, dashboards, alerts, user configs, portal cards:
  all generated from it. Onboarding a model is a PR, not a platform change.
- **One command for users**: paste a curl, get an opencode config pointed at
  the gateway. Done in ~2 minutes; nothing to install.

## Tiers (roles, not speed)

| Tier | Model | Hardware | For | Notional $/Mtok in/out |
|---|---|---|---|---|
| frontier | Qwen 3.8 27B FP8, 131k ctx | rented MI300X (192GB) | hardest agentic work; 24 tok/s each at 32 seats; falls back to core when dark | 0.60 / 2.40 |
| core | Qwen 3.8 Flash Next NVFP4, 262k ctx | 4x L40S on OpenShift | daily driver; 19 tok/s each at 10 seats; the gateway default | 0.20 / 1.00 |
| bulk | Qwen 3.8 27B NVFP4, 32k ctx | RTX 5090 | batch/background; 12.5 tok/s each, 100 tok/s aggregate ceiling | 0.30 / 1.20 |
| lab | 27B FP8 / Flash-Next NVFP4 | DGX Sparks | bake-offs, direct endpoints over VPN, no SLA | unmetered |

Seat numbers measured on the live boxes 2026-09-08. Frontier re-entered the
day a 27B FP8 on rented MI300X beat everything we could self-host for agentic
work — same rule as retirement: models earn their tier.

## Money: we watch it like users do

- Savings dashboard: notional frontier prices x8 multiplier, per-user and
  per-model, live in Grafana.
- GPU schedule: the L40S node is torn down 18:00 and back by 04:00 weekdays —
  serving when people work, not when they sleep.
- Rented compute has a delete-not-stop policy: the meter only stops when the
  VM is gone; every wake/provision fires a Slack alert; balance is watched.
- Weight caches warm from S3 in parallel — cold starts in minutes, not an
  hour of egress.
- We retired a frontier model (GLM-4.5-Air on a rented MI300X) the day its
  tool-calling quality failed our own usage — deleted the box, purged the
  config, kept the platform. Same-day teardown, no sunk-cost drift. If a
  model can't earn its $/hr here, it goes.

## Status today

- Live now: all three metered tiers serving through the gateway (frontier on
  MI300X, core on 4xL40S, bulk on the 5090) + two lab sparks over VPN;
  portal, keys, budgets, dashboards all up.
- Next: provider/placement system (models pick their hardware; self-serve
  wake with cost alerts), a second GPU cloud, community onboarding docs.

## Call to action

Log in → create a key → paste the one-liner → ship. Portal:
https://octo-app-octo-models.apps.emerg.pcbk.p1.openshiftapps.com
Feedback: #octo-open-model-platform. Repo: github.com/redhat-et/open-models
