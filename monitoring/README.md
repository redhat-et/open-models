# Monitoring

Prometheus + Grafana. vLLM exposes `/metrics` natively - no proxy needed.
Each host additionally runs `node_exporter` (CPU/RAM/disk) and, where NVIDIA
DCGM works, `dcgm-exporter` for per-GPU stats.

## Start the stack (on the monitoring host)

```bash
GRAFANA_PASSWORD=... docker compose up -d
```

No docker? Same thing with raw podman (tested on Fedora):

```bash
podman run -d --name om-prometheus --network host \
  -v ./monitoring/prometheus:/etc/prometheus-data:ro,z \
  -v prom-data:/prometheus:z docker.io/prom/prometheus:latest \
  --config.file=/etc/prometheus-data/prometheus.yml --web.enable-lifecycle
podman run -d --name om-grafana --network host \
  -e GF_SECURITY_ADMIN_PASSWORD=admin -e GF_AUTH_ANONYMOUS_ENABLED=true \
  -e GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer -e DS_PROM_URL=http://localhost:9090 \
  -v ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro,z \
  -v ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro,z \
  docker.io/grafana/grafana:latest
```

Note: `--network host` (not a bridge) so containers use the host's DNS -
rootless pasta DNS cannot resolve internal corporate or cluster hostnames.

Grafana datasource + the "Usage & Activity" dashboard provision themselves.
Front-end link lives in `platform.dashboard_base` in `catalog.yaml`.

## Per-host exporters

```bash
# node_exporter on every host (rootless podman: no /proc bind, use --pid host)
podman run -d --name om-node-exporter --restart unless-stopped \
  --network host --pid host --security-opt label=disable \
  quay.io/prometheus/node-exporter:latest

# dcgm-exporter on NVIDIA hosts (RTX 5090 now; cluster nodes if reachable)
docker run -d --name dcgm-exporter --restart unless-stopped \
  --gpus all --network host --cap-add SYS_ADMIN \
  nvcr.io/nvidia/k8s/dcgm-exporter:latest
```

DGX Spark (GB10) and Strix Halo: use `node_exporter`; vLLM `/metrics` covers
the model side. Check whether `dcgm-exporter` supports GB10 on your DGX OS
version before adding it (RFE).

## Targets

Model scrape targets are GENERATED from `catalog.yaml`
(`scripts/gen_prom_targets.py` -> `targets-http/https.json`, file_sd;
Prometheus reloads them within 60s, or `curl -X POST localhost:9090/-/reload`).
After editing the catalog run `make gen`. The `node`/`dcgm` jobs stay manual
until exporters land on the model hosts.

## Alerts (`prometheus/alert.rules.yml`)

- ModelEndpointDown - vLLM `/metrics` scrape target down >5m
- ModelQueueBacklog - >5 waiting requests for 10m
- SlowTimeToFirstToken - p95 TTFT >5s for 15m
- HostHighMemoryPressure - >95% memory (unified-memory boxes evict models)
