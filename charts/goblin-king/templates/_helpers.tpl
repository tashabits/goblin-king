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

{{- define "goblin-king.controlPlaneImage" -}}
{{- if .Values.image.digest -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- else -}}
{{- printf "%s:%s" .Values.image.repository .Values.image.tag -}}
{{- end -}}
{{- end -}}

{{- define "goblin-king.resultForwarderImage" -}}
{{- $image := .Values.scheduler.resultForwarder.image -}}
{{- if or $image.repository $image.tag $image.digest -}}
{{- $repository := default .Values.image.repository $image.repository -}}
{{- if $image.digest -}}
{{- printf "%s@%s" $repository $image.digest -}}
{{- else -}}
{{- printf "%s:%s" $repository (default .Values.image.tag $image.tag) -}}
{{- end -}}
{{- else -}}
{{- include "goblin-king.controlPlaneImage" . -}}
{{- end -}}
{{- end -}}

{{- define "goblin-king.workerImagePullPolicy" -}}
{{- default .Values.image.pullPolicy .Values.scheduler.workerImagePullPolicy -}}
{{- end -}}

{{- define "goblin-king.resultForwarderImagePullPolicy" -}}
{{- default .Values.image.pullPolicy .Values.scheduler.resultForwarder.pullPolicy -}}
{{- end -}}

{{- define "goblin-king.workloadImagePullSecretNames" -}}
{{- $names := list -}}
{{- range .Values.image.pullSecrets -}}
{{- $name := "" -}}
{{- if kindIs "string" . -}}
{{- $name = . -}}
{{- else if and (kindIs "map" .) (hasKey . "name") -}}
{{- $name = get . "name" -}}
{{- end -}}

{{- if $name -}}{{- $names = append $names $name -}}{{- end -}}
{{- end -}}
{{- range .Values.scheduler.workloadImagePullSecrets -}}
{{- $name := "" -}}
{{- if kindIs "string" . -}}
{{- $name = . -}}
{{- else if and (kindIs "map" .) (hasKey . "name") -}}
{{- $name = get . "name" -}}
{{- end -}}
{{- if $name -}}{{- $names = append $names $name -}}{{- end -}}
{{- end -}}
{{- $names | uniq | toJson -}}
{{- end -}}

{{- define "goblin-king.restrictedWorkloadSettings" -}}
{{- $settings := .Values.scheduler.workloadSecurity.restricted -}}
{{- $workerResources := dict
  "cpu_request" $settings.workerResources.cpuRequest
  "cpu_limit" $settings.workerResources.cpuLimit
  "memory_request" $settings.workerResources.memoryRequest
  "memory_limit" $settings.workerResources.memoryLimit -}}
{{- $forwarderResources := dict
  "cpu_request" $settings.resultForwarderResources.cpuRequest
  "cpu_limit" $settings.resultForwarderResources.cpuLimit
  "memory_request" $settings.resultForwarderResources.memoryRequest
  "memory_limit" $settings.resultForwarderResources.memoryLimit -}}
{{- dict
  "run_as_user" $settings.runAsUser
  "run_as_group" $settings.runAsGroup
  "fs_group" $settings.fsGroup
  "worker_resources" $workerResources
  "result_forwarder_resources" $forwarderResources
  "worker_service_account_names" $settings.workerServiceAccounts
  | toJson -}}
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
