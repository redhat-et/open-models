{{- define "octo-models.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "octo-models.labels" -}}
app.kubernetes.io/part-of: octo-open-models
app.kubernetes.io/managed-by: {{ .Release.Service }}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version }}
{{- end -}}

{{- define "octo-models.dburl" -}}
postgresql://{{ .Values.postgres.user }}:{{ .Values.secrets.postgresPassword }}@{{ .Release.Name }}-postgres:5432/{{ .Values.postgres.database }}
{{- end -}}
