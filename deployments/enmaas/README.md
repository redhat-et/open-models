# EnMaaS monitoring — moved

The EnMaaS monitoring stack (Prometheus, Alertmanager, Grafana, dashboards,
scrape RBAC and the oauth2-proxy frontends) that used to live here under
`deployments/enmaas/monitoring/` has moved to the dedicated EnMaaS operations
repository:

- **https://github.com/redhat-et/EnMaaS-opps** → `base/projects/enmaas-monitoring/`

It is rendered per environment there with `make render ENV=<env>` (OpenShift
Templates + param files). Keeping a single home avoids configuration drift
between this repo and the live `enmaas-monitoring` namespace.
