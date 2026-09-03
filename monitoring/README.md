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

Gotcha: rootless podman DNS may not resolve internal corporate hostnames -
use IPs in `prometheus.yml` targets if you see `context deadline exceeded`.

Grafana datasource + the "Usage & Activity" dashboard provision themselves.
Front-end link lives in `platform.dashboard_base` in `catalog.yaml`.

## Per-host exporters

```bash
# node_exporter on every host (binary or container)
docker run -d --name node-exporter --restart unless-stopped \
  --pid host --network host \
  -v /proc:/host/proc:ro -v /sys:/host/sys:ro \
  quay.io/prometheus/node-exporter:latest \
  --path.procfs=/host/proc --path.sysfs=/host/sys

# dcgm-exporter on NVIDIA hosts (RTX 5090 now; cluster nodes if reachable)
docker run -d --name dcgm-exporter --restart unless-stopped \
  --gpus all --network host --cap-add SYS_ADMIN \
  nvcr.io/nvidia/k8s/dcgm-exporter:latest
```

DGX Spark (GB10) and Strix Halo: use `node_exporter`; vLLM `/metrics` covers
the model side. Check whether `dcgm-exporter` supports GB10 on your DGX OS
version before adding it (RFE).

## Targets

`prometheus/prometheus.yml` mirrors `catalog.yaml` hosts. Keep them in sync
(automating that is a good first issue - generate the scrape list from the
catalog).

## Alerts (`prometheus/alert.rules.yml`)

- ModelEndpointDown - vLLM `/metrics` scrape target down >5m
- ModelQueueBacklog - >5 waiting requests for 10m
- SlowTimeToFirstToken - p95 TTFT >5s for 15m
- HostHighMemoryPressure - >95% memory (unified-memory boxes evict models)
