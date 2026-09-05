# Platform Rebuild Plan (v2)

Approved 2026-09-04. Rebuilds the user-facing stack around a central API
gateway; replaces the v1 FastAPI UI. Answers the updated mission spec.

## Decisions

| Choice | Decision |
|---|---|
| Identity | GitHub OAuth; org membership OR username allowlist (ConfigMap) |
| First cluster | Current emerg cluster (has g6e); migrate to Ryan Cook's later |
| Repo | Evolve `open-models` into the platform monorepo |
| Cluster admin | None -> namespace-scoped Prometheus (+ remote-write/federation from baremetal), Grafana deployed by chart |
| Packaging | One Helm chart for gateway + app + monitoring |

## Architecture

```
 users --GitHub OAuth--> app backend --> LiteLLM admin API (keys)
                |                              |
                v                              v
 PatternFly SPA (catalog/keys/usage)    LiteLLM gateway (2 replicas,
                |                       Postgres + Redis)  [OCP]
                |                              |
                |            model groups / fallbacks / RPM+TPM+parallel caps
                |                              |
     Grafana <---+--- vllm @ g6e (OCP, AWS) / 5090 / hot-aisle box (planned)
       ^          listed/unmetered (VPN direct, not in gateway): sparks, strix
       |          baremetal metrics remote-write --> ns prom
       |
   dashboards (KV, queue, TTFT/TPOT, req/s, errors, GPU util)
```

- LiteLLM licensing workaround: all virtual keys live under ONE litellm
  user; our Postgres maps key -> github_user. One github user may hold
  many keys.
- `catalog.yaml` stays the source of truth: generates litellm config,
  app config, and Grafana/Prometheus provisioning.

## QoS scope

v1 (stock LiteLLM config): per-key RPM+TPM, max_parallel_requests (429),
soft budgets, model-group aliasing (future: hot-aisle/strix join `fast`),
fallback chains, 2 stateless replicas + Redis, vLLM
`--max-num-seqs`/`--max-model-len`.
Sparks/strix are `listed` in the catalog: connection info shown on the web
page for VPN users, NOT in the gateway, NOT metered (slow + VPN-only;
not worth tunneling serving traffic).

v2 (deferred): budget-exhaustion auto-downgrade to 7B tier, fair-share
scheduling, smart routing.

## Workstreams

1. **Gateway** (Benny): litellm on OCP w/ postgres+redis, generated config,
   per-key defaults, model groups, fallbacks; vLLM engine caps; smoke test
   minted key -> all models.
2. **Backend** (Nick): GitHub OAuth + org/allowlist, JWT, key CRUD via
   litellm admin API (shared user + metadata.github_user), spend endpoints
   (personal/org), pricing from litellm model-prices snapshot (OpenRouter
   fallback).
3. **Frontend** (Maryam): PatternFly React, litemaas-inspired pages:
   login, model catalog (endpoint, curl/OpenAI-SDK/opencode snippets,
   context, price, org usage), my keys, my spend, org spend, grafana links.
4. **Monitoring + chart** (Matt): namespace prometheus + grafana on OCP,
   remote-write from baremetal, dashboard JSON extended (KV/queue/TTFT/
   TPOT/errors/GPU), umbrella Helm chart with values for serving-only vs
   full-platform.

## Milestones

- M1 DONE (2026-09-04): gateway live on emerg cluster ns `octo-models`
  (`make deploy`, release `om`), key -> gateway -> flash/fast verified,
  fallback verified, notional spend metrics flowing (litellm_spend_metric_total,
  prom scrapes gateway+backends, edge route). hot-aisle box = next metered GPU.
- M2 DONE (2026-09-04): web app live at route `octo-app-<ns>` — GitHub OAuth
  (org-owned app, redhat-et gate), catalog cards with copyable snippets,
  key create/list/delete with per-key spend, built in-cluster via `om-backend`
  BuildConfig (`make backend-image`). M3 largely folded in: spend + grafana
  already on OCP; remaining = deeper vLLM dashboards (Matt).
- TESTBED DONE (2026-09-05): offline lab — `make lab` / `make lab-test`
  (podman stack + mock vLLM; 8 e2e tests incl. fallback, rate limit, spend,
  prom targets). Manual matrix in `TESTBED.md`; per-user bootstrap in
  `scripts/setup-user.sh` (verified with fresh `omtest` user through reboot).
- M3: personal/org spend views with pricing, grafana on OCP, dashboards green
- M4: old UI retired to redirect, strix-halo + spark3 onboarded as aliases,
      benchmark runs recorded, config docs regenerated for the gateway
