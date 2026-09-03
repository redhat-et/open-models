# RFE: instrument + speed up vLLM startup

Problem: restarts/redeploys take forever and we can't tell which phase is
slow (weights download from cache vs load to GPU vs CUDA graph capture vs
API bind). This hurts iteration during model selection on the Sparks.

Plan:
1. Wrapper script `scripts/timeline-startup.sh`: start container, poll
   `/health` at 1s, timestamp each phase parsed from vLLM logs
   ("Loading safetensors", "Capturing CUDA graphs", "Uvicorn running").
2. Store one `startup.json` per host+model+flags in `benchmarks/`.
3. Compare on each platform:
   - `--enforce-eager` (skip CUDA graph capture; slower steady-state, faster boot)
   - `--load-format fastsafetensors` (when supported)
   - prefix caching / reasoning parser combos
   - warm vs cold page cache (unified memory boxes are sensitive)
4. Output: recommended boot flags per platform in each
   `deployments/*/README.md`, and startup p50/p95 on the dashboard.

Acceptance: team can explain any startup > 2x p50 with the timeline data.
