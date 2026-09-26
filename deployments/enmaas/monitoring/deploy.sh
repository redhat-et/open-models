#!/usr/bin/env bash
# One-command bootstrap for the enmaas-monitoring stack (best-practices rule 1:
# "bootstrap must be one command"). Idempotent: safe to re-run; recreates a
# deleted namespace from scratch.
#
# Reads secrets from ~/.config/octo-models/enmaas-monitoring.cred (chmod 600).
# On a genuinely cold cluster (no cred file) it generates fresh values and
# writes them there - never commit that file.
set -euo pipefail
cd "$(dirname "$0")"

CRED="${CRED:-$HOME/.config/octo-models/enmaas-monitoring.cred}"
NS=enmaas-monitoring
umask 077

gen() { /usr/bin/openssl rand -base64 15 | tr -d '\n' | cut -c1-20; }

if [[ ! -f "$CRED" ]]; then
  echo "no cred file at $CRED - generating fresh secrets"
  mkdir -p "$(dirname "$CRED")"
  BASIC_PASS=$(gen); GRAFANA_PASS=$(gen)
  BASIC_USER=prom; GRAFANA_USER=admin
  HTPASSWD=$(htpasswd -Bnb "$BASIC_USER" "$BASIC_PASS")
  PROXY_COOKIE=$(head -c 24 /dev/urandom | base64 -w0)          # exactly 32 chars
  PROXY_CLIENT=$(head -c 24 /dev/urandom | base64 -w0 | cut -c1-32)
  SLACK_WEBHOOK="${SLACK_WEBHOOK:-https://hooks.slack.com/services/REPLACE_ME}"
  cat > "$CRED" <<EOF
BASIC_USER=$BASIC_USER
BASIC_PASS=$BASIC_PASS
GRAFANA_USER=$GRAFANA_USER
GRAFANA_PASS=$GRAFANA_PASS
HTPASSWD=$HTPASSWD
PROXY_COOKIE=$PROXY_COOKIE
PROXY_CLIENT=$PROXY_CLIENT
SLACK_WEBHOOK=$SLACK_WEBHOOK
EOF
  echo "wrote $CRED - EDIT SLACK_WEBHOOK before alerts can fire"
fi

# shellcheck disable=SC1090
source "$CRED"

create_secret() { # name key value
  oc -n "$NS" create secret generic "$1" --from-literal="$2=$3" \
    --dry-run=client -o yaml | oc apply -f - >/dev/null
}

echo "==> namespace + RBAC"
oc apply -f 00-namespace.yaml >/dev/null
oc apply -f 05-scrape-rbac.yaml >/dev/null
oc apply -f 08-platform-federation-rbac.yaml >/dev/null

echo "==> secrets"
create_secret monitoring-basic   htpasswd      "$HTPASSWD"
create_secret proxy-cookie       cookie-secret "$PROXY_COOKIE"
create_secret proxy-client       client-secret "$PROXY_CLIENT"
create_secret alertmanager-slack slack-url     "$SLACK_WEBHOOK"
create_secret grafana-admin      admin-user    "${GRAFANA_USER:-admin}"
create_secret grafana-admin      admin-password "$GRAFANA_PASS"

echo "==> services (render + apply)"
oc apply -f 04-cnpg-metrics.yaml >/dev/null
oc apply -f 06-aigateway-pg-metrics.yaml >/dev/null
oc process --local -f 01-prometheus.template.yaml   --param-file=params/enmaas-prod-prometheus.params   | oc apply -n "$NS" -f - >/dev/null
oc process --local -f 02-alertmanager.template.yaml --param-file=params/enmaas-prod-alertmanager.params | oc apply -n "$NS" -f - >/dev/null
oc process --local -f 03-grafana.template.yaml      --param-file=params/enmaas-prod-grafana.params      | oc apply -n "$NS" -f - >/dev/null
oc apply -f 07-grafana-dashboards.yaml >/dev/null

echo "==> waiting for rollouts"
oc -n "$NS" rollout status deploy/prometheus   --timeout=180s >/dev/null
oc -n "$NS" rollout status deploy/alertmanager --timeout=180s >/dev/null
oc -n "$NS" rollout status deploy/grafana      --timeout=180s >/dev/null

echo "==> smoke"
for i in $(seq 1 12); do
  sleep 10
  code=$(curl -sk -o /dev/null -w '%{http_code}' -u "${BASIC_USER:-prom}:${BASIC_PASS}" \
    "https://prometheus-${NS}.apps.rosa.enmaas-prod.187f.p3.openshiftapps.com/-/ready" 2>/dev/null || echo 000)
  [[ "$code" == "200" ]] && { echo "prometheus ready"; break; }
  [[ $i == 12 ]] && { echo "SMOKE FAILED (last code $code)"; exit 1; }
done
n=$(curl -sk -u "${BASIC_USER:-prom}:${BASIC_PASS}" \
  "https://prometheus-${NS}.apps.rosa.enmaas-prod.187f.p3.openshiftapps.com/api/v1/targets" \
  | python3 -c "import json,sys; d=json.load(sys.stdin)['data']['activeTargets']; up=sum(1 for t in d if t['health']=='up'); print(f'{up}/{len(d)} targets up')")
echo "==> $n"
echo "done. dashboards: https://grafana-${NS}.apps.rosa.enmaas-prod.187f.p3.openshiftapps.com"
