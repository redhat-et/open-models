# EnMaaS engineering journal

Monthly write-ups of what we built, what broke, and what we learned standing
up the EnMaaS platform. Written for the people who will operate this — the
war stories are the point, so they include the specific error strings and the
things we got wrong.

| Date | Post | Covers |
|------|------|--------|
| 2026-09 | [EnMaaS goes prod: standing up a monitoring stack the boring way](2026-09-enmaas-monitoring.md) | Prometheus, Alertmanager, Grafana, SLOs, and the OpenShift HCP gotchas behind them |
| 2026-10 | [EnMaaS goes public: DNS, TLS, and the guardrails that bit back](2026-10-enmaas-goes-public.md) | Public hostnames, cert-manager TLS, the move to managed RDS, a functional test suite, and the ways our own safety mechanisms misfired |

These are internal engineering notes, not published articles. Posts intended
for a wider audience go through the OCTO review process and get adapted and
sanitised first.

Each post ends with a "parts we still owe" list. The following month's post
opens by settling it — that is the only mechanism keeping the debt list
honest, so please keep it.
