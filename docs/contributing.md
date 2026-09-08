# Contributing to OCTO Open Models

Open models to offset token spend — built by OCTO, open for OCTO today,
built to become a community project. Everything runs from one repo,
everything reads from `catalog.yaml`.

## Links

| What | Where |
|---|---|
| **This platform's repo** | https://github.com/redhat-et/open-models |
| GPU rental provider CLI | https://github.com/hotaisle/hotaisle-cli |
| GPU rental cloud-init templates | https://github.com/hotaisle/cloud-init-templates |
| Portal (keys, catalog, spend) | https://octo-app-octo-models.apps.emerg.pcbk.p1.openshiftapps.com |
| Docs hub (this page) | portal → `/guide` |
| Usage & activity dashboard | Grafana — link on the portal home page |
| Ask / feedback / alerts | Slack `#octo-open-model-platform` |

Repo docs: `docs/user-setup.md` (your machine → first token),
`docs/model-selection.md` (which tier for which job), `docs/howto.md`
(operator runbook), `docs/plan.md` (architecture + roadmap, including the
provider/model system design), `docs/model-onboarding.md` (getting a model
served), `docs/monitoring-howto.md`, `TESTBED.md` (offline lab),
`README.md` (quick start).

## The shape of the code

```
catalog.yaml        single source of truth: models, hosts, tiers, fallbacks
backend/            FastAPI portal (auth, keys, spend, /guide, compute hooks)
charts/octo-models/ Helm chart: gateway (LiteLLM+Postgres+Redis), portal,
                    Prometheus, gpu-scheduler CronJobs
deployments/        manifests kept for reference (gateway config, flash-next)
scripts/            the muscle: model on/off, hotaisle VM lifecycle, S3 seed,
                    config generators (gen_*.py)
monitoring/         Prometheus targets + Grafana dashboards
lab/                offline podman testbed (make lab / make lab-test)
docs/               rendered at portal /guide/<name>
```

## Developer loop

```
make setup          # deps, secrets wiring (one time)
make test           # backend + web test suites (CI runs these)
make gen            # regenerate configs from catalog.yaml
make deploy APP=1   # helm upgrade (gateway, portal, monitoring)
make backend-image  # rebuild portal image (Containerfile / scripts / catalog)
make model-on MODEL=<id>          # bring a model + its compute up
make model-off MODEL=<id> [COMPUTE=1]  # pause (+ optionally stop the box)
```

Edit → `make test` → `make deploy`/`make backend-image` → verify → commit →
push. GitHub Actions (`ci`) runs the suites on every push; the image is
built in-cluster by the `om-backend` BuildConfig.

## What to contribute

- **Models** — the hottest path. Onboard a model: pick weights/quant, pick
  hardware (see `docs/model-onboarding.md` and the requirements/placement
  design in `docs/plan.md`), wire the catalog entry, measure
  `seats`/tok-s, add to the right tier/fallback.
- **Providers** — a GPU source that implements the 5-verb contract
  (`spec | list | up | down | status`) in a `providers/` dir: clouds, lab
  boxes, k8s clusters. Contract + test harness land with the provider/model
  system (see `docs/plan.md`, PROVIDER/MODEL SYSTEM).
- **Benchmarks** — `benchmarks/` + the standard bench that fills placement
  matrices ("this model does X tok/s on Y hardware, cold-start Z min").
- **Portal & dashboards** — the FastAPI portal and Grafana dashboards are
  small and readable; /guide, key UX, spend views.
- **Ops** — the runbook (`docs/howto.md`) is the onboarding doc; everything
  cost-related (wake/delete cycles, schedules, alerts) is worth hardening.
- **Docs** — if it was hard to figure out, document it; docs render straight
  to the portal.

Good first tasks: run the `lab` testbed, add a doc page to `DOC_PAGES` in
`backend/app.py`, write a benchmark for an existing model, or file an issue
about anything surprising.

## Ground rules

- `catalog.yaml` is the source of truth — change it, then `make gen`, never
  hand-edit generated deploy files.
- Anything that bills (rented GPUs, nodes) must have a matching off path and
  a note in the runbook; cost levers live in `docs/plan.md` (COST DIAL).
- Destructive ops (VM delete, node scale) are scripted, tested, and logged —
  extend those scripts rather than improvising.
- No secrets in the repo: real keys live in `~/.config/octo-models/secrets.env`
  and flow in via chart `--set` at deploy time.
- Slack `#octo-open-model-platform` for coordination; alert notifications
  post there too.
