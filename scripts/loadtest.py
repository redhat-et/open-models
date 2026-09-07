#!/usr/bin/env python3
"""Measure how many concurrent sessions a model serves before tokens get slow.

Usage: loadtest.py --url http://host:8000/v1 --model NAME [--key KEY]
                   [--levels 1,2,4,8] [--sessions 2] [--min-tokens 120]
                   [--min-tps 15] [--catalog-model ID]

Streams chat completions, counts streamed chunks as ~1 token, and prints
per-level aggregate + per-session tok/s. Recommended seats = highest level
whose per-session rate still clears --min-tps. Feed the number into
catalog.yaml model `seats:` and the portal card.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path

import urllib.request

import yaml


def one_stream(url: str, model: str, key: str, min_tokens: int) -> tuple[int, float]:
    body = json.dumps({
        "model": model, "max_tokens": min_tokens, "temperature": 0.7,
        "stream": True,
        "messages": [{"role": "user",
                      "content": "Count slowly from 1 upward, one number per line."}],
    }).encode()
    req = urllib.request.Request(f"{url}/chat/completions", data=body, headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {key or 'none'}",
    })
    t0 = time.time()
    n = 0
    with urllib.request.urlopen(req, timeout=600) as resp:
        for line in resp:
            if not line.startswith(b"data:"):
                continue
            payload = line[5:].strip()
            if payload == b"[DONE]":
                break
            try:
                delta = json.loads(payload)["choices"][0]["delta"]
            except (KeyError, ValueError):
                continue
            n += 1 if (delta.get("content") or delta.get("reasoning")
                       or delta.get("reasoning_content")) else 0
    return n, max(time.time() - t0, 1e-6)


def run_level(url, model, key, level, sessions, min_tokens):
    results: list = []
    lock = threading.Lock()

    def worker():
        try:
            r = one_stream(url, model, key, min_tokens)
            with lock:
                results.append(r)
        except Exception as exc:  # noqa: BLE001
            print(f"  session error: {exc}", file=sys.stderr)

    threads = [threading.Thread(target=worker) for _ in range(level * sessions)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    wall = time.time() - t0
    return results, wall


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url")
    ap.add_argument("--model")
    ap.add_argument("--key", default=os.environ.get("OCTO_KEY", ""))
    ap.add_argument("--levels", default="1,2,4,8")
    ap.add_argument("--sessions", type=int, default=2, help="sessions per seat")
    ap.add_argument("--min-tokens", type=int, default=120)
    ap.add_argument("--min-tps", type=float, default=15.0)
    ap.add_argument("--catalog-model", help="resolve url/model from catalog host")
    args = ap.parse_args(argv[1:])

    url, model = args.url, args.model
    if args.catalog_model and not (url and model):
        cat = yaml.safe_load(open(Path(__file__).resolve().parent.parent / "catalog.yaml"))
        m = next((x for x in cat["models"] if x["id"] == args.catalog_model), None)
        if not m:
            sys.exit(f"no such catalog model: {args.catalog_model}")
        url = url or m.get("base_url")
        model = model or m.get("served_model")
    if not url or not model:
        sys.exit("need --url and --model (or --catalog-model)")

    print(f"target: {url} model={model} sessions/seat={args.sessions} min-tps={args.min_tps}")
    print(f"{'seats':>6} {'wall_s':>7} {'total tok/s':>12} {'tok/s per session':>18}  verdict")
    seats_ok = 0
    for level in [int(x) for x in args.levels.split(",")]:
        results, wall = run_level(url, model, args.key, level, args.sessions, args.min_tokens)
        if not results:
            print(f"{level:>6}  all sessions failed")
            continue
        total_tok = sum(n for n, _ in results)
        per = [n / dur for n, dur in results]
        worst = min(per)
        avg = statistics.mean(per)
        verdict = "ok" if worst >= args.min_tps else f"below {args.min_tps} tok/s"
        if worst >= args.min_tps:
            seats_ok = level
        print(f"{level:>6} {wall:>7.1f} {total_tok / wall:>12.1f} {avg:>11.1f} (worst {worst:>5.1f})  {verdict}")
    print(f"\nrecommended seats (>= {args.min_tps} tok/s each): {seats_ok}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
