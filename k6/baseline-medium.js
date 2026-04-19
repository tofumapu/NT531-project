// k6 baseline - Medium load
// 30-50 VUs, 10 phút
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend } from 'k6/metrics';

const voteUrl = __ENV.VOTE_URL || 'http://20.189.249.7:31000';
const choices = ['a', 'b'];

export const options = {
  stages: [
    { duration: '1m', target: 30 },
    { duration: '8m', target: 50 },
    { duration: '1m', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.02'],
    http_req_duration: ['p(95)<1500'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

const voteLatency = new Trend('vote_latency_ms');

export default function () {
  http.get(`${voteUrl}/`, { tags: { kind: 'get' } });
  const choice = choices[Math.floor(Math.random() * choices.length)];
  const r = http.post(
    `${voteUrl}/`,
    `vote=${choice}`,
    {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      tags: { kind: 'vote' },
    }
  );
  check(r, { 'POST vote 2xx': (x) => x.status >= 200 && x.status < 400 });
  voteLatency.add(r.timings.duration);
  sleep(0.5 + Math.random() * 0.5);
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data),
    'baseline-medium-summary.json': JSON.stringify(data, null, 2),
  };
}

function textSummary(data) {
  const m = data.metrics;
  const get = (k, p) => (m[k] && m[k].values && m[k].values[p] !== undefined ? m[k].values[p].toFixed(1) : 'N/A');
  return `
=== k6 Medium Load Summary ===
Iterations:    ${m.iterations ? m.iterations.values.count : '?'}
RPS avg:       ${m.http_reqs ? (m.http_reqs.values.rate || 0).toFixed(1) : '?'}
HTTP errors:   ${m.http_req_failed ? (m.http_req_failed.values.rate * 100).toFixed(2) : '?'}%
Latency p50:   ${get('http_req_duration', 'med')} ms
Latency p95:   ${get('http_req_duration', 'p(95)')} ms
Latency p99:   ${get('http_req_duration', 'p(99)')} ms
==============================
`;
}
