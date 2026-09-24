# EnMaaS monitoring stack

Prometheus + Alertmanager + Grafana in the `enmaas-monitoring` namespace,
delivered as OCP templates (no Helm). Every route sits behind an oauth-proxy
sidecar using the **openshift provider** — access is gated by cluster SSO
(Red Hat login), no passwords to manage. No cluster-scoped RBAC: this stack
monitors EnMaaS services; platform monitoring stays in `openshift-monitoring`.

## One-time bootstrap per cluster (secrets are never in templates)

```bash
NS=enmaas-monitoring
oc create ns $NS   # or apply 00-namespace.yaml first
oc -n $NS create secret generic proxy-cookie \
  --from-literal=cookie-secret="$(head -c 24 /dev/urandom | base64 -w0)"   # exactly 32 chars
CS="$(head -c 24 /dev/urandom | base64 -w0 | cut -c1-32)"
oc -n $NS create secret generic proxy-client --from-literal=client-secret="$CS"
# OAuthClients are created by the templates without a secret; inject it out-of-band:
for s in prometheus alertmanager grafana; do
  oc patch oauthclient $s -p "{\"secret\":\"$CS\"}" --type merge
done
oc -n $NS create secret generic grafana-admin \
  --from-literal=admin-user=admin \
  --from-literal=admin-password="$(/usr/bin/openssl rand -base64 15)"
```

## Deploy / update

HCP data planes do NOT serve the `Template` API kind, and `oc process
--local` drops object namespaces — so rendering is always:

```bash
cd deployments/enmaas/monitoring
oc apply -f 00-namespace.yaml
oc process --local -f 01-prometheus.template.yaml   --param-file=params/enmaas-prod-prometheus.params   | oc apply -n enmaas-monitoring -f -
oc process --local -f 02-alertmanager.template.yaml --param-file=params/enmaas-prod-alertmanager.params | oc apply -n enmaas-monitoring -f -
oc process --local -f 03-grafana.template.yaml      --param-file=params/enmaas-prod-grafana.params      | oc apply -n enmaas-monitoring -f -
```

Never pipe `oc apply` through `head`/`tail` (SIGPIPE truncates the apply
mid-stream). Config changes: apply the ConfigMap, then
`oc -n enmaas-monitoring rollout restart deploy/<name>`.

## Verify

```bash
oc -n enmaas-monitoring get pods    # 3x 2/2 Running
# each route must 302 to https://oauth.enmaas-prod.../oauth/authorize
curl -skI https://prometheus-enmaas-monitoring.apps.rosa.enmaas-prod.187f.p3.openshiftapps.com/ | grep -i location
```

Open the Grafana route in a browser; complete SSO; default datasource
(Prometheus) is provisioned. Grafana admin login (emergency/direct DB) is
`admin` + the grafana-admin secret — change it in the UI after first use.

## Hard-won notes (enmaas-prod, 2026-09)
- `restricted-v2` SCC: do NOT pin `runAsUser`/`fsGroup`; the namespace is
  assigned a uid range (e.g. 1000850000-…) and fixed values are rejected.
- oauth2-proxy v7 removed standalone password providers; origin-oauth-proxy
  4.22 has no htpasswd provider either — openshift-provider SSO is the only
  supported no-IdP-login path.
- origin-oauth-proxy flags that do NOT exist: `--user-id-header`,
  standalone htpasswd. Does exist: `--client-secret-file`,
  `--cookie-secret-file`, `--openshift-service-account`.
- Proxy TLS: service annotation
  `service.beta.openshift.io/serving-cert-secret-name: <svc>-proxy-tls`
  auto-provisions the cert the router trusts (route uses `reencrypt`).
- Let oauth-proxy auto-detect the cluster CA —
  `/var/run/secrets/openshift/serviceaccount/ca.crt` is not always present.
- grafana image is on docker.io; mirror to our quay org if pulls get rate
  limited, then flip GRAFANA_IMAGE param.
- Alertmanager `default` receiver is a blackhole; wire Slack/PagerDuty in a
  follow-up PR (secret-backed creds, same bootstrap pattern).
- Recovery drill (rule 1): delete namespace, rerun bootstrap + deploy;
  dashboards live in the grafana-data PVC (export to git once stable).
