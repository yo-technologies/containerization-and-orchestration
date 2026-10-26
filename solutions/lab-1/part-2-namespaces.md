# Часть 2: первый опыт с namespaces

Статус: опыты с оболочкой и API выполнены студентом. Сохранены [результат оболочки](evidence/part-2-shell-observation.md) и [результат API со снимками изнутри/снаружи](evidence/part-2-api-observation.md). Проверка понимания пройдена 22 сентября 2026 года и отражена в основном отчёте. Команды ниже оставлены для воспроизводимости; повторять запуск сейчас не требуется.

Открыть оболочку учебного Linux-хоста из терминала macOS:

```sh
limactl shell --workdir=/tmp container-lab
```

В этой оболочке выполнить без `sudo`:

```sh
unshare --user --map-root-user --pid --fork --mount --mount-proc \
  --net --uts --ipc bash --noprofile --norc
```

Команда создаёт шесть namespaces. `--map-root-user` отображает текущего пользователя в UID 0 внутри нового user namespace. `--fork` запускает дочерний процесс в новом PID namespace; `--mount-proc` предоставляет соответствующий ему `/proc`. Это важно для наблюдения процессов через `ps`. Mount namespace отдельно управляет монтированиями; он сам по себе не создаёт новый root filesystem. Основание: [unshare(1)](https://man7.org/linux/man-pages/man1/unshare.1.html); наличие использованных параметров сверено с `unshare --help` установленной версии 2.41.

В появившейся оболочке:

```sh
echo "shell PID=$$"
id
hostname lab-api
hostname
ps -eo pid,ppid,uid,comm
ip -br addr
cat /proc/self/uid_map
```

Здесь `hostname lab-api` меняет имя только в созданном UTS namespace. Сохранить вывод и пока оставить оболочку открытой. Ожидается PID оболочки 1, UID 0, hostname `lab-api`, единственный сетевой интерфейс `lo` в состоянии DOWN и отображение UID `0 → 501`. В `ps` будет видна сама диагностическая команда помимо оболочки. Это ожидаемые признаки, а не уже полученные результаты.

Не запускать этот опыт повторно внутри уже изолированной оболочки. Если `unshare` завершится ошибкой, прислать её вывод. Для выхода из этого промежуточного опыта служит `exit`.

## Продолжение: API вместо оболочки

Ассистент собрал вспомогательный сервис для Linux ARM64 и скопировал его в VM в `/tmp/lab1-api`. В уже открытой изолированной оболочке `bash-5.2#` выполнить:

```sh
ip link set lo up
exec /tmp/lab1-api -listen 127.0.0.1:18080
```

Первая команда включает loopback внутри отдельной сети. `exec` заменяет программу в текущем процессе и сохраняет его PID. Полученный стартовый JSON-лог подтвердил `pid: 1` и `uid: 0`; внешнее наблюдение — сохранение PID 1488. Основание механизма: [execve(2)](https://man7.org/linux/man-pages/man2/execve.2.html).

Открыть второй терминал macOS и войти в обычную оболочку Linux-хоста:

```sh
limactl shell --workdir=/tmp container-lab
```

В новом терминале Linux:

```sh
lab_pid=$(pgrep -u 501 -x lab1-api)
ps -p "$lab_pid" -o pid,ppid,uid,args
grep -E '^(Pid|NSpid|Uid):' "/proc/$lab_pid/status"
hostname
sudo nsenter -t "$lab_pid" --uts hostname
sudo nsenter -t "$lab_pid" --user --mount --pid --uts --ipc --net \
  ps -eo pid,ppid,uid,comm
sudo nsenter -t "$lab_pid" --net \
  curl --noproxy '*' --max-time 3 -i http://127.0.0.1:18080/health
```

Продолжать после `pgrep` следует, только если найден ровно один PID. Если сервис не найден или найдено несколько экземпляров, прислать вывод вместо выбора произвольного процесса.

`ps` и `/proc` снаружи показывают Linux PID и UID процесса; `NSpid` связывает внешнюю и внутреннюю нумерацию. Два вызова `hostname` сравнивают имена хоста и изолированной среды. `nsenter` запускает диагностику в уже существующих namespaces; для HTTP-доступа достаточно войти в сеть сервиса. Это не публикация порта на Linux-хост или Mac. Основание: [nsenter(1)](https://man7.org/linux/man-pages/man1/nsenter.1.html); параметры сверены с версией util-linux 2.41 внутри VM.

Вывод и снимки обоих терминалов получены: старт API с внутренним PID/UID и внешний `ps` плюс диагностика через `nsenter`. Вопросы части 2 и принятые после уточнений ответы отражены в основном отчёте. Cgroup-лимиты и ограничения привилегий относятся к последующим этапам.
