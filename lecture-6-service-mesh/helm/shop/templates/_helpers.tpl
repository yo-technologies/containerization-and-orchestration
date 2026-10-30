{{- define "shop.labels" -}}
app.kubernetes.io/part-of: shop
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
owner: itmo
{{- end }}
