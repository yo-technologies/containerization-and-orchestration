#!/usr/bin/env bash
# Current teaching experiment: retain 16 MiB, burn for 90s, then stop this API.
set -euo pipefail

group=/sys/fs/cgroup/lab1-mydocker
lab_pid=$(pgrep -u "$(id -u)" -x lab1-api || true)
if [[ ! "$lab_pid" =~ ^[0-9]+$ ]] ||
   [[ $(cat "/proc/$lab_pid/cgroup") != '0::/lab1-mydocker' ]] ||
   [[ $(cat "$group/memory.max") != 67108864 ]] ||
   [[ $(cat "$group/cpu.max") != '50000 100000' ]] ||
   (( $(cat "$group/memory.current") >= 33554432 )); then
  printf 'Expected one API in lab1-mydocker, 64MiB/0.5 CPU, current memory below 32MiB.\n' >&2
  exit 1
fi
lab_start=$(awk '{print $22}' "/proc/$lab_pid/stat")

request() {
  sudo nsenter -t "$lab_pid" --net \
    curl --noproxy '*' --max-time 5 -fsS "$1"
  printf '\n'
}

request http://127.0.0.1:18080/health

cleanup() {
  # Do not signal a replacement process if the original API has exited.
  if [[ -r /proc/$lab_pid/stat ]] &&
     [[ $(awk '{print $22}' "/proc/$lab_pid/stat") == "$lab_start" ]] &&
     [[ $(cat "/proc/$lab_pid/cgroup") == '0::/lab1-mydocker' ]]; then
    kill -TERM "$lab_pid" || true
    printf 'Stopped the measured API; exporter remains running.\n'
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

printf 'Before load:\n'
cat "$group/memory.current" "$group/cpu.stat"
request 'http://127.0.0.1:18080/eat?mb=16'
request http://127.0.0.1:18080/burn
printf 'Load running for 90 seconds. Watch the Grafana dashboard.\n'
sleep 90
printf 'After load (before stopping API):\n'
cat "$group/memory.current" "$group/cpu.stat" "$group/memory.events"
request http://127.0.0.1:18080/health
