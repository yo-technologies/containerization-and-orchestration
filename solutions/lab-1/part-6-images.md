# Часть 6: образы и сохранность данных

Начато 26 октября 2026 года после закрытия части 5. Последовательность: обычная сборка и HTTP-проверка; multi-stage и сравнение размера/слоёв; повторная сборка и cache; потеря файла при пересоздании и сохранение через volume. Обе сборки, HTTP, сравнение, cache и понимание образов/cache подтверждены; текущий шаг — опыт с сохранностью данных.

## Подготовка первого Dockerfile

Подготовлен [Dockerfile.single](api/Dockerfile.single). Одна стадия на Go SDK компилирует исходники в `/api` и запускает бинарник от UID/GID 65532. Go toolchain, исходники и файловый build cache остаются в конечном образе: это исходный вариант для последующего сравнения. `CGO_ENABLED=0` и одинаковые будущие флаги сборки позволят сравнить именно состав финального образа.

Go-модуль требует Go 1.24 и не имеет внешних зависимостей, поэтому отдельный go.sum и go mod download не добавляются. Базовый тег `golang:1.26.8-alpine3.23` проверен через manifest inspect: есть linux/arm64. Локальный именованный Go-образ перед сборкой не найден; download и build студентом ещё не выполнены, наличие отдельных blob в BuildKit cache не проверялось. Тег учебного результата `lab1-api:single` отсутствует.

[.dockerignore](api/.dockerignore) допускает в контекст только go.mod и Go-файлы. Существующий bin с macOS/Linux-бинарниками исключён. Dockerfile сам передаётся builder для исполнения, но не копируется в образ. Приложение слушает `0.0.0.0:8080`; EXPOSE — метаданные, публикацию порта задаёт docker run.

Перед подготовкой проверены отсутствие listener на macOS TCP 18082 и отсутствие контейнера `lab1-api-single`. Docker Desktop работает в `desktop-linux`, linux/arm64. Предыдущий контейнер `lab1-docker-compare` работает отдельно и не изменялся ассистентом.

## Выполнить студенту на macOS

Из каталога отчёта посмотреть Dockerfile и собрать образ:

```sh
cd /Users/semyondanshin/Programming/Uni/containerization-and-orchestration/solutions/lab-1
cat api/Dockerfile.single
docker --context desktop-linux build --progress=plain \
  -f api/Dockerfile.single -t lab1-api:single api &&
docker --context desktop-linux image inspect lab1-api:single \
  --format 'size_bytes={{.Size}} filesystem_layers={{len .RootFS.Layers}}' &&
docker --context desktop-linux history lab1-api:single
```

`api` — build context. Сборка компилирует API из исходников в Linux, а не копирует подготовленный бинарник с macOS. Показатель Size относится к локальному образу и нужен для сравнения двух вариантов; он не равен размеру загрузки из registry. History включает также metadata-инструкции с нулевым размером, поэтому число строк не обязано совпадать с RootFS.Layers.

После успешной сборки запустить образ и проверить HTTP:

```sh
docker --context desktop-linux run -d --rm --name lab1-api-single \
  --memory 64m --memory-swap 64m --cpus 0.5 --pids-limit 32 \
  --cap-drop ALL --security-opt no-new-privileges=true \
  --security-opt "seccomp=$PWD/seccomp/deny-getppid.json" \
  -p 127.0.0.1:18082:8080 lab1-api:single &&
curl --noproxy '*' --retry 5 --retry-connrefused --retry-delay 1 \
  --max-time 3 -i http://127.0.0.1:18082/health
```

API теперь находится в образе и запускается без bind mount бинарника. Host-порт ограничен loopback macOS. При ошибке сборки запуск контейнера не продолжать, прислать ошибку. Ожидаются успешная сборка, размер и слойность из inspect/history, затем HTTP 200 с ok. Значения размера заранее не выдумываются.

Результаты первого шага получены: размер 331828006 байт, 9 filesystem layers, HTTP 200 с ok. Сам API — 5636258 байт. Протокол, image/container ID и два снимка — [одноэтапный образ](evidence/part-6-single.md). Контейнер работает на localhost:18082; ассистент его не останавливал. Всего сохранено 23 снимка ЛР1.

## Multi-stage и cache — подтверждено

