#!/usr/bin/env python3
"""Install the Lab 3 shop platform on an existing local Lab 2 kind cluster."""
import argparse
import base64
from contextlib import ExitStack, contextmanager
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent.parent


def run(args, capture=False, data=None, check=True, env=None):
    proc = subprocess.run(args, input=data, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          stderr=subprocess.PIPE if capture else None, env=env)
    if check and proc.returncode:
        if capture and proc.stderr:
            print(proc.stderr.strip(), file=sys.stderr)
        raise RuntimeError("Command failed: " + " ".join(args))
    return proc


def wait_for(description, action, timeout=180):
    deadline = time.monotonic() + timeout
    while True:
        result = action()
        if result:
            return result
        if time.monotonic() >= deadline:
            raise RuntimeError("Timed out: " + description)
        time.sleep(2)


class Setup:
    def __init__(self, cluster, work, yaml):
        self.cluster, self.work, self.yaml = cluster, work, yaml
        self.context = "kind-" + cluster
        self.kubeconfig = work / "kubeconfig"
        self.flags = ["--kubeconfig", str(self.kubeconfig), "--context", self.context]

    def k(self, *args, capture=True, data=None, check=True):
        return run(["kubectl", *self.flags, *args], capture, data, check)

    def get(self, kind, name, namespace="observability", optional=False):
        args = ["-n", namespace, "get", kind, name, "-o", "json"]
        if optional:
            args.append("--ignore-not-found")
        value = self.k(*args).stdout.strip()
        return json.loads(value) if value else None

    def helm(self, *args, capture=False):
        return run(["helm", *args, "--kubeconfig", str(self.kubeconfig),
                    "--kube-context", self.context], capture)

    def apply(self, obj):
        self.k("apply", "-f", "-", data=json.dumps(obj), capture=False)

    def preflight(self):
        for tool in ("docker", "kind", "kubectl", "helm"):
            if not shutil.which(tool):
                raise RuntimeError("Install required tool: " + tool)
        for name in ("api/Dockerfile", "helm/shop/Chart.yaml", "helm/shop/values-v2.yaml",
                     "helm/kyverno-values.yaml", "helm/cnpg-values.yaml",
                     "helm/prometheus-operator-values.yaml", "configs/kyverno/guardrails.yaml",
                     "configs/kyverno/valid.yaml", "configs/prometheus/prometheus.yaml",
                     "configs/prometheus/prometheus-rbac.yaml", "dashboards/shop.json"):
            if not (ROOT / name).is_file():
                raise RuntimeError("Missing " + name + "; copy the entire Lab 3 folder")
        self.invalid = sorted((ROOT / "configs/kyverno/invalid").glob("*.yaml"))
        if len(self.invalid) != 5:
            raise RuntimeError("Expected five invalid policy examples")
        run(["docker", "info"], capture=True)
        if self.cluster not in run(["kind", "get", "clusters"], capture=True).stdout.split():
            raise RuntimeError("No kind cluster " + self.cluster + "; install Lab 2 first")
        # Obtain this kind cluster's config without changing the user's current context.
        self.kubeconfig.write_text(run(["kind", "get", "kubeconfig", "--name", self.cluster], True).stdout)
        self.kubeconfig.chmod(0o600)
        self.k("wait", "--for=condition=Ready", "nodes", "--all", "--timeout=120s", capture=False)
        nodes = json.loads(self.k("get", "nodes", "-o", "json").stdout)["items"]
        architectures = {n["status"]["nodeInfo"]["architecture"] for n in nodes}
        if len(nodes) not in (1, 2) or len(architectures) != 1 or not architectures <= {"amd64", "arm64"}:
            raise RuntimeError("Labs 3–4 require 1–2 nodes with one supported architecture (amd64/arm64)")
        self.arch = architectures.pop()
        if len(nodes) == 1:
            print("NOTE: one node detected; the spreading experiment in Lab 4 needs a second node.")
        self.cluster_uid = self.get("namespace", "kube-system")["metadata"]["uid"]
        releases = json.loads(self.helm("list", "-n", "observability", "-o", "json", capture=True).stdout)
        release = next((r for r in releases if r["name"] == "prometheus" and r["status"] == "deployed"), None)
        if not release or not release["chart"].startswith("prometheus-"):
            raise RuntimeError("Expected the working Lab 2 prometheus Helm release in observability")
        self.legacy_version = release["chart"][len("prometheus-"):]
        for svc in ("grafana", "prometheus-server", "prometheus-alertmanager"):
            self.get("service", svc)
        self.k("-n", "observability", "rollout", "status", "deployment/grafana", "--timeout=180s", capture=False)
        self.legacy = self.get("deployment", "prometheus-server")
        self.prom = self.yaml.safe_load((ROOT / "configs/prometheus/prometheus.yaml").read_text())
        images = [c["image"] for c in self.legacy["spec"]["template"]["spec"]["containers"]]
        if self.prom["spec"]["image"] not in images:
            raise RuntimeError("Lab 2 Prometheus image differs from configs/prometheus/prometheus.yaml; align versions first")
        self.backup_dir = ROOT / ".local" / "setup-shop" / self.cluster_uid
        self.backup_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        pvc = self.get("pvc", "prometheus-server")
        backup = self.backup_dir / "prometheus-before.json"
        if backup.exists():
            self.before = json.loads(backup.read_text())
            if self.before["pvc_uid"] != pvc["metadata"]["uid"]:
                raise RuntimeError("Prometheus PVC changed; inspect the saved backup before continuing")
        else:
            self.before = {"pvc_uid": pvc["metadata"]["uid"],
                           "configmap": self.get("configmap", "prometheus-server"),
                           "values": json.loads(self.helm("get", "values", "prometheus", "-n", "observability", "-o", "json", capture=True).stdout)}
            temporary = backup.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.before, indent=2))
            temporary.chmod(0o600)
            temporary.replace(backup)
        config = self.yaml.safe_load(self.before["configmap"]["data"]["prometheus.yml"])
        self.scrapes = config["scrape_configs"]
        self.groups = []
        for key, value in self.before["configmap"]["data"].items():
            if key != "prometheus.yml":
                rule = self.yaml.safe_load(value)
                if isinstance(rule, dict):
                    self.groups.extend(rule.get("groups", []))
        if not self.groups:
            raise RuntimeError("No Lab 2 alert rules found in the original Prometheus ConfigMap")

    def build(self, skip):
        print("\nBuilding and loading shop images (" + self.arch + ")", flush=True)
        if not skip:
            endpoint = os.environ.get("DOCKER_HOST") or run(
                ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"], True).stdout.strip()
            config = self.work / "docker"
            config.mkdir()
            (config / "config.json").write_text('{"auths":{}}\n')
            plugins = Path(os.environ.get("DOCKER_CONFIG", str(Path.home() / ".docker"))) / "cli-plugins"
            if plugins.is_dir():
                (config / "cli-plugins").symlink_to(plugins)
            build_env = os.environ.copy()
            build_env.pop("DOCKER_CONTEXT", None)
            for version in ("0.1.0", "0.2.0"):
                run(["docker", "--config", str(config), "--host", endpoint, "build",
                     "--platform", "linux/" + self.arch, "--build-arg", "VERSION=" + version,
                     "-t", "ghcr.io/itmo-labs/shop:" + version, str(ROOT / "api")], env=build_env)
        images = ["ghcr.io/itmo-labs/shop:" + v for v in ("0.1.0", "0.2.0")]
        for image in images:
            arch = run(["docker", "image", "inspect", image, "--format", "{{.Architecture}}"], True).stdout.strip()
            if arch != self.arch:
                raise RuntimeError("Wrong image architecture for " + image + "; rerun without --skip-build")
        run(["kind", "load", "docker-image", *images, "--name", self.cluster])

    def platform(self):
        print("\nInstalling operators and guardrails", flush=True)
        charts = [("kyverno", "kyverno", "https://kyverno.github.io/kyverno/", "3.9.1", "kyverno", "helm/kyverno-values.yaml"),
                  ("cnpg", "cloudnative-pg", "https://cloudnative-pg.github.io/charts", "0.29.1", "cnpg-system", "helm/cnpg-values.yaml"),
                  ("monitoring-operator", "kube-prometheus-stack", "https://prometheus-community.github.io/helm-charts", "92.10.1", "observability", "helm/prometheus-operator-values.yaml")]
        for release, chart, repo, version, namespace, values in charts:
            self.helm("upgrade", "--install", release, chart, "--repo", repo, "--version", version,
                      "-n", namespace, "--create-namespace", "-f", str(ROOT / values), "--wait", "--timeout", "10m")
        self.k("wait", "--for=condition=Established", "crd/clusters.postgresql.cnpg.io",
               "crd/prometheuses.monitoring.coreos.com", "crd/servicemonitors.monitoring.coreos.com",
               "crd/prometheusrules.monitoring.coreos.com", "--timeout=120s", capture=False)
        self.apply({"apiVersion": "v1", "kind": "Namespace", "metadata": {"name": "shop"}})
        self.k("apply", "-f", str(ROOT / "configs/kyverno/guardrails.yaml"), capture=False)
        policies = list(self.yaml.safe_load_all((ROOT / "configs/kyverno/guardrails.yaml").read_text()))
        self.k("wait", "--for=condition=Ready", *["clusterpolicy/" + p["metadata"]["name"] for p in policies], "--timeout=120s", capture=False)
        self.k("apply", "--dry-run=server", "-f", str(ROOT / "configs/kyverno/valid.yaml"), capture=False)
        for file in self.invalid:
            proc = self.k("apply", "--dry-run=server", "-f", str(file), check=False)
            policy = "shop-" + re.sub(r"^\d+-", "", file.stem)
            if proc.returncode == 0 or policy + ":" not in proc.stderr:
                raise RuntimeError("Expected admission rejection by " + policy)
            print("PASS: " + policy + " rejected the invalid Pod")

    def migrate(self):
        print("\nConnecting the existing Prometheus to the operator", flush=True)
        self.apply({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "lab2-additional-scrapes", "namespace": "observability"},
                    "stringData": {"scrapes.yaml": self.yaml.safe_dump(self.scrapes)}})
        self.k("apply", "-f", str(ROOT / "configs/prometheus/prometheus-rbac.yaml"), capture=False)
        self.apply({"apiVersion": "monitoring.coreos.com/v1", "kind": "PrometheusRule",
                    "metadata": {"name": "lab2-existing", "namespace": "observability", "labels": {"monitoring": "lab2"}},
                    "spec": {"groups": self.groups}})
        managed = self.get("prometheus", "prometheus", optional=True)
        legacy_count = self.legacy["spec"].get("replicas", 1)
        if legacy_count and managed and managed["spec"].get("replicas", 1):
            raise RuntimeError("Both Prometheus workloads are enabled; inspect them before opening one TSDB twice")
        switched = False
        try:
            if legacy_count:
                switched = True
                self.legacy_scale(0)
                self.k("-n", "observability", "wait", "--for=delete", "pod", "-l",
                       "app.kubernetes.io/component=server,app.kubernetes.io/instance=prometheus", "--timeout=180s", capture=False)
            self.apply(self.prom)
            wait_for("operator StatefulSet", lambda: self.get("statefulset", "prometheus-prometheus", optional=True))
            self.k("-n", "observability", "rollout", "status", "statefulset/prometheus-prometheus", "--timeout=300s", capture=False)
            if self.get("pvc", "prometheus-server")["metadata"]["uid"] != self.before["pvc_uid"]:
                raise RuntimeError("Prometheus PVC UID changed")
        except BaseException:
            if switched:
                print("Migration failed; restoring the previous Prometheus Deployment", file=sys.stderr)
                if self.get("prometheus", "prometheus", optional=True):
                    self.k("-n", "observability", "patch", "prometheus", "prometheus", "--type=merge", "-p", '{"spec":{"replicas":0}}', capture=False)
                    if self.get("pod", "prometheus-prometheus-0", optional=True):
                        self.k("-n", "observability", "wait", "--for=delete", "pod/prometheus-prometheus-0", "--timeout=180s", capture=False)
                self.legacy_scale(legacy_count)
            raise

    def legacy_scale(self, replicas):
        self.helm("upgrade", "prometheus", "prometheus", "--repo", "https://prometheus-community.github.io/helm-charts",
                  "--version", self.legacy_version, "-n", "observability", "--reuse-values",
                  "--set", "server.replicaCount=" + str(replicas), "--wait", "--timeout", "5m")

    @contextmanager
    def forward(self, namespace, service, remote_port, local_port=0):
        if not local_port:
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                local_port = sock.getsockname()[1]
        logfile = self.work / (service + "-" + str(local_port) + ".log")
        with logfile.open("w") as output:
            proc = subprocess.Popen(["kubectl", *self.flags, "-n", namespace, "port-forward",
                                     "service/" + service, str(local_port) + ":" + str(remote_port),
                                     "--address", "127.0.0.1"], stdout=output, stderr=subprocess.STDOUT)
        try:
            def ready():
                if proc.poll() is not None:
                    raise RuntimeError("port-forward failed for " + service + ": " + logfile.read_text())
                return "Forwarding from 127.0.0.1:" in logfile.read_text()
            wait_for("port-forward " + service, ready, 30)
            yield "http://127.0.0.1:" + str(local_port), proc
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()

    def http(self, base, path, payload=None, auth=None):
        headers = {"Content-Type": "application/json"}
        if auth:
            headers["Authorization"] = "Basic " + auth
        request = urllib.request.Request(base + path, headers=headers,
                                         data=json.dumps(payload).encode() if payload is not None else None)
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)

    def verify(self, keep_ports):
        with ExitStack() as stack:
            api, api_proc = stack.enter_context(self.forward("shop", "shop-api", 8080, 18083 if keep_ports else 0))
            prom, prom_proc = stack.enter_context(self.forward("observability", "prometheus-server", 80, 19091 if keep_ports else 0))
            grafana, grafana_proc = stack.enter_context(self.forward("observability", "grafana", 80, 13001 if keep_ports else 0))
            def orders_ready():
                try:
                    return {"orders": self.http(api, "/orders")["orders"]}
                except urllib.error.HTTPError as error:
                    if error.code != 503:
                        raise
                    return None
            wait_for("application database initialization", orders_ready)
            order = self.http(api, "/order", {"item": "setup-check", "quantity": 1})
            wait_for("worker processes the test order", lambda: any(
                row["id"] == order["id"] and row["processed"] for row in self.http(api, "/orders")["orders"]))
            print("PASS: order " + str(order["id"]) + " created and processed")
            def targets_ready():
                targets = self.http(prom, "/api/v1/targets")["data"]["activeTargets"]
                targets = [t for t in targets if t["labels"].get("namespace") == "shop"]
                return len(targets) == 5 and all(t["health"] == "up" for t in targets)
            wait_for("five shop targets up", targets_ready)
            expected = {"ShopAPIUnavailable", "ShopDatabaseUnavailable", "ShopOrderBacklog"}
            wait_for("three loaded shop alerts", lambda: expected <= {
                rule.get("name") for group in self.http(prom, "/api/v1/rules")["data"]["groups"] for rule in group["rules"]})
            secret = self.get("secret", "grafana")["data"]
            user = base64.b64decode(secret["admin-user"]).decode()
            password = base64.b64decode(secret["admin-password"]).decode()
            auth = base64.b64encode((user + ":" + password).encode()).decode()
            dashboard = json.loads((ROOT / "dashboards/shop.json").read_text())
            result = self.http(grafana, "/api/dashboards/db", {"dashboard": dashboard, "overwrite": True}, auth)
            installed = self.http(grafana, "/api/dashboards/uid/" + dashboard["uid"], auth=auth)
            if result.get("status") != "success" or len(installed["dashboard"]["panels"]) != len(dashboard["panels"]):
                raise RuntimeError("Grafana dashboard import verification failed")
            print("PASS: five shop targets up, three alerts loaded, Grafana dashboard imported")
            self.k("-n", "shop", "get", "deploy,pods,svc,cluster", capture=False)
            print("\nLab 3 platform is ready for Lab 4. Chart: helm/shop; namespace: shop; context: " + self.context)
            if keep_ports:
                print("API: " + api + "/orders\nPrometheus: " + prom + "\nGrafana: " + grafana + "/d/itmo-shop")
                print("Keep this terminal open; Ctrl+C closes only these three forwards.", flush=True)
                while True:
                    if any(p.poll() is not None for p in (api_proc, prom_proc, grafana_proc)):
                        raise RuntimeError("A port-forward stopped; inspect its log in " + str(self.work))
                    time.sleep(2)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cluster", default="observability", help="existing kind cluster name (default: observability)")
    parser.add_argument("--skip-build", action="store_true", help="reuse both architecture-matching local shop images")
    parser.add_argument("--no-port-forward", action="store_true", help="finish after verification; do not keep interfaces open")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", args.cluster) or len(args.cluster) > 63:
        parser.error("invalid kind cluster name")
    try:
        import yaml
    except ImportError:
        parser.error("install PyYAML: python3 -m pip install PyYAML")
    os.chdir(ROOT)
    os.environ["KIND_EXPERIMENTAL_PROVIDER"] = "docker"
    with tempfile.TemporaryDirectory(prefix="shop-setup-") as work:
        setup = Setup(args.cluster, Path(work), yaml)
        print("Checking existing Lab 2 cluster: " + args.cluster, flush=True)
        setup.preflight()
        setup.build(args.skip_build)
        setup.platform()
        setup.migrate()
        print("\nInstalling shop", flush=True)
        setup.helm("upgrade", "--install", "shop", str(ROOT / "helm/shop"), "-n", "shop",
                   "-f", str(ROOT / "helm/shop/values-v2.yaml"), "--wait", "--timeout", "10m")
        setup.k("-n", "shop", "wait", "--for=condition=Ready", "cluster/shop-postgres", "--timeout=600s", capture=False)
        setup.verify(not args.no_port_forward)


def stop(*_):
    raise KeyboardInterrupt()


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop)
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped. Cluster resources and database data remain.")
        sys.exit(130)
    except (RuntimeError, subprocess.CalledProcessError, urllib.error.URLError, ValueError, KeyError, OSError) as error:
        print("ERROR: " + str(error), file=sys.stderr)
        sys.exit(1)
