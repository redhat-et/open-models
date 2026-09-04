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
METERED_STATUSES = {"running", "live"}
LISTED_STATUSES = {"listed"}
STATUS_IN_CONFIGS = METERED_STATUSES | LISTED_STATUSES


def load_catalog(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def _metered_groups(catalog: dict) -> dict:
    groups: dict[str, dict] = {}
    for model in catalog.get("models", []):
        if model.get("status") not in METERED_STATUSES:
            continue
        group = model.get("group", model["id"])
        g = groups.setdefault(
            group,
            {
                "name": model["name"],
                "context": model.get("context", 8192),
                "max_output": model.get("max_output", 8192),
                "tool_call": bool(model.get("tool_call", False)),
                "reasoning": bool(model.get("reasoning", False)),
                "tier": model.get("tier"),
            },
        )
        g["context"] = max(g["context"], model.get("context", 8192))
        g["max_output"] = max(g["max_output"], model.get("max_output", 8192))
    return groups


def opencode_config(catalog: dict) -> dict:
    hosts = catalog.get("hosts", {})
    gw_url = catalog.get("gateway", {}).get("base_url", "")
    providers = {}
    defaults: dict[str, str] = {}

    if gw_url:
        gateway_models = {}
        for group, g in _metered_groups(catalog).items():
            gateway_models[group] = {
                "name": f"{g['name']} [gateway]",
                "limit": {"context": g["context"], "output": g["max_output"]},
                "tool_call": g["tool_call"],
                "reasoning": g["reasoning"],
                "temperature": True,
            }
            defaults.setdefault(g["tier"], f"octo-gateway/{group}")
        providers["octo-gateway"] = {
            "npm": OPENAI_COMPAT_NPM,
            "name": "OCTO Open Models gateway (metered; key from the web app)",
            "options": {"baseURL": gw_url, "apiKey": "$OCTO_KEY"},
            "models": gateway_models,
        }

    for model in catalog.get("models", []):
        if model.get("status") not in LISTED_STATUSES:
            continue
        host_cfg = hosts.get(model["host"], {})
        prov_id = f"octo-{model['id'].replace('_', '-')}"
        hardware = host_cfg.get("hardware", model["host"])
        providers[prov_id] = {
            "npm": OPENAI_COMPAT_NPM,
            "name": f"OCTO Open Models ({hardware} - direct/unmetered, VPN)",
            "options": {"baseURL": model["base_url"], "apiKey": model.get("api_key", "none")},
            "models": {
                model["served_model"]: {
                    "name": model["name"],
                    "limit": {"context": model.get("context", 8192), "output": model.get("max_output", 32768)},
                    "tool_call": bool(model.get("tool_call", False)),
                    "reasoning": bool(model.get("reasoning", False)),
                    "temperature": True,
                }
            },
        }

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
    gw_url = catalog.get("gateway", {}).get("base_url", "")
    lines = [
        "# Open WebUI: paste into Admin Settings -> External Connections,",
        "# or set as env vars for the open-webui container.",
    ]
    if gw_url:
        lines += [
            "#",
            "# RECOMMENDED: one connection to the OCTO gateway (metered, budgets,",
            "# fallbacks). Create your key at the OCTO web app, then:",
            f"#   API Base URL : {gw_url}",
            "#   API Key      : your sk-... key from the web app",
        ]
        for group, g in _metered_groups(catalog).items():
            lines.append(f"#   Model ID     : {group}  ({g['name']} [{g['tier']}])")
    lines.append("#")
    lines.append("# Direct endpoints (VPN, unmetered) - one connection each:")
    for model in catalog.get("models", []):
        if model.get("status") not in LISTED_STATUSES:
            continue
        lines.append(f"# {model['name']}  [{model['tier']} @ {model['host']}]  [DIRECT/UNMETERED - VPN]")
        lines.append(f"#   API Base URL : {model['base_url']}")
        lines.append("#   API Key      : none")
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
