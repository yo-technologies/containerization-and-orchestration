#!/usr/bin/env bash
# Reuses the Helm configurations developed during Lab 2.
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_dir=$(cd -- "$script_dir/.." && pwd)
cluster=observability
namespace=observability
skip_build=false
open_ports=true
prometheus_values="$repo_dir/helm/prometheus-values.yaml"
port_pids=()
run_dir=

usage() {
  cat <<'HELP'
Usage: bash scripts/setup-observability.sh [options]

Deploy the Lab 2 stack and open its local interfaces.
Requires a running Docker engine, kind, kubectl, Helm 3+ and internet access.

  --cluster NAME             kind cluster name (default: observability)
  --skip-build               reuse the local Docker image lab-api:0.1
  --no-port-forward          finish after installation and readiness checks
  --prometheus-values FILE   full Prometheus values, e.g. for another lab
  -h, --help                 show this help

The namespace and release names stay fixed because the existing configurations
reference them. Ctrl+C closes this script's forwards; cluster and data remain.
HELP
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
log() { printf '\n%s\n' "$*"; }

while [ "$#" -gt 0 ]; do
  case "$1" in
    --cluster)
      [ "$#" -ge 2 ] && [ -n "$2" ] || die '--cluster needs a name'
      cluster=$2; shift 2 ;;
    --prometheus-values)
      [ "$#" -ge 2 ] && [ -n "$2" ] || die '--prometheus-values needs a file'
      prometheus_values=$2; shift 2 ;;
    --skip-build) skip_build=true; shift ;;
    --no-port-forward) open_ports=false; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

[[ "$cluster" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]] || die 'Invalid cluster name'
[ "${#cluster}" -le 63 ] || die 'Cluster name is too long'
context="kind-$cluster"

for command in docker kind kubectl helm; do
  command -v "$command" >/dev/null 2>&1 || die "Install $command first"
done
for file in "$prometheus_values" "$repo_dir/api/Dockerfile" \
  "$repo_dir/dashboards/api-red.json" "$repo_dir/helm/grafana-dashboard-values.yaml" \
  "$repo_dir/helm/api/Chart.yaml" "$repo_dir/helm/webhook/Chart.yaml" \
  "$repo_dir/helm/api/values.yaml" "$repo_dir/helm/grafana-values.yaml" \
  "$repo_dir/helm/loki-values.yaml" "$repo_dir/helm/jaeger-values.yaml" \
  "$repo_dir/helm/alloy-values.yaml" "$repo_dir/helm/karma-values.yaml" \
  "$repo_dir/helm/blackbox-values.yaml"; do
  [ -f "$file" ] || die "Missing file: $file (copy the whole lab directory)"
done
# Resolve relative paths before any change of working directory.
prometheus_values=$(cd -- "$(dirname -- "$prometheus_values")" && pwd)/$(basename -- "$prometheus_values")
docker info >/dev/null 2>&1 || die 'Docker is unavailable; start Docker Desktop or Docker Engine'
export KIND_EXPERIMENTAL_PROVIDER=docker

cleanup() {
  local pid
  # Bash 3.2 treats expansion of an empty array as unset under set -u.
  [ "${#port_pids[@]}" -gt 0 ] || return 0
  for pid in "${port_pids[@]}"; do
    kill "$pid" 2>/dev/null || true
  done
  for pid in "${port_pids[@]}"; do
    wait "$pid" 2>/dev/null || true
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

log "Preparing kind cluster: $cluster"
clusters=$(kind get clusters)
if ! printf '%s\n' "$clusters" | grep -Fxq -- "$cluster"; then
  kind create cluster --name "$cluster" --image kindest/node:v1.37.0 --wait 120s
else
  log 'Reusing existing cluster'
fi
kind export kubeconfig --name "$cluster"
kubectl --context "$context" wait --for=condition=Ready nodes --all --timeout=120s
node_arch=$(kubectl --context "$context" get nodes -o jsonpath='{.items[0].status.nodeInfo.architecture}')
case "$node_arch" in
  amd64|arm64) ;;
  *) die "Unsupported node architecture: $node_arch" ;;
esac

log 'Preparing API image'
if [ "$skip_build" = false ]; then
  docker build --platform "linux/$node_arch" -t lab-api:0.1 "$repo_dir/api"
fi
image_arch=$(docker image inspect --format '{{.Architecture}}' lab-api:0.1) || die 'No local lab-api:0.1; run without --skip-build'
[ "$image_arch" = "$node_arch" ] || die "Image architecture $image_arch differs from node $node_arch; rebuild the image"
image_id=$(docker image inspect --format '{{.Id}}' lab-api:0.1) || die 'No local lab-api:0.1; run without --skip-build'
[[ "$image_id" =~ ^sha256:[a-f0-9]{64}$ ]] || die "Unexpected Docker image ID: $image_id"
# Content-based tags make Deployment roll out when the helper code changes.
api_image="lab-api:${image_id:7:12}"
docker tag lab-api:0.1 "$api_image"
kind load docker-image "$api_image" --name "$cluster"

