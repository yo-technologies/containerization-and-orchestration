# Источник метрик: cgroup собственного запускателя в Lima

Часть 8 завершена 26 октября. Проверка понимания схемы сбора пройдена 26 октября: Running недостаточно без экспорта и scrape метрик; cAdvisor kind не наблюдает отдельную Lima VM.

Используем прежний mydocker.sh и его cgroup lab1-mydocker. Она существует, но на момент подготовки пуста; лимиты 64 MiB и 0,5 CPU сохранены. Подготовлен [вспомогательный exporter](cgroup-exporter.py): Python stdlib, чтение фиксированной cgroup, HTTP /metrics. Полная генерация вспомогательного сервиса допускается AGENTS.md; инфраструктуру запускает студент. Файлы cgroup не изменяются. Exporter работает снаружи ограничиваемой группы от обычного пользователя; root и Docker socket ему не нужны.

## Что экспортируется

Все метрики имеют префикс lab1_cgroup_ и метку cgroup="lab1-mydocker".

| Суффикс | Источник | Тип |
| --- | --- | --- |
| memory_current_bytes | memory.current: суммарная учитываемая память группы, не только RSS API | gauge |
| memory_limit_bytes | memory.max | gauge |
| cpu_usage_seconds_total | cpu.stat usage_usec / 1000000 | counter |
| cpu_periods_total | cpu.stat nr_periods | counter |
| cpu_throttled_periods_total | cpu.stat nr_throttled | counter |
| cpu_throttled_seconds_total | cpu.stat throttled_usec / 1000000 | counter |
| cpu_limit_cores | cpu.max: quota / period | gauge |
| memory_oom_kills_total | memory.events oom_kill | counter |
| pids_current | pids.current: число задач, включая потоки | gauge |
| populated | cgroup.events populated | gauge |

Отсутствующие/нечитаемые поля дают HTTP 503, а не выдуманные нули. При отсутствии лимита значение ceiling равно +Inf. CPU и throttling счётчики отдают накопленное время в секундах; для будущих графиков потребуется изменение счётчика за интервал. Статистика группы могла сохраниться от прежних запусков: абсолютное ненулевое значение не доказывает новую нагрузку. При пересоздании cgroup счётчики сбрасываются. Чтение нескольких файлов не является атомарным снимком.

## Выполненный опыт: запуск и локальная проверка

Exporter скопирован в /tmp/lab1-cgroup-exporter.py в Lima. API и HTTP listener exporter ассистентом не запускаются. Все команды ниже выполняются внутри Lima от обычного пользователя; новый терминал VM можно открыть через limactl shell --workdir=/tmp container-lab на macOS.

В первом терминале Lima запустить собственный запускатель:

```sh
bash /tmp/lab1-mydocker.sh
```

Оставить процесс работающим. В другом терминале Lima запустить exporter вне cgroup API:

```sh
python3 /tmp/lab1-cgroup-exporter.py
```

В третьем терминале Lima проверить источник:

```sh
curl --noproxy '*' --max-time 3 -fsS http://127.0.0.1:19100/metrics
```

Получены memory_limit_bytes=67108864, cpu_limit_cores=0.5 и populated=1. На снимке память 3268608 байт, pids_current=7, CPU 0.338709 секунды и throttled_periods=3. [Снимок и протокол](../evidence/part-8-exporter.md). Само наличие CPU/throttling counters пока не доказывает новую нагрузку или scrape Prometheus. Запросы /burn и /eat на этом шаге не выполнялись.

Listener работает на loopback Lima. Дополнительная проверка из macOS на 127.0.0.1:19100 и из prometheus-server Pod на host.docker.internal:19100 получила все метрики. Сетевая доступность между средами подтверждена. Prometheus затем настроен опрашивать exporter по расписанию; результат ниже.

## Выполненный шаг: подключить источник к Prometheus

Подготовлен [overlay values](prometheus-lima-values.yaml): api/api-health остаются, добавляется lab1-cgroup с target host.docker.internal:19100, интервалом 5 секунд и таймаутом 3 секунды. extraScrapeConfigs является одной YAML-строкой, поэтому она заменяется целиком: оба прежних job включены явно. Остальные values берутся из существующего Helm release через --reuse-values, версия chart сохраняется.

