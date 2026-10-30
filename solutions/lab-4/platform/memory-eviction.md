# Подготовка memory eviction в kind

На стенде kind worker имеет явный `evictionHard` только для дисковых сигналов, все равны 0%. В действующем configz `mergeDefaultEvictionSettings=false`; поэтому отсутствующий `memory.available` не получает стандартный порог 100Mi. До опыта это проверено через API kubelet.

Обе ноды используют память общей Docker VM: лимиты памяти контейнеров kind отсутствуют, VM имеет около 7,75 GiB. Для опыта задаём учебный порог kubelet выше обычного, чтобы пересечь его небольшим буфером, сохранив запас памяти всей VM. Этот порог будет явно указан в отчёте: мы проверяем настоящее решение eviction manager по настроенному `memory.available`, а не исчерпываем физическую RAM.

Скрипт `memory-eviction.py prepare` читает текущее availableBytes worker, выбирает порог на 256–320 MiB ниже него, сохраняет оригинальный конфиг вне Git и добавляет только memory-сигнал и transition period 15s. Другие сигналы сохраняются. При доступной памяти меньше 2 GiB подготовка прерывается. Скрипт перезапускает kubelet worker, проверяет фактически загруженный configz; при ошибке возвращает оригинальный файл. Нагрузку он не запускает.

Из корня репозитория:

```bash
python3 solutions/lab-4/platform/memory-eviction.py prepare
```

Ожидаем Ready=True и MemoryPressure=False до нагрузки. Число `threshold` и фактическое available сохраняются в `$HOME/.local/state/container-lab/lab4-memory-eviction/state.json`; полный исходный конфиг там же в `original.yaml`. Backup привязан к ID контейнера kind, чтобы не применить его к пересозданной ноде.

Повторная проверка:

```bash
python3 solutions/lab-4/platform/memory-eviction.py status
```

Возврат после опыта:

```bash
python3 solutions/lab-4/platform/memory-eviction.py restore
```

Ресурсы и размещение batch задаются отдельно через Helm. У его удерживаемого буфера должен быть запас до memory limit контейнера; иначе получится собственный OOMKilled, который не доказывает node-pressure eviction. Для отбора жертвы kubelet учитывает превышение requests, приоритет и размер превышения, а не одно название QoS.

## Ограниченный опыт под нагрузкой

После успешной подготовки в первом терминале запустить:

```bash
DURATION=5m SUMMARY_PATH=solutions/lab-4/evidence/part-7-load-summary.json \
  k6 run solutions/lab-4/load/orders.js
```

Параллельно во втором:

```bash
python3 solutions/lab-4/platform/run-memory-eviction.py
```

Скрипт проверяет подготовку, Ready критических Pod и запас 128–384 MiB до порога. Если память сильно изменилась, опыт не начинается: нужно выполнить `restore`, затем `prepare`, чтобы заново выбрать порог.

Через override `experiments/memory-pressure.yaml` один batch направляется на worker: буфер 512Mi, CPU workers 0, memory request 64Mi, limit 768Mi. На наблюдение отведено 120 секунд. После первого нового batch Pod с reason Evicted и сообщением о памяти скрипт применяет `memory-stop.yaml` (batch replicas 0), собирает ещё 20 секунд состояния, возвращает defaults chart и исходный kubelet config. При ошибке или Ctrl+C также выполняются попытки возврата; ошибки cleanup явно сохраняются и завершают скрипт с ошибкой.

В `evidence/part-7-eviction.json` сохраняются временные ряды node memory, MemoryPressure, Pod Ready/QoS/resources, статус жертвы и события Evicted, а также результат возврата. Готовность критических Pod во всех снимках отражается отдельным полем; это не заменяет HTTP-результаты k6. Повторный запуск требует нового пути `--output`, чтобы не затереть первый опыт.

При первом опыте один Pod успел получить phase Succeeded без reason, хотя событие kubelet Evicted с его UID уже существовало. Обнаружение исправлено: учитывается и Pod reason, и событие Evicted о памяти, сопоставленное по UID с новым batch. Исходный статус Pod не подменяется в evidence. Это позволяет остановить pressure по первому достоверному событию, не дожидаясь следующей жертвы.

`completed` отражает завершение опыта и cleanup, а `critical_ready_all_samples` отдельно показывает непрерывную готовность критических Pod в снимках. Второе поле нельзя игнорировать при оценке защиты приложения. После restore скрипт также проверяет готовность критических Pod и считает её отсутствие ошибкой cleanup.

`--check` выполняет только чтение и подходит для проверки подготовки. Сам опыт через него не запускается. Пример ручного возврата после незавершённого опыта:

```bash
helm upgrade shop solutions/lab-4/helm/shop \
  --kube-context kind-shop-lab4 -n shop --reset-values --wait --timeout 5m
python3 solutions/lab-4/platform/memory-eviction.py restore
```

Источник: [Node-pressure eviction](https://kubernetes.io/docs/concepts/scheduling-eviction/node-pressure-eviction/), [MergeDefaultEvictionSettings](https://kubernetes.io/docs/reference/config-api/kubelet-config.v1beta1/).
