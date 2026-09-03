# Using the models

All endpoints speak OpenAI-compatible APIs at `/v1`. Base URLs and model ids
come from the front-end page (source of truth: `catalog.yaml`).

## curl smoke test

```bash
curl http://<model-host>:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "<served_model>", "messages": [{"role":"user","content":"hello"}]}'
```

## opencode

1. Download `configs/opencode.json` from the front end (or grab it from the
   repo).
2. Drop it at `~/.config/opencode/opencode.json` - or merge the `provider`
   block into your existing config.
3. Restart opencode (config loads once at startup).
4. Pick a model with `/models`, e.g. `octo-spark-smart/Qwen/Qwen3.8-27B`.
   The config presets the default (`model`) and the cheap background model
   (`small_model`) for you.

Want it project-wide instead? Put the same file at `./opencode.json` in your
repo root (it deep-merges over your global config).

## Open WebUI

Per endpoint, add a connection:

1. Admin Settings -> External Connections -> Add OpenAI API
2. Base URL: `http://<model-host>:8000/v1`  API key: `none`
3. The model appears in the model dropdown.

`configs/open-webui.env` lists every endpoint as env-var form
(`OPENAI_API_BASE_URL` + `OPENAI_API_KEY`) if you self-host the container.

## House rules

- Free, but shared: heavy batch jobs go to the long-context cluster model or
  run off-peak; don't DoS the interactive tiers.
- No auth on the LAN today - never paste secrets into prompts. (AuthN is on
  the RFE list.)
- Endpoint down or weird? Check the dashboard, then file it with the team.
