# Часть 5.3: сравнение с Docker

Подготовлено 26 октября 2026 года после верных ответов о порядке настройки mydocker.sh. Единый запускатель подтверждён [снимком](evidence/part-5-mydocker-api.md). Docker-запуск с сопоставимыми настройками и проверка понимания сравнения подтверждены; часть 5 завершена.

## Подготовка — проверено ассистентом

- Docker context `desktop-linux`, Engine 29.8.0, containerd 2.3.4, runc 1.4.3.
- Docker-хост: Linux `7.0.12-linuxkit`, ARM64, cgroup v2. Это Linux VM Docker Desktop; mydocker.sh работает в отдельной Lima VM с kernel `6.12.107+deb13-cloud-arm64`.
- Локально доступен `alpine:3.23.3`, linux/arm64. Image ID: `sha256:1ab49c19c53ebca95c787b482aeda86d1d681f58cdf19278c476bcaf37d96de1`; RepoDigest: `alpine@sha256:25109184c71bdad752c8312a8623239686a9a2071e8825f20acb8f2198c3f659`.
- Контейнер с точным именем `lab1-docker-compare` отсутствует. Никакие контейнеры ассистентом не запускались и не останавливались.
- Тот же Linux ARM64-бинарник подготовлен в `solutions/lab-1/api/bin/api-linux-arm64`; SHA-256 `4fa14e29b5e459bf8e2799d3d314de0181858319db7e2f24212ecaea8a1519a4` совпал с бинарником для Lima. Существующий `api/bin/api` сохранён. Каталог bin игнорируется Git.

## Выполнить студенту на macOS

Открыть отдельный терминал macOS. Работающий mydocker.sh в Lima можно оставить. Перейти в каталог отчёта:

```sh
cd /Users/semyondanshin/Programming/Uni/containerization-and-orchestration/solutions/lab-1
```

Запустить контейнер из уже имеющегося образа, подключив бинарник только для чтения:

```sh
docker --context desktop-linux run -d --rm --name lab1-docker-compare \
  --hostname lab-api --network none \
  --memory 64m --memory-swap 64m --cpus 0.5 --pids-limit 32 \
  --cap-drop ALL --security-opt no-new-privileges=true \
  --security-opt "seccomp=$PWD/seccomp/deny-getppid.json" \
  --mount "type=bind,src=$PWD/api/bin/api-linux-arm64,dst=/lab1-api,readonly" \
  --pull=never alpine:3.23.3 /lab1-api -listen 127.0.0.1:18080
```

Смысл параметров: `--memory-swap` задаёт сумму RAM и swap, поэтому равенство с `--memory` запрещает swap; `--cpus 0.5` соответствует квоте 50000/100000; `--network none` создаёт отдельную сеть с поднятым loopback. CLI передаёт Docker содержимое того же seccomp-профиля. `--rm` удалит только этот контейнер после его остановки; сейчас `-d` оставляет его работающим в фоне.

Затем проверить состояние именно PID 1 и HTTP-ответ внутри контейнера:

```sh
docker --context desktop-linux exec lab1-docker-compare sh -ec '
  hostname
  grep -E "^(Name|Pid|Uid|NSpid|Cap(Inh|Prm|Eff|Bnd|Amb)|NoNewPrivs|Seccomp|Seccomp_filters):" /proc/1/status
  cat /proc/1/cgroup
  cat /sys/fs/cgroup/memory.max /sys/fs/cgroup/memory.swap.max \
      /sys/fs/cgroup/cpu.max /sys/fs/cgroup/pids.max
  wget -Y off -S -O - http://127.0.0.1:18080/health
'
```

Проверка выполняется через docker exec: при `--network none` порт наружу не публикуется. Прочитанные capabilities относятся к API из `/proc/1/status`, а не к проверяющему shell. Не используется Bash brace expansion: внутри Alpine запускается sh.

