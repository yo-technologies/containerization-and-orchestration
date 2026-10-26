# Часть 3: подготовка к опыту с pids

Источник: чтение состояния VM ассистентом через `limactl shell --workdir=/tmp container-lab` 22 сентября 2026 года, после принятия ответов по CPU. Команды ничего не меняли и не запускали нагрузку.

Получено:

```text
uid=501(semyondanshin) gid=1000(semyondanshin) groups=1000(semyondanshin),999(systemd-journal)
stress-ng: not installed
/usr/bin/setpriv
cpuset cpu io memory pids
lab1-pids: absent
```

Проверки наличия `stress-ng` и каталога `lab1-pids` выполнены условными командами оболочки; строки `not installed` и `absent` описывают их результат. `/usr/bin/setpriv` — вывод `command -v setpriv`; список контроллеров получен чтением корневого `cgroup.subtree_control`.

`pgrep -a -u 501 -x lab1-api` не нашёл процессов. Чтение `/sys/fs/cgroup/lab1-cpu/cgroup.procs` также не вывело PID. Это дополнительное подтверждение завершения предыдущего CPU-опыта после SIGTERM, не содержащееся в его исходном снимке.

Следующий предложенный шаг — установить `stress-ng` из пакетов Debian и создать пустую группу `lab1-pids` с `pids.max = 32`. Установка, создание группы и нагрузка на момент этой записи ещё не выполнены.
