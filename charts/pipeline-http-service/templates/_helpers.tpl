{{- define "pipeline.podSecurity" -}}
serviceAccountName: {{ .Release.Name }}-app
automountServiceAccountToken: false
securityContext:
  runAsNonRoot: true
  runAsUser: 65532
  runAsGroup: 65532
  fsGroup: 65532
  seccompProfile:
    type: RuntimeDefault
{{- if .Values.pullSecret }}
imagePullSecrets:
  - name: {{ .Values.pullSecret }}
{{- end }}
{{- end }}

{{- define "pipeline.container" -}}
image: {{ .Values.image | quote }}
imagePullPolicy: IfNotPresent
env:
  - name: SOURCE_REVISION
    value: {{ .Values.sourceRevision | quote }}
  - name: DATA_DIR
    value: /data
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: [ALL]
resources:
  {{- .Values.resources | toYaml | nindent 2 }}
volumeMounts:
  - name: data
    mountPath: /data
  - name: tmp
    mountPath: /tmp
{{- end }}

{{- define "pipeline.volumes" -}}
volumes:
  - name: data
    {{- if .Values.persistence.enabled }}
    persistentVolumeClaim:
      claimName: {{ .Release.Name }}-data
    {{- else }}
    emptyDir:
      sizeLimit: 256Mi
    {{- end }}
  - name: tmp
    emptyDir:
      sizeLimit: 64Mi
{{- end }}
