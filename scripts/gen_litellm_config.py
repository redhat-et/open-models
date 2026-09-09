#!/usr/bin/env python3
"""Generate the LiteLLM gateway config from catalog.yaml.

model_name = catalog `group` (one alias can front several deployments ->
cross-hardware redundancy). Only running/live models are emitted.

Usage: python scripts/gen_litellm_config.py [catalog.yaml] [out.yaml]
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

STATUS_LIVE = {"running", "live"}
HEADER = "# GENERATED from catalog.yaml by scripts/gen_litellm_config.py - do not edit"


def load_catalog(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def litellm_config(catalog: dict) -> dict:
    gw = catalog.get("gateway", {})
    model_list = []
    groups = []
    for model in catalog.get("models", []):
        if model.get("status") not in STATUS_LIVE:
            continue
        group = model.get("group", model["id"])
        if group not in groups:
            groups.append(group)
        params = {
            "model": f"openai/{model['served_model']}",
            "api_base": model["base_url"],
            "api_key": model.get("api_key", "none"),
        }
        if model.get("max_output"):
            params["max_tokens"] = model["max_output"]
        price = model.get("price") or {}
        if price.get("input"):
            params["input_cost_per_token"] = price["input"] / 1_000_000
        if price.get("output"):
            params["output_cost_per_token"] = price["output"] / 1_000_000
        if model.get("rate"):
            params.update(model["rate"])
        model_list.append(
            {
                "model_name": group,
                "litellm_params": params,
                "model_info": {
                    "id": model["id"],
                    "max_input_tokens": model.get("context", 8192),
                    "supported_api": ["chat", "completions"],
                },
            }
        )

    fallback_map = gw.get("fallbacks") or {}
    # a group with zero live deployments would otherwise vanish from
    # /v1/models and hard-400 its users; keep the name alive as a shadow
    # deployment wired to the first live fallback target (litellm has no
    # fallback for unknown names, only for failing deployments).
    by_group: dict = {}
    for entry in model_list:
        by_group.setdefault(entry["model_name"], entry)
    for group, targets in fallback_map.items():
        if group in by_group:
            continue
        for target in targets:
            if target in by_group:
                import copy

                shadow = copy.deepcopy(by_group[target])
                shadow["model_name"] = group
                shadow["model_info"]["id"] = f"shadow-{group}"
                model_list.append(shadow)
                break

    fallbacks = [
        {group: targets}
        for group, targets in fallback_map.items()
        if group in groups
    ]

    return {
        "model_list": model_list,
        "router_settings": {
            "routing_strategy": "least-busy",
            "num_retries": 1,
            "retry_after": 2,
            **({"fallbacks": fallbacks} if fallbacks else {}),
        },
        "litellm_settings": {
            "master_key": "os.environ/LITELLM_MASTER_KEY",
            "drop_params": True,
            "set_verbose": False,
            "callbacks": ["prometheus", "octo_plugin.proxy_handler_instance"],
        },
        "general_settings": {
            "database_url": "os.environ/DATABASE_URL",
            "store_model_in_db": False,
            "master_key": "os.environ/LITELLM_MASTER_KEY",
        },
    }


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    catalog_path = Path(argv[1]) if len(argv) > 1 else root / "catalog.yaml"
    out_path = Path(argv[2]) if len(argv) > 2 else root / "deployments" / "gateway" / "litellm-config.yaml"
    catalog = load_catalog(catalog_path)
    cfg = litellm_config(catalog)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    body = yaml.safe_dump(cfg, sort_keys=False)
    out_path.write_text(f"{HEADER}\n{body}")
    groups = sorted({m["model_name"] for m in cfg["model_list"]})
    print(f"wrote {out_path}: {len(cfg['model_list'])} deployments, groups={groups}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
