#!/usr/bin/env python3
"""Generate Prometheus file_sd targets from catalog.yaml.

Writes monitoring/prometheus/targets-http.json and targets-https.json.
prometheus.yml file_sd_configs pick them up automatically (no restart needed
on scrape-interval reload). Run this after every catalog.yaml change:

    scripts/venv-run.sh scripts/gen_prom_targets.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import yaml

STATUS_SCRAPED = {"running", "live"}


def load_catalog(path: Path) -> dict:
    with path.open() as fh:
        return yaml.safe_load(fh)


def split_targets(catalog: dict) -> tuple[list[dict], list[dict]]:
    http, https = [], []
    for model in catalog.get("models", []):
        if model.get("status") not in STATUS_SCRAPED:
            continue
        parsed = urlparse(model.get("scrape_url") or model["base_url"])
        entry = {
            "targets": [parsed.netloc],
            "labels": {"model": model["id"], "tier": model.get("tier", "")},
        }
        (https if parsed.scheme == "https" else http).append(entry)
    return http, https


def main(argv: list[str]) -> int:
    root = Path(__file__).resolve().parent.parent
    out_dir = Path(argv[1]) if len(argv) > 1 else root / "monitoring" / "prometheus"
    http, https = split_targets(load_catalog(root / "catalog.yaml"))
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "targets-http.json").write_text(json.dumps(http, indent=2) + "\n")
    (out_dir / "targets-https.json").write_text(json.dumps(https, indent=2) + "\n")
    print(f"wrote {len(http)} http + {len(https)} https scrape targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
