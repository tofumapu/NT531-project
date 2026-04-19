// k6 baseline - Spike load
// 20 -> 200 VUs trong 30-60s
import http from 'k6/http';
import { check, sleep } from 'k6';

const voteUrl = __ENV.VOTE_URL || 'http://20.189.249.7:31000';
const choices = ['a', 'b'];

export const options = {
  stages: [
    { duration: '30s', target: 20 },     // warm-up nhẹ
    { duration: '15s', target: 200 },    // spike sharp
    { duration: '60s', target: 200 },    // hold spike 1 phút
    { duration: '30s', target: 0 },      // ramp-down
  ],
  // KHÔNG fail trên error rate cao – mục tiêu là đo phản ứng spike
  thresholds: {
    http_req_duration: ['p(99)<5000'],
  },
  summaryTrendStats: ['avg', 'min', 'med', 'p(90)', 'p(95)', 'p(99)', 'max'],
};

export default function () {
  const choice = choices[Math.floor(Math.random() * choices.length)];
  const r = http.post(
    `${voteUrl}/`,
    `vote=${choice}`,
    {
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      tags: { kind: 'vote' },
    }
  );
  check(r, { 'no 5xx': (x) => x.status < 500 });
  sleep(0.1);
}

export function handleSummary(data) {
  return {
    'stdout': textSummary(data),
    'baseline-spike-summary.json': JSON.stringify(data, null, 2),
  };
}

function textSummary(data) {
  const m = data.metrics;
  const get = (k, p) => (m[k] && m[k].values && m[k].values[p] !== undefined ? m[k].values[p].toFixed(1) : 'N/A');
  return `
=== k6 Spike Summary ===
Iterations:    ${m.iterations ? m.iterations.values.count : '?'}
RPS avg:       ${m.http_reqs ? (m.http_reqs.values.rate || 0).toFixed(1) : '?'}
HTTP errors:   ${m.http_req_failed ? (m.http_req_failed.values.rate * 100).toFixed(2) : '?'}%
Latency p95:   ${get('http_req_duration', 'p(95)')} ms
Latency p99:   ${get('http_req_duration', 'p(99)')} ms
Latency max:   ${get('http_req_duration', 'max')} ms
========================
`;
}
