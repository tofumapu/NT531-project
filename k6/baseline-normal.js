// k6 baseline - Normal load
// 10-20 VUs, 10 phút
// Chạy: k6 run -e VOTE_URL=http://20.189.249.7:31000 baseline-normal.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const voteUrl = __ENV.VOTE_URL || 'http://20.189.249.7:31000';
const choices = ['a', 'b'];

export const options = {
  stages: [
    { duration: '30s', target: 10 },   // ramp-up
    { duration: '9m', target: 15 },    // hold ~10 phút tổng
    { duration: '30s', target: 0 },    // ramp-down
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],            // <1% lỗi
    http_req_duration: ['p(95)<800'],          // p95 <800ms
    'http_req_duration{kind:vote}': ['p(99)<2000'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const voteSubmits = new Counter('vote_submits');
const voteLatency = new Trend('vote_latency_ms');

export default function () {
  // GET trang vote (test app live)
  const r1 = http.get(`${voteUrl}/`, { tags: { kind: 'get' } });
  check(r1, { 'GET / 200': (r) => r.status === 200 });

  // Submit vote
  const choice = choices[Math.floor(Math.random() * choices.length)];
  const r2 = http.post(
    `${voteUrl}/`,
    `vote=${choice}`,
    {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      tags: { kind: 'vote' },
    }
  );
  check(r2, { 'POST vote 2xx': (r) => r.status >= 200 && r.status < 400 });
  voteSubmits.add(1);
  voteLatency.add(r2.timings.duration);

  sleep(1 + Math.random());
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data),
    'baseline-normal-summary.json': JSON.stringify(data, null, 2),
  };
}

// inline minimal textSummary tránh import từ jslib (offline-friendly)
function textSummary(data) {
  const m = data.metrics;
  const get = (k, p) => (m[k] && m[k].values && m[k].values[p] !== undefined ? m[k].values[p].toFixed(1) : 'N/A');
  return `
=== k6 Baseline Normal Summary ===
VUs max:       ${data.state.testRunDurationMs ? data.state.maxVUs || '?' : '?'}
Duration:      ${(data.state.testRunDurationMs / 1000).toFixed(1)}s
Iterations:    ${m.iterations ? m.iterations.values.count : '?'}
RPS avg:       ${m.http_reqs ? (m.http_reqs.values.rate || 0).toFixed(1) : '?'}
HTTP errors:   ${m.http_req_failed ? (m.http_req_failed.values.rate * 100).toFixed(2) : '?'}%
Latency avg:   ${get('http_req_duration', 'avg')} ms
Latency p50:   ${get('http_req_duration', 'med')} ms
Latency p95:   ${get('http_req_duration', 'p(95)')} ms
Latency p99:   ${get('http_req_duration', 'p(99)')} ms
Vote latency p95: ${m['http_req_duration{kind:vote}'] ? m['http_req_duration{kind:vote}'].values['p(95)'].toFixed(1) : '?'} ms
Vote submits:  ${m.vote_submits ? m.vote_submits.values.count : '?'}
==================================
`;
}
