# Текущий опыт: запрет getppid через seccomp

Подготовлено 26 октября 2026 года. Это один шаг части 4, не готовый запускатель из части 5.

`deny-getppid.json` разрешает системные вызовы по умолчанию, но для `getppid` возвращает errno 1 (`EPERM`). Вызов не требует специальных полномочий; сравнение до и после позволяет отличить отказ seccomp от отказа из-за capabilities.

`run.py` читает этот профиль, через libseccomp разрешает имя syscall для текущей архитектуры, задаёт `no_new_privs`, устанавливает фильтр и заменяет себя указанной программой через `exec`. Поддерживаются только поля и действия этого учебного профиля. `probe.py` делает настоящий syscall и показывает результат, errno и состояние seccomp процесса. Номер syscall не зашит под ARM64 или x86-64.

Исходники этих вспомогательных инструментов подготовлены ассистентом. Проверены синтаксис Python и JSON. Затем студент применил фильтр; отказ getppid подтверждён [снимком и выводом](../evidence/part-4-seccomp-getppid.md).

## Подготовка VM — выполнено ассистентом

VM `container-lab` запущена. Текущая среда: Linux `6.12.107+deb13-cloud-arm64 aarch64`, UID 501, GID 1000. Доступны `/usr/bin/python3`, `/usr/bin/setpriv` и `/lib/aarch64-linux-gnu/libseccomp.so.2`. Исходное состояние проверяющего процесса: `NoNewPrivs = 0`, `Seccomp = 0`, `Seccomp_filters = 0`. Старый API не найден, `/tmp/lab1-api` отсутствует после перезапуска. Перед последующей проверкой HTTP бинарник потребуется восстановить.

Файлы скопированы в VM:

- `/tmp/lab1-seccomp-run.py`;
- `/tmp/lab1-seccomp-probe.py`;
- `/tmp/lab1-seccomp-deny-getppid.json`.

## Выполнить студенту

Открыть обычную оболочку VM:

```sh
limactl shell --workdir=/tmp container-lab
```

Прочитать правило и сравнить одну программу без фильтра и с фильтром:

```sh
cat /tmp/lab1-seccomp-deny-getppid.json
python3 /tmp/lab1-seccomp-probe.py
python3 /tmp/lab1-seccomp-run.py /tmp/lab1-seccomp-deny-getppid.json \
  python3 /tmp/lab1-seccomp-probe.py
```

В первом запуске ожидаются `Seccomp: 0` и положительный результат `getppid` с errno 0. Во втором — `NoNewPrivs: 1`, `Seccomp: 2`, `Seccomp_filters: 1`, `getppid result=-1 errno=1 (EPERM)`. Число `Seccomp: 2` обозначает режим фильтрации. Наборы capabilities проверяются у самой probe, не у загрузчика. Фильтр применяется к запускаемому процессу; обычная управляющая оболочка остаётся снаружи.

Результат получен 26 октября: без фильтра UID 501, PID 973, `CapEff = 0`, `NoNewPrivs = 0`, `Seccomp = 0`, `Seccomp_filters = 0`, `getppid = 966`, errno 0. С фильтром UID 501, PID 974, `CapEff = 0`, `NoNewPrivs = 1`, `Seccomp = 2`, `Seccomp_filters = 1`, `getppid = -1`, errno 1 (`EPERM`). Отказ подтверждён; к части 5 пока не переходим.

## API под тем же профилем — подтверждено

Ассистент повторно собрал прежний сервис для Linux ARM64 с `CGO_ENABLED=0` и скопировал его в `/tmp/lab1-api`, права `0755`. Проверены статическая ELF ARM64-сборка и совпадение SHA-256 на macOS и в VM: `4fa14e29b5e459bf8e2799d3d314de0181858319db7e2f24212ecaea8a1519a4`. Перед подготовкой процесса API и listener на порту 18080 в VM не было. Сервис ассистентом не запускался.

В текущей обычной оболочке VM выполнить:

```sh
python3 /tmp/lab1-seccomp-run.py /tmp/lab1-seccomp-deny-getppid.json \
  /tmp/lab1-api -listen 127.0.0.1:18080
```

Оставить сервис в переднем плане. Во втором терминале войти в обычную оболочку той же VM:

```sh
limactl shell --workdir=/tmp container-lab
```

Выполнить проверку состояния именно API и его HTTP-ответа:

```sh
lab_pid=$(pgrep -u 501 -x lab1-api)
if [[ "$lab_pid" =~ ^[0-9]+$ ]]; then
  ps -p "$lab_pid" -o pid,uid,args
  grep -E '^(CapEff|NoNewPrivs|Seccomp|Seccomp_filters):' "/proc/$lab_pid/status"
  curl --noproxy '*' --max-time 3 -i http://127.0.0.1:18080/health
else
  printf 'Ожидался один API; найдены PID: %s\n' "$lab_pid"
fi
```

В этом отдельном опыте сеть остаётся общей с VM, поэтому curl не требуется nsenter. Загрузчик применяет профиль к своему процессу и заменяет себя API через exec.

Результат получен 26 октября: API с PID 1105, UID 501 имеет `CapEff = 0`, `NoNewPrivs = 1`, `Seccomp = 2`, `Seccomp_filters = 1`; `/health` вернул HTTP 200 с `ok`. Полный вывод и снимок — [API под seccomp](../evidence/part-4-seccomp-api.md). API на момент проверки работает, остановка не подтверждена.

## Проверка понимания — пройдена

1. Чем seccomp отличается от capabilities: почему getppid запрещён, хотя обычно доступен обычному пользователю?
2. Если API создаст дочерний процесс и тот выполнит exec, сможет ли он вызвать getppid? Почему?

26 октября студент верно ответил: capabilities ограничивают полномочия, seccomp — системные вызовы; фильтр наследуется дочерними процессами и сохраняется после exec. Практика и проверка понимания seccomp завершены. Далее начата [сборка запускателя, шаг 5.1](../part-5-launcher.md); готового единого запуска API пока нет.

Источники: [seccomp(2)](https://man7.org/linux/man-pages/man2/seccomp.2.html), [seccomp_rule_add(3)](https://man7.org/linux/man-pages/man3/seccomp_rule_add.3.html), [seccomp_load(3)](https://man7.org/linux/man-pages/man3/seccomp_load.3.html).
