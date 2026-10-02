# EnMaaS monitoring stack

Prometheus + Alertmanager + Grafana in the `enmaas-monitoring` namespace,
delivered as OCP templates (no Helm). Each route is fronted by an **nginx
basic-auth sidecar**. No cluster-scoped RBAC: this stack monitors EnMaaS
services; platform monitoring stays in `openshift-monitoring`.

> **Why basic auth and not SSO:** the templates originally used oauth-proxy
> with the openshift provider (cluster SSO). On 2026-09-24 a `rosa create idp`
> wedged the enmaas-prod hosted control plane: the API NLB stopped accepting
> TCP for ~15h and the OAuth server still cannot create login sessions
> (`/authorize` → login page returns HTTP 400), which locked everyone out of
> all three UIs. Red Hat case is open. nginx basic auth has zero dependency on
> cluster OAuth. **Revert to SSO once Red Hat resolves the case** — the
> oauth-proxy version is in git history (see "revert to SSO" below).

Credentials live in `~/.config/octo-models/enmaas-monitoring.cred` (chmod 600).

## One-time bootstrap per cluster (secrets are never in templates)

```bash
NS=enmaas-monitoring
oc apply -f 00-namespace.yaml
# route basic auth (bcrypt; nginx supports $2y$)
PASS="$(/usr/bin/openssl rand -base64 15 | tr -d '\n' | cut -c1-20)"
oc -n $NS create secret generic monitoring-basic \
  --from-literal=htpasswd="$(htpasswd -Bnb prom "$PASS")"
# grafana app login
GPASS="$(/usr/bin/openssl rand -base64 15 | tr -d '\n' | cut -c1-20)"
oc -n $NS create secret generic grafana-admin \
  --from-literal=admin-user=admin --from-literal=admin-password="$GPASS"
# record BOTH passwords in ~/.config/octo-models/enmaas-monitoring.cred
```

## Deploy / update

HCP data planes do NOT serve the `Template` API kind, and `oc process --local`
drops object namespaces — so rendering is always `--local` + `apply -n`:

```bash
cd deployments/enmaas/monitoring
oc apply -f 00-namespace.yaml
oc process --local -f 01-prometheus.template.yaml   --param-file=params/enmaas-prod-prometheus.params   | oc apply -n enmaas-monitoring -f -
oc process --local -f 02-alertmanager.template.yaml --param-file=params/enmaas-prod-alertmanager.params | oc apply -n enmaas-monitoring -f -
oc process --local -f 03-grafana.template.yaml      --param-file=params/enmaas-prod-grafana.params      | oc apply -n enmaas-monitoring -f -
```

Never pipe `oc apply` through `head`/`tail` (SIGPIPE truncates the apply
mid-stream — this silently skipped half of 00-namespace.yaml once). Config
changes: apply the ConfigMap, then `oc -n enmaas-monitoring rollout restart
deploy/<name>`.

## Verify

```bash
. ~/.config/octo-models/enmaas-monitoring.cred
B=apps.rosa.enmaas-prod.187f.p3.openshiftapps.com
oc -n enmaas-monitoring get pods                      # 3x 2/2 Running
curl -sk -o /dev/null -w '%{http_code}\n' https://prometheus-enmaas-monitoring.$B/            # 401
curl -sk -u "$BASIC_USER:$BASIC_PASS" "https://prometheus-enmaas-monitoring.$B/api/v1/targets" \
  | python3 -c "import json,sys;[print(t['labels']['job'],t['health']) for t in json.load(sys.stdin)['data']['activeTargets']]"
```

Grafana: basic auth prompt first, then Grafana's own login
(`$GRAFANA_USER`/`$GRAFANA_PASS`). Prometheus is provisioned as the default
datasource.

## Revert to SSO (after Red Hat fixes OAuth)

`git log --oneline -- deployments/enmaas/monitoring` → revert the "nginx
basic-auth" commit, re-run the oauth bootstrap (proxy-cookie + proxy-client
secrets, patch the three OAuthClients with the client secret), re-apply.
Validate with `curl -skI <route>` → expect 302 to `/oauth/authorize`.

