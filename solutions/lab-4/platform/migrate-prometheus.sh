#!/usr/bin/env bash
# Adapted from Timur's Lab 3 setup-shop.py; only the Prometheus migration step.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
kubeconfig=${KUBECONFIG:-"$HOME/.local/state/container-lab/lab4.kubeconfig"}
context=kind-shop-lab4
state_dir=${LAB4_PROMETHEUS_STATE_DIR:-"$HOME/.local/state/container-lab/prometheus-shop-lab4"}
k=(kubectl --kubeconfig "$kubeconfig" --context "$context")
h=(helm --kubeconfig "$kubeconfig" --kube-context "$context")
legacy_stopped=false

legacy_scale() {
  "${h[@]}" upgrade prometheus prometheus \
    --repo https://prometheus-community.github.io/helm-charts --version 29.35.0 \
    -n observability --reuse-values --set "server.replicaCount=$1" --wait --timeout 5m
}

rollback() {
  local code=$1 managed
  trap - ERR INT TERM
  if [ "$legacy_stopped" = true ]; then
    printf '\nMigration interrupted; stopping the managed writer before restoring Deployment.\n' >&2
    managed=$("${k[@]}" -n observability get prometheus prometheus --ignore-not-found -o name) || exit "$code"
    if [ -n "$managed" ]; then
      "${k[@]}" -n observability patch prometheus prometheus \
        --type merge -p '{"spec":{"replicas":0}}' || exit "$code"
    fi
    "${k[@]}" -n observability wait --for=delete pod \
      -l app.kubernetes.io/component=server,app.kubernetes.io/instance=prometheus \
      --timeout=180s || exit "$code"
    legacy_scale 1 || exit "$code"
    printf 'Previous Prometheus Deployment restored.\n' >&2
  fi
  exit "$code"
}
trap 'rollback $?' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

python3 "$script_dir/prepare-prometheus-migration.py" \
  --kubeconfig "$kubeconfig" --context "$context" --state-dir "$state_dir"
if [ "${1:-}" = --check ]; then
  printf 'Preparation complete; cluster resources were not changed.\n'
  exit 0
fi
if [ "$#" -ne 0 ]; then
  printf 'Usage: bash migrate-prometheus.sh [--check]\n' >&2
  exit 2
fi

"${k[@]}" -n observability create secret generic lab2-additional-scrapes \
  --from-file="scrapes.yaml=$state_dir/scrapes.yaml" --dry-run=client -o yaml |
  "${k[@]}" apply -f -
"${k[@]}" apply -f "$script_dir/prometheus/prometheus-rbac.yaml"
"${k[@]}" apply -f "$state_dir/lab2-rules.json"
"${k[@]}" apply --dry-run=server -f "$script_dir/prometheus/prometheus.yaml" >/dev/null

printf '\nStopping the legacy writer; keeping its PVC.\n'
legacy_stopped=true
legacy_scale 0
"${k[@]}" -n observability wait --for=delete pod \
  -l app.kubernetes.io/component=server,app.kubernetes.io/instance=prometheus \
  --timeout=180s

"${k[@]}" apply -f "$script_dir/prometheus/prometheus.yaml"
for attempt in $(seq 1 60); do
  if "${k[@]}" -n observability get statefulset prometheus-prometheus >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
"${k[@]}" -n observability rollout status statefulset/prometheus-prometheus --timeout=300s
current_uid=$("${k[@]}" -n observability get pvc prometheus-server -o jsonpath='{.metadata.uid}')
[ "$current_uid" = "$(cat "$state_dir/original-pvc-uid")" ]
legacy_stopped=false
trap - ERR INT TERM
printf '\nOperator-managed Prometheus is ready; PVC UID is unchanged.\n'
"${k[@]}" -n observability get pods -l app.kubernetes.io/component=server
"${k[@]}" -n observability get endpointslices -l kubernetes.io/service-name=prometheus-server
