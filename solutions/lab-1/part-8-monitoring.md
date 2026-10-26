# Часть 8: мониторинг ресурсов контейнера

Статус на 26 октября 2026 года: часть 7 завершена. Студент развернул стек решения сокомандника в kind-observability; Pod/PVC/releases подтверждены, CPU и память API kind уже есть в Prometheus. Проверка понимания первого шага пройдена. API и exporter запущены, метрики доступны в Lima и из kind. Scrape job применён, up=1 и все десять метрик подтверждены. Понимание counters пройдено. [Baseline dashboard](evidence/part-8-dashboard.md) установлен и визуально подтверждён. [90-секундный опыт](evidence/part-8-load.md) выполнен: графики и counters подтверждены. API штатно остановлен. Финальная интерпретация и [выбор трёх метрик для алертов](monitoring/alerts.md) подтверждены. Часть 8 и ЛР1 завершены 26 октября.

Цель — получить графики памяти, CPU и throttling, сопоставить их с нагрузкой и выбрать три метрики для алертов с обоснованием. Работа разбита на три шага: локальный стек Prometheus/Grafana, подключение метрик cgroup, затем dashboard и выбор алертов. Следующие конфигурации готовятся по мере выполнения текущего шага.

## Найденные материалы

В lecture-2-observability исходного репозитория есть Helm values, dashboard и scripts/setup-observability.sh. Студент запустил скрипт с отдельным kubeconfig и развернул решение в локальном kind. Дополнительные scrape jobs api и api-health собирают прикладные RED-метрики и проверку доступности. Фактическая конфигурация chart также включает штатный kubernetes-nodes-cadvisor: подтверждены CPU и память контейнера API в kind. Для Lima VM такой job источником не является.

## Проверка инструментов

На macOS доступны Homebrew 7.0.6, kubectl client v1.36.1, Helm v4.0.4 и Docker Desktop. Docker-хосту доступны около 8,32 GB RAM и 8 CPU. В Docker Desktop работают три учебных контейнера ЛР1. Первоначально kind отсутствовал; затем студент установил kind v0.33.0, go1.27.1, darwin/arm64. Вывод get clusters: No kind clusters found. Дополнительная проверка подтвердила версию и отсутствие кластеров. Текущий kube context — twc-reasonable-woodpecker. Команды к этому кластеру не отправлялись, прочитано только имя контекста.

При создании учебного кластера используем отдельный файл KUBECONFIG и явный Docker context desktop-linux. Скрипт сокомандника вызывает kind export kubeconfig, поэтому запуск без отдельного KUBECONFIG изменил бы текущий контекст в обычном пользовательском файле. Сам файл здесь не менялся. Рендеринг всех внешних charts под Helm 4 проверен; последующая фактическая установка также подтверждена ниже.

## Выполненный шаг: установить kind

В терминале macOS:

```sh
brew install kind &&
kind version &&
DOCKER_CONTEXT=desktop-linux kind get clusters
```

kind создаёт Kubernetes-ноды как Docker-контейнеры. Установка CLI и get clusters ещё не создают кластер и не разворачивают стек. Получены kind v0.33.0 и No kind clusters found.

## Проверка перед развёртыванием

26 октября выполнены без создания ресурсов Kubernetes:

- bash -n для setup-observability.sh: exit 0.
- helm lint для локальных charts api и webhook: 2 charts, 0 failed; только информационные замечания о необязательном icon и отсутствующем отдельном values.yaml webhook.
- docker manifest inspect kindest/node:v1.37.0: доступны linux/arm64 и linux/amd64; ARM64 digest sha256:6a3345f517a7df05185c648f83d18d464f2a94ea4a69eaee39b7e1de02611e59.
- helm template с --namespace observability --kube-version 1.37.0 и исходными values: все семь внешних charts завершились с exit 0. Grafana дополнительно получила dashboard values и api-red.json, как в скрипте.

