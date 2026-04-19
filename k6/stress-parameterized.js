/**
 * stress-parameterized.js - Parameterized load test cho KB1 (5 runs)
 *
 * Usage:
 *   k6 run -e VUS=100 -e DURATION=3m -e RAMP=30s -e SCENARIO=normal \
 *          -e VOTE_URL=http://IP:31000 stress-parameterized.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend, Rate, Gauge } from 'k6/metrics';

const voteUrl  = __ENV.VOTE_URL  || 'http://localhost:31000';
const maxVUs   = parseInt(__ENV.VUS)      || 50;
const holdTime = __ENV.DURATION  || '3m';
const rampTime = __ENV.RAMP      || '30s';
const scenario = __ENV.SCENARIO  || 'unknown';
const sleepMs  = parseFloat(__ENV.SLEEP_MS || '500');

export const options = {
  stages: [
    { duration: rampTime, target: maxVUs },
    { duration: holdTime, target: maxVUs },
    { duration: rampTime, target: 0 },
  ],
  thresholds: {
    http_req_failed:   ['rate<0.20'],   // warn ở 20% - không fail test để đo được bottleneck
    http_req_duration: ['p(99)<15000'], // chỉ fail nếu p99 > 15s
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
};

// Custom metrics
const voteErrors    = new Counter('vote_errors_total');
const voteLatency   = new Trend('vote_latency_ms', true);
const errorRate     = new Rate('vote_error_rate');
const activeVUs     = new Gauge('active_vus_gauge');

export default function () {
  activeVUs.add(__VU);

  // GET trang chủ
  const r1 = http.get(`${voteUrl}/`, {
    tags: { kind: 'get_home', scenario: scenario },
    timeout: '10s',
  });
  const homeOk = check(r1, {
    'GET / status 200': (r) => r.status === 200,
    'GET / < 5s':       (r) => r.timings.duration < 5000,
  });
  errorRate.add(!homeOk);
  if (!homeOk) voteErrors.add(1);

  // POST vote
  const choice = Math.random() < 0.5 ? 'a' : 'b';
  const r2 = http.post(
    `${voteUrl}/`,
    `vote=${choice}`,
    {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      tags:    { kind: 'post_vote', scenario: scenario },
      timeout: '10s',
    }
  );
  const voteOk = check(r2, {
    'POST vote 2xx':  (r) => r.status >= 200 && r.status < 400,
    'POST vote < 8s': (r) => r.timings.duration < 8000,
  });
  errorRate.add(!voteOk);
  if (!voteOk) voteErrors.add(1);
  voteLatency.add(r2.timings.duration);

  sleep(sleepMs / 1000);
}

export function handleSummary(data) {
  const m   = data.metrics;
  const get = (k, p) => {
    const v = m[k] && m[k].values && m[k].values[p];
    return v !== undefined ? v.toFixed(2) : 'N/A';
  };
  const getInt = (k, p) => {
    const v = m[k] && m[k].values && m[k].values[p];
    return v !== undefined ? Math.round(v) : 'N/A';
  };

  const summary = {
    scenario:    scenario,
    vus_max:     maxVUs,
    duration_s:  data.state.testRunDurationMs / 1000,
    iterations:  getInt('iterations', 'count'),
    rps_avg:     parseFloat(get('http_reqs', 'rate') || 0).toFixed(1),
    errors_total: getInt('vote_errors_total', 'count'),
    error_rate_pct: (parseFloat(get('vote_error_rate', 'rate') || 0) * 100).toFixed(3),
    latency_ms: {
      avg: get('http_req_duration', 'avg'),
      p50: get('http_req_duration', 'med'),
      p90: get('http_req_duration', 'p(90)'),
      p95: get('http_req_duration', 'p(95)'),
      p99: get('http_req_duration', 'p(99)'),
      max: get('http_req_duration', 'max'),
    },
    vote_latency_ms: {
      p95: get('vote_latency_ms', 'p(95)'),
      p99: get('vote_latency_ms', 'p(99)'),
    },
    thresholds_passed: !Object.values(data.metrics).some(
      m2 => m2.thresholds && Object.values(m2.thresholds).some(t => !t.ok)
    ),
  };

  const text = `
╔══════════════════════════════════════════════════════╗
║  k6 Result: ${scenario.toUpperCase().padEnd(10)} | VUs: ${String(maxVUs).padEnd(5)} | ${new Date().toISOString()}
╠══════════════════════════════════════════════════════╣
║  Duration:    ${(data.state.testRunDurationMs/1000).toFixed(0)}s
║  Iterations:  ${summary.iterations}
║  RPS avg:     ${summary.rps_avg}
║  Errors:      ${summary.errors_total}  (${summary.error_rate_pct}%)
╠──────────────────────────────────────────────────────╣
║  Latency  p50: ${summary.latency_ms.p50}ms   p90: ${summary.latency_ms.p90}ms
║           p95: ${summary.latency_ms.p95}ms   p99: ${summary.latency_ms.p99}ms
║           max: ${summary.latency_ms.max}ms
║  Vote p95:    ${summary.vote_latency_ms.p95}ms
╠──────────────────────────────────────────────────────╣
║  Thresholds:  ${summary.thresholds_passed ? 'PASS ✓' : 'FAIL ✗'}
╚══════════════════════════════════════════════════════╝
`;

  return {
    stdout: text,
    'summary.json': JSON.stringify(summary, null, 2),
  };
}
