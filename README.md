# OCTO Open Models

> We need to make our platform the answer to our token spend problem.

Open models for OCTO associates to use to offset their spend and/or when they
run out of their monthly token budget. Metered traffic flows through a central
LiteLLM gateway on OpenShift; direct-attach endpoints (DGX Sparks, Strix Halo)
are listed on the web app for VPN users but stay out of token accounting.

## Architecture

```
   browser ──> web app (backend/)  ── GitHub OAuth (redhat-et) ── login, keys, spend
                    │  FastAPI + PatternFly UI, serves /api/*
                    │  mints keys via litellm admin API
                    v
   opencode / open-webui / curl ──> LiteLLM gateway (litellm/)     [metered]
                    │                   /v1 group: qwen38-flash-next ── vLLM @ OpenShift g6e (AWS)
                    │                   /v1 group: qwen38-27b-fast  ── vLLM @ RTX 5090 (+ hot aisle, soon)
                    │                   fallbacks + per-user budgets + Prometheus spend
                    ├── Postgres (keys/spend) + Redis (cache)
                    └── namespace Prometheus ──> Grafana dashboard

   listed only (VPN, unmetered):  vLLM @ spark2 / spark3 / strix-halo  (copy-paste URLs)
```

`catalog.yaml` is the single source of truth: models, tiers, hosts, endpoints,
prices, fallbacks, key defaults. The litellm config, Prometheus targets, the
web UI catalog, and the generated user configs are all derived from it.

## Layout

| Path               | What                                                       |
|--------------------|------------------------------------------------------------|
| `catalog.yaml`     | Model catalog: ids, tiers, hosts, endpoints, prices         |
| `backend/`         | FastAPI app: GitHub OAuth, key self-service, spend, UI      |
| `charts/octo-models/` | One Helm chart for the whole platform (gateway, app, monitoring) |
| `scripts/`         | Generators: litellm config, prom targets, user configs      |
| `configs/`         | Generated user configs (opencode.json, open-webui env)      |
| `web/`             | v1 read-only UI (being superseded by backend/)              |
| `monitoring/`      | Local prom/grafana dev stack + dashboard JSON               |
| `benchmarks/`      | guidellm harness + quality eval                             |
| `docs/`            | Plan, hardware inventory, user setup                        |

## Deploy (OpenShift, no cluster-admin)

```bash
make deploy              # gateway + pg + redis + prometheus + grafana
make deploy APP=1        # + web app (needs ~/.config/octo-models/secrets.env)
make backend-image       # rebuild + roll the app after touching backend/
make gen                 # re-run after editing catalog.yaml (then make deploy)
```

`secrets.env` holds `PG_PASSWORD`, `RD_PASSWORD`, `LLM_MASTER_KEY`,
`SESSION_SECRET`, `GITHUB_CLIENT_ID/SECRET/ORG`. The app builds in-cluster via
the `om-backend` BuildConfig (binary upload, no external registry).

## Dev loop

```bash
make test            # web + backend suites, offline
make web             # v1 UI retired - points at the portal
make backend         # v2 app on :8020 (loads secrets.env, needs LITELLM_URL)
make monitor-up      # local prometheus :9090 + grafana :3000 via podman
```

## Deliverables

- [x] LiteLLM gateway live: model groups, fallbacks, budgets, Prometheus spend
- [x] Web app on-cluster: GitHub login, catalog + snippets, key CRUD + spend
- [x] Monitoring on-cluster: namespace Prometheus + Grafana dashboard
- [x] `catalog.yaml` -> generated opencode.json / open-webui configs
- [x] 3 model instances live (g6e flash-next, RTX 5090 fast, spark2 smart [listed])
- [x] Hot-aisle GPU added to gateway (in `qwen38-27b-fast` group as bulk redundancy; currently paused)
- [x] Old `web/` UI retired; portal is the single UI (M4)
- [x] Grafana deepened: Environment Monitor v2 - TTFT/E2E p95, KV cache, queueing, per-group usage; linked from the portal home page
- [ ] Per-platform benchmarks recorded (harness ready)
- [x] User how-to: `docs/user-setup.md`

## Bugs / RFEs

- [ ] Contribution guide (`docs/contributing.md`): links to this repo, the
      OpenShift cluster/console, portal, gateway, Grafana dashboard +
      Prometheus (with creds location), hotaisle admin console, model-host
      inventory - plus the add-a-model and deploy flows (make gen /
      backend-image / deploy)
- [ ] Instrument and speed up the vLLM startup process (`docs/rfe-vllm-startup.md`)
- [ ] "Monitoring of models: best practices" how-to doc (`docs/monitoring-howto.md`)
- [x] On-prem metrics -> cluster Prometheus via remote_write - dropped 2026-09-07: only the on-cluster scrape matters, receiver removed from the deployment

## Team

Bennyturns, Nick Cao, Maryam Tahhan, Mathew Odden