| Chart | Версия |
| --- | --- |
| prometheus-blackbox-exporter | 11.20.0 |
| loki | 18.15.0 |
| jaeger | 4.14.1 |
| alloy | 1.13.1 |
| prometheus | 29.35.0 |
| grafana | 13.3.1 |
| karma | 2.13.1 |

Проверены доступность charts и локальный рендеринг шаблонов, а не готовность Pod, доступность всех образов или фактический scrape.

## Выполненный шаг: развернуть готовый стек

В терминале macOS выполнить:

```sh
lab_state=/Users/semyondanshin/Documents/Codex/2026-09-08/crk/work/observability
mkdir -p "$lab_state" &&
DOCKER_CONTEXT=desktop-linux \
KUBECONFIG="$lab_state/kubeconfig" \
bash /Users/semyondanshin/Programming/Uni/containerization-and-orchestration/lecture-2-observability/scripts/setup-observability.sh \
  --no-port-forward
```

Скрипт создаст kind observability с Kubernetes-ноды v1.37.0 в Docker Desktop, соберёт вспомогательный API ЛР2, загрузит образ в kind и установит Helm releases из решения сокомандника. kubeconfig сохранится в work/observability вне сдаваемых материалов; не копировать его в solutions и не публиковать. DOCKER_CONTEXT/KUBECONFIG заданы только для этого запуска скрипта. --no-port-forward завершит команду после установки и проверок; интерфейсы откроем отдельно после проверки ресурсов.

Получено сообщение Installation finished. Проверки API/Loki успешны, десять Pod Ready/Running без рестартов, четыре PVC Bound, девять Helm releases deployed. Дополнительная read-only проверка подтвердила состояние и наличие CPU/памяти API в Prometheus. Для API не найден ряд throttled_periods с проверенным фильтром; отсутствие ряда не следует подменять нулевым значением. [Снимок, запрос и протокол](evidence/part-8-stack.md).

## Понимание схемы сбора — пройдено

На Linux-ноде cgroup содержит счётчики ресурсов. cAdvisor публикует контейнерные метрики, Prometheus опрашивает endpoint и хранит временные ряды, Grafana запрашивает их для панелей. Статус Running у Pod подтверждает запуск процесса, но не наличие нужной метрики, успешный scrape или правильный фильтр по workload. Сборщик в kind наблюдает соответствующую Linux-среду; отдельная Lima VM требует своего источника либо явной организации сбора.

1. Почему Running у всех Pod ещё недостаточно, чтобы утверждать, что Grafana сможет показать throttling нужного API?
2. Будет ли cAdvisor ноды kind автоматически видеть процесс mydocker в отдельной Lima VM? Почему?

26 октября студент верно ответил: Running подтверждает запуск, для графика нужны экспорт и scrape метрики; cAdvisor видит cgroup своей ноды, Lima — другая VM. Оба ответа без ошибок. Подготовлен [локальный опыт с exporter](monitoring/README.md): он читает lab1-mydocker в Lima. Проверены преобразование единиц и чтение пустой реальной группы, файл скопирован в VM. Студент затем запустил API/exporter; [локальные метрики и доступ из kind подтверждены](evidence/part-8-exporter.md). Overlay с job lab1-cgroup применён студентом; [target и все десять метрик в Prometheus подтверждены](evidence/part-8-prometheus.md). Подготовлен JSON [dashboard ресурсов](monitoring/dashboard.md), baseline установлен и визуально подтверждён; подготовлена нагрузка 16 MiB плюс /burn на 90 секунд с последующей остановкой API. Опыт затем [выполнен и подтверждён](evidence/part-8-load.md); финальная интерпретация и [обоснование выбранных метрик](monitoring/alerts.md) затем получены, часть 8 завершена.

Источник установки и отдельного kubeconfig: [официальный Quick Start kind](https://kind.sigs.k8s.io/docs/user/quick-start/).
