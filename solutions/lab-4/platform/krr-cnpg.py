#!/usr/bin/env python3
"""Add read-only CloudNativePG discovery to KRR v1.28.0.

KRR's metric loaders, strategy, rounding and formatters are unchanged.
The Cluster object is reported as Cluster, not as a synthetic StatefulSet.
Run with the same arguments as `krr simple`, including explicit namespace(s).
"""

import asyncio
from types import SimpleNamespace
from typing import Literal

from kubernetes.client import V1LabelSelector
from robusta_krr.core.integrations.kubernetes import ClusterLoader
from robusta_krr.core.models.allocations import ResourceAllocations
from robusta_krr.core.models.objects import K8sObjectData
from robusta_krr.main import run


class CNPGObject(K8sObjectData):
    kind: Literal["Cluster"] = "Cluster"

    @property
    def selector(self):
        return V1LabelSelector(match_labels={"cnpg.io/cluster": self.name})


original_discovery = ClusterLoader.list_scannable_objects


async def discover_with_cnpg(self):
    objects = await original_discovery(self)
    if self.namespaces == "*":
        raise ValueError("CNPG adapter requires an explicit namespace (-n shop)")

    loop = asyncio.get_running_loop()
    for namespace in self.namespaces:
        clusters = await loop.run_in_executor(
            self.executor,
            lambda ns=namespace: self.custom_objects.list_namespaced_custom_object(
                "postgresql.cnpg.io", "v1", ns, "clusters"
            ),
        )
        for cluster in clusters["items"]:
            meta = cluster["metadata"]
            pods = await loop.run_in_executor(
                self.executor,
                lambda ns=namespace, name=meta["name"]: self.core.list_namespaced_pod(
                    ns, label_selector=f"cnpg.io/cluster={name}"
                ),
            )
            containers = {}
            for pod in pods.items:
                # Check ownership as well as the label before reading allocations.
                if not any(
                    owner.kind == "Cluster" and owner.uid == meta["uid"]
                    for owner in pod.metadata.owner_references or []
                ):
                    continue
                for container in pod.spec.containers:
                    allocations = ResourceAllocations.from_container(container)
                    if container.name in containers and containers[container.name] != allocations:
                        raise ValueError("CNPG instances have differing allocations; analyze separately")
                    containers[container.name] = allocations
            if not containers:
                raise ValueError(f"No owned CNPG Pod found for {namespace}/{meta['name']}")
            for container, allocations in containers.items():
                obj = CNPGObject(
                    cluster=self.cluster,
                    namespace=namespace,
                    name=meta["name"],
                    container=container,
                    hpa=None,
                    allocations=allocations,
                    labels=meta.get("labels", {}),
                    annotations={},
                )
                # KRR's API fallback needs only the namespace and our selector.
                obj._api_resource = SimpleNamespace(metadata=SimpleNamespace(namespace=namespace))
                objects.append(obj)
    return objects


if __name__ == "__main__":
    ClusterLoader.list_scannable_objects = discover_with_cnpg
    run()
