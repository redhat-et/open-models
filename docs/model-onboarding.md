# Onboarding a new model

The catalog is the only source of truth; everything else (gateway config, portal
cards, user opencode configs, prometheus targets) is generated or read from it.
Worked example: `hotaisle-air` (GLM-4.5-Air-FP8), onboarded 2026-09-06.

## Admin: add a model

0. **Serving first.** Get an OpenAI-compatible endpoint up somewhere (vLLM etc.)
   and verify locally: `curl <base>/v1/chat/completions` returns a completion.
   Check the exact `served-model-name` — it must match the catalog later.
   (`HOTAISLE_MODEL=<hf-id> ./scripts/hotaisle.sh serve` for hot-aisle boxes.)
1. **catalog.yaml, status: planned.** Copy an existing entry. Fill id/host/name/
   served_model/tier/group/price/context. Use an existing `group` to add redundant
   capacity (gateway load-balances + failover across the group), or a new group for
   a new user-facing alias. Add `gateway.fallbacks` entries if it needs a floor.
   `planned` = invisible everywhere, so it's safe to commit early.
2. **Flip live:** set `base_url` + `status: running`, then
   `make model-on MODEL=<id>` (or edit + `make deploy APP=1`). Gateway rolls by
   itself (config checksum annotation); fallbacks auto-activate.
3. **Portal needs a rebuild** — the catalog is baked into the backend image:
   `make backend-image`. (Yes: catalog change = one rebuild. If this annoys us,
   move catalog to a mounted ConfigMap.)
4. **Verify:**
   - `curl <gateway>/v1/models` shows the group; one chat call 200s
   - `<gateway>/v1/chat/completions` with `model: <old-group>` still works (fallbacks intact)
   - portal `/api/catalog` lists it; prometheus target `up`
5. If new host: add `hosts:` entry + `compute: off/on` hooks so the cost dial
   covers it (`scripts/compute.py` refuses to stop a host still serving others).
6. `make gen` is inside `deploy`; run `make test` before pushing (CI mirrors it).

**Kill switch any time:** `make model-off MODEL=<id> COMPUTE=1` — pause first,
then power down; `model-on COMPUTE=1` reverses safely.

## Users: after a new model appears

Nothing, for models in existing groups — same key, same alias.

For a brand-new group (e.g. `glm-45-air` appearing), re-paste the one-liner
from the portal's key section (or `~/.config/octo` + re-run `setup-user.sh`):
the script now fetches `GET <portal>/setup/models.json`, so the opencode config
is rebuilt from the live lineup — new default model, new alias, same stored key.
`/models` in opencode will show it. No other action; spend keeps flowing to
their portal key.

## Hardware moves (GPU resizing)

Instances are ephemeral, catalog entries are not: power off old → boot new →
update `base_url` (+ `hosts.compute` hooks) → `make deploy APP=1`. Keep
`HOTAISLE_SSH`/`HOTAISLE_MODEL` overrides in the host's compute hook so the
dial reboots the right thing. Resize-for-demo-day policy: biggest models (235B
TP=2) are an afternoon rental, not a monthly line item.
