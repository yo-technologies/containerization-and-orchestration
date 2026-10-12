# Скриншоты лабораторной

Скопированы исходные изображения, присланные в этом чате. Содержимое PNG не изменялось.

| Доказательство | Файл |
| --- | --- |
| RED: RPS, доля ошибок, p95 | [grafana-red-dashboard.png](grafana-red-dashboard.png) |
| Изменение RPS при нагрузке | [grafana-request-rate.png](grafana-request-rate.png) |
| Рост доли ошибок | [grafana-error-rate.png](grafana-error-rate.png) |
| JSON логи API, включая ERROR для /fail | [grafana-api-logs.png](grafana-api-logs.png) |
| Лог /fail с полным trace_id и span_id | [grafana-fail-trace-id.png](grafana-fail-trace-id.png) |
| Waterfall /slow с дочерним slow-op, длительность 2,7 с | [jaeger-slow.png](jaeger-slow.png) |
| Waterfall /fail: HTTP 500, error=true, событие exception | [jaeger-fail.png](jaeger-fail.png) |
| APIHighErrorRate и APIHighLatency в Alertmanager | [alertmanager-errors-and-latency-firing.png](alertmanager-errors-and-latency-firing.png) |
| APIUnavailable в Alertmanager | [alertmanager-unavailable-firing.png](alertmanager-unavailable-firing.png) |
| APIHighErrorRate и APIHighLatency в Karma | [karma-errors-and-latency-firing.png](karma-errors-and-latency-firing.png) |
| APIUnavailable в Karma | [karma-unavailable-firing.png](karma-unavailable-firing.png) |
| Подробности APIHighErrorRate в Karma | [karma-error-rate-details.png](karma-error-rate-details.png) |
| Полученное webhook уведомление APIHighLatency: firing | [webhook-latency-firing.png](webhook-latency-firing.png) |
| Полученное webhook уведомление APIHighErrorRate: firing | [webhook-error-rate-firing.png](webhook-error-rate-firing.png) |

Дополнительный диагностический снимок редактора Helm лежит в `diagnostics/`; для сдачи он не требуется.

## Полнота скриншотов по lab.md

Все обязательные категории представлены: RED дашборд, логи Grafana, waterfall `/slow` и `/fail`, firing алерты Alertmanager и Karma.

В логе `/fail` видны `trace_id=246eb66dc80a2ced442fcf41eb48f17c` и `span_id=16310c038d9685fe`. На снимке Jaeger `/fail` отображается префикс Trace ID `246eb66` и тот же полный Span ID `16310c038d9685fe`.

Новый общий RED снимок показывает рост RPS, доли ошибок и p95; панель задержки названа `Latency p95`. Предыдущая версия сохранена в [archive/grafana-red-dashboard-initial.png](archive/grafana-red-dashboard-initial.png).

## Что стоит дополнить для ясности доказательств

- Webhook уведомление для APIUnavailable и уведомления `resolved`, если нужно показать доставку и восстановление всех трёх алертов. Это дополнительные доказательства, а не отдельное требование к скриншотам в lab.md.

Снимок `kind-ready` был прислан раньше, но его исходный временный файл уже отсутствует. Он не входит в обязательный список скриншотов.

JSON дашборда и отчёт README проверяются отдельно: скриншоты не заменяют конфигурацию или описание работы.
