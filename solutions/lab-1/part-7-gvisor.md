# Часть 7. gVisor: подготовка среды

Статус на 26 октября 2026 года: часть 7 завершена, практика и проверка понимания подтверждены. Студент установил Docker и runsc release-20261019.0, зарегистрировал systrap и запустил прежний образ API через gVisor. HTTP 200 и host-процесс Sentry подтверждены.

gVisor перехватывает системные вызовы приложения и обрабатывает их в Sentry, реализующем интерфейс Linux в userspace. В обычном контейнере приложение обращается к Linux-ядру хоста напрямую с ограничениями namespaces, capabilities и seccomp. Sentry тоже использует ядро хоста: дополнительный слой уменьшает доступную приложению поверхность ядра, но не устраняет зависимость от него.

## Проверенная среда и выбор платформы

В Lima container-lab: Debian 13.7, Linux 6.12.107+deb13-cloud-arm64, aarch64; UID 501/GID 1000. Docker, containerd, runc и runsc не установлены. Файлы /etc/docker/daemon.json, /etc/apt/sources.list.d/gvisor.list и ключ gVisor отсутствуют. Свободно около 18 GiB диска, доступно около 1,7 GiB RAM, swap отсутствует. /dev/kvm отсутствует.

gVisor поддерживает ARM64 и Linux 5.6+. Используем systrap, платформу по умолчанию: она перехватывает syscalls через seccomp/SIGSYS и подходит для запуска внутри VM без KVM. Совместимость конкретного приложения проверим фактическим запуском.

Устанавливаем Docker и runsc в учебную Lima VM. Docker Desktop остаётся отдельным движком. Выбран официальный apt-пакет runsc: он устанавливает необходимые сопутствующие файлы. Проверка установленного postinst показала дополнительное условие авторегистрации: /etc/docker/daemon.json должен уже существовать. В нашей свежей установке его не было.

## Выполненный шаг: установка

На macOS войти в VM:

```sh
limactl shell --workdir=/tmp container-lab
```

В VM выполнить блоки по порядку; при ошибке остановиться и сохранить вывод. Первый блок устанавливает Docker и инструменты проверки подписанного apt-репозитория:

```sh
sudo apt-get update &&
sudo apt-get install -y docker.io apt-transport-https ca-certificates curl gnupg
```

Следующий блок добавляет ключ и release-репозиторий gVisor, затем устанавливает runsc. Скобки ограничивают действие set -e/set -o pipefail текущим блоком:

```bash
(
  set -e
  set -o pipefail
  curl -fsSL https://gvisor.dev/archive.key |
    sudo gpg --dearmor -o /usr/share/keyrings/gvisor-archive-keyring.gpg
  printf 'deb [arch=%s signed-by=/usr/share/keyrings/gvisor-archive-keyring.gpg] https://storage.googleapis.com/gvisor/releases release main\n' "$(dpkg --print-architecture)" |
    sudo tee /etc/apt/sources.list.d/gvisor.list >/dev/null
  sudo apt-get update
  sudo apt-get install -y runsc
)
```

После установки студент перезапустил Docker внутри Lima и получил фактические версии и список runtimes:

```sh
sudo systemctl restart docker &&
runsc --version &&
sudo docker version &&
sudo docker info --format '{{json .Runtimes}}' &&
sudo cat /etc/docker/daemon.json
```

Результат: runsc release-20261019.0, Docker 26.1.5+dfsg1, containerd 1.7.24~ds1, runc 1.1.15+ds1. В info только runc и io.containerd.runc.v2, daemon.json отсутствует. Причина пропуска авторегистрации подтверждена чтением /var/lib/dpkg/info/runsc.postinst. Снимок и протокол — [установка](evidence/part-7-install.md).

## Выполненный шаг: регистрация runtime

Внутри Lima выполнить:

```sh
sudo runsc install --runtime=runsc -- --platform=systrap &&
sudo systemctl restart docker &&
sudo docker info --format '{{range $name, $runtime := .Runtimes}}{{println $name $runtime.Path}}{{end}}' &&
sudo cat /etc/docker/daemon.json
```

runsc install создаёт конфигурацию Docker и добавляет именованный runtime; аргумент после -- явно выбирает systrap. Перезапуск заставляет Docker прочитать настройку. Краткий вывод info показывает только имена и пути, без большого списка возможностей runc. Ожидаются runtime runsc с путём /usr/bin/runsc и runtimeArgs с --platform=systrap. Само наличие бинарника до регистрации ещё не позволяет Docker выбирать его через --runtime=runsc.

Регистрация подтверждена: Docker видит runsc с путём /usr/bin/runsc; daemon.json содержит --platform=systrap. [Снимок и протокол](evidence/part-7-runtime-registered.md). Повторная read-only проверка подтвердила конфигурацию; Docker в Lima пока не содержит образов и контейнеров, порт 18084 свободен.

## Выполненный шаг: тот же образ под gVisor

В новом терминале macOS экспортировать образ из Docker Desktop и скопировать в Lima. Образ не пересобирается, поэтому сохраняем содержимое приложения и его image ID:

