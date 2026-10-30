# Сбор метрик после перехода Prometheus

29 октября 2026 года, кластер `shop-lab4`. Запросы выполнены к HTTP API существующего Service `prometheus-server` из Pod API ЛР2 в namespace `observability`.

```promql
sum by (service) (up{namespace="shop"})
```

Результат: `shop-api = 3`, `shop-worker = 2`.

```promql
sum by (job) (up{job=~"kubernetes-nodes.*"})
```

Результат: `kubernetes-nodes = 2`, `kubernetes-nodes-cadvisor = 2`.

```promql
count by (pod) (container_cpu_usage_seconds_total{namespace="shop",container!=""})
```

Получено по одному ряду для каждого из семи Pod shop: трёх API, двух worker, batch и PostgreSQL.

В `/api/v1/rules` загружены группы `api` и `shop`, по три правила. У всех `health=ok`, состояние `inactive`:

- APIHighErrorRate, APIHighLatency, APIUnavailable;
- ShopAPIUnavailable, ShopDatabaseUnavailable, ShopOrderBacklog.

Запрос `count(kube_pod_container_resource_requests{namespace="shop"})` пока возвращает пустой результат. Сбор метаданных requests/limits через kube-state-metrics ещё предстоит подключить перед krr.
