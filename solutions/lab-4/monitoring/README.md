# Мониторинг shop

ServiceMonitor выбирает API и worker текущего Helm release, собирает `/metrics` каждые 15 секунд. Prometheus Operator выбирает ServiceMonitor и PrometheusRule по метке `monitoring: lab2` во всех namespace. Наличие объектов само по себе не подтверждает сбор: запросы и состояние правил нужно проверить в Prometheus.

В chart ровно три алерта, каждый требует непрерывного выполнения условия в течение минуты:

| Алерт | Условие | Зачем |
| --- | --- | --- |
| ShopAPIUnavailable | Ни один API не отвечает на scrape либо все его targets исчезли | Полная потеря API или его наблюдаемости требует внимания; `up` не заменяет бизнес-проверку заказа |
| ShopDatabaseUnavailable | Все worker сообщают об ошибке последней операции БД либо метрики отсутствуют | Показывает отказ зависимости на пути к заказам; не позволяет скрыть отказ исчезновением серии |
| ShopOrderBacklog | Общая очередь больше 10 заказов | Заказы принимаются, но фоновая обработка отстаёт; warning позволяет заметить это до большой очереди |

Backlog измеряется через `max`, поскольку worker экспортируют копии одной общей очереди БД. Порог 10 и выдержка 1m — учебные значения. Они вынесены в `monitoring.alerts` values. Эти три диагностических алерта не являются полной системой контроля SLA: сетевые ошибки и соответствие формату ответа проверяет k6.

Дашборд хранится в `helm/shop/dashboards/shop.json`, chart создаёт ConfigMap в namespace Grafana. В нём восемь панелей: p95 и ошибки с согласованными границами, частота запросов, очередь, готовность критических Pod, Pending/Running batch, MemoryPressure и состояния трёх алертов. Без трафика latency/доля ошибок могут не иметь данных. Отсутствие активных алертов также нормально.

`grafana-values.yaml` добавляет отдельный file provider и mount ConfigMap, сохраняя прежний provider ЛР2. Применять поверх установленной Grafana через `--reuse-values`, с той же версией chart 13.3.1. Установка shop должна идти первой, чтобы ConfigMap уже существовал.

```bash
export KUBECONFIG="$HOME/.local/state/container-lab/lab4.kubeconfig"
helm upgrade shop solutions/lab-4/helm/shop --kube-context kind-shop-lab4 \
  -n shop --reset-values --wait --timeout 5m
helm upgrade grafana grafana-community/grafana --version 13.3.1 \
  --kube-context kind-shop-lab4 -n observability --reuse-values \
  -f solutions/lab-4/monitoring/grafana-values.yaml --wait --timeout 5m
python3 solutions/lab-4/platform/run-alert-demo.py
```

Последняя команда намеренно применяет `api.replicas: 0` через Helm. Прямое уменьшение Deployment не проходит через Eviction API, поэтому PDB его не остановит. Скрипт ждёт настоящий Firing, сохраняет ответ Prometheus, возвращает обычные значения в `finally` и проверяет исчезновение алерта. Результат — `evidence/part-9-alert-demo.json`; повтор требует другого `--output`, чтобы сохранить исходное доказательство.

Для браузера в отдельном терминале:

```bash
kubectl --kubeconfig "$HOME/.local/state/container-lab/lab4.kubeconfig" \
  --context kind-shop-lab4 -n observability port-forward svc/grafana 13000:80
```

Открыть `http://localhost:13000/d/shop-lab4`. Для снимка нагрузки части 8 выбрать интервал 30 октября 2026, 22:40–22:52 по Москве. Для алерта — окно запуска демонстрации. Сама панель строит состояния из метрики `ALERTS`, а JSON демонстрации сохраняет авторитетный ответ API Prometheus.