```sh
docker --context desktop-linux image save -o /tmp/lab1-api-multi-runsc.tar lab1-api:multi &&
limactl copy /tmp/lab1-api-multi-runsc.tar container-lab:/tmp/lab1-api-multi-runsc.tar
```

В существующем терминале Lima загрузить образ и проверить ID:

```sh
sudo docker image load -i /tmp/lab1-api-multi-runsc.tar &&
sudo docker image inspect lab1-api:multi --format '{{.Id}} {{.Os}}/{{.Architecture}}'
```

Ожидается sha256:df2e57025cd2bd967bd1dddf89ca3c77741e19066377c177404611814c7166b6, linux/arm64. Если ID отличается, сначала разобрать перенос.

В Lima запустить API через зарегистрированный runsc:

```sh
sudo docker run -d --pull=never --name lab1-api-gvisor \
  --runtime=runsc --hostname lab-api \
  --cap-drop ALL --security-opt no-new-privileges=true \
  -p 127.0.0.1:18084:8080 lab1-api:multi &&
sudo docker inspect lab1-api-gvisor \
  --format 'status={{.State.Status}} runtime={{.HostConfig.Runtime}} image={{.Image}} host_pid={{.State.Pid}}' &&
sudo docker logs lab1-api-gvisor &&
curl --noproxy '*' --retry 5 --retry-connrefused --retry-delay 1 \
  --max-time 3 -i http://127.0.0.1:18084/health
```

--runtime=runsc выбирает gVisor вместо обычного runc. USER 65532 задан самим образом; capabilities удалены и no-new-privileges включён. Порт опубликован на loopback Linux VM: curl выполняется внутри Lima. В scratch нет shell, поэтому для проверки не используется docker exec sh. Контейнер оставляем доступным для inspect/logs и разбора ошибки.

Это первоначальная проверка совместимости и выбора runtime: индивидуальные memory/CPU/pids лимиты здесь не заданы, /eat и /burn не вызывались. Она не повторяет ресурсный профиль части 5 и не является сравнением производительности. Получены status=running, runtime=runsc, прежний image ID и HTTP 200 с ok. В логе API pid=1, uid=65532. Host-процесс 9383 — gvisor_sentry, exe=/usr/bin/gvisor-bin/gvisor_sentry, в аргументах --platform=systrap. [Оба снимка и протокол](evidence/part-7-api.md). Контейнер оставлен работающим.

## Сравнение трёх вариантов

| Вариант | Кто обслуживает системные вызовы API | Файловая система и управление |
| --- | --- | --- |
| Наш mydocker.sh | Linux-ядро VM; namespaces, cgroups, capabilities и seccomp ограничивают доступ | Корень VM, отдельный mount namespace; без подготовленного rootfs и менеджера образов |
| Обычный Docker/runc | Linux-ядро Docker-хоста; те же базовые механизмы ограничений | Rootfs из образа, writable layer, volumes, управление жизненным циклом и сетью |
| Docker/runsc | Sentry реализует Linux-интерфейс в userspace; сам ограниченно обращается к Linux-хосту | Тот же OCI-образ API; дополнительная граница между приложением и host kernel |

У обычного Linux-контейнера общий с Linux-хостом kernel: смена rootfs, PID или UID namespace отдельного ядра не создаёт. Ошибка в доступной приложению части ядра потенциально затрагивает границу изоляции. На macOS Linux-хостом Docker является его VM, а не ядро Darwin.

gVisor перехватывает вызовы и реализует их семантику в Sentry; это не только фильтрация разрешённых syscall. Обращения Sentry к хосту не обязаны соответствовать вызовам приложения один к одному. Сам Sentry дополнительно ограничен механизмами Linux. Host kernel остаётся частью системы, а совместимость Linux ABI и накладные расходы могут отличаться от runc. Эксперимент подтвердил запуск, но не измерял степень защищённости или скорость. Архитектурное сравнение основано на [официальном описании](https://gvisor.dev/docs/architecture_guide/intro/).

## Проверка понимания — пройдена

1. Если сделать seccomp-профиль обычного Docker строже, станет ли он эквивалентом gVisor? Чем отличается обработка разрешённого syscall?
2. Какую часть ОС обычный контейнер всегда делит с Linux-хостом и почему отдельные rootfs и PID namespace не устраняют этот предел изоляции?

26 октября студент верно ответил: seccomp управляет допуском syscall, а gVisor обрабатывает их в Sentry; обычный контейнер делит ядро Linux-хоста, тогда как rootfs и PID namespace меняют видимость без создания отдельного ядра. Оба ответа без ошибок. Практика и понимание подтверждены, часть 7 завершена. Далее — [подготовка мониторинга](part-8-monitoring.md).

Источники: [официальная установка](https://gvisor.dev/docs/user_guide/install/), [платформы и systrap](https://gvisor.dev/docs/architecture_guide/platforms/), [платформа по умолчанию](https://gvisor.dev/docs/user_guide/platforms/), [Docker runtime](https://gvisor.dev/docs/user_guide/quick_start/docker/), [архитектура изоляции](https://gvisor.dev/docs/architecture_guide/intro/).
