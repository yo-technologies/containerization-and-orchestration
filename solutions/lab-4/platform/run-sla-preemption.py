#!/usr/bin/env python3
"""Observe Pending and preemption during a separate, already running k6 test.

Prepare one idle batch first using scheduling-pressure + sla-preemption +
scheduling-idle values. This runner restores ordinary chart values afterwards.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import signal
import time

LAB = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("memory_runner", LAB / "platform/run-memory-eviction.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
PRESSURE = ["-f", str(LAB / "experiments/scheduling-pressure.yaml"),
            "-f", str(LAB / "experiments/sla-preemption.yaml")]


def main(output):
    if output.exists():
        raise FileExistsError(f"Evidence already exists: {output}")
    result = {"started_at": datetime.now(timezone.utc).isoformat(), "samples": [], "events": [], "cleanup_errors": []}

    def sample(phase):
        s = runner.snapshot(phase)
        result["samples"].append(s)
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
        return s

    before = sample("baseline")
    api = [p for p in before["pods"] if p["role"] == "api" and not p["deleting"]]
    assert len(api) == 3 and all(p["ready"] for p in api)
    initial_events = runner.kubectl(["-n", "shop", "get", "events", "--field-selector=reason=Preempted", "-o", "json"])
    old_events = {e["metadata"]["uid"] for e in initial_events["items"]}
    failure = None
    try:
        print("Baseline traffic: 20 seconds.", flush=True)
        for _ in range(10):
            time.sleep(2)
            sample("baseline")
        result["pressure_applied_at"] = runner.now()
        result["pressure_helm"] = runner.helm(PRESSURE)
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            s = sample("reserved-cpu")
            batch = [p for p in s["pods"] if p["role"] == "batch" and not p["deleting"]]
            pending = [p for p in batch if p["phase"] == "Pending" and not p["node"]]
            running = [p for p in batch if p["phase"] == "Running" and p["ready"]]
            if pending and len(pending) + len(running) == 40:
                print(f"Reserved CPU: {len(running)} batch Ready, {len(pending)} Pending.", flush=True)
                result["batch_before_preemption"] = {"ready": len(running), "pending": len(pending)}
                break
            time.sleep(2)
        else:
            raise RuntimeError("Could not observe stable Pending batch within 90 seconds")
        result["preemption_applied_at"] = runner.now()
        result["preemption_helm"] = runner.helm(PRESSURE + ["-f", str(LAB / "experiments/preemption.yaml")])
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            s = sample("preemption")
            api = [p for p in s["pods"] if p["role"] == "api" and not p["deleting"]]
            events = runner.kubectl(["-n", "shop", "get", "events", "--field-selector=reason=Preempted", "-o", "json"])
            api_uids = {p["uid"] for p in api}
            new_events = [e for e in events["items"] if e["metadata"]["uid"] not in old_events
                          and (e.get("involvedObject") or e.get("regarding") or {}).get("name", "").startswith("shop-batch-")
                          and any(uid in e.get("message", e.get("note", "")) for uid in api_uids)]
            result["events"] = new_events
            if len(api) == 7 and all(p["ready"] for p in api) and new_events:
                print(f"Seven API Ready; {len(new_events)} matched preemption events.", flush=True)
                break
            time.sleep(2)
        else:
            raise RuntimeError("Did not observe seven Ready API and a matched preemption event")
        for _ in range(15):
            time.sleep(2)
            sample("after-preemption")
    except BaseException as error:
        failure = error
        result["error"] = repr(error)
    finally:
        print("Restoring normal replicas and chart defaults.", flush=True)
        try:
            result["restore_helm"] = runner.helm(["--wait", "--timeout", "5m"], timeout=330)
            sample("restored")
        except Exception as error:
            result["cleanup_errors"].append(repr(error))
        result["completed_at"] = runner.now()
        result["completed"] = failure is None and not result["cleanup_errors"]
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    print(json.dumps({"completed": result["completed"], "events": len(result["events"]),
                      "cleanup_errors": result["cleanup_errors"], "evidence": str(output)}, indent=2), flush=True)
    if failure:
        raise failure


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=LAB / "evidence/part-8-preemption.json")
    args = parser.parse_args()

    def terminate(*_):
        raise KeyboardInterrupt()

    signal.signal(signal.SIGTERM, terminate)
    main(args.output)
