# Запуск стека для команды

Скрипт собирает уже настроенный в лабораторной № 2 стек из файлов `helm/`. Нужно передать товарищу всю директорию лабораторной, включая `api/`, `helm/`, `dashboards/` и `scripts/`.

## Требования и запуск

Нужны Bash 3.2+, работающий Docker Engine или Docker Desktop, `kind`, `kubectl`, Helm 3+ и доступ в интернет для загрузки charts, базовых образов и Go dependencies. Go на компьютере не требуется: сервис собирается внутри Docker. На Windows можно запускать из WSL с настроенным доступом к Docker.

Из корня лабораторной:

```bash
bash scripts/setup-observability.sh
```

Скрипт создаёт kind `observability`, если его ещё нет, и использует существующий кластер при повторном запуске. Новая нода создаётся из `kindest/node:v1.37.0`. Namespace и имена релизов фиксированы: `observability`, `api`, `loki`, `jaeger` и остальные имена из текущих values.

Образ API собирается для архитектуры ноды (`amd64` или `arm64`), загружается в kind и получает тег на основе image ID. Изменение образа меняет Deployment, а повторный запуск с тем же образом не требует принудительного рестарта API.

Затем Helm устанавливает webhook, blackbox, Loki, Jaeger, API, Alloy, Prometheus/Alertmanager, Grafana и Karma. Версии charts закреплены. Проверяются готовность релизов, `/health` API и `/ready` Loki; это ещё не проверка всех возможных ошибок, логов и трасс.

После установки открываются шесть port-forward на `127.0.0.1`:

| Интерфейс | Адрес |
| --- | --- |
| API | http://localhost:18081/health |
| Prometheus | http://localhost:19090 |
| Grafana, дашборд API RED (portable) | http://localhost:13000/d/api-red-portable |
| Jaeger | http://localhost:17686 |
| Alertmanager | http://localhost:19093 |
| Karma | http://localhost:18080 |

Нужно оставить терминал открытым. `Ctrl+C` закрывает только port-forward этого запуска. Helm релизы, кластер и PVC сохраняются. Если порт занят предыдущим forward, завершить его и повторить запуск. Логи forwards находятся в указанной скриптом временной директории.

Grafana: пользователь `admin`; команда получения пароля выводится скриптом, сам пароль он не читает. Дашборд автоматически загружается из [api-red.json](../dashboards/api-red.json) через [дополнительные values](../helm/grafana-dashboard-values.yaml). Это новая переносимая версия трёх панелей лабораторной, а не экспорт пользовательского дашборда из текущей Grafana. У неё отдельный UID.

Чтобы изменения этого дашборда переживали повторное provisioning, нужно обновлять JSON в репозитории. Его данные поступают из Prometheus; JSON не содержит историю метрик.

## Полезные варианты

Другой kind кластер, с теми же внутренними именами компонентов:

```bash
bash scripts/setup-observability.sh --cluster team-observability
```

Повторный запуск с уже собранным локальным `lab-api:0.1`:

```bash
bash scripts/setup-observability.sh --skip-build
```

Установка без запуска локальных forwards:

```bash
bash scripts/setup-observability.sh --no-port-forward
```

Ручной forward после такого запуска, например для Grafana:

```bash
kubectl --context kind-observability -n observability port-forward service/grafana 13000:80
```

Скрипт экспортирует kubeconfig для выбранного kind кластера. Все Helm/kubectl операции явно адресованы его контексту. Повторное применение Helm обновляет релизы до конфигурации из файлов; перед запуском нужно сохранить собственные правки в values.

## Метрики сервиса следующей лабораторной

По умолчанию Prometheus собирает метрики только `api:8080` и probe `/health`. Другой сервис должен предоставлять `/metrics` в формате Prometheus и быть доступен из этого кластера.

Скопировать итоговые values:

```bash
cp helm/prometheus-values.yaml helm/prometheus-lab3-values.yaml
```

Внутрь существующего блока `extraScrapeConfigs: |` добавить ещё один job, сохранив jobs `api` и `api-health`:

```yaml
  - job_name: lab3-api
    metrics_path: /metrics
    static_configs:
      - targets: ["lab3-api.lab3.svc.cluster.local:8080"]
```

Здесь `lab3-api` — пример имени Kubernetes Service, `lab3` — namespace, `8080` — порт Service. Нужно подставить имена своего сервиса. Это in-cluster DNS адрес, а не `localhost` компьютера.

Запуск с полным новым файлом values:

```bash
bash scripts/setup-observability.sh \
  --skip-build \
  --prometheus-values helm/prometheus-lab3-values.yaml
```

Если стек уже работает, для изменения только Prometheus достаточно:

```bash
helm upgrade prometheus prometheus-community/prometheus \
  --kube-context kind-observability -n observability \
  --version 29.35.0 -f helm/prometheus-lab3-values.yaml --wait --timeout 10m
```

Проверка в Prometheus: `up{job="lab3-api"}`. RED панели и существующие алерты используют `job="api"`; для нового job нужны его панели и подходящие правила. Названия метрик нового приложения также могут отличаться.

Alloy сейчас читает только `/var/log/pods/observability_api-*/api/*.log`. Для логов нового приложения потребуется отдельная настройка targets, а для трасс — instrumentation и endpoint `http://jaeger.observability.svc.cluster.local:4318`. Запуск стека сам по себе не инструментирует другие приложения.

## Ограничения сохранности

Jaeger хранит трассы в памяти. Данные PVC и positions Alloy локальны для kind ноды. Удаление кластера не является способом сохранить или перенести историю. Скрипт не удаляет кластер и не очищает volumes.
