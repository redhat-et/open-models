# Using OCTO Open Models

All metered models go through our OpenAI-compatible **gateway**. One base URL,
one key, all models — with automatic fallback and per-user budgets.

- Web app (login + create your key): **https://octo-app-octo-models.apps.emerg.pcbk.p1.openshiftapps.com**
- Gateway base URL: **https://litellm-octo-models.apps.emerg.pcbk.p1.openshiftapps.com/v1**
- Models available to you (see the web app for the live list):
  - `qwen38-flash-next` — Qwen 3.8 Flash Next, 262k context (default; falls back to fast when busy)
  - `qwen38-27b-fast` — Qwen 3.8 27B, snappy everyday model
- Login: GitHub, requires membership in the `redhat-et` org.
- Limits: 600 req/min, $20/day worth of tokens (measured at public API price —
  effectively enormous). Ask in #octo if you need more.

First step for everything below: log into the web app and **create a key**
(`sk-...`, shown once — save it).

## opencode

Drop `configs/opencode.json` (from the repo, or the web app) into
`~/.config/opencode/opencode.json`, then export your key — the config
references `$OCTO_KEY`:

```bash
export OCTO_KEY=sk-...your-key...
opencode
```

Both gateway models are pre-defined; `model`/`small_model` defaults are set.

## Open WebUI

Admin Settings → External Connections → add one OpenAI API connection:

- **API Base URL**: the gateway URL above
- **API Key**: your `sk-...` key
- **Model ID(s)**: `qwen38-flash-next,qwen38-27b-fast`

(`configs/open-webui.env` mirrors this.)

## curl smoke test

```bash
curl https://litellm-octo-models.apps.emerg.pcbk.p1.openshiftapps.com/v1/chat/completions \
  -H "Authorization: Bearer $OCTO_KEY" -H "Content-Type: application/json" \
  -d '{"model": "qwen38-flash-next", "messages": [{"role":"user","content":"hello"}], "max_tokens": 64}'
```

## Direct endpoints (VPN required, unmetered, no fallback)

The web app lists these with copy-paste snippets, e.g. the DGX Spark boxes.
Use them when you want a dedicated box or you're off the gateway path; base
URL is the host's own `:8000/v1`, key `none`. Usage is not tracked and you get
no fallback — prefer the gateway.

## Monitoring

Grafana (dashboards for gateway traffic + per-model GPU/vLLM metrics):
**https://grafana-octo-models.apps.emerg.pcbk.p1.openshiftapps.com**
