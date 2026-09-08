VENV := $(CURDIR)/web/.venv
PY := $(VENV)/bin/python

.PHONY: dev dev-down model-off model-on model-status lab lab-test lab-down setup test gen web monitor-up monitor-down logs quality image

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
	  --set secrets.promUser=$$PROM_USER \
	  --set secrets.promPassword=$$PROM_PASS \
	  --set secrets.promBcrypt=$$PROM_BCRYPT \
	  --set secrets.slackWebhook=$$SLACK_WEBHOOK_URL \
	  --set secrets.hotaisleApiToken=$$HOTAISLE_API_TOKEN \
	  --set secrets.hotaisleS3KeyId=$$HOTAISLE_S3_KEY_ID \
	  --set secrets.hotaisleS3KeySecret=$$HOTAISLE_S3_KEY_SECRET \
	  $(if $(APP),--set app.enabled=true \
	    --set secrets.sessionSecret=$$SESSION_SECRET \
	    --set secrets.githubClientId=$$GITHUB_CLIENT_ID \
	    --set secrets.githubClientSecret=$$GITHUB_CLIENT_SECRET \
	    --set secrets.githubOrg=$$GITHUB_ORG,)

model-off:
	@test -n "$(MODEL)" || { echo "usage: make model-off MODEL=<catalog-id>"; exit 2; }
	python3 scripts/model_toggle.py $(MODEL) off
	$(MAKE) deploy APP=1
	oc rollout status deploy/$(shell oc get deploy -n $(NAMESPACE) --no-headers | awk '/litellm/{print $$1; exit}') -n $(NAMESPACE) --timeout=180s
	$(if $(COMPUTE),python3 scripts/compute.py $(MODEL) off,)

model-on:
	@test -n "$(MODEL)" || { echo "usage: make model-on MODEL=<catalog-id>"; exit 2; }
	$(if $(COMPUTE),python3 scripts/compute.py $(MODEL) on,)
	python3 scripts/model_toggle.py $(MODEL) on
	$(MAKE) deploy APP=1
	oc rollout status deploy/$(shell oc get deploy -n $(NAMESPACE) --no-headers | awk '/litellm/{print $$1; exit}') -n $(NAMESPACE) --timeout=180s

model-status:
	@python3 scripts/model_toggle.py --help 2>/dev/null; for m in $$(grep -E '^  - id: ' catalog.yaml | sed 's/.*id: //'); do python3 scripts/model_toggle.py $$m status; done

# GPU node cost control (ncao 4xL40S node): cluster-autoscaler manages the pool
# (max 1). replicas=0 -> CA deletes the node in ~10 min (billing stops);
# replicas=1 -> CA provisions a node (~7 min) + vLLM cold start (~5-10 min,
# /cache is a PVC so weights + compile cache persist).
# Do NOT scale the machineset directly (webhook: Red Hat-managed resource).
FLASH_NS ?= ncao
FLASH_DEPLOY ?= qwen38-flash-next

gpu-off:
	@oc get nodes -l nvidia.com/gpu.present=true --no-headers | awk '{print $$1}' | while read -r n; do \
	  echo "GPU node $$n runs:"; oc get pods -n $(FLASH_NS) --field-selector spec.nodeName=$$n --no-headers | awk '{print "  " $$1 $$3}'; \
	done
	@oc scale deploy/$(FLASH_DEPLOY) -n $(FLASH_NS) --replicas=0
	@echo "flash-next scaled to 0 - cluster-autoscaler deletes the GPU node in ~10 min (billing stops)."
	@echo "restore: make gpu-on"

gpu-on:
	@oc scale deploy/$(FLASH_DEPLOY) -n $(FLASH_NS) --replicas=1
	@echo "scaling up: CA provisions the node (~7 min) + vLLM cold start. This blocks until Ready."
	@oc wait --for=condition=Ready pod -l app=$(FLASH_DEPLOY) -n $(FLASH_NS) --timeout=25m

DEV_NS ?= octo-models-dev
# per-namespace SCC uid range; fall back to this cluster's restricted-v2 default
DEV_UID = $(shell oc get ns $(DEV_NS) -o jsonpath='{.metadata.annotations.openshift\\.io/sa\\.scc\\.run-as-user-range}' 2>/dev/null | cut -d/ -f1 | grep -E '^[0-9]+$$' || echo 1001490000)
dev:
	oc create namespace $(DEV_NS) 2>/dev/null || true
	. $(SECRETS); helm upgrade --install omdev charts/octo-models -n $(DEV_NS) \
	  -f charts/octo-models/values-dev.yaml \
	  --set runAsUser=$(DEV_UID) --set fsGroup=$(DEV_UID) \
	  --set secrets.postgresPassword=$$PG_PASSWORD \
	  --set secrets.redisPassword=$$RD_PASSWORD \
	  --set secrets.llmMasterKey=$$LLM_MASTER_KEY \
	  --set secrets.promUser=$$PROM_USER \
	  --set secrets.promPassword=$$PROM_PASS \
	  --set secrets.promBcrypt=$$PROM_BCRYPT \
	  --set secrets.slackWebhook=$$SLACK_WEBHOOK_URL \
	  --set secrets.hotaisleApiToken=$$HOTAISLE_API_TOKEN \
	  --set secrets.hotaisleS3KeyId=$$HOTAISLE_S3_KEY_ID \
	  --set secrets.hotaisleS3KeySecret=$$HOTAISLE_S3_KEY_SECRET \
	  --set secrets.sessionSecret=$$SESSION_SECRET \
	  --set secrets.githubClientId=$$GITHUB_CLIENT_ID \
	  --set secrets.githubClientSecret=$$GITHUB_CLIENT_SECRET \
	  --set secrets.githubOrg=$$GITHUB_ORG
	rm -rf /tmp/om-backend-build && mkdir -p /tmp/om-backend-build && \
	cp -r backend /tmp/om-backend-build/backend && \
	cp catalog.yaml /tmp/om-backend-build/catalog.yaml && \
	cp -r scripts /tmp/om-backend-build/scripts && cp -r docs /tmp/om-backend-build/docs && \
	oc start-build om-backend -n $(DEV_NS) --from-dir /tmp/om-backend-build --follow --wait && \
	oc set image deploy/omdev-app app=$$(oc get istag om-backend:latest -n $(DEV_NS) -o jsonpath='{.image.dockerImageReference}') -n $(DEV_NS)
	oc rollout status deploy/omdev-app -n $(DEV_NS) --timeout=180s || true

dev-down:
	helm uninstall omdev -n $(DEV_NS) 2>/dev/null || true
	oc delete namespace $(DEV_NS) 2>/dev/null || true

# build the backend image in-cluster (binary BuildConfig, no external registry)
backend-image:
	rm -rf /tmp/om-backend-build && mkdir -p /tmp/om-backend-build && \
	cp -r backend /tmp/om-backend-build/backend && \
	cp catalog.yaml /tmp/om-backend-build/catalog.yaml && \
	cp -r scripts /tmp/om-backend-build/scripts && cp -r docs /tmp/om-backend-build/docs && \
	oc start-build om-backend -n $(NAMESPACE) --from-dir /tmp/om-backend-build --follow --wait && \
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
