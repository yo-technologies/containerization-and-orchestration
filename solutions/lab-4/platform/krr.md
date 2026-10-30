# Запуск krr для shop

Использована версия исходников [krr v1.28.0](https://github.com/robusta-dev/krr/tree/v1.28.0). Python-пакет этого тега имеет внутреннюю версию `1.8.2-dev`, а CLI из архива пишет `dev`: версию запуска фиксируем по тегу и SHA256 исходного архива, а не по этой строке.

Источник: `https://codeload.github.com/robusta-dev/krr/tar.gz/refs/tags/v1.28.0`.
SHA256: `ed3b3e2fd61e6cb395deaff442600dead22732f87669a95422d878fce87224c6`.

На стенде krr установлен в отдельном Python 3.12.6 venv. К зависимостям пакета добавлен `cachetools==5.5.0` (используется кодом, но отсутствует в pyproject), Click закреплён `<8.2` для совместимости с Typer 0.7. Пример воспроизведения установки через uv:

```bash
krr_env="$HOME/.local/state/container-lab/krr-1.28.0"
uv venv --python 3.12.6 "$krr_env"
uv pip install --python "$krr_env/bin/python" \
  'robusta-krr @ https://codeload.github.com/robusta-dev/krr/tar.gz/refs/tags/v1.28.0' \
  'cachetools==5.5.0' 'click<8.2'
```

Из отдельного терминала открыть Prometheus:

```bash
kubectl --kubeconfig "$HOME/.local/state/container-lab/lab4.kubeconfig" \
  --context kind-shop-lab4 -n observability \
  port-forward svc/prometheus-server 19091:80
```

Обычный запуск обнаруживает три Deployment, но пропускает PostgreSQL: CloudNativePG управляет Pod через ресурс `Cluster`, без StatefulSet. Сохранён отдельно как `evidence/part-3-krr-native.json`.

Адаптер [krr-cnpg.py](krr-cnpg.py) добавляет только обнаружение CNPG Cluster: читает Cluster и принадлежащие ему Pod, проверяет owner UID и передаёт реальные allocations контейнера. Тип в отчёте остаётся `Cluster`. История владельцев Pod, метрики, алгоритм `simple`, округление и нижние границы — штатные krr; записей в Kubernetes адаптер не делает. Рассчитан на этот стенд с одинаковыми ресурсами всех экземпляров Cluster; при разных allocations останавливается.

Из корня репозитория:

```bash
krr_env="$HOME/.local/state/container-lab/krr-1.28.0"
MPLCONFIGDIR="$krr_env/mpl-cache" XDG_CACHE_HOME="$krr_env/cache" \
  "$krr_env/bin/python" solutions/lab-4/platform/krr-cnpg.py simple \
  --kubeconfig "$HOME/.local/state/container-lab/lab4.kubeconfig" \
  -c kind-shop-lab4 -n shop -r Deployment \
  -p http://127.0.0.1:19091 \
  --history_duration 1 --timeframe_duration 0.5 \
  -f json --fileoutput solutions/lab-4/evidence/krr-rerun.json
```

`-r Deployment` ограничивает штатное обнаружение; адаптер дополнительно включает CNPG Cluster в явно указанных namespace. Итоговый исходный запуск сохранён как `part-3-krr-cnpg.json`; новое выполнение пишет в другой файл, потому что окно истории сдвинется.

Параметры запуска: один час истории (минимум, разрешённый этой версией), шаг 30 секунд, CPU p95, пик памяти +15%, минимум 100 точек, нижние границы CPU 10m и памяти 100 MiB. Запуск с адаптером выполнен 29 октября около 15:32 UTC: окно содержит все 15 минут нагрузки и периоды простоя рядом с ней. Это не расчёт только по нагруженным 15 минутам.

Метрики каждого анализируемого объекта найдены: нет предупреждений `NoPrometheusPods`, `NoPrometheusCPUMetrics`, `NoPrometheusMemoryMetrics`, все рекомендации числовые. Сводка ресурсов всего кластера пуста: scoped kube-state-metrics экспортирует `shop`, а запрос krr для общекластерной сводки ожидает requests `kube-system`. На индивидуальные рекомендации shop это не влияет; её пустое значение не используем как оценку свободных ресурсов.

Результаты не применяются автоматически. Стратегия `simple` предлагает пустой CPU limit, а политики ЛР3 требуют его наличия. Выбор обязательного limit и изменение значений через Helm — отдельный шаг. Batch измерен, но его параметры не переносим автоматически из рекомендаций: это управляемый источник фоновой нагрузки.
