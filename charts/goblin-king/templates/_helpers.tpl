{{- define "goblin-king.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "goblin-king.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "goblin-king.selectorLabels" -}}
app.kubernetes.io/name: {{ include "goblin-king.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "goblin-king.componentLabels" -}}
{{ include "goblin-king.selectorLabels" .root }}
app.kubernetes.io/component: {{ .component }}
{{- end -}}

{{- define "goblin-king.imagePullSecrets" -}}
{{- with .Values.image.pullSecrets }}
imagePullSecrets:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "goblin-king.podSecurityContext" -}}
{{- with .Values.podSecurityContext }}
securityContext:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "goblin-king.containerSecurityContext" -}}
{{- with .Values.securityContext }}
securityContext:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}

{{- define "goblin-king.serviceAccountName" -}}
{{- $root := .root -}}
{{- $component := .component -}}
{{- $settings := index $root.Values $component -}}
{{- if $settings.serviceAccount.create -}}
{{- default (printf "%s-%s" (include "goblin-king.fullname" $root) $component) $settings.serviceAccount.name -}}
{{- else -}}
{{- default "default" $settings.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "goblin-king.directoryUiServiceAccountName" -}}
{{- if .Values.directoryUi.serviceAccount.create -}}
{{- default (printf "%s-directory-ui" (include "goblin-king.fullname" .)) .Values.directoryUi.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.directoryUi.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "goblin-king.directoryApiServiceAccountName" -}}
{{- if .Values.repository.serviceAccount.create -}}
{{- default (printf "%s-directory-api" (include "goblin-king.fullname" .)) .Values.repository.serviceAccount.name -}}
{{- else -}}
{{- default "default" .Values.repository.serviceAccount.name -}}
{{- end -}}
{{- end -}}

{{- define "goblin-king.repositoryUrl" -}}
{{- if .Values.repository.url -}}
{{- .Values.repository.url -}}
{{- else if .Values.repository.enabled -}}
{{- printf "http://%s-directory-api:%v" (include "goblin-king.fullname" .) .Values.repository.port -}}
{{- end -}}
{{- end -}}

{{- define "goblin-king.podPlacement" -}}
{{- with .nodeSelector }}
nodeSelector:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- with .affinity }}
affinity:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- with .tolerations }}
tolerations:
{{- toYaml . | nindent 2 }}
{{- end }}
{{- end -}}