Подготовлен основной [Dockerfile](api/Dockerfile): builder полностью повторяет FROM/WORKDIR/COPY/RUN одноэтапного варианта, затем `FROM scratch` начинает финальную стадию, в которую копируется только `/api`. UID/GID, EXPOSE, ENTRYPOINT и CMD совпадают. Исходники и флаги компиляции не изменялись. Так сравнивается состав финального образа, а не разные варианты приложения.

`scratch` обозначает пустую базу. В финальном образе нет Go SDK, shell и системных утилит; наш статический API с CGO_ENABLED=0 работает без libc. Для проверяемого plain HTTP эндпоинта дополнительные runtime-файлы не добавляются. В существующем контейнере single измерен хеш API, но совпадение бинарника с новым образом можно подтвердить только после новой сборки.

Проверены отсутствие тега lab1-api:multi, контейнера lab1-api-multi и listener macOS на 18083. Multi-stage ассистентом не собирался. Используется уже проверенная база Go; совпавшие инструкции builder могут переиспользовать cache одноэтапной сборки.

На macOS из каталога отчёта выполнить:

```sh
cat api/Dockerfile
docker --context desktop-linux build --progress=plain \
  -f api/Dockerfile -t lab1-api:multi api &&
docker --context desktop-linux image inspect lab1-api:single lab1-api:multi \
  --format '{{index .RepoTags 0}} size_bytes={{.Size}} filesystem_layers={{len .RootFS.Layers}}' &&
docker --context desktop-linux history lab1-api:multi
```

После успешной сборки проверить второй API на другом локальном порту:

```sh
docker --context desktop-linux run -d --rm --name lab1-api-multi \
  --memory 64m --memory-swap 64m --cpus 0.5 --pids-limit 32 \
  --cap-drop ALL --security-opt no-new-privileges=true \
  --security-opt "seccomp=$PWD/seccomp/deny-getppid.json" \
  -p 127.0.0.1:18083:8080 lab1-api:multi &&
curl --noproxy '*' --retry 5 --retry-connrefused --retry-delay 1 \
  --max-time 3 -i http://127.0.0.1:18083/health
```

Затем повторить сборку без правок:

```sh
docker --context desktop-linux build --progress=plain \
  -f api/Dockerfile -t lab1-api:multi api
```

Результаты получены: multi-stage имеет размер 5636258 байт и один filesystem layer, API отвечает HTTP 200 с ok. Повторная сборка показывает CACHED у WORKDIR, COPY go.mod, COPY Go-файлов, RUN go build и финального COPY. Image ID не изменился. Дополнительное сравнение `/api` из обоих контейнеров дало один SHA-256 `8c794d09a9c9dff4abb2d0eb92acd8b80e78c35487982cd38bbc5917a26f568a`. Контейнер multi-stage работает на localhost:18083. Все три снимка и точные значения — [multi-stage/cache](evidence/part-6-multi-cache.md).

| Вариант | Size, байт | Filesystem layers | HTTP |
| --- | ---: | ---: | --- |
| Single | 331828006 | 9 | 200 ok |
| Multi-stage | 5636258 | 1 | 200 ok |

Финальный образ стал примерно в 58,87 раза меньше, на 98,30%. Это изменение состава образа при одинаковом API, не измерение производительности. Всего сохранено 26 снимков ЛР1.

## Понимание образов и cache — проверено

1. Почему Go SDK и сборочные слои не попали в финальный образ, хотя использовались при сборке?
2. Если изменить код ответа /health в main.go, но оставить Dockerfile прежним, сможет ли Docker взять старый результат RUN go build при следующей сборке? Почему?

26 октября студент верно ответил: в финальную стадию scratch копируется только бинарник, SDK остаётся в builder; изменение исходников инвалидирует COPY и следующий RUN. Оба ответа без ошибок. Сам эксперимент с изменением кода не проводился. Затем [выполнен опыт writable layer / volume](part-6-volumes.md): файл без mount потерян при пересоздании, с named volume сохранён. Финальная проверка понимания сохранности данных также пройдена 26 октября; часть 6 завершена.

Источники принципов: [официальное руководство Docker для Go](https://docs.docker.com/guides/golang/), [multi-stage](https://docs.docker.com/build/building/multi-stage/), [build cache](https://docs.docker.com/build/cache/invalidation/).
