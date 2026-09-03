# OCTO Open Models

> We need to make our platform the answer to our token spend problem.

Open models for OCTO associates to use to offset their spend and/or when they
run out of their monthly token budget. Run on existing hardware: DGX Sparks,
Strix Halo, an OpenShift cluster with GPU nodes, and an RTX 5090.

## Architecture

```
                    +--------------------+
                    |   web/ front end   |  model list + how-to + status
                    +---------+----------+
                              |
   opencode.json  ----\       |       /---- vLLM @ spark2  (smart)
   open-webui env -----+------v------/----- vLLM @ spark3  (fast)
   (copy-paste         |  catalog.yaml -----+----- vLLM @ strix-halo (best of both)
    configs/)          |  (source of        |
                       |   truth)           +----- vLLM @ openshift g6e (long context)
                       |
                configs/ (generated)
                       |
               monitoring/ (Prometheus + Grafana, linked from the front end)
```

- `catalog.yaml` is the single source of truth: every model, host, endpoint,
  and capability flag. Everything else is generated from it.
- `configs/opencode.json` is generated from the catalog so the team can just
  copy it to `~/.config/opencode/opencode.json` (all models predefined).
- `web/` (FastAPI) lists the models, live-probes each endpoint, shows
  copy-paste snippets, and links to the Grafana dashboards.
- `monitoring/` is a Prometheus + Grafana stack; each host runs the
  exporters, and vLLM `/metrics` is scraped directly.

## Layout

| Path            | What                                                    |
|-----------------|---------------------------------------------------------|
| `catalog.yaml`  | Model catalog: ids, tiers, hosts, endpoints, capabilities |
| `web/`          | FastAPI front end (model list, status, how-to)          |
| `configs/`      | Generated user configs (opencode.json, open-webui env)  |
| `scripts/`      | `gen_configs.py`, `gen_prom_targets.py`, `timeline-startup.sh` |
| `deployments/`  | Per-platform deploy files + OpenShift manifests for the web app |
| `monitoring/`   | Prometheus, Grafana, exporters, dashboards (running locally) |
| `benchmarks/`   | guidellm harness + 30-prompt quality eval               |
| `docs/`         | Hardware inventory, model selection, user setup         |
| `Makefile`, `Dockerfile` | Dev loop + front-end container               |

## Quick start (team dev loop)

```bash
make setup    # venv + deps
make test     # offline tests
make gen      # catalog -> configs/ + prometheus file_sd targets
make web      # front end on :8010 (8000 is probably your wheel-picker)
make monitor-up    # prometheus :9090 + grafana :3000 via podman (host net)
make image    # build the front-end container (deployments/openshift/web-openshift.yaml
              # holds the manifests to put it on the cluster)
```

Editing `catalog.yaml`? `make gen` (only) is needed; Prometheus reloads
file_sd targets automatically.

## Deliverables

- [ ] 3-6 open model instances for OCTO engineers to use
  - [ ] DGX Spark: smart model (deploy)
  - [ ] DGX Spark: fast model (deploy)
  - [ ] DGX Spark: best-of-both model (deploy)
  - [ ] OpenShift g6e.12xlarge: long-context model (deploy)
  - [ ] Strix Halo: (grab HW, pick + deploy model)
  - [ ] RTX 5090: fast workhorse candidate
- [x] `catalog.yaml` + generated opencode.json / open-webui configs (copy-paste)
- [ ] Web front end listing models + how to use them (running locally on :8010;
      container image + OpenShift manifests ready to push)
- [ ] Monitoring stack + usage/activity dashboard (running locally; move to a
      always-on host next)
- [ ] Per-platform model benchmarks recorded in `benchmarks/` (harness ready:
      `bench.sh` + quality eval prompts)
- [ ] Other hardware scouted (scavenge/borrow/beg list in `docs/hardware.md`)
- [ ] CPU-only model evaluated (see `docs/hardware.md`)
- [ ] User how-to: configure opencode + open-webui with our models (`docs/user-setup.md`)

## Bugs / RFEs

- [ ] Instrument and speed up the vLLM startup process (`docs/rfe-vllm-startup.md`)
- [ ] "Monitoring of models: best practices" how-to doc (`docs/monitoring-howto.md`)
- [ ] AuthN/authZ for model endpoints (currently open on the LAN)

## Team

Bennyturns, Nick Cao, Maryam Tahhan, Mathew Odden
