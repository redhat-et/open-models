#!/usr/bin/env python3
"""Generate user-facing configs (configs/opencode.json, configs/open-webui.env)
from the single-source-of-truth catalog.yaml.

Usage: python scripts/gen_configs.py [catalog.yaml] [configs_dir]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

OPENAI_COMPAT_NPM = "@ai-sdk/openai-compatible"
STATUS_IN_CONFIGS = {"running", "live"}


def load_catalog(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def opencode_config(catalog: dict) -> dict:
    hosts = catalog.get("hosts", {})
    providers = {}
    defaults: dict[str, str] = {}
    for model in catalog.get("models", []):
        if model.get("status") not in STATUS_IN_CONFIGS:
            continue
        host_cfg = hosts.get(model["host"], {})
        # one provider per endpoint: baseURL is per-provider in opencode
        prov_id = f"octo-{model['id'].replace('_', '-')}"
        base_url = model["base_url"]
        entry = providers.setdefault(
            prov_id,
            {
                "npm": OPENAI_COMPAT_NPM,
                "name": f"OCTO Open Models ({host_cfg.get('hardware', model['host'])})",
                "options": {"baseURL": base_url, "apiKey": model.get("api_key", "none")},
                "models": {},
            },
        )
        entry["models"][model["served_model"]] = {
            "name": model["name"],
            "limit": {"context": model.get("context", 8192), "output": model.get("max_output", 32768)},
            "tool_call": bool(model.get("tool_call", False)),
            "reasoning": bool(model.get("reasoning", False)),
            "temperature": True,
        }
        defaults.setdefault(model["tier"], f"{prov_id}/{model['served_model']}")

    cfg: dict = {
        "$schema": "https://opencode.ai/config.json",
        "provider": providers,
    }
    if "balanced" in defaults:
        cfg["model"] = defaults["balanced"]
    if "fast" in defaults:
        cfg["small_model"] = defaults["fast"]
    return cfg


def open_webui_env(catalog: dict) -> str:
    lines = [
        "# Open WebUI: paste into Admin Settings -> External Connections,",
        "# or set as env vars for the open-webui container.",
        "# One OpenAI API connection per model endpoint.",
    ]
    for model in catalog.get("models", []):
        if model.get("status") not in STATUS_IN_CONFIGS:
            continue
        lines.append(f"# {model['name']}  [{model['tier']} @ {model['host']}]")
        lines.append(f"#   API Base URL : {model['base_url']}")
        lines.append(f"#   API Key      : none")
        lines.append(f"#   Model ID     : {model['served_model']}")
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    catalog_path = Path(argv[1]) if len(argv) > 1 else root / "catalog.yaml"
    out_dir = Path(argv[2]) if len(argv) > 2 else root / "configs"
    catalog = load_catalog(catalog_path)

    out_dir.mkdir(parents=True, exist_ok=True)
    oc = opencode_config(catalog)
    (out_dir / "opencode.json").write_text(json.dumps(oc, indent=2) + "\n")
    (out_dir / "open-webui.env").write_text(open_webui_env(catalog))
    live = len(oc["provider"])
    print(f"wrote configs/opencode.json ({live} providers) and configs/open-webui.env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
