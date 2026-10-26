# Dashboard ресурсов mydocker

Проверка понимания counters пройдена 26 октября: прирост 30 CPU-секунд за 60 секунд означает 0,5 ядра в среднем; up exporter не заменяет запрос /health самого API.

Подготовлен [JSON dashboard](lab1-resources.json), UID lab1-cgroup-lima. Он использует существующий datasource prometheus, фильтрует job=lab1-cgroup и cgroup=lab1-mydocker. Это ресурсные метрики Lima, а не RED API ЛР2.

## Панели и смысл

- Память: memory.current и memory.max в байтах. Счётчик учитывает всю cgroup, не только RSS Go API.
- CPU: rate(cpu_usage_seconds_total[1m]) и quota/period. Единица — ядра; потолок 0,5 CPU.
- Throttling: rate(throttled_periods_total[1m]) / rate(periods_total[1m]), доля периодов с ограничением. Это не доля потерянного CPU-времени. При отсутствии периодов 0/0 даёт NaN и пробел в графике, а не выдуманный ноль.
- OOM kills: накопленное число убийств, не скорость. Для событий за окно потребуется increase.
- Populated: наличие процессов в группе, не HTTP-проверка приложения.
- Exporter up: успешный scrape, отдельно от работоспособности API.

JSON синтаксически сформирован; Helm upgrade с --dry-run=client --hide-secret завершился exit 0. Все восемь запросов панелей проверены через Prometheus API. Фактическая установка и визуальная проверка baseline затем подтверждены снимком; Grafana release revision 2, status deployed.

## Выполненный шаг: открыть baseline dashboard

На macOS установить JSON через существующий file provider Grafana:

```sh
helm upgrade grafana grafana-community/grafana \
  --version 13.3.1 --reuse-values \
  --kubeconfig /Users/semyondanshin/Documents/Codex/2026-09-08/crk/work/observability/kubeconfig \
  --kube-context kind-observability -n observability \
  --set-file dashboards.default.lab1-resources.json=/Users/semyondanshin/Programming/Uni/containerization-and-orchestration/solutions/lab-1/monitoring/lab1-resources.json \
  --wait --timeout 5m
```

--reuse-values сохраняет datasource, provider и dashboard API RED; добавляется ещё один файл dashboard. File provisioning читает его при старте Grafana. Текущие provisioning-настройки допускают редактирование в UI, но сохранённый JSON остаётся источником воспроизводимого состояния.

После успешного upgrade в отдельном терминале macOS открыть port-forward и оставить его работающим:

```sh
kubectl --kubeconfig /Users/semyondanshin/Documents/Codex/2026-09-08/crk/work/observability/kubeconfig \
  --context kind-observability -n observability \
  port-forward --address 127.0.0.1 service/grafana 13000:80
```

Открыть http://localhost:13000/d/lab1-cgroup-lima. Логин admin. Если пароль неизвестен, прочитать локально, не присылать его в чат или на снимке:

```sh
kubectl --kubeconfig /Users/semyondanshin/Documents/Codex/2026-09-08/crk/work/observability/kubeconfig \
  --context kind-observability -n observability \
  get secret grafana -o jsonpath='{.data.admin-password}' | base64 --decode
```

Прислать baseline dashboard. Должны быть память около текущего уровня, линия 64 MiB, квота 0,5 CPU, exporter up=1 и populated=1. Throttling без нагрузки может не иметь значения, если CPU-периоды в окне не идут. Ошибки запросов и отсутствие рядов нужно отличать от этого случая.

API PID 9568 в Lima дополнительно проверен перед подготовкой: обычный host UID 501, /health через nsenter вернул ok; memory.current=3268608, cpu.max=50000 100000. Port 13000 на macOS на момент проверки свободен. На момент этой предварительной проверки новые /eat и /burn ещё не вызывались. Последующий опыт с нагрузкой выполнен ниже; после него студент выбрал и обосновал три метрики для алертов.

## Выполненный шаг: показать реакцию на нагрузку

Baseline [подтверждён снимком](../evidence/part-8-dashboard.md). Подготовлен [скрипт опыта](load-90s.sh), скопирован в Lima как /tmp/lab1-load-90s.sh. Проверен bash -n локально и внутри VM; нагрузка ассистентом не запускалась.

Скрипт проверяет один API текущего пользователя в lab1-mydocker, лимиты 64 MiB и 0,5 CPU, текущее потребление ниже 32 MiB. Запрашивает /health, затем один раз /eat?mb=16 и /burn, ждёт 90 секунд, показывает счётчики и повторно проверяет /health. В конце или при прерывании останавливает только исходный API через SIGTERM: у /burn нет отдельной команды выключения. PID дополнительно сопоставляется со временем старта и cgroup. Exporter и Grafana продолжают работать. Остановка API в конце — ожидаемая часть сценария.

На macOS выполнить:

```sh
limactl shell --workdir=/tmp container-lab bash /tmp/lab1-load-90s.sh
```

В Grafana выбрать Last 5 minutes. Наблюдать рост памяти после удержания 16 MiB, CPU около квоты 0,5 ядра и появление доли throttled periods. Окно rate составляет минуту, поэтому график CPU изменяется постепенно. Дождаться завершения опыта и прислать вывод терминала плюс screenshot dashboard с участком нагрузки. После остановки populated станет 0, exporter up останется 1; история графиков сохраняется в Prometheus. Описанный опыт затем выполнен; результаты и снимки приведены ниже.

Чтобы впоследствии вернуть API, в его первом терминале Lima снова выполнить bash /tmp/lab1-mydocker.sh. Пока не повторять /eat и /burn: запланирован один ограниченный опыт.

## Полученный результат и финальные вопросы

Опыт выполнен, [оба снимка и точные счётчики сохранены](../evidence/part-8-load.md). Память выросла примерно с 3,1 до 19,5 MiB, CPU-время на 45,06246 секунды, 901 из 902 новых периодов содержал throttling. /health ответил под нагрузкой, OOM не было. API остановлен сценарием, cgroup пуста, exporter остаётся доступен. Обязательный dashboard получен.

1. Почти 100% на панели throttling означает ли, что приложение почти 100% времени не получало CPU? Как правильно читать эту панель?
2. Какие три метрики выбрать для алертов? Для каждой объяснить, какую проблему она обнаруживает и почему это важно. Выбор делает студент; правила и пороги не подставляются вместо его обоснования.

26 октября студент верно объяснил долю периодов с исчерпанием квоты и выбрал память относительно лимита, долю throttling и новые OOM kills. [Обоснование сохранено](alerts.md). Часть 8 и ЛР1 завершены; повторная нагрузка не требуется.

Источник: [file provisioning Grafana](https://grafana.com/docs/grafana/latest/administration/provisioning/#dashboards), [CPU controller cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html#cpu).
