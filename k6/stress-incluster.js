/**
 * stress-incluster.js - k6 script chạy BÊN TRONG cluster
 * Đo app-side latency thuần túy, không có network WSL2→Azure overhead.
 *
 * Output: text summary + JSON block ở stdout (có delimiter để parse)
 *
 * Usage (trong cluster pod):
 *   k6 run -e VOTE_URL=http://vote:8080 -e VUS=100 -e DURATION=3m \
 *           -e RAMP=30s -e SCENARIO=r1-normal -e SLEEP_MS=300 \
 *           /scripts/stress-incluster.js
 */
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend, Rate } from 'k6/metrics';

const voteUrl  = __ENV.VOTE_URL  || 'http://vote:8080';
const maxVUs   = parseInt(__ENV.VUS)      || 50;
const holdTime = __ENV.DURATION  || '3m';
const rampTime = __ENV.RAMP      || '30s';
const scenario = __ENV.SCENARIO  || 'unknown';
const sleepMs  = parseFloat(__ENV.SLEEP_MS || '300');

export const options = {
  stages: [
    { duration: rampTime, target: maxVUs },
    { duration: holdTime, target: maxVUs },
    { duration: rampTime, target: 0 },
  ],
  thresholds: {
    // Threshold rộng – mục tiêu là đo, không fail sớm
    http_req_failed:   ['rate<0.30'],
    http_req_duration: ['p(99)<30000'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max', 'count'],
};

const voteErrors  = new Counter('vote_errors_total');
const voteLatency = new Trend('vote_latency_ms', true);
const errorRate   = new Rate('vote_error_rate');

export default function () {
  const r1 = http.get(`${voteUrl}/`, {
    tags: { kind: 'get_home' },
    timeout: '10s',
  });
  const homeOk = check(r1, {
    'GET / 200': (r) => r.status === 200,
    'GET / <5s': (r) => r.timings.duration < 5000,
  });
  errorRate.add(!homeOk);
  if (!homeOk) voteErrors.add(1);

  const choice = Math.random() < 0.5 ? 'a' : 'b';
  const r2 = http.post(
    `${voteUrl}/`,
    `vote=${choice}`,
    {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      tags:    { kind: 'post_vote' },
      timeout: '10s',
    }
  );
  const voteOk = check(r2, {
    'POST 2xx': (r) => r.status >= 200 && r.status < 400,
    'POST <8s': (r) => r.timings.duration < 8000,
  });
  errorRate.add(!voteOk);
  if (!voteOk) voteErrors.add(1);
  voteLatency.add(r2.timings.duration);

  sleep(sleepMs / 1000);
}

export function handleSummary(data) {
  const m   = data.metrics;
  const g   = (k, p) => { const v = m[k]&&m[k].values&&m[k].values[p]; return v!==undefined ? +v : null; };

  const summary = {
    scenario:        scenario,
    vus_max:         maxVUs,
    duration_s:      +(data.state.testRunDurationMs / 1000).toFixed(1),
    iterations:      g('iterations','count'),
    rps_avg:         +(g('http_reqs','rate')||0).toFixed(2),
    errors_total:    g('vote_errors_total','count') || 0,
    error_rate_pct:  +((g('vote_error_rate','rate')||0)*100).toFixed(4),
    latency_ms: {
      avg: +(g('http_req_duration','avg')||0).toFixed(2),
      p50: +(g('http_req_duration','med')||0).toFixed(2),
      p90: +(g('http_req_duration','p(90)')||0).toFixed(2),
      p95: +(g('http_req_duration','p(95)')||0).toFixed(2),
      p99: +(g('http_req_duration','p(99)')||0).toFixed(2),
      max: +(g('http_req_duration','max')||0).toFixed(2),
    },
    vote_post_p95: +(g('vote_latency_ms','p(95)')||0).toFixed(2),
  };

  const text = `
╔══════════════════════════════════════════════════════════╗
║  [IN-CLUSTER] ${scenario.padEnd(14)} | VUs: ${String(maxVUs).padEnd(5)}
╠══════════════════════════════════════════════════════════╣
║  Duration:  ${summary.duration_s}s   Iterations: ${summary.iterations}
║  RPS avg:   ${summary.rps_avg}
║  Errors:    ${summary.errors_total}  (${summary.error_rate_pct}%)
╠──────────────────────────────────────────────────────────╣
║  p50: ${summary.latency_ms.p50}ms   p90: ${summary.latency_ms.p90}ms
║  p95: ${summary.latency_ms.p95}ms   p99: ${summary.latency_ms.p99}ms
║  max: ${summary.latency_ms.max}ms
╚══════════════════════════════════════════════════════════╝
__K6_JSON_BEGIN__
${JSON.stringify(summary)}
__K6_JSON_END__
`;

  return { stdout: text };
}