log 'Adding Helm repositories'
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts --force-update
helm repo add grafana-community https://grafana-community.github.io/helm-charts --force-update
helm repo add grafana https://grafana.github.io/helm-charts --force-update
helm repo add jaegertracing https://jaegertracing.github.io/helm-charts --force-update
helm repo add wiremind https://wiremind.github.io/wiremind-helm-charts --force-update
helm repo update prometheus-community grafana-community grafana jaegertracing wiremind

helm_args=(--kube-context "$context" --namespace "$namespace" --create-namespace --wait --timeout 10m)
install_chart() {
  local release=$1 chart=$2 version=$3 values=$4
  log "Installing $release ($version)"
  helm upgrade --install "$release" "$chart" --version "$version" \
    "${helm_args[@]}" -f "$values"
}

helm lint "$repo_dir/helm/api" "$repo_dir/helm/webhook"
helm upgrade --install alert-webhook "$repo_dir/helm/webhook" "${helm_args[@]}"
install_chart blackbox prometheus-community/prometheus-blackbox-exporter 11.20.0 "$repo_dir/helm/blackbox-values.yaml"
install_chart loki grafana-community/loki 18.15.0 "$repo_dir/helm/loki-values.yaml"
install_chart jaeger jaegertracing/jaeger 4.14.1 "$repo_dir/helm/jaeger-values.yaml"
helm upgrade --install api "$repo_dir/helm/api" "${helm_args[@]}" --set-string "image=$api_image"
install_chart alloy grafana/alloy 1.13.1 "$repo_dir/helm/alloy-values.yaml"
install_chart prometheus prometheus-community/prometheus 29.35.0 "$prometheus_values"
log 'Installing Grafana with the portable API RED dashboard'
helm upgrade --install grafana grafana-community/grafana --version 13.3.1 \
  "${helm_args[@]}" -f "$repo_dir/helm/grafana-values.yaml" \
  -f "$repo_dir/helm/grafana-dashboard-values.yaml" \
  --set-file "dashboards.default.api-red.json=$repo_dir/dashboards/api-red.json"
install_chart karma wiremind/karma 2.13.1 "$repo_dir/helm/karma-values.yaml"

log 'Checking API, Loki and the deployed resources'
kubectl --context "$context" -n "$namespace" exec deployment/api -- wget -qO- http://api:8080/health
kubectl --context "$context" -n "$namespace" exec deployment/api -- wget -qO- http://loki:3100/ready
kubectl --context "$context" -n "$namespace" get pods,pvc
helm list --kube-context "$context" -n "$namespace"
printf '\nGrafana login: admin. To read its password locally:\n'
printf "kubectl --context '%s' -n observability get secret grafana -o jsonpath='{.data.admin-password}' | base64 --decode\n" "$context"

if [ "$open_ports" = false ]; then
  log 'Installation finished. See scripts/README.md for port-forward commands.'
  exit 0
fi

run_dir=$(mktemp -d "${TMPDIR:-/tmp}/observability-ports.XXXXXX")
forward() {
  local service=$1 mapping=$2 logfile="$run_dir/$1.log" pid attempt
  kubectl --context "$context" -n "$namespace" port-forward \
    --address 127.0.0.1 "service/$service" "$mapping" >"$logfile" 2>&1 &
  pid=$!
  port_pids+=("$pid")
  for attempt in $(seq 1 40); do
    if ! kill -0 "$pid" 2>/dev/null; then
      cat "$logfile" >&2
      die "port-forward failed for $service; free the local port and retry"
    fi
    if grep -q 'Forwarding from 127.0.0.1:' "$logfile"; then
      return 0
    fi
    sleep 0.25
  done
  cat "$logfile" >&2
  die "port-forward timed out for $service"
}

log "Opening interfaces (forward logs: $run_dir)"
forward api 18081:8080
forward prometheus-server 19090:80
forward grafana 13000:80
forward jaeger 17686:16686
forward prometheus-alertmanager 19093:9093
forward karma 18080:80
cat <<'URLS'

API:          http://localhost:18081/health
Prometheus:   http://localhost:19090
Grafana RED:  http://localhost:13000/d/api-red-portable
Jaeger:       http://localhost:17686
Alertmanager: http://localhost:19093
Karma:        http://localhost:18080

Keep this terminal open. Ctrl+C closes only its port-forward processes.
URLS
while :; do
  for pid in "${port_pids[@]}"; do
    kill -0 "$pid" 2>/dev/null || die "A port-forward stopped; inspect $run_dir and rerun the script"
  done
  sleep 2
done
