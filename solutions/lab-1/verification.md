# Проверка вспомогательного сервиса

Дата: 8 сентября 2026 года. Среда: Go 1.24.4, darwin/arm64.

Команды выполнялись в каталоге `lab-1/api`. В проверке использовался отдельный `GOCACHE` в рабочем каталоге задачи.

```text
$ go test -race -v ./...
=== RUN   TestHealth
--- PASS: TestHealth (0.00s)
=== RUN   TestEatRetainsTouchedPagesAcrossRequestsAndGC
2026/09/08 16:22:25 INFO memory_held allocated_bytes=1048576 held_bytes=1048576
2026/09/08 16:22:25 INFO memory_held allocated_bytes=1048576 held_bytes=2097152
--- PASS: TestEatRetainsTouchedPagesAcrossRequestsAndGC (0.00s)
=== RUN   TestEatRejectsInvalidSizes
--- PASS: TestEatRejectsInvalidSizes (0.00s)
=== RUN   TestBurnStartsOnlyOneWorkerAndStopsWithContext
--- PASS: TestBurnStartsOnlyOneWorkerAndStopsWithContext (0.01s)
PASS
ok  example.com/container-labs/api  1.749s

$ go vet ./...
(нет диагностик, код завершения 0)
```

Сборка для macOS прошла успешно. `file` определил результат как `Mach-O 64-bit executable arm64`.

Кросс-компиляция с `CGO_ENABLED=0 GOOS=linux GOARCH=arm64` прошла успешно. `file` определил результат как `ELF 64-bit LSB executable, ARM aarch64, statically linked`.

Проверки подтверждают контракт обработчиков, сохранение ссылок на затронутые страницы памяти после GC, отклонение ошибочных размеров и запуск CPU-работника до отмены контекста. Они не подтверждают долю CPU на реальном ядре, cgroup-лимиты, OOM, namespaces, seccomp, работу Docker или gVisor. Linux-бинарник на данном этапе только собран, не запущен.
