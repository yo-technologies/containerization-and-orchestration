#!/usr/bin/env python3
"""Run one bounded memory-eviction experiment and restore the lab afterwards."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import signal
import subprocess
import time


LAB = Path(__file__).resolve().parents[1]
module_spec = importlib.util.spec_from_file_location("memory_eviction", LAB / "platform/memory-eviction.py")
memory = importlib.util.module_from_spec(module_spec)
module_spec.loader.exec_module(memory)
HELM = ["helm", "upgrade", "shop", str(LAB / "helm/shop"), "--kubeconfig", memory.KUBE[2],
        "--kube-context", memory.CONTEXT, "-n", "shop", "--reset-values"]
PRESSURE = ["-f", str(LAB / "experiments/memory-pressure.yaml")]
STOP = ["-f", str(LAB / "experiments/memory-stop.yaml")]


def now():
    return datetime.now(timezone.utc).isoformat()


def helm(extra, timeout=90):
    result = subprocess.run(HELM + extra, text=True, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stdout)
    return result.stdout


def kubectl(args):
    return json.loads(memory.command(memory.KUBE + args))


def snapshot(phase):
    node = kubectl(["get", "node", memory.NODE, "-o", "json"])
    stats = memory.get(f"/api/v1/nodes/{memory.NODE}/proxy/stats/summary")
    pods = kubectl(["-n", "shop", "get", "pods", "-o", "json"])
    rows = []
    for p in pods["items"]:
        role = p["metadata"].get("labels", {}).get("app.kubernetes.io/name")
        if p["metadata"]["name"].startswith("shop-postgres-"):
            role = "postgres"
        rows.append({"name": p["metadata"]["name"], "uid": p["metadata"]["uid"], "role": role,
                     "node": p["spec"].get("nodeName"), "deleting": bool(p["metadata"].get("deletionTimestamp")),
                     "phase": p["status"].get("phase"), "reason": p["status"].get("reason"),
                     "message": p["status"].get("message"), "qos": p["status"].get("qosClass"),
                     "priority": p["spec"].get("priority", 0),
                     "ready": any(c["type"] == "Ready" and c["status"] == "True" for c in p["status"].get("conditions", [])),
                     "containers": [{"name": c["name"], "resources": c.get("resources")} for c in p["spec"]["containers"]],
                     "containerStatus": [{k: c.get(k) for k in ("name", "ready", "restartCount", "state", "lastState")}
                                         for c in p["status"].get("containerStatuses", [])]})
    return {"time": now(), "phase": phase, "conditions": {c["type"]: c["status"] for c in node["status"]["conditions"]},
            "node_memory": stats["node"].get("memory"), "pods": rows,
            "worker_pod_memory": [{"pod": p["podRef"], "memory": p.get("memory"),
                                   "containers": [{"name": c["name"], "memory": c.get("memory")} for c in p.get("containers", [])]}
                                  for p in stats.get("pods", []) if p["podRef"]["namespace"] == "shop"]}


def main(output, check=False):
    if output.exists():
        raise FileExistsError(f"Evidence already exists: {output}. Choose another --output for a new run.")
    state = json.loads((memory.STATE / "state.json").read_text())
    assert state["active"], "Run memory-eviction.py prepare first"
    assert state["container_id"] == memory.identity()
    assert hashlib.sha256(memory.read_config()).hexdigest() == state["prepared_sha256"]
    before = snapshot("before")
    threshold_bytes = int(state["threshold"].removesuffix("Mi")) * memory.MIB
    gap = before["node_memory"]["availableBytes"] - threshold_bytes
    assert 128 * memory.MIB < gap < 384 * memory.MIB, "Available memory changed: run restore, then prepare again"
    assert before["conditions"]["Ready"] == "True" and before["conditions"]["MemoryPressure"] == "False"
    critical = [p for p in before["pods"] if p["role"] in ("api", "postgres") and not p["deleting"]]
    assert len(critical) == 4 and all(p["ready"] and p["qos"] == "Guaranteed" for p in critical)
    if check:
        print(json.dumps({"threshold": state["threshold"], "available_Mi": round(before["node_memory"]["availableBytes"] / memory.MIB, 1),
                          "gap_Mi": round(gap / memory.MIB, 1), "critical_ready": len(critical),
                          "conditions": before["conditions"], "load_started": False}, indent=2))
        return
    baseline_uids = {p["uid"] for p in before["pods"]}
    result = {"started_at": now(), "threshold": state["threshold"], "samples": [before], "victim": None,
              "completed": False, "cleanup_errors": []}
    output.parent.mkdir(parents=True, exist_ok=True)

    def save():
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))

    save()
    failure = None
    try:
        print(f"Applying 512 MiB batch buffer on {memory.NODE}; threshold {state['threshold']}.", flush=True)
        result["pressure_helm"] = helm(PRESSURE)
        deadline = time.monotonic() + 120
        last = None
        while time.monotonic() < deadline:
            sample = snapshot("pressure")
            result["samples"].append(sample)
            batch = [p for p in sample["pods"] if p["role"] == "batch" and p["uid"] not in baseline_uids]
            events = kubectl(["-n", "shop", "get", "events", "--field-selector=reason=Evicted", "-o", "json"])
            memory_events = {}
            for event in events["items"]:
                reference = event.get("involvedObject") or event.get("regarding") or {}
                if "memory" in event.get("message", event.get("note", "")).lower():
                    memory_events.setdefault(reference.get("uid"), []).append(event)
            display = (sample["conditions"]["MemoryPressure"], [(p["name"], p["phase"], p["reason"]) for p in batch])
            if display != last:
                print(f"MemoryPressure={display[0]}; batch={display[1]}", flush=True)
                last = display
            victims = [p for p in batch if p["uid"] in memory_events
                       or (p["reason"] == "Evicted" and "memory" in (p["message"] or "").lower())]
            save()
            if victims:
                result["victim"] = victims[0]
                result["eviction_events"] = memory_events.get(victims[0]["uid"], [])
                result["eviction_message"] = (result["eviction_events"][0].get("message")
                                              if result["eviction_events"] else victims[0]["message"])
                print(f"Evicted {victims[0]['name']}: {result['eviction_message']}", flush=True)
                break
            if any(c.get("state", {}).get("terminated", {}).get("reason") == "OOMKilled"
                   or c.get("lastState", {}).get("terminated", {}).get("reason") == "OOMKilled"
                   for p in batch for c in p["containerStatus"]):
                raise RuntimeError("Observed OOMKilled, not the intended memory eviction")
            time.sleep(2)
        if result["victim"] is None:
            raise RuntimeError("No memory eviction observed within 120 seconds")
    except BaseException as error:
        failure = error
        result["error"] = repr(error)
    finally:
        print("Stopping batch pressure and collecting recovery observations.", flush=True)
        try:
            result["stop_helm"] = helm(PRESSURE + STOP)
            for _ in range(10):
                time.sleep(2)
                result["samples"].append(snapshot("after-stop"))
                save()
        except Exception as error:
            result["cleanup_errors"].append(repr(error))
        try:
            print("Restoring normal Helm values.", flush=True)
            result["restore_helm"] = helm(["--wait", "--timeout", "5m"], timeout=330)
        except Exception as error:
            result["cleanup_errors"].append(repr(error))
        try:
            print("Restoring original worker kubelet configuration.", flush=True)
            memory.main("restore")
        except Exception as error:
            result["cleanup_errors"].append(repr(error))
        result["completed_at"] = now()
        result["saw_memory_pressure"] = any(s["conditions"]["MemoryPressure"] == "True" for s in result["samples"])
        try:
            restored = snapshot("restored")
            result["samples"].append(restored)
            unready = [p["name"] for p in restored["pods"]
                       if p["role"] in ("api", "postgres") and not p["deleting"] and not p["ready"]]
            if unready:
                result["cleanup_errors"].append(f"Critical Pods not Ready after restore: {unready}")
        except Exception as error:
            result["cleanup_errors"].append(repr(error))
        result["completed"] = failure is None and not result["cleanup_errors"] and result["saw_memory_pressure"]
        original_critical = {p["uid"] for p in critical}
        result["critical_ready_all_samples"] = all(
            original_critical == {p["uid"] for p in s["pods"] if p["role"] in ("api", "postgres") and not p["deleting"]}
            and all(p["ready"] for p in s["pods"] if p["uid"] in original_critical)
            for s in result["samples"])
        save()
    print(json.dumps({"completed": result["completed"], "saw_memory_pressure": result["saw_memory_pressure"],
                      "critical_ready_all_samples": result["critical_ready_all_samples"],
                      "victim": (result["victim"] or {}).get("name"), "cleanup_errors": result["cleanup_errors"],
                      "evidence": str(output)}, indent=2), flush=True)
    if failure is not None:
        raise failure
    if not result["completed"]:
        raise RuntimeError("Experiment or cleanup incomplete; inspect the saved evidence")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=LAB / "evidence/part-7-eviction.json")
    parser.add_argument("--check", action="store_true", help="Read-only preflight; do not apply workload or restore configuration")
    args = parser.parse_args()

    def terminate(*_):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, terminate)
    main(args.output, args.check)
