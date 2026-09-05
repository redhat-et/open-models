#!/usr/bin/env python3
"""Run a host's compute on/off commands from catalog.yaml, with safety checks.

Usage: compute.py MODEL_ID (off|on)
Looks up the model's host, refuses `off` if another live model still needs it,
then executes hosts.<host>.compute.off / .on from catalog.yaml.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import yaml

CATALOG = Path(os.environ.get("OCTO_CATALOG",
               Path(__file__).resolve().parent.parent / "catalog.yaml"))
LIVE = {"running", "live", "listed"}


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in {"off", "on"}:
        sys.exit(__doc__)
    model_id, action = argv[1], argv[2]
    cat = yaml.safe_load(CATALOG.read_text())
    models = {m["id"]: m for m in cat.get("models", [])}
    if model_id not in models:
        sys.exit(f"unknown model id '{model_id}'")
    host = models[model_id].get("host")
    hconf = (cat.get("hosts") or {}).get(host) or {}
    cmd = (hconf.get("compute") or {}).get(action)
    if not cmd:
        print(f"{host}: no compute.{action} command configured - stop it manually "
              f"(see docs/howto.md)")
        return 0
    if action == "off":
        others = [m["id"] for m in cat.get("models", [])
                  if m.get("host") == host and m["id"] != model_id
                  and (m.get("status") or "running") in LIVE]
        if others:
            sys.exit(f"refusing: host {host} still serves {', '.join(others)} "
                     f"(pause those models first or accept the outage)")
    print(f"$ {cmd}")
    return subprocess.call(cmd, shell=True, cwd=CATALOG.parent)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
