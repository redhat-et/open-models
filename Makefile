VENV := web/.venv
PY := $(VENV)/bin/python

.PHONY: setup test gen web monitor-up monitor-down logs quality image

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r web/requirements.txt

test:
	cd web && $(PY) -m pytest

gen:
	$(PY) scripts/gen_configs.py
	$(PY) scripts/gen_prom_targets.py

web:
	cd web && $(PY) uvicorn app.main:app --reload --port 8010

monitor-up: gen
	podman rm -f om-prometheus om-grafana 2>/dev/null || true
	podman run -d --name om-prometheus --network host \
		-v ./monitoring/prometheus:/etc/prometheus-data:ro,z \
		-v prom-data:/prometheus:z docker.io/prom/prometheus:latest \
		--config.file=/etc/prometheus-data/prometheus.yml \
		--web.enable-lifecycle
	podman run -d --name om-grafana --network host \
		-e GF_SECURITY_ADMIN_PASSWORD=admin -e GF_AUTH_ANONYMOUS_ENABLED=true \
		-e GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer -e DS_PROM_URL=http://localhost:9090 \
		-v ./monitoring/grafana/provisioning:/etc/grafana/provisioning:ro,z \
		-v ./monitoring/grafana/dashboards:/var/lib/grafana/dashboards:ro,z \
		docker.io/grafana/grafana:latest

monitor-down:
	podman rm -f om-prometheus om-grafana

logs:
	podman logs -f om-prometheus om-grafana

# web/.venv/bin/python benchmarks/quality/run_quality.py <base_url> <model>
quality:

image:
	podman build -t open-models-web .
