VENV := $(CURDIR)/web/.venv
PY := $(VENV)/bin/python

.PHONY: lab lab-test lab-down setup test gen web monitor-up monitor-down logs quality image

setup:
	python3 -m venv $(VENV)
	$(VENV)/bin/pip install -r web/requirements.txt

lab:
	./scripts/lab.sh up

lab-test:
	$(PY) -m pytest lab/tests -x

lab-down:
	./scripts/lab.sh down

test:
	cd web && $(PY) -m pytest
	$(PY) -m pytest backend

backend:
	. $(SECRETS); set -a; \
	SESSION_SECRET=$$(python3 -c "import secrets;print(secrets.token_hex(16))"); \
	LITELLM_URL=$(GATEWAY); \
	$(PY) -m uvicorn backend.app:app --reload --host 0.0.0.0 --port 8020

gen:
	$(PY) scripts/gen_configs.py
	$(PY) scripts/gen_prom_targets.py
	$(PY) scripts/gen_litellm_config.py
	cp deployments/gateway/litellm-config.yaml charts/octo-models/files/litellm-config.yaml
	cp monitoring/grafana/dashboards/open-models.json charts/octo-models/files/dashboards/open-models.json

# cluster deploy: needs oc logged in + octo-models namespace; secrets in
# ~/.config/octo-models/secrets.env (PG_PASSWORD RD_PASSWORD LLM_MASTER_KEY)
SECRETS ?= $(HOME)/.config/octo-models/secrets.env
NAMESPACE ?= octo-models
RUN_AS ?= 1001470000
GATEWAY ?= https://litellm-octo-models.apps.emerg.pcbk.p1.openshiftapps.com
APP_STREAM ?= om-backend
APP_TAG ?= latest
deploy: gen
	. $(SECRETS); helm upgrade --install om charts/octo-models -n $(NAMESPACE) \
	  -f charts/octo-models/values-targets.yaml \
	  --set runAsUser=$(RUN_AS) --set fsGroup=$(RUN_AS) \
	  --set secrets.postgresPassword=$$PG_PASSWORD \
	  --set secrets.redisPassword=$$RD_PASSWORD \
	  --set secrets.llmMasterKey=$$LLM_MASTER_KEY \
	  $(if $(APP),--set app.enabled=true \
	    --set secrets.sessionSecret=$$SESSION_SECRET \
	    --set secrets.githubClientId=$$GITHUB_CLIENT_ID \
	    --set secrets.githubClientSecret=$$GITHUB_CLIENT_SECRET \
	    --set secrets.githubOrg=$$GITHUB_ORG,)

# build the backend image in-cluster (binary BuildConfig, no external registry)
backend-image:
	rm -rf /tmp/om-backend-build && mkdir -p /tmp/om-backend-build && \
	cp -r backend /tmp/om-backend-build/backend && \
	cp catalog.yaml /tmp/om-backend-build/catalog.yaml && \
	oc start-build om-backend -n $(NAMESPACE) --from-dir /tmp/om-backend-build --follow && \
	oc set image deploy/om-app app=$(APP_STREAM):$(APP_TAG) -n $(NAMESPACE)

web:
	@echo "v1 UI retired (M4). The portal is the UI:"
	@echo "  https://octo-app-octo-models.apps.emerg.pcbk.p1.openshiftapps.com"
	@echo "web/ is kept for reference + tests; serve it locally with:"
	@echo "  cd web && $(PY) -m uvicorn app.main:app --port 8010"

monitor-up: gen
	podman rm -f om-prometheus om-grafana om-node-exporter 2>/dev/null || true
	podman run -d --name om-node-exporter --network host --pid host \
		--security-opt label=disable quay.io/prometheus/node-exporter:latest
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
	podman rm -f om-prometheus om-grafana om-node-exporter

logs:
	podman logs -f om-prometheus om-grafana

# web/.venv/bin/python benchmarks/quality/run_quality.py <base_url> <model>
quality:

image:
	podman build -t open-models-web .
