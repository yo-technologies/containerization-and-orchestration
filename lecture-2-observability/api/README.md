# Сервис для лабораторной № 2

Go-сервис `api` из части 0 задания. Контекст сборки Docker — эта папка.
Развёртывание сервиса и стека мониторинга выполняется в Kubernetes через Helm,
как требует лабораторная. Здесь описан только интерфейс приложения.

| GET-эндпоинт | Поведение |
|---|---|
| `/health` | HTTP 200, текст `ok` |
| `/fail` | HTTP 500, JSON с ошибкой; увеличивает счётчик ошибок и помечает span как failed |
| `/slow` | Пауза 1–3 секунды во вложенном span `slow-op`, затем JSON с `slept_ms` |
| `/load` | Ждёт завершения 100 запросов к собственному `/health`, до 10 одновременно |
| `/metrics` | Метрики Prometheus в текстовом формате |

У `/load` есть параметры: `count=1..1000`, `concurrency=1..50`,
`target=health` или `target=fail`. Например:
`/load?count=500&concurrency=25&target=fail` создаёт всплеск ошибок.
Ответ содержит `requested`, `completed` (полученные HTTP-ответы),
`failed` (ошибки транспорта, чтения тела или HTTP >= 400) и `target`.
Одновременно разрешён один всплеск на экземпляр; следующий получает 429.
Общий таймаут всплеска — 20 секунд. Запросы идут на loopback этого же процесса,
поэтому при нескольких репликах нагрузка остаётся на выбранном экземпляре.

## Метрики и логи

- `http_requests_total{method,route,code}` — завершённые запросы.
- `http_errors_total{method,route,code}` — завершённые запросы с HTTP 5xx.
- `http_request_duration_seconds{method,route,code}` — гистограмма времени ответа
  в секундах; есть серии `_bucket`, `_sum`, `_count`.
- Дополнительно доступны стандартные метрики Go и процесса.

Scrape `/metrics` исключён из RED-метрик, логов запросов и трейсов:
сам мониторинг не увеличивает измеряемый RPS. Счётчики создаются при первом
запросе с соответствующим набором labels и сбрасываются при перезапуске.
Неизвестные пути получают общий label `route="unmatched"`.

Каждый запрос приложения пишет одну JSON-строку в stdout с `method`, `route`,
`status`, `duration_seconds`, `trace_id`, `span_id`; HTTP 5xx имеют уровень ERROR.
Ответы также содержат заголовок `X-Trace-ID`. Логи запуска, остановки и экспорта
не относятся к HTTP-запросу, поэтому у них нет `trace_id`.

## Настройки

| Переменная | Значение / назначение |
|---|---|
| `PORT` | `8080` по умолчанию; слушает все интерфейсы |
| `OTEL_SERVICE_NAME` | `api` по умолчанию; имя в Jaeger |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | URL получателя OTLP **HTTP/protobuf**, например `http://<receiver>:4318`; экспортёр добавляет `/v1/traces` |
| `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` | Полный URL, включая `/v1/traces`; имеет приоритет над общей настройкой |
| `OTEL_RESOURCE_ATTRIBUTES` | Дополнительные resource attributes, например `deployment.environment.name=lab` |

Если оба endpoint не заданы, экспорт выключен, но spans и trace IDs создаются.
Для просмотра в Jaeger нужно задать доступный из pod адрес его OTLP/HTTP-приёмника.
Этот сервис использует HTTP-экспортёр; порт OTLP/gRPC `4317` ему не подходит.
Ошибки доставки трейсов пишутся в stdout; ответы API от доступности Jaeger не зависят.

OpenTelemetry создаёт серверный span на входящий запрос, принимает W3C
`traceparent` и передаёт контекст в исходящие запросы `/load`. Для учебных
экспериментов включено 100% семплирование. При SIGTERM/SIGINT сервис завершает
активные запросы и сбрасывает очередь трейсов.

## Проверка исходников

Из этой папки:

```sh
go test -race ./...
go vet ./...
```

Инструментация использует официальные библиотеки
[Prometheus](https://prometheus.io/docs/guides/go-application/) и
[OpenTelemetry OTLP/HTTP](https://opentelemetry.io/docs/languages/sdk-configuration/otlp-exporter/).
