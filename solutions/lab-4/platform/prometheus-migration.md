# Подключение существующего Prometheus к оператору

Манифесты в `prometheus/` взяты из архива ЛР3 Тимура. `migrate-prometheus.sh` повторяет только шаг миграции из его установщика; chart shop и приложения он не обновляет.

Оператор строит конфигурацию для ресурса Prometheus, выбирая ServiceMonitor и PrometheusRule по label `monitoring: lab2`. Установка CRD сама по себе не меняет конфигурацию прежнего Deployment.

Порядок перехода:

1. Сохранить ConfigMap, Helm values, описание Deployment и UID PVC в отдельный закрытый каталог вне Git. Это снимок конфигурации и метаданных; данные TSDB остаются на PVC.
2. Передать прежние scrape jobs через Secret, а правила ЛР2 — через PrometheusRule.
3. Через Helm установить прежнему Deployment ноль реплик и дождаться удаления его Pod.
4. Создать Prometheus CR с прежним образом и PVC. Дождаться готовности StatefulSet и сравнить UID PVC.

Во время перехода сбор метрик кратковременно прервётся. При ошибке или Ctrl+C после остановки старого Pod скрипт сначала останавливает управляемый оператором экземпляр, ждёт удаления Pod и только затем возвращает прежний Deployment. Если остановить новый экземпляр не удалось, старый автоматически не запускается, чтобы избежать двух писателей в одну TSDB.

Запуск из корня репозитория:

```bash
export KUBECONFIG="$HOME/.local/state/container-lab/lab4.kubeconfig"
bash solutions/lab-4/platform/migrate-prometheus.sh
```

Ключ `--check` только сохраняет входные данные, без изменений в кластере. Скрипту нужен Python с PyYAML. По умолчанию снимок сохраняется в `~/.local/state/container-lab/prometheus-shop-lab4/`; путь можно задать через `LAB4_PROMETHEUS_STATE_DIR`.

После перехода Service `prometheus-server`, Grafana и Alertmanager сохраняют прежние адреса. Обычный установщик ЛР2 пока не учитывает миграцию и при повторном запуске вернёт старый Deployment; повторять его на этом стенде не следует. Фактический сбор shop проверяется отдельно через Prometheus API.
