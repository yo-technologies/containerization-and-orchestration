#!/usr/bin/env python3
"""Export a standalone SLA figure and its original Prometheus query responses."""
import argparse
from datetime import datetime, timezone
import json
import re
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.dates as dates
import matplotlib.pyplot as plt
import numpy as np

REQUESTS = 'shop_http_requests_total{namespace="shop",service="shop-api",route="/order",method="POST"}'
QUERIES = {
    "p95_ms": '1000 * histogram_quantile(0.95, sum by(le) (rate(shop_http_request_duration_seconds_bucket{namespace="shop",service="shop-api",route="/order"}[1m])))',
    "error_percent": '(sum(rate(shop_http_requests_total{namespace="shop",service="shop-api",route="/order",method="POST",code!="201"}[1m])) or vector(0)) / sum(rate(' + REQUESTS + '[1m])) * 100',
    "rps": 'sum(rate(' + REQUESTS + '[1m]))',
    "api_ready": 'sum(kube_pod_status_ready{namespace="shop",pod=~"shop-api-.*",condition="true"})',
    "postgres_ready": 'max(kube_pod_status_ready{namespace="shop",pod="shop-postgres-1",condition="true"})',
    "batch_pending": 'sum(kube_pod_status_phase{namespace="shop",pod=~"shop-batch-.*",phase="Pending"})',
    "worker_available_mib": '(max(machine_memory_bytes{job="kubernetes-nodes-cadvisor",instance="shop-lab4-worker"}) - max(container_memory_working_set_bytes{job="kubernetes-nodes-cadvisor",instance="shop-lab4-worker",id="/"})) / 1024 / 1024',
    "memory_pressure": 'max(kube_node_status_condition{node="shop-lab4-worker",condition="MemoryPressure",status="true"})',
}
TZ = ZoneInfo("Europe/Moscow")


def timestamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def series(response):
    rows = response.get("data", {}).get("result", [])
    if len(rows) != 1:
        raise ValueError(f"Expected one aggregated series, received {len(rows)}")
    values = rows[0]["values"]
    return [datetime.fromtimestamp(float(t), timezone.utc) for t, _ in values], np.array([float(v) for _, v in values])


