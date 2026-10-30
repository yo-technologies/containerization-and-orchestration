#!/usr/bin/env python3
"""Read the live Lab 2 configuration and save migration inputs; no cluster writes."""
import argparse
import json
import os
from pathlib import Path
import subprocess

import yaml


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--kubeconfig", required=True)
    parser.add_argument("--context", required=True)
    parser.add_argument("--state-dir", required=True)
    args = parser.parse_args()
    kube = ["kubectl", "--kubeconfig", args.kubeconfig, "--context", args.context]
    helm = ["helm", "--kubeconfig", args.kubeconfig, "--kube-context", args.context]

    def run(cmd):
        return subprocess.run(cmd, check=True, text=True, capture_output=True).stdout

    def get(kind, name, namespace="observability", optional=False):
        cmd = kube + ["-n", namespace, "get", kind, name, "-o", "json"]
        if optional:
            cmd += ["--ignore-not-found"]
        raw = run(cmd).strip()
        return json.loads(raw) if raw else None

    cluster_uid = get("namespace", "kube-system", "default")["metadata"]["uid"]
    pvc = get("pvc", "prometheus-server")
    if pvc["status"]["phase"] != "Bound":
        raise ValueError("Prometheus PVC is not Bound")
    deployment = get("deployment", "prometheus-server")
    managed = get("prometheus", "prometheus", optional=True)
    if deployment["spec"].get("replicas", 1) != 1:
        raise ValueError("Expected one legacy Prometheus replica; inspect migration state")
    if managed and managed["spec"].get("replicas", 1) != 0:
        raise ValueError("Operator-managed Prometheus already enabled; do not migrate again")
    desired = yaml.safe_load((Path(__file__).parent / "prometheus/prometheus.yaml").read_text())
    images = [c["image"] for c in deployment["spec"]["template"]["spec"]["containers"]]
    if desired["spec"]["image"] not in images:
        raise ValueError("Prometheus image differs from the migration manifest")
    releases = json.loads(run(helm + ["list", "-n", "observability", "-o", "json"]))
    if not any(r["name"] == "prometheus" and r["chart"] == "prometheus-29.35.0"
               and r["status"] == "deployed" for r in releases):
        raise ValueError("Expected deployed prometheus chart 29.35.0")

    state = Path(args.state_dir).expanduser()
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    state.chmod(0o700)

    def write(name, text):
        fd = os.open(state / name, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as file:
            os.fchmod(file.fileno(), 0o600)
            file.write(text)

    snapshot_path = state / "before.json"
    if snapshot_path.exists():
        snapshot = json.loads(snapshot_path.read_text())
        if snapshot["cluster_uid"] != cluster_uid or snapshot["pvc_uid"] != pvc["metadata"]["uid"]:
            raise ValueError("Saved snapshot belongs to a different cluster or PVC")
    else:
        snapshot = {
            "cluster_uid": cluster_uid,
            "pvc_uid": pvc["metadata"]["uid"],
            "deployment": deployment,
            "configmap": get("configmap", "prometheus-server"),
            "values": json.loads(run(helm + ["get", "values", "prometheus", "-n", "observability", "-o", "json"])),
        }
        write("before.json", json.dumps(snapshot, indent=2))
    data = snapshot["configmap"]["data"]
    config = yaml.safe_load(data["prometheus.yml"])
    scrapes = config["scrape_configs"]
    groups = []
    for name, value in data.items():
        if name != "prometheus.yml":
            rule = yaml.safe_load(value)
            if isinstance(rule, dict):
                groups.extend(rule.get("groups", []))
    if not scrapes or not groups:
        raise ValueError("Expected existing scrape jobs and alert rules")
    write("scrapes.yaml", yaml.safe_dump(scrapes, sort_keys=False))
    write("lab2-rules.json", json.dumps({
        "apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
        "metadata": {"name": "lab2-existing", "namespace": "observability", "labels": {"monitoring": "lab2"}},
        "spec": {"groups": groups},
    }, indent=2))
    write("original-pvc-uid", snapshot["pvc_uid"] + "\n")
    print(f"Saved {len(scrapes)} scrape jobs and {sum(len(g['rules']) for g in groups)} rules")
    print("Configuration snapshot:", snapshot_path)


if __name__ == "__main__":
    try:
        main()
    except (subprocess.CalledProcessError, ValueError, KeyError, OSError) as error:
        raise SystemExit(str(error)) from error
