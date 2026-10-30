{{- define "shop.labels" -}}
app.kubernetes.io/part-of: shop
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
owner: itmo
{{- end }}

{{- define "shop.apiSpreading" -}}
{{- if .Values.api.spreading.enabled -}}
{{- if eq .Values.api.spreading.mechanism "topologySpreadConstraints" -}}
topologySpreadConstraints:
  - maxSkew: 1
    topologyKey: kubernetes.io/hostname
    whenUnsatisfiable: DoNotSchedule
    labelSelector:
      matchLabels:
        app.kubernetes.io/instance: {{ .Release.Name }}
        app.kubernetes.io/name: api
{{- else if eq .Values.api.spreading.mechanism "podAntiAffinity" -}}
affinity:
  podAntiAffinity:
    requiredDuringSchedulingIgnoredDuringExecution:
      - topologyKey: kubernetes.io/hostname
        labelSelector:
          matchLabels:
            app.kubernetes.io/instance: {{ .Release.Name }}
            app.kubernetes.io/name: api
{{- else -}}
{{- fail "api.spreading.mechanism must be topologySpreadConstraints or podAntiAffinity" -}}
{{- end -}}
{{- end -}}
{{- end }}