Ожидаются hostname `lab-api`, API как PID 1, нулевые capabilities, no_new_privs, режим seccomp filter, лимиты 67108864/0/50000 100000/32 и HTTP 200 с `ok`. Значения и особенности cgroup namespace/UID mapping нужно сопоставить с реальным выводом; их совпадение с Lima заранее не утверждается. Сборка собственных образов относится к части 6 и ещё не начата.

Результаты получены: Docker вернул ID контейнера; внутри API — PID 1, UID 0, нулевые Cap-наборы, `NoNewPrivs = 1`, `Seccomp = 2`, один фильтр, `0::/`, ожидаемые resource limits и HTTP 200 с `ok`. Протокол, оба снимка и дополнительные inspect/top — [Docker-сравнение](evidence/part-5-docker-comparison.md).

## Фактическое сравнение

| Свойство | mydocker.sh в Lima | Docker Desktop, данный запуск |
| --- | --- | --- |
| API, PID внутри, hostname | Тот же бинарник; PID 1; lab-api | Тот же бинарник; PID 1; lab-api |
| Лимиты | 64 MiB, swap 0, 0,5 CPU, 32 задачи | Те же значения |
| Capabilities, no_new_privs, seccomp | Пять пустых Cap-наборов; NNP 1; filter mode, один фильтр | То же; профиль передан явно |
| UID на Linux-хосте | 501; root отображается через user namespace | 0 по docker top; в этом запуске remapping к 501 отсутствует |
| Видимый путь cgroup | 0::/lab1-mydocker | 0::/ при private cgroup namespace |
| Корень файловой системы | Корень Lima VM; скрипт не меняет rootfs | Файлы образа Alpine с собственным writable layer; API смонтирован отдельно read-only |
| Управление | Shell-скрипт, фиксированная группа, ручная проверка через procfs/nsenter | Container ID/name, exec/inspect/top/logs/stop и выбранное --rm |

`0::/` у Docker — путь относительно корня его cgroup namespace. Контроллеры и лимиты остаются активны, что видно по прочитанным memory.max/cpu.max/pids.max. Источник: [cgroup_namespaces(7)](https://man7.org/linux/man-pages/man7/cgroup_namespaces.7.html).

Наш mount namespace отделяет таблицу монтирований, но сам по себе не подготавливает отдельный набор файлов. В скрипте нет подготовки rootfs и смены корня: процесс продолжает видеть файловую систему VM по своим правам. Docker использует файловую систему образа; управление образами, слоями, volumes и сетевыми драйверами в нашем скрипте не реализовано. Источник для устройства запуска Docker: [docker run](https://docs.docker.com/engine/containers/run/).

Равенство лимитов/профиля задано намеренно и не описывает Docker по умолчанию: обычно он использует собственный seccomp-профиль, который здесь заменён учебным профилем. Сравниваются механизмы и наблюдаемые настройки на двух разных Linux VM.

На момент проверки Docker-контейнер работает, API в Lima уже отсутствует и его cgroup пуста. Ничего не останавливалось ассистентом.

## Проверка понимания сравнения — пройдена

1. У Docker показано `0::/`. Значит ли это, что лимиты больше не действуют? Почему?
2. Почему одинаковые PID 1 и cgroup-лимиты ещё не означают одинаковый набор видимых файлов? Чего не делает одно создание mount namespace?

26 октября студент верно объяснил: `0::/` обозначает корень cgroup namespace и не отменяет лимитов; mount namespace изолирует монтирования, но для отдельного набора файлов нужно подготовить rootfs Alpine и сделать его корнем процесса. Оба ответа без ошибок. Часть 5 завершена, далее начата [часть 6 с образами](part-6-images.md).

Источники: [docker run](https://docs.docker.com/engine/containers/run/), [resource constraints](https://docs.docker.com/engine/containers/resource_constraints/), [none network](https://docs.docker.com/engine/network/drivers/none/), [custom seccomp](https://docs.docker.com/engine/security/seccomp/).