В терминале macOS выполнить:

```sh
helm upgrade prometheus prometheus-community/prometheus \
  --version 29.35.0 --reuse-values \
  --kubeconfig /Users/semyondanshin/Documents/Codex/2026-09-08/crk/work/observability/kubeconfig \
  --kube-context kind-observability -n observability \
  -f /Users/semyondanshin/Programming/Uni/containerization-and-orchestration/solutions/lab-1/monitoring/prometheus-lima-values.yaml \
  --wait --timeout 5m
```

Оставить терминалы API/exporter работающими. При успехе прислать результат upgrade; затем проверить активный target, up{job="lab1-cgroup"}=1 и реальные lab1_cgroup_* в Prometheus. Успех Helm сам по себе scrape не доказывает. Подготовленный upgrade проверен с --dry-run=client --hide-secret, exit 0; release этим не изменялся. Студент применил overlay: revision 2, status deployed. [Проверка target и TSDB](../evidence/part-8-prometheus.md) подтвердила up=1 и все десять метрик lab1_cgroup_*. Прежние api/api-health также имеют up=1. Нагрузка не запускалась.

## Понимание counters — пройдено

cpu_usage_seconds_total — накопленное CPU-время cgroup. Для средней занятости CPU нужна скорость прироста, например rate(lab1_cgroup_cpu_usage_seconds_total{job="lab1-cgroup"}[1m]); CPU-секунды в секунду соответствуют числу занятых ядер. rate также учитывает сбросы counter. memory_current_bytes — текущее значение, его не следует интерпретировать как накопительный counter.

1. За 60 секунд cpu_usage_seconds_total вырос на 30. Сколько CPU-ядер группа использовала в среднем?
2. up{job="lab1-cgroup"}=1 гарантирует ли работу самого API? Что именно проверено этим значением и чем отдельно проверить API?

26 октября студент верно ответил: 0,5 ядра в среднем; up означает доступность exporter и успешный scrape, а API проверяется отдельным /health. Подготовлен [dashboard ресурсов](dashboard.md), baseline установлен и [подтверждён снимком](../evidence/part-8-dashboard.md). [90-секундный опыт выполнен](../evidence/part-8-load.md): рост памяти, CPU до квоты и throttling подтверждены. API остановлен сценарием, exporter работает. Финальная интерпретация пройдена, [обоснованы три метрики для алертов](alerts.md); часть 8 завершена. Источники: [rate](https://prometheus.io/docs/prometheus/latest/querying/functions/#rate), [значение up](https://prometheus.io/docs/concepts/jobs_instances/).

## Проверка вспомогательного кода

26 октября проверены синтаксис Python и синтетическая fixture: 1250000 usec дают 1,25 секунды, 250000 usec дают 0,25 секунды, quota/period даёт 0,5 CPU; типы counters/gauges и байтовые значения сохранены. Неограниченные ceiling представлены +Inf. Удаление cpu.stat вызывает ошибку чтения, без подстановки нулей. Это проверка преобразования данных, не результат учебной нагрузки.

Первоначально функция collect отдельно вызвана в Lima без HTTP listener и без запуска нагрузки. На пустой реальной lab1-mydocker она вернула десять метрик, включая memory_limit_bytes=67108864, cpu_limit_cores=0.5, pids_current=0, populated=0. cpu_usage_seconds_total=0.190789, cpu_periods_total=35, cpu_throttled_periods_total=1 и cpu_throttled_seconds_total=0.046358 относятся к сохранённой статистике прежнего запуска. Затем студент запустил API/exporter, HTTP endpoint и доступ из kind проверены выше.

SHA-256 подготовленного Python-файла: f4282c68b40abf1207966e66121d0bb1d66618d19ebf15902f9ccfe3531aa2ea.

Источники: [интерфейс cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html), [правила exporter и единиц измерения](https://prometheus.io/docs/instrumenting/writing_exporters/).
