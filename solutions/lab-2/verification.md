# Проверка кода сервиса ЛР2

Дата: 8 сентября 2026 года. Среда тестов: Go 1.24.4, darwin/arm64. Зависимости и cache размещены в рабочем каталоге задачи; версии зависимостей зафиксированы в `api/go.mod` и `api/go.sum`.

Первый запуск в sandbox остановился на запрете временного localhost listener (`bind: operation not permitted`). Повторный запуск с разрешением локальных тестовых портов завершился успешно.

Команда: `go test -race -v ./...` в каталоге `api`.

```text
=== RUN   TestHealthFailureMetricsAndLogTraceCorrelation
--- PASS: TestHealthFailureMetricsAndLogTraceCorrelation (0.01s)
=== RUN   TestSlowNestedSpanAndIncomingTracePropagation
--- PASS: TestSlowNestedSpanAndIncomingTracePropagation (2.00s)
=== RUN   TestLoadActuallyCallsItselfAndPropagatesTrace
--- PASS: TestLoadActuallyCallsItselfAndPropagatesTrace (0.01s)
=== RUN   TestLoadRejectsUnboundedOrRecursiveTargets
--- PASS: TestLoadRejectsUnboundedOrRecursiveTargets (0.00s)
=== RUN   TestOTLPHTTPExport
--- PASS: TestOTLPHTTPExport (0.01s)
PASS
ok  example.com/container-labs/observable-api  3.628s
```

Код завершения — 0; детектор гонок не сообщил об ошибках. Линковщик macOS при подготовке тестового бинарника выдал предупреждение `malformed LC_DYSYMTAB`; оно не остановило сборку или тесты. Предупреждение не считается устранённым.

`go vet ./...` завершился с кодом 0 без диагностик.

Сборка с `CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build -trimpath ...` завершилась с кодом 0. `file` определил результат как `ELF 64-bit LSB executable, ARM aarch64, statically linked`.

Тест OTLP запускает временный HTTP-приёмник, принимает POST на `/v1/traces`, декодирует protobuf и проверяет `service.name`, error status и trace ID. Это подтверждает работу экспортёра по протоколу, но не наличие Jaeger и не его интерфейс.

Тест `/load` запускает временный HTTP-сервер приложения и проверяет 12 клиентских spans и 12 серверных spans `/fail` в одной трассировке. Тест `/slow` измеряет длительность вложенного span и его связь с родительским.

На этапе этой проверки Dockerfile ещё не собирался, Kubernetes, Helm и компоненты наблюдаемости не развёртывались. Снимков Grafana, Jaeger, Alertmanager и Karma тогда ещё не было.

Этот каталог содержит ранний вспомогательный API. Командное прохождение со стеком и скриншотами описано отдельно в [основном отчёте ЛР2](../../lecture-2-observability/README.md).
