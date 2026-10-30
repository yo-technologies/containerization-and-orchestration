# Скриншоты лабораторной 3

Сняты 18 октября 2026 года. GUI-кадры получены с настоящих Grafana/Prometheus.
Остальные кадры показывают **реальные сохранённые журналы в браузере**:
это не изображение терминала в момент выполнения. Исходные TXT/JSON/CSV
лежат в [evidence/](evidence) и доступны для проверки.

| Файл | Что видно | Тип источника |
| --- | --- | --- |
| [01-policy-checks.jpg](01-policy-checks.jpg) | Принятие корректного Pod и пять отдельных отказов | Сохранённый вывод admission |
| [02-api-reconciliation.jpg](02-api-reconciliation.jpg) | Удалённый UID и UID оставшихся/нового Pod | Сохранённый JSON |
| [02-rollouts.jpg](02-rollouts.jpg) | Число HTTP-запросов, ошибки, min ready и max Pod | Сохранённый журнал измерений |
| [03-postgres-reconciliation.jpg](03-postgres-reconciliation.jpg) | Другой UID PostgreSQL и сохранённый заказ | Сохранённый JSON |
| [04-control-plane.jpg](04-control-plane.jpg) | Остановленный API server, работа etcd, заказы, HTTP | Сохранённый JSON |
| [04-management-failure.jpg](04-management-failure.jpg) | Управляющая запись не прошла | Сохранённая ошибка kubectl |
| [05-alert-firing.jpg](05-alert-firing.jpg) | ShopAPIUnavailable в FIRING | Настоящий UI Prometheus |
| [05-dashboard.jpg](05-dashboard.jpg) | Targets, RPS, создание/обработка, очередь | Настоящий UI Grafana |
| [05-dashboard-bottom.jpg](05-dashboard-bottom.jpg) | Очередь, наблюдение БД, p95 | Настоящий UI Grafana |
| [06-final-resources.jpg](06-final-resources.jpg) | Финальные API 3/3, worker 2/2, PostgreSQL Ready | Сохранённый вывод Kubernetes |

Инструмент доступа к UI запретил работу с Terminal. Команды запускались через
разрешённый CLI, результаты сохранены; просмотр журналов в браузере использован
как безопасный способ сделать иллюстрации без имитации терминала.
Если преподаватель требует именно живые Lens/Terminal-скриншоты, порядок
установки дан в [инструкции](../README.md#2-как-повторить-развёртывание), команды проведённых опытов —
в [журналах](evidence/README.md).
