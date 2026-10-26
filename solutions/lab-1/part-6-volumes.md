# Часть 6.4: writable layer и named volume

Подготовлено 26 октября 2026 года после верных ответов по multi-stage и cache. Цель — записать один файл, пересоздать контейнер и сравнить хранение в writable layer с хранением в named volume. Обе части опыта выполнены и подтверждены; проверка понимания пройдена, часть 6 завершена.

## Подготовка и смысл опыта

Используется `lab1-api:single`, поскольку он содержит sh и файловые утилиты. Финальный scratch-образ этих программ не содержит. API запускать не требуется: entrypoint временно заменяется на sh. Пользователь образа сохраняется — UID/GID 65532. `/tmp` в single имеет mode 1777, owner 0:0, поэтому этот пользователь может создать свой файл.

Ассистент проверил отсутствие контейнера `lab1-storage-demo`, volume `lab1-data-20261026` и маркера `/tmp/lab1-note.txt` в работающем single. Проверены Image ID и права /tmp. Volume, новые контейнеры и marker-файлы ассистентом не создавались.

Каждый run ниже создаёт новый одноразовый контейнер и удаляет его после выхода благодаря --rm. Hostname выводится как краткий идентификатор, чтобы было видно пересоздание. Путь файла в обеих частях одинаковый. Named volume имеет собственный жизненный цикл и после удаления контейнеров остаётся. Простая остановка существующего контейнера без удаления сама по себе не уничтожает его writable layer.

## Выполнить студенту на macOS

Из каталога отчёта задать функцию только в текущем терминале:

```sh
cd /Users/semyondanshin/Programming/Uni/containerization-and-orchestration/solutions/lab-1
lab1_storage_run() {
  docker --context desktop-linux run --rm --name lab1-storage-demo \
    --network none --memory 64m --memory-swap 64m --cpus 0.5 --pids-limit 32 \
    --cap-drop ALL --security-opt no-new-privileges=true \
    --security-opt "seccomp=$PWD/seccomp/deny-getppid.json" \
    --entrypoint sh "$@"
}
```

### Без volume

Первый контейнер записывает файл в свой writable layer и завершается; --rm удаляет этот одноразовый контейнер. Второй создаётся из исходного образа и проверяет отсутствие файла:

```sh
lab1_storage_run lab1-api:single -ec \
  'printf "container=%s\n" "$(hostname)"; printf "layer-data\n" > /tmp/lab1-note.txt; cat /tmp/lab1-note.txt' &&
lab1_storage_run lab1-api:single -ec \
  'printf "container=%s\n" "$(hostname)"; test ! -e /tmp/lab1-note.txt; printf "file absent\n"'
```

Ожидаются разные container-идентификаторы, сначала `layer-data`, затем `file absent`. Образ не менялся: запись происходила в слое конкретного контейнера.

### С named volume

Создать новый учебный named volume и повторить запись/чтение в двух разных контейнерах:

```sh
docker --context desktop-linux volume create \
  --label course=container-lab --label purpose=lab1-storage lab1-data-20261026 &&
lab1_storage_run --mount type=volume,src=lab1-data-20261026,dst=/tmp \
  lab1-api:single -ec \
  'printf "container=%s\n" "$(hostname)"; printf "volume-data\n" > /tmp/lab1-note.txt; cat /tmp/lab1-note.txt' &&
lab1_storage_run --mount type=volume,src=lab1-data-20261026,dst=/tmp \
  lab1-api:single -ec \
  'printf "container=%s\n" "$(hostname)"; cat /tmp/lab1-note.txt'
```

Mount перекрывает /tmp контейнера хранилищем volume. При первом подключении пустой volume инициализируется из /tmp образа с его атрибутами; volume-nocopy не задан. Ожидаются два новых container-идентификатора и `volume-data` в обоих случаях. Именованный volume не удаляется вместе с --rm контейнером.

Результат получен: без volume первый контейнер вывел layer-data, новый — file absent; с volume два новых контейнера вывели volume-data. Все четыре идентификатора различаются. Протокол и снимок — [файловый опыт](evidence/part-6-volumes.md). Дополнительный inspect показал сохранённый named volume с ожидаемыми метками, одноразовый контейнер отсутствует. Учебный volume с файлом остаётся; удаление volumes не выполнялось.

## Проверка понимания сохранности данных — пройдена

1. Если создать контейнер без --rm и без volume, затем выполнить docker stop и docker start того же контейнера, сохранится ли файл? Почему?
2. Почему --rm удалил контейнеры, но файл в named volume остался? К чему привязан его жизненный цикл?

26 октября студент верно ответил: «файл останется в writable layer того же контейнера, stop/start его не удаляет»; «named volume — отдельный ресурс Docker, его жизненный цикл не привязан к контейнеру». Оба ответа без ошибок, часть 6 завершена. Stop/start без удаления в данном опыте не проверялся, вопрос проверяет понимание различия остановки и пересоздания. Следующий шаг — [подготовка gVisor](part-7-gvisor.md).

Источники: [container layer basics](https://docs.docker.com/engine/storage/), [volume lifecycle и инициализация](https://docs.docker.com/engine/storage/volumes/).
