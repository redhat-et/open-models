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
- M4 DONE (2026-09-05): old UI retired (`make web` -> portal notice + sunset banner),
      config docs regenerated for gateway. Open: strix-halo + spark3 aliases
      onboarding, recorded benchmark runs (needs hardware time)
- TIER-2 DONE (2026-09-05): staging release `omdev` in ns `octo-models-dev`
  (`make dev`/`dev-down`) — real gateway+portal+prometheus against in-cluster
  mock backends; own GitHub login (callback registered), per-ns SCC uid auto-derived,
  configOverride in chart values; verified: oauth redirect, mock-backed chat,
  prom targets up. `charts/octo-models/values-dev.yaml` is the overlay.
- COST DIAL (2026-09-05): `make model-off/model-on/model-status MODEL=<id>` —
  catalog `status: paused` (+ previous_status restore), regen+roll gateway,
  portal paused badge; `docs/howto.md` covers the two-layer stop (gateway, then compute)
  and per-host power-down commands.
- NEXT: UI rework with richer data (per-model live status/latency, spend charts);
  spark/strix onboarding + repeatable spark workflow; nightly soak combining
  lab chaos + benchmarks; hot-aisle re-serve when box is powered back on.
- SMART TIER (2026-09-06): GLM-4.5-Air-FP8 (106B-A12B) on resized 1x MI300X
  hot-aisle ($3/hr vs $6): quality/coding tier `glm-45-air` (fallback -> fast),
  27B fast stays on the free 5090, 235B TP=2 reserved for resize-for-demo-days.
- COST DIAL v2 (2026-09-07): hot-aisle rental reality — billing runs while the
  VM EXISTS; only DELETE stops the meter, and delete destroys the disk. Full
  lifecycle now API-driven (`scripts/hotaisle-api.sh` list/spec/off/up wired as
  catalog compute hooks; validated end-to-end destructive: delete -> re-provision
  -> serve -> gateway). 112GB GLM weights mirrored to
  `s3://octo-models-weights-us-east-1` (seed: `scripts/hotaisle-seed-laptop.sh`,
  scoped `octo-models-s3-write` user + `aws s3 sync` — presigned PUTs 403
  SignatureDoesNotMatch on this account, GETs work); wake warms the HF cache
  8-way parallel from S3 (cold ~25 min vs ~50 min HF-bound). flash-next
  (ncao) `/cache` moved emptyDir -> 200Gi PVC: weights + torch-compile cache
  now survive GPU node swaps; `make gpu-off/gpu-on` lets cluster-autoscaler
  delete/provision the g6e node (replicas 0 = node gone ~10 min, back ~15 min).
- PROVIDER/MODEL SYSTEM (design approved 2026-09-08). Three layers:
  1. **Providers** (`providers/` dir): each GPU source implements 5 verbs
     (`spec | list | up | down | status`) with a type: `cloud-api`
     (hotaisle today; AWS/Vast/RunPod later), `ssh-lab` (5090, RHT lab boxes,
     sparks), `k8s-cluster` (ncao ROSA). `spec` declares shapes: hotaisle
     offers exactly two flavors — 1x MI300X (~$2.99/hr) and 2x MI300X
     (~$6/hr). `up` returns the live endpoint; the VM name/IP must never be
     hand-kept in the catalog again (reprovision changes both).
  2. **Models**: machine-readable `requirements` (vram_gb, gpus, gpu_family,
     ram_gb, S3 warm cache) = "what HW can it run on"; measured `placement`
     matrix (tok_s, seats, cold_min per provider, via `make model-bench`) =
     "where does it run best".
  3. **Scheduler**: `model-on MODEL=x` (and later the portal Wake button) ->
     filter providers by requirements -> **pick cheapest** (decision;
     `PROVIDER=` pin overrides) -> `up` -> wait -> scheduler writes the
     returned endpoint into generated catalog state -> deploy -> on.
  Decisions: users may self-serve wakes; **cost is org-borne and watched
  closely** — Slack alerts (existing SLACK_WEBHOOK_URL plumbing) on every
  wake/provision event, hourly burn while any paid compute is up, and
  balance-floor warnings. Cleanup: the stale 2x MI300X ghost entry
  (`hosts.hotaisle`, enc1-gpuvm005) is gone from the API — remove it, or
  re-provision through the new lifecycle as the 235B home.
  Phases: (1) re-shelve today's scripts behind the provider contract +
  `provider-test.sh`; (2) requirements/placement/bench + onboard 1-2 models
  end-to-end (gpt-oss-120b -> spark, 235B -> 2x MI300X); (3) portal Wake +
  cost alerts; (4) second cloud provider (RTX Pro 6000 replacing the 4xL40S
  per the openshift host note). Onboarding entry points:
  `onboard-provider.sh <name>`, `onboard-model.sh <hf-repo>`.
- TOMORROW (Tue 2026-09-08, launch is Wed 2026-09-09):
  1. GLM box wake cycle (unattended, stop on failure): `hotaisle-api.sh up` ->
     re-provision gets a NEW VM name+IP -> update catalog `base_url` +
     HOTAISLE_VM/HOTAISLE_IP -> `make deploy APP=1` -> `scripts/hotaisle.sh
     serve` (time the S3 warm) -> `make model-on MODEL=hotaisle-air` ->
     verify glm-45-air fingerprint through gateway.
  2. GPU node cost cycle: `make gpu-off` -> node gone ~10 min -> `make gpu-on`
     -> flash serving <15 min from PVC -> gateway verify.
  3. DONE (2026-09-08): GPU schedule in-cluster — CronJobs `om-gpu-on/off`
     scale flash-next 04:00 on / 18:00 off Mon-Fri America/New_York
     (`deployments/gpu-scheduler/gpu-scheduler.yaml`, live + job-tested).
  4. Post launch announcement #octo-open-model-platform.
  5. Backlog: automate catalog IP update after reprovision; flash readiness
     probe; hotaisle balance alerting; shadow-vs-glm quality spot-check;
     optional 5090 as third tier.
