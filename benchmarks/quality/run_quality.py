#!/usr/bin/env python3
"""Run the 30-prompt quality eval against an endpoint and record answers.

Usage: python run_quality.py <base_url> <served_model> [api_key]
Writes results/<model>-<date>.jsonl; two humans then score into scores.csv
(1-5 per prompt; tool_call prompts auto-check that a tool was actually
called). Run from the repo root with the web venv:
    web/.venv/bin/python benchmarks/quality/run_quality.py http://... Model
"""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import httpx
import yaml

HERE = Path(__file__).resolve().parent


def main(argv: list[str]) -> int:
    base = argv[1].rstrip("/")
    model = argv[2]
    key = argv[3] if len(argv) > 3 else "none"
    prompts = yaml.safe_load((HERE / "prompts.yaml").read_text())

    out = HERE / "results" / f"{model.replace('/', '_')}-{date.today()}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as fh:
        for section, items in prompts.items():
            for i, prompt in enumerate(items):
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 1024,
                }
                record = {"section": section, "index": i, "prompt": prompt}
                try:
                    r = httpx.post(
                        f"{base}/chat/completions",
                        json=payload,
                        headers={"Authorization": f"Bearer {key}"},
                        timeout=120,
                    )
                    r.raise_for_status()
                    choice = r.json()["choices"][0]
                    record["answer"] = choice["message"].get("content")
                    record["used_tool_call"] = bool(choice["message"].get("tool_calls"))
                    record["finish_reason"] = choice.get("finish_reason")
                except Exception as exc:  # record and keep going
                    record["error"] = str(exc)
                fh.write(json.dumps(record) + "\n")
                print(f"[{section} {i:02d}] {record.get('error') or 'ok'}")
    print(f"results: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
