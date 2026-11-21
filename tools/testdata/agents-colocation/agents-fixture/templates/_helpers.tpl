{{- define "agents-fixture.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "agents-fixture.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{- define "agents-fixture.mcpFullname" -}}
{{- printf "%s-mcp" (include "agents-fixture.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "agents-fixture.a2aFullname" -}}
{{- printf "%s-a2a" (include "agents-fixture.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

{{- define "agents-fixture.selectorLabels" -}}
app.kubernetes.io/name: {{ include "agents-fixture.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{- define "agents-fixture.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{ include "agents-fixture.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{- define "agents-fixture.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "agents-fixture.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{- define "agents-fixture.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) }}
{{- end }}

{{- define "agents-fixture.databaseSecretName" -}}
{{- default (printf "%s-db" (include "agents-fixture.fullname" .)) .Values.database.existingSecret }}
{{- end }}

{{- define "agents-fixture.kongPluginNames" -}}
{{- $names := list -}}
{{- range $name, $cfg := .Values.kongPlugins -}}
{{- if $cfg.enabled -}}
{{- $names = append $names (printf "%s-%s" (include "agents-fixture.fullname" $) $name) -}}
{{- end -}}
{{- end -}}
{{- join "," $names -}}
{{- end }}

{{/* "true" when cmd/mcp and cmd/a2a share the <release>-agents Deployment (agents.colocate, capacity step 3). */}}
{{- define "agents-fixture.agentsColocated" -}}
{{- if and (.Values.agents | default dict).colocate .Values.mcp.enabled .Values.a2a.enabled }}true{{ end -}}
{{- end }}

{{- define "agents-fixture.agentsFullname" -}}
{{- printf "%s-agents" (include "agents-fixture.fullname" .) | trunc 63 | trimSuffix "-" }}
{{- end }}
