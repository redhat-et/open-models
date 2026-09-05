#!/usr/bin/env python3
"""Toggle a catalog model's serving status without reflowing catalog.yaml.

Usage: model_toggle.py MODEL_ID (on|off|status)
  off -> status: paused (keeps previous_status for `on` to restore)
  on  -> restore previous_status (default: running)
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

CATALOG = Path(
    os.environ.get("OCTO_CATALOG", Path(__file__).resolve().parent.parent / "catalog.yaml")
)


def block(lines: list[str], model_id: str) -> tuple[int, int]:
    start = None
    for i, ln in enumerate(lines):
        if re.match(rf"^  - id: {re.escape(model_id)}\s*$", ln):
            start = i
        elif start is not None and re.match(r"^  - id: ", ln):
            return start, i
    if start is None:
        ids = re.findall(r"^  - id: (.+)$", "\n".join(lines), re.M)
        sys.exit(f"unknown model id '{model_id}' (known: {', '.join(ids)})")
    return start, len(lines)


def main(argv: list[str]) -> int:
    if len(argv) != 3 or argv[2] not in {"on", "off", "status"}:
        sys.exit(__doc__)
    model_id, action = argv[1], argv[2]
    lines = CATALOG.read_text().splitlines()
    lo, hi = block(lines, model_id)
    seg = lines[lo:hi]
    cur = re.search(r"^    status: (\S+)\s*$", "\n".join(seg), re.M)
    cur = cur.group(1) if cur else "running"
    if action == "status":
        print(f"{model_id}: {cur}")
        return 0
    if cur == "paused" and action == "off":
        print(f"{model_id}: already paused")
        return 0
    if cur != "paused" and action == "on":
        print(f"{model_id}: already serving ({cur})")
        return 0

    prev = re.search(r"^    previous_status: (\S+)\s*$", "\n".join(seg), re.M)
    if action == "off":
        seg = [re.sub(r"^    status: \S+\s*$", "    status: paused", ln) for ln in seg]
        tag = f"    previous_status: {cur}"
        seg = [ln for ln in seg if not re.match(r"^    previous_status: ", ln)]
        seg = [re.sub(r"^    status: paused\s*$", f"    status: paused\n{tag}", ln, count=1) for ln in seg]
        new = "paused"
    else:
        target = prev.group(1) if prev else "running"
        seg = [re.sub(r"^    status: \S+\s*$", f"    status: {target}", ln) for ln in seg]
        seg = [ln for ln in seg if not re.match(r"^    previous_status: ", ln)]
        new = target
    lines[lo:hi] = seg
    CATALOG.write_text("\n".join(lines) + "\n")
    group = re.search(r"^    group: (\S+)", "\n".join(seg), re.M)
    print(f"{model_id}: {cur} -> {new}" + (f" (group {group.group(1)})" if group else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
