import http from 'k6/http';
import { check } from 'k6';
import { Counter, Rate } from 'k6/metrics';

const baseURL = (__ENV.BASE_URL || 'http://127.0.0.1:18085').replace(/\/$/, '');
const rate = Number(__ENV.RATE || 10);
const duration = __ENV.DURATION || '15m';
const created = new Counter('orders_created');
const failed = new Rate('orders_failed');

if (!Number.isInteger(rate) || rate < 1) {
  throw new Error('RATE must be a positive integer');
}

export const options = {
  thresholds: {
    http_req_duration: ['p(95)<=300'],
    orders_failed: ['rate<=0.005'],
    dropped_iterations: ['count==0'],
  },
  scenarios: {
    orders: {
      executor: 'constant-arrival-rate',
      rate,
      timeUnit: '1s',
      duration,
      preAllocatedVUs: 10,
      maxVUs: 50,
      gracefulStop: '10s',
    },
  },
  summaryTrendStats: ['avg', 'med', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const response = http.post(
    `${baseURL}/order`,
    JSON.stringify({ item: 'krr-baseline', quantity: 1 }),
    {
      headers: { 'Content-Type': 'application/json' },
      tags: { name: 'POST /order' },
      timeout: '5s',
      redirects: 0,
    },
  );
  let hasID = false;
  if (response.status === 201) {
    try {
      const id = response.json('id');
      hasID = Number.isInteger(id) && id > 0;
    } catch (_) {
      // A malformed success response is still a failed order.
    }
  }
  check(response, {
    'order returned 201': (r) => r.status === 201,
    'order has an id': () => hasID,
  });
  failed.add(!hasID);
  if (hasID) created.add(1);
}

export function handleSummary(data) {
  const metric = (name) => (data.metrics[name] || {}).values || {};
  const compact = {
    target_rps: rate,
    duration,
    requests: metric('http_reqs').count || 0,
    achieved_rps: metric('http_reqs').rate || 0,
    orders_created: metric('orders_created').count || 0,
    failed_order_fraction: metric('orders_failed').rate ?? null,
    p95_ms: metric('http_req_duration')['p(95)'] ?? null,
    p99_ms: metric('http_req_duration')['p(99)'] ?? null,
    dropped_iterations: metric('dropped_iterations').count || 0,
  };
  compact.sla_passed = rate === 10 && compact.requests > 0
    && compact.p95_ms !== null && compact.p95_ms <= 300
    && compact.failed_order_fraction !== null && compact.failed_order_fraction <= 0.005
    && compact.dropped_iterations === 0;
  return {
    stdout: `\nOrder load result:\n${JSON.stringify(compact, null, 2)}\n`,
    [__ENV.SUMMARY_PATH || 'orders-summary.json']: JSON.stringify({
      finished_at: new Date().toISOString(),
      base_url: baseURL,
      sla: { target_rps: 10, max_p95_ms: 300, min_success_fraction: 0.995, max_dropped_iterations: 0 },
      result: compact,
      summary: data,
    }, null, 2),
  };
}
