#!/usr/bin/env python3
"""Prepare/restore one kind worker's memory eviction threshold for Lab 4.

This changes only kubelet configuration; workload resources remain in Helm.
No memory load is started here. Backups are stored outside the repository.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

import yaml

NODE = "shop-lab4-worker"
CONTEXT = "kind-shop-lab4"
CONFIG = "/var/lib/kubelet/config.yaml"
STATE = Path.home() / ".local/state/container-lab/lab4-memory-eviction"
KUBE = ["kubectl", "--kubeconfig", str(Path.home() / ".local/state/container-lab/lab4.kubeconfig"),
        "--context", CONTEXT, "--request-timeout=5s"]
DOCKER = ["docker", "--context", "desktop-linux"]
MIB = 1024 * 1024


def command(args, data=None):
    return subprocess.check_output(args, input=data, timeout=20)


def get(path):
    return json.loads(command(KUBE + ["get", "--raw", path]))


def identity():
    info = json.loads(command(DOCKER + ["inspect", NODE]))[0]
    assert info["Config"]["Labels"].get("io.x-k8s.kind.cluster") == "shop-lab4"
    assert info["State"]["Running"], "Worker must be running"
    return info["Id"]


def read_config():
    return command(DOCKER + ["exec", NODE, "cat", CONFIG])


def install(data):
    command(DOCKER + ["exec", "-i", NODE, "sh", "-c", f"cat > {CONFIG}"], data)
    command(DOCKER + ["exec", NODE, "systemctl", "restart", "kubelet"])


def check_threshold(expected):
    for _ in range(15):
        try:
            config = get(f"/api/v1/nodes/{NODE}/proxy/configz")["kubeletconfig"]
            if config.get("evictionHard", {}).get("memory.available") == expected:
                return
        except (subprocess.SubprocessError, KeyError, ValueError):
            pass
        time.sleep(2)
    raise RuntimeError("Kubelet did not report the expected memory threshold")


def status():
    config = get(f"/api/v1/nodes/{NODE}/proxy/configz")["kubeletconfig"]
    memory = get(f"/api/v1/nodes/{NODE}/proxy/stats/summary")["node"]["memory"]
    node = json.loads(command(KUBE + ["get", "node", NODE, "-o", "json"]))
    result = {"node": NODE, "memory_available_Mi": round(memory["availableBytes"] / MIB, 1),
              "evictionHard": config.get("evictionHard"),
              "pressure_transition": config.get("evictionPressureTransitionPeriod"),
              "conditions": {x["type"]: x["status"] for x in node["status"]["conditions"]}}
    print(json.dumps(result, indent=2), flush=True)


def main(action):
    container_id = identity()
    manifest = STATE / "state.json"
    if action == "status":
        status()
        return
    if action == "restore":
        state = json.loads(manifest.read_text())
        assert state["container_id"] == container_id, "Worker was recreated; do not restore stale config"
        original = (STATE / "original.yaml").read_bytes()
        assert hashlib.sha256(original).hexdigest() == state["original_sha256"]
        install(original)
        check_threshold(yaml.safe_load(original).get("evictionHard", {}).get("memory.available"))
        state["active"] = False
        manifest.write_text(json.dumps(state, indent=2))
        status()
        return
    if manifest.exists():
        state = json.loads(manifest.read_text())
        if state.get("active"):
            assert state["container_id"] == container_id
            assert hashlib.sha256(read_config()).hexdigest() == state["prepared_sha256"], "Config changed since preparation"
            print("Preparation already active; no config changes.", flush=True)
            status()
            return

    original = read_config()
    config = yaml.safe_load(original)
    memory = get(f"/api/v1/nodes/{NODE}/proxy/stats/summary")["node"]["memory"]
    available = memory["availableBytes"]
    assert available > 2 * 1024 * MIB, "Less than 2 GiB available; do not start this experiment"
    # Reserve a 256-320 MiB gap below current availability. A 512 MiB batch
    # buffer should cross it while leaving substantial RAM in the shared VM.
    threshold = f"{((available // MIB - 256) // 64) * 64}Mi"
    config.setdefault("evictionHard", {})["memory.available"] = threshold
    config["evictionPressureTransitionPeriod"] = "15s"
    prepared = yaml.safe_dump(config, sort_keys=False).encode()
    STATE.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE, 0o700)
    (STATE / "original.yaml").write_bytes(original)
    os.chmod(STATE / "original.yaml", 0o600)
    state = {"container_id": container_id, "active": True,
             "original_sha256": hashlib.sha256(original).hexdigest(),
             "prepared_sha256": hashlib.sha256(prepared).hexdigest(),
             "before_available_bytes": available, "threshold": threshold}
    manifest.write_text(json.dumps(state, indent=2))
    os.chmod(manifest, 0o600)
    try:
        install(prepared)
        check_threshold(threshold)
        after = get(f"/api/v1/nodes/{NODE}/proxy/stats/summary")["node"]["memory"]
        threshold_bytes = int(threshold.removesuffix("Mi")) * MIB
        assert after["availableBytes"] > threshold_bytes + 128 * MIB, "Memory changed during preparation; restoring config"
    except Exception:
        install(original)
        state["active"] = False
        manifest.write_text(json.dumps(state, indent=2))
        raise
    print(f"Prepared {NODE}: memory.available < {threshold}; no load started.", flush=True)
    status()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "status", "restore"])
    main(parser.parse_args().action)
