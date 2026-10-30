# Опыт с нехваткой CPU requests

`scheduling-pressure.yaml` задаёт 40 batch Pod, каждый с CPU request 500m и limit 2 CPU. Всего они запрашивают 20 CPU, а две ноды объявляют по 8 CPU, часть которых уже зарезервирована другими Pod. Поэтому часть batch должна остаться Pending с причиной Insufficient cpu.

В этом опыте `cpuWorkers: 0` и `memoryMiB: 0`: batch почти бездействует. У процесса остаются небольшие расходы runtime; memory request 16Mi и limit 64Mi заданы явно. Так проверяем учёт reservations scheduler без дополнительного вычислительного цикла на Mac. Batch сохраняет Burstable, низкий приоритет и preemptionPolicy Never.

Для batch разрешён RollingUpdate с `maxUnavailable: 100%` и `maxSurge: 0`. При первоначальном RollingUpdate сохранились пять старых Pod: контроллер удерживал минимальную доступность, пока новым Pod не хватало CPU requests. Так контроллер может завершить старые batch, даже когда часть новых намеренно остаётся Pending. У API сохранён RollingUpdate с нулём недоступных реплик.

Из корня репозитория, в терминале с k6:

```bash
DURATION=10m SUMMARY_PATH=solutions/lab-4/evidence/part-6-load-summary.json \
  k6 run solutions/lab-4/load/orders.js
```

Во втором терминале:

```bash
export KUBECONFIG="$HOME/.local/state/container-lab/lab4.kubeconfig"
helm upgrade shop solutions/lab-4/helm/shop \
  --kube-context kind-shop-lab4 -n shop --reset-values \
  -f solutions/lab-4/experiments/scheduling-pressure.yaml
```

Здесь специально не задан `--wait`: все 40 batch не поместятся. Успешный ответ Helm подтверждает применение манифестов; готовность всех Pod в этом опыте не ожидается. После появления событий scheduler сохранить Deployment, Pending Pod и FailedScheduling.

После фиксации нехватки CPU добавить новые высокоприоритетные API:

Если первый k6 уже завершён, запустить отдельный замер для preemption:

```bash
DURATION=5m SUMMARY_PATH=solutions/lab-4/evidence/part-6-preemption-load-summary.json \
  k6 run solutions/lab-4/load/orders.js
```

Во втором терминале применить:

```bash
helm upgrade shop solutions/lab-4/helm/shop \
  --kube-context kind-shop-lab4 -n shop --reset-values \
  -f solutions/lab-4/experiments/scheduling-pressure.yaml \
  -f solutions/lab-4/experiments/preemption.yaml
```

Число API вырастает с трёх до семи, ресурсы каждого остаются 250m/100Mi request=limit. Четыре новых API требуют ещё один CPU. На каждой ноде, где уже размещено максимально возможное число batch с request 500m, остаток меньше 500m; суммарно свободно меньше одного CPU. Поэтому для новых API ожидаем вытеснение batch. Конкретное число жертв зависит от размещения. Наблюдение события Preempted необходимо: одно только появление API не доказывает preemption.

Возврат к обычным значениям после завершения опыта:

```bash
helm upgrade shop solutions/lab-4/helm/shop \
  --kube-context kind-shop-lab4 -n shop --reset-values \
  --wait --timeout 5m
```

`--reset-values` нужен и при возврате: он сбрасывает прошлые overrides до defaults chart. Простое отсутствие `-f` само по себе не гарантирует сброс сохранённых значений release.

## Контрольные прогоны части 8

Повторный запуск сохранять под новым именем, чтобы не заменить существующее доказательство. Перед preemption сначала заменить обычный batch на один без вычислительной нагрузки:

```bash
export KUBECONFIG="$HOME/.local/state/container-lab/lab4.kubeconfig"
helm upgrade shop solutions/lab-4/helm/shop --kube-context kind-shop-lab4 \
  -n shop --reset-values -f solutions/lab-4/experiments/scheduling-pressure.yaml \
  -f solutions/lab-4/experiments/sla-preemption.yaml \
  -f solutions/lab-4/experiments/scheduling-idle.yaml --wait --timeout 5m
```

В первом терминале запустить k6, затем сразу во втором — наблюдатель. Он создаёт нехватку reservations, увеличивает API до семи и возвращает defaults.

```bash
DURATION=3m SUMMARY_PATH=solutions/lab-4/evidence/part-8-preemption-repeat-load-summary.json \
  k6 run solutions/lab-4/load/orders.js
# Во втором терминале:
python3 solutions/lab-4/platform/run-sla-preemption.py \
  --output solutions/lab-4/evidence/part-8-preemption-repeat.json
```

Для memory-контроля сначала выполнить `memory-eviction.py prepare` по [инструкции](../platform/memory-eviction.md). Затем аналогично запустить трёхминутный k6 и параллельно `run-memory-eviction.py --output ...` с отдельными именами memory-repeat. Наблюдатель после эвикшена останавливает pressure и возвращает конфигурацию. Не запускать оба вида перегрузки одновременно.

Графики экспортирует `platform/plot-sla.py` (Python с matplotlib и numpy):

```bash
python3 solutions/lab-4/platform/plot-sla.py --kind preemption \
  --summary solutions/lab-4/evidence/part-8-preemption-load-summary.json \
  --experiment solutions/lab-4/evidence/part-8-preemption.json \
  --output solutions/lab-4/evidence/part-8-preemption-sla \
  --kubeconfig "$KUBECONFIG"
```

Для второго графика заменить preemption на memory. Данные читаются через Kubernetes Service proxy, поэтому локальный port-forward не требуется.
