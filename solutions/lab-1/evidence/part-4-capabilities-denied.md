# Часть 4: отказ смены hostname после удаления capabilities

Источник: снимок терминала студента от 22 сентября 2026 года. Продолжение [исходного замера](part-4-capabilities-baseline.md) в той же изолированной оболочке. Выполнен `exec setpriv` с очисткой bounding, inheritable и ambient наборов и флагом `--no-new-privs`, затем запущена новая bash в текущем процессе.

## Полученный вывод

```text
shell PID=1
uid=0(root) gid=0(root) groups=0(root),65534(nogroup)
CapInh: 0000000000000000
CapPrm: 0000000000000000
CapEff: 0000000000000000
CapBnd: 0000000000000000
CapAmb: 0000000000000000
NoNewPrivs: 1
hostname: you must be root to change the host name
hostname exit=1
cap-before
```

![UID 0 без capabilities: отказ смены hostname](part-4-capabilities-denied.png)

## Вывод

После `exec setpriv` процесс внутри PID namespace имеет PID 1 и UID 0, но все пять показанных наборов capabilities пусты. Попытка `hostname cap-after` завершается отказом и кодом 1; последующее чтение показывает прежнее имя `cap-before`. До удаления полномочий аналогичное действие успешно изменило hostname.

Текст утилиты «you must be root» не следует понимать как доказательство ненулевого UID: непосредственно перед ним `id` показывает UID 0. Для `sethostname` нужна `CAP_SYS_ADMIN` в user namespace, которому принадлежит UTS namespace; после удаления capabilities этого полномочия нет. Источник: [sethostname(2)](https://man7.org/linux/man-pages/man2/sethostname.2.html). Численный errno системного вызова не снимался: зафиксирован код завершения утилиты, а не трассировка syscall.

Это подтверждает отказ привилегированного действия при UID 0 без capabilities. Работа API без capabilities ещё не проверена; проверка понимания capabilities и опыт seccomp впереди.

Оригинальный снимок сохранён без изменений. SHA-256: `7bc29a90523dca33565b5c620cb1aff4a297d94437437e5312b473368133c6ca`.
