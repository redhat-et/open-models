# Benchmarks

`guidellm` wrappers (same tool as `qwen3-serving/benchmarks.json`, v0.7.3).

## Selecting the Spark models (smart / fast / balanced)

For each candidate model on a Spark, run all three profiles:

```bash
./bench.sh http://spark2:8000 Qwen/Qwen3.8-27B latency
./bench.sh http://spark2:8000 Qwen/Qwen3.8-27B load
```

Acceptance targets (tune with the team after first runs):

| Tier      | Gate                                                        |
|-----------|-------------------------------------------------------------|
| fast      | single-stream > 30 tok/s; p95 TTFT < 2s @ 8 concurrent       |
| smart     | scores well enough on quality evals; > 10 tok/s single-stream |
| balanced  | > 20 tok/s single-stream; > 4 tok/s/user @ 8 concurrent      |

Quality side of "smart": run a small fixed eval (e.g. `lm_eval` subset or a
team-written 30-prompt set incl. coding tasks) against each candidate and
compare. Record picks by updating `catalog.yaml` and this file's decision log.

## Decision log

| date | host | winner | runner-up | notes |
|------|------|--------|-----------|-------|
| 2026-09-02 | spark2 | Qwen3.8-27B-FP8 | - | smart/slow tier, 256k ctx; benchmark to confirm |
| 2026-09-02 | rtx5090 | Qwen3.8-27B-NVFP4 | - | fast tier; benchmark to confirm |
| 2026-09-02 | openshift | Qwen3.8-Flash-Next-NVFP4 | - | best-of-both tier; benchmark to confirm |

## Historical data

`../qwen3-serving/benchmarks.{csv,json}` - guidellm v0.7.3 runs against
192.168.1.166 (the RTX 5090 box). Move copies here when convenient.
