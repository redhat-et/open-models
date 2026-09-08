# How-to

## Turn a model off / on (save money)

Model serving has two layers to stop: **metering** (gateway) and **compute** (the box).
Off means: no users routed to it, and its GPU box can idle/power down.

```bash
make model-off MODEL=rtx5090-fast COMPUTE=1   # pause in gateway, then stop the container
make model-on  MODEL=rtx5090-fast COMPUTE=1   # start container, wait, then un-pause
make model-status                   # see every model's state
make model-on  MODEL=rtx5090-fast   # restores previous state
```

- Paused models drop out of `/v1/models` immediately and fall back normally
  (`qwen38-27b-fast → qwen38-flash-next`, `glm-45-air → qwen38-flash-next`;
  flash-next is the floor and never falls back). So pausing the 5090's 27B
  keeps bulk traffic alive on the core cluster, a bit slower per seat.
- The portal shows a grey **paused** badge and hides the snippet.
- Catalog keeps `previous_status` while paused so `model-on` restores exactly.

Then stop the actual compute so you stop paying:
- **Hot Aisle rented VM** (GLM box): billing runs while the VM *exists* —
  powering it off saves nothing; only **deleting** it stops the meter, and a
  deleted VM loses its disk. Full lifecycle:
  `./scripts/hotaisle-api.sh off` (delete VM) and `./scripts/hotaisle-api.sh up`
  (re-provision from the committed `scripts/hotaisle-vm.json` specs, wait for
  ssh, then serve — cold ~25 min: provision ~2 min, image pull ~5 min,
  ~112GB weight warm from S3 `octo-models-weights-us-east-1` 8-way parallel
  ~14 min, load ~5 min). Weight cache was seeded 2026-09-07 via
  `./scripts/hotaisle-seed-laptop.sh` (re-run after any model/quant change).
  One-time setup: create an API key (`ssh admin.hotaisle.app`
  TUI -> your name -> API keys), put `HOTAISLE_API_TOKEN=...` in secrets.env,
  `make deploy APP=1`, then record the box with `hotaisle-api.sh spec` while
  it exists. Provider CLI alternative: github.com/hotaisle/hotaisle-cli.
- **4xL40S GPU node** (flash-next, ncao): the node pool is autoscaled by
  cluster-autoscaler (max 1) — do NOT scale the machineset (webhook blocks
  Red Hat-managed resources); scale the deployment instead:
  `make gpu-off` (CA deletes the node in ~10 min, billing stops) and
  `make gpu-on` (CA provisions a node ~7 min + vLLM cold start ~5-10 min —
  `/cache` is a 200Gi PVC, so weights + torch-compile cache persist across
  node swaps).   Scheduled: in-cluster CronJobs (`deployments/gpu-scheduler/gpu-scheduler.yaml`)
  scale the deployment 04:00 on / 18:00 off, Mon-Fri America/New_York.
  Dry-run a schedule change with `make gpu-off` / `make gpu-on` by hand.
- **RTX 5090** (desk box, sshd on port **8022**): `COMPUTE=1` handles it
  (`podman stop qwen38-nvfp4`); manual:
  `ssh -p 8022 bturner@136.47.153.236 'podman stop qwen38-nvfp4'`
  (or `192.168.1.166:8022` on the home LAN; start = same with `start`).
- **OCP-hosted endpoint**: `oc scale deploy/<name> -n <ns> --replicas=0`.

Rule of thumb: pause the model in the gateway **first**, then kill compute;
start compute **first**, then `model-on`.

## Test changes without touching prod (staging)

```bash
make dev        # release omdev in octo-models-dev: real gateway+portal+prom, MOCK GPU backends
make dev-down
```

Dev portal: `https://octo-app-octo-models-dev.apps.emerg.pcbk.p1.openshiftapps.com`
Dev gateway: `https://litellm-octo-models-dev.apps.emerg.pcbk.p1.openshiftapps.com/v1`
Same GitHub login (callback URL already registered). Charts changes / LiteLLM
version bumps / config experiments go here first.

Fully offline (no cluster): `make lab`, `make lab-test`, `make lab-down`.

## Onboard a new GPU host

1. Get it reachable (SSH key or VPN), note vendor/VRAM.
2. Serve with vLLM behind an OpenAI-compatible endpoint; add a local API key.
3. Add a `hosts:` + `models:` entry to `catalog.slim.yaml` (`status: listed` until
   tested, then `running` for metering; set `group:` to share/duplicate an alias for
   redundancy). Leave out `served_model`/`context` if the box is already serving -
   `make gen` fills them in from its live `/v1/models`.
4. `make gen && make deploy APP=1` — prometheus scrape targets come from
   `values-targets.yaml` (`make gen` writes it).
5. Verify: `curl <gateway>/v1/models`, a chat call, then prometheus `up==1`.

See `docs/model-onboarding.md` for the full new-model workflow (admin + user side),
and `docs/user-setup.md` for the client side (per-user keys/opencode).
Setup script + live model list: `GET <portal>/setup.sh`, `GET <portal>/setup/models.json`.

## Measure seats & idle power policy

Seat counts on the portal cards come from `scripts/loadtest.py` (streams
chat completions at increasing concurrency until per-session tok/s drops
below `--min-tps`):

```
python3 scripts/loadtest.py --url http://<host>:8000/v1 --model <served> [--key sk-local] \
    --levels 1,2,4,8,16 --sessions 1 --min-tokens 150 --min-tps 10
```

Put the recommended number in the model's `seats:` field in `catalog.slim.yaml`
(shown as "🪑 ~N seats" on the card).

Idle-off: hosts with `idle_off: true` + a `compute.off` hook get powered off
by the portal backend after `IDLE_OFF_MINUTES` (default 45) with no vLLM
activity, once a per-host ssh key secret `om-ssh` is mounted (see
`charts/octo-models` `app.ssh`). A "⏻ Wake" button on the card runs
`compute.on`. Alerts (down/recovery/idle-off) go to `SLACK_WEBHOOK_URL` in
`om-secrets` if set.

## Bump the LiteLLM version

Pin `litellm.image` in `charts/octo-models/values.yaml` **and** the `ghcr.io/berriai/litellm:<tag>`
reference in `scripts/lab.sh` to the same tag; run `make lab-test` (8 e2e tests incl.
fallback + rate-limit) before `make deploy APP=1`. Prod + lab lab-tested pins: `v1.99.1`.