## Scraping the EnMaaS stack

Prometheus auto-discovers workload metrics via **Service annotations** — adding a
component to monitoring is just tagging its Service, no config change:

```bash
oc -n enmaas annotate svc <name> prometheus.io/scrape="true" prometheus.io/port="<metrics-port>"
```

Discovery (`role: service` in namespaces `enmaas`, `cnpg-system`) needs
get/list/watch on Services there — granted by the least-privilege Roles in
`05-scrape-rbac.yaml`. CNPG's operator metrics are exposed via the
`cnpg-controller-metrics` Service (`04-cnpg-metrics.yaml`, port 8080).

**RDS Postgres (prod DB since 2026-09-30):** the exporter itself is NOT in
this repo. It lives with the application that owns the database, in
`redhat-et/pricetag` (`deploy/openshift/overlays/enmaas/rds-exporter.yaml`),
together with the NetworkPolicies that let us scrape it and let it reach RDS.
It runs in the `enmaas` namespace and is picked up here by the
annotation-driven `enmaas` job (`job="rds-postgres-exporter"`, port 9187) with
no scrape_config change — the post-RDS replacement for the CNPG exporters
(`04`, `06`, which now only describe the retired CNPG cluster). Alerting for
it stays here: `MonitoringComponentDown` covers `rds-postgres-exporter`.
Grafana: `pg_stat_database`, `pg_stat_replication`, `pg_stat_activity` and
per-relation I/O under the `postgres_*` metric family.

Currently scraped: `praxis:9901` (proxy request counters), `maas-api:9090`
(Go runtime), `cnpg-controller-metrics:8080`, `rds-postgres-exporter:9187`.
`metering-service` has no metrics endpoint yet (TODO in that service). Note the
`enmaas` stack manifests live in the cluster but NOT in this repo yet — bring
them in (best-practices rule #1) and add the annotations there.

## Hard-won notes (enmaas-prod, 2026-09)
- **NetworkPolicy must match the sidecar port.** The policy admits external
  traffic on 8443 only; nginx first listened on 8080 and every route returned
  503 with healthy pods. Sidecars must listen on 8443.
- `restricted-v2` SCC: do NOT pin `runAsUser`/`fsGroup`; the namespace gets an
  assigned uid range (e.g. 1000850000-…) and fixed values are rejected.
- Grafana only reads `GF_SECURITY_ADMIN_PASSWORD` when it initialises its DB.
  Changing the secret later does nothing — use
  `grafana cli --homepath /usr/share/grafana admin reset-admin-password <p>`.
- oauth2-proxy v7 removed standalone password providers; origin-oauth-proxy
  4.22 has no htpasswd provider either (and no `--user-id-header`). Its
  openshift provider needs `--client-secret-file`, `--cookie-secret-file`,
  `--openshift-service-account`, and the service annotation
  `service.beta.openshift.io/serving-cert-secret-name` with a reencrypt route.
- grafana + nginx images come from docker.io; mirror to our quay org before
  rate limits bite, then flip the *_IMAGE params.
- Alertmanager `default` receiver is a blackhole; wire Slack/PagerDuty next.
- Prometheus uses endpoint discovery for annotated Services in `enmaas` and
  `cnpg-system`, plus platform federation. Do not switch back to Service
  discovery: a Service VIP load-balances scrapes across replicas, making
  counters oscillate between pods and corrupting every rate/SLO calculation.
- Grafana dashboards are provisioned from `07-grafana-dashboards.yaml` and
  `09-grafana-rds-dashboard.yaml`; the PVC holds Grafana state, not the source
  of truth. The RDS dashboard intentionally targets
  `prometheuscommunity/postgres-exporter`. Grafana.com dashboard 24298 targets
  the unrelated `nbari/pg_exporter` schema and is mostly blank with our
  exporter even after its datasource uid is corrected.
- Recovery drill (rule 1): delete namespace, rerun bootstrap + deploy; the
  provisioned datasources and dashboards must reappear without UI imports.