def main(args):
    summary = json.loads(args.summary.read_text())
    experiment = json.loads(args.experiment.read_text())
    end = timestamp(summary["finished_at"])
    start = end - summary["summary"]["state"]["testRunDurationMs"] / 1000
    sources = {"summary": str(args.summary), "experiment": str(args.experiment),
               "start": start, "end": end, "step_seconds": 5, "queries": {}}
    for name, query in QUERIES.items():
        params = urllib.parse.urlencode({"query": query, "start": start - 15, "end": end + 15, "step": 5})
        url = args.prometheus.rstrip("/") + "/api/v1/query_range?" + params
        if args.kubeconfig:
            path = '/api/v1/namespaces/observability/services/http:prometheus-server:80/proxy/api/v1/query_range?' + params
            data = json.loads(subprocess.check_output(
                ['kubectl', '--kubeconfig', str(args.kubeconfig), '--context', 'kind-shop-lab4',
                 '--request-timeout=30s', 'get', '--raw', path], timeout=40))
        else:
            with urllib.request.urlopen(url, timeout=30) as response:
                data = json.load(response)
        if data.get("status") != "success":
            raise ValueError(data)
        sources["queries"][name] = {"promql": query, "response": data}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.with_suffix(".sources.json").write_text(json.dumps(sources, indent=2))
    lines = {name: series(data["response"]) for name, data in sources["queries"].items()}
    for name in ["p95_ms", "rps"]:
        if not np.isfinite(lines[name][1]).any():
            raise ValueError(f"No finite data for {name}; do not publish an empty graph")
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.spines.top": False,
                         "axes.spines.right": False, "axes.titleweight": "bold", "axes.grid": True,
                         "grid.alpha": 0.18, "svg.fonttype": "path"})
    fig, panels = plt.subplots(2, 2, figsize=(13.2, 8.2), constrained_layout=True)
    result = summary["result"]
    title = "Preemption" if args.kind == "preemption" else "Memory eviction"
    fig.suptitle(f"{title}: контроль SLA при 10 запросах/с\n"
                 f"k6: {result['requests']} запросов · ошибок {result['failed_order_fraction']*100:.2f}% · "
                 f"p95 {result['p95_ms']:.2f} мс · пропусков {result['dropped_iterations']}", fontsize=15)

    def plot(ax, key, label, color, step=False):
        x, y = lines[key]
        if step:
            ax.step(x, y, where="post", label=label, color=color, linewidth=2)
        else:
            ax.plot(x, y, label=label, color=color, linewidth=2)

    ax = panels[0, 0]
    plot(ax, "p95_ms", "p95 сервера, окно 1 мин", "#2563a5")
    ax.axhline(300, color="#b44b3a", linestyle="--", label="Граница SLA: 300 мс")
    ax.set(title="Latency POST /order", ylabel="Миллисекунды", ylim=(0, max(330, np.nanmax(lines['p95_ms'][1]) * 1.1)))
    ax.legend(loc="upper left", fontsize=9)
    ax = panels[0, 1]
    plot(ax, "error_percent", "HTTP ответы, отличные от 201", "#b44b3a")
    ax.axhline(0.5, color="#6b7280", linestyle="--", label="Граница SLA: 0,5%")
    ax.set(title="Доля ошибок на сервере, окно 1 мин", ylabel="Проценты", ylim=(-0.03, max(0.65, np.nanmax(lines['error_percent'][1]) * 1.2)))
    ax.legend(loc="upper left", fontsize=9)
    ax = panels[1, 0]
    if args.kind == "preemption":
        plot(ax, "batch_pending", "Batch Pending", "#b57918", step=True)
        plot(ax, "api_ready", "API Ready", "#23835f", step=True)
        ax.set(title="Нехватка requests и размещение API", ylabel="Количество Pod", ylim=(-0.5, max(18, np.nanmax(lines['batch_pending'][1]) + 2)))
    else:
        plot(ax, "worker_available_mib", "memory.available worker", "#2563a5")
        threshold = int(experiment["threshold"].removesuffix("Mi"))
        ax.axhline(threshold, color="#b44b3a", linestyle="--", label=f"Учебный порог: {threshold} MiB")
        for event in experiment.get("eviction_events", []):
            match = re.search(r"available: ([0-9]+)Ki", event.get("message", ""))
            value = event.get("eventTime") or event["metadata"].get("creationTimestamp")
            if match and value:
                ax.scatter(datetime.fromtimestamp(timestamp(value), timezone.utc), int(match[1]) / 1024,
                           color="#b44b3a", marker="x", s=55, zorder=5, label="available из события Evicted")
                break
        ax.set(title="Память worker и порог kubelet", ylabel="MiB")
    ax.legend(loc="best", fontsize=9)
    ax = panels[1, 1]
    plot(ax, "api_ready", "API Ready", "#23835f", step=True)
    plot(ax, "postgres_ready", "PostgreSQL Ready", "#8056a8", step=True)
    ax.set(title="Готовность критического пути", ylabel="Количество Pod", ylim=(-0.2, 7.7 if args.kind == "preemption" else 3.7))
    ax.legend(loc="best", fontsize=9)

    event_times = []
    events = experiment.get("events", []) if args.kind == "preemption" else experiment.get("eviction_events", [])
    for event in events:
        value = event.get("eventTime") or event["metadata"].get("creationTimestamp")
        if value:
            event_times.append(timestamp(value))
    event_at = min(event_times) if event_times else None
    for ax in panels.flat:
        ax.set_xlim(datetime.fromtimestamp(start - 15, timezone.utc), datetime.fromtimestamp(end + 15, timezone.utc))
        ax.xaxis.set_major_locator(dates.SecondLocator(bysecond=[0, 30]))
        ax.xaxis.set_major_formatter(dates.DateFormatter("%H:%M:%S", tz=TZ))
        ax.tick_params(axis="x", labelsize=8)
        ax.set_xlabel("Время, Москва (UTC+3)")
        if event_at:
            ax.axvline(datetime.fromtimestamp(event_at, timezone.utc), color="#df751e", alpha=0.8, linestyle=":", linewidth=1.5)
        if args.kind == "memory":
            begin = None
            for sample in experiment["samples"]:
                t = timestamp(sample["time"])
                if sample["conditions"]["MemoryPressure"] == "True" and begin is None:
                    begin = t
                elif sample["conditions"]["MemoryPressure"] != "True" and begin is not None:
                    ax.axvspan(datetime.fromtimestamp(begin, timezone.utc), datetime.fromtimestamp(t, timezone.utc), color="#c44333", alpha=0.09)
                    begin = None
    fig.supxlabel("Источник графиков: Prometheus. p95 по histogram — оценка; итог SLA проверяет клиент k6. "
                  "Оранжевый пунктир — событие вытеснения.", fontsize=9)
    fig.savefig(args.output.with_suffix(".png"), dpi=160)
    fig.savefig(args.output.with_suffix(".svg"))
    plt.close(fig)
    print(args.output.with_suffix(".png"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--kind", choices=["preemption", "memory"], required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prometheus", default="http://127.0.0.1:19092")
    parser.add_argument("--kubeconfig", type=Path, help="Read through Kubernetes Service proxy instead of port-forward")
    main(parser.parse_args())
