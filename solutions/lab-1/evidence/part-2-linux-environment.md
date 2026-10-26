# Среда Linux для части 2

Дата получения: 22 сентября 2026 года. Экземпляр Lima: `container-lab`.

Студент прислал результаты:

```text
$ limactl shell container-lab uname -srm
Linux 6.12.95+deb13-cloud-arm64 aarch64
$ limactl shell container-lab id
uid=501(semyondanshin) gid=1000(semyondanshin) groups=1000(semyondanshin),999(systemd-journal)
$ limactl shell container-lab unshare --version
unshare from util-linux 2.41
```

Дополнительная read-only проверка ассистента через `limactl shell --workdir=/tmp container-lab` подтвердила того же пользователя и инструменты:

```text
/usr/bin/unshare
/usr/bin/nsenter
/usr/bin/ps
/usr/bin/ip
/usr/bin/curl
/usr/bin/hostname
```

`stat -fc %T /sys/fs/cgroup` вернул `cgroup2fs`. Локальный `limactl --version` вернул `limactl version 2.2.0`.

Подтверждена готовность Linux-среды. Создание namespaces и запуск API внутри них на этом шаге не выполнялись и этим протоколом не подтверждаются.
