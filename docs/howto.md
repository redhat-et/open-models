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
  (e.g. pausing flash still serves its `qwen38-27b-fast` fallback group).
- The portal shows a grey **paused** badge and hides the snippet.
- Catalog keeps `previous_status` while paused so `model-on` restores exactly.

Then stop the actual compute so you stop paying:
- **hot-aisle box**: `./scripts/hotaisle.sh off` (never stop it while it's the only
  endpoint for a live group — check `make model-status` first).
- **RTX 5090** (`192.168.1.166`): `COMPUTE=1` handles it (`podman stop qwen38-nvfp4`);
  manual: `ssh 192.168.1.166 'podman stop qwen38-nvfp4'` (start = same with `start`).
  Note it's currently the ONLY fast endpoint while hotaisle is paused - turning it
  off leaves the fast group empty.
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
3. Add a `hosts:` + `models:` entry to `catalog.yaml` (`status: listed` until tested,
   then `running` for metering; set `group:` to share/duplicate an alias for redundancy).
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

Put the recommended number in the model's `seats:` field in `catalog.yaml`
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
