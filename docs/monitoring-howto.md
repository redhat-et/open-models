# Monitoring models: best practices how-to

Audience: anyone adding a model/host to the platform. Goal: every instance is
visible in Grafana within 15 minutes of booting, with the same panels and
alerting as everything else.

## 1. Expose the right signals

| Signal | Source | Endpoint |
|--------|--------|----------|
| Model activity (req/s, queue, TTFT, KV cache) | vLLM `/metrics` | `:8000/metrics` |
| Host (CPU, RAM, disk, thermals) | node_exporter | `:9100/metrics` |
| NVIDIA GPU (util, VRAM, power) | dcgm-exporter | `:9400/metrics` |

Rules of thumb:
- Scrape, never push. vLLM already exposes Prometheus metrics - no sidecar
  needed; llama.cpp needs `--metrics` (or blackbox-exporter as a fallback).
- Label every target with `model:` (catalog id) and `tier:` - the dashboard
  and alerts group on those, not on raw instance names.
- Scrape interval 15s is plenty; histograms need rate(), not raw resets.

## 2. Wire it up

1. Add the model to `catalog.yaml` (status `candidate` until verified).
2. Add scrape targets in `monitoring/prometheus/prometheus.yml` mirroring it.
3. `docker compose up -d` + reload Prometheus. Confirm in `Status -> Targets`.
4. Front end + Grafana pick it up automatically.

## 3. What to watch (the four golden signals, model edition)

- **Traffic**: `rate(vllm:generation_tokens_total[5m])` - actual usage per
  model. This is the "offset frontier spend" number for leadership.
- **Latency**: p95 TTFT + e2e per model; alert at 5s/30s.
- **Errors**: scrape target `up`; errored vs successful in guidellm runs.
- **Saturation**: `vllm:num_requests_waiting` (queue) and
  `gpu_cache_usage_perc`; host memory >95% on unified boxes = eviction risk.

## 4. Alert etiquette

- Page only on down (ModelEndpointDown). Everything else is a dashboard
  trend or a team ping.
- New alert rule ships with a runbook line in the annotation.

## 5. Capacity reviews

Monthly: token-throughput panel -> busiest tier at busiest hour. If a tier
sits at >50% queue time, either add a replica (cluster) or move a Spark to
that model. Record the decision in `benchmarks/README.md`.
