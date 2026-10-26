#!/usr/bin/env bash
# Part 5: start the API in six namespaces with resource and privilege limits.
set -euo pipefail

if [[ $(uname -s) != Linux ]]; then
  printf 'Run this script inside the Linux VM.\n' >&2
  exit 1
fi

# unshare starts this branch as PID 1 inside the new user/PID namespaces.
if [[ ${1:-} == --inside ]]; then
  if (( $# != 4 || $$ != 1 || EUID != 0 )); then
    printf 'The internal entry point requires PID 1 in the new user namespace.\n' >&2
    exit 1
  fi
  hostname lab-api
  ip link set lo up
  # Configure the environment before dropping the required capabilities.
  exec setpriv --bounding-set=-all --inh-caps=-all --ambient-caps=-all \
    --no-new-privs python3 "$3" "$4" "$2" -listen 127.0.0.1:18080
fi

if (( $# != 0 )); then
  printf 'Usage: bash mydocker.sh\n' >&2
  exit 1
fi
if (( EUID == 0 )); then
  printf 'Run as your ordinary VM user, without sudo on the whole script.\n' >&2
  exit 1
fi
if [[ $(stat -f -c %T /sys/fs/cgroup) != cgroup2fs ]]; then
  printf 'cgroup v2 is required.\n' >&2
  exit 1
fi

readonly lab_cgroup=/sys/fs/cgroup/lab1-mydocker
readonly lab_uid=$(id -u)
readonly lab_gid=$(id -g)
readonly lab_script=$(readlink -f -- "${BASH_SOURCE[0]}")
readonly lab_api=${LAB_API_BIN:-/tmp/lab1-api}
readonly lab_seccomp_run=${LAB_SECCOMP_RUN:-/tmp/lab1-seccomp-run.py}
readonly lab_seccomp_profile=${LAB_SECCOMP_PROFILE:-/tmp/lab1-seccomp-deny-getppid.json}

if [[ ! -x "$lab_api" || ! -r "$lab_seccomp_run" || ! -r "$lab_seccomp_profile" ]]; then
  printf 'API binary, seccomp loader or profile is missing.\n' >&2
  exit 1
fi

for controller in memory cpu pids; do
  if ! grep -qw "$controller" /sys/fs/cgroup/cgroup.subtree_control; then
    printf 'Controller not enabled for child groups: %s\n' "$controller" >&2
    exit 1
  fi
done

if [[ -e "$lab_cgroup" ]]; then
  if ! grep -qx 'populated 0' "$lab_cgroup/cgroup.events"; then
    printf 'Group is already populated; leave it unchanged: %s\n' "$lab_cgroup" >&2
    exit 1
  fi
else
  sudo mkdir "$lab_cgroup"
fi

printf '67108864\n' | sudo tee "$lab_cgroup/memory.max" >/dev/null
printf '0\n' | sudo tee "$lab_cgroup/memory.swap.max" >/dev/null
printf '50000 100000\n' | sudo tee "$lab_cgroup/cpu.max" >/dev/null
printf '32\n' | sudo tee "$lab_cgroup/pids.max" >/dev/null

printf 'Starting API in %s: memory=64MiB cpu=0.5 pids=32\n' "$lab_cgroup"

# Only this new child enters the group; the controlling terminal stays out.
# Drop back to the caller's host UID before creating the user namespace.
exec sudo bash --noprofile --norc -euc '
  printf "%s\n" "$$" > "$1/cgroup.procs"
  exec setpriv --reuid="$2" --regid="$3" --init-groups \
    unshare --user --map-root-user --pid --fork --kill-child --mount --mount-proc \
      --net --uts --ipc bash --noprofile --norc "$4" --inside "$5" "$6" "$7"
' lab1-mydocker "$lab_cgroup" "$lab_uid" "$lab_gid" "$lab_script" \
    "$lab_api" "$lab_seccomp_run" "$lab_seccomp_profile"
