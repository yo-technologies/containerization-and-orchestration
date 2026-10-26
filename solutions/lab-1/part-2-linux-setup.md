# Подготовка Linux к части 2

Статус: подготовка среды выполнена. 22 сентября 2026 года студент прислал вывод проверок; результаты сохранены в [протоколе](evidence/part-2-linux-environment.md). Ниже оставлены команды для воспроизводимости, повторять установку и создание VM не требуется.

Цель следующего этапа — увидеть, как один процесс получает собственные PID, hostname, сеть, mount table, IPC и отображение UID. Для этого нужен Linux kernel. Учебная VM через Lima предоставит Linux-хост; namespaces будут создаваться внутри него отдельно.

На Mac обнаружены ARM64 и 16 GiB RAM. Для текущих экспериментов задаются 2 CPU, 2 GiB RAM и виртуальный диск 20 GiB. Выбран Debian 13; автоматическая установка containerd выключена, поскольку на текущем этапе нужен обычный Linux-процесс и ручной `unshare`.

На macOS:

```sh
brew install lima
limactl start --name=container-lab --vm-type=vz \
  --cpus=2 --memory=2 --disk=20 --containerd=none \
  template:debian-13
```

Если появится меню, выбрать `Proceed with the current configuration`. Дождаться сообщения `READY`. Команда скачает образ Linux и создаст локальную VM.

После запуска, также из терминала macOS:

```sh
limactl shell container-lab uname -srm
limactl shell container-lab id
limactl shell container-lab unshare --version
```

Вывод этих трёх команд уже получен: Linux `6.12.95+deb13-cloud-arm64 aarch64`, UID `501`, GID `1000`, util-linux `2.41`. Наличие `unshare` ещё не доказывает, что все нужные namespaces доступны: это будет проверено самим экспериментом.

Ранее запущенный API на macOS можно остановить через Ctrl+C в его терминале: исходный результат уже сохранён. Перед экспериментами процесс будет заново запущен и зафиксирован в Linux.

Документация, проверенная 22 сентября 2026 года: [установка Lima](https://lima-vm.io/docs/installation/), [параметры limactl start](https://lima-vm.io/docs/reference/limactl_start/), [запуск команд в VM](https://lima-vm.io/docs/usage/).
