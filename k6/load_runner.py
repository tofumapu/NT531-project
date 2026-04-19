#!/usr/bin/env python3
"""
Python Load Runner - thay thế k6 cho NT531 demo
(Vì k6 binary tải chậm trên WSL, dùng Python stdlib là đủ cho các kịch bản này.)

Usage:
  python3 load_runner.py --url http://40.81.186.16:31000 \
      --concurrency 10 --duration 600 --output normal-summary.json

Fields output:
  total_requests, rps, error_rate, latency_avg/p50/p95/p99 (ms), duration_s
"""
import argparse
import json
import random
import statistics
import sys
import threading
import time
import urllib.request
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

CHOICES = ["a", "b"]


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.latencies_ms: list[float] = []
        self.success = 0
        self.errors = 0
        self.start_ts = None

    def record(self, latency_ms: float, ok: bool):
        with self.lock:
            self.latencies_ms.append(latency_ms)
            if ok:
                self.success += 1
            else:
                self.errors += 1


def submit_vote(url: str) -> tuple[float, bool]:
    """POST 1 vote, trả (latency_ms, ok)."""
    choice = random.choice(CHOICES)
    data = urllib.parse.urlencode({"vote": choice}).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            resp.read(16)  # discard body
            ok = 200 <= resp.status < 400
    except Exception:
        ok = False
    latency_ms = (time.perf_counter() - t0) * 1000
    return latency_ms, ok


def worker(url: str, stats: Stats, stop_event: threading.Event, sleep_s: float):
    while not stop_event.is_set():
        latency, ok = submit_vote(url)
        stats.record(latency, ok)
        if sleep_s > 0:
            time.sleep(sleep_s + random.random() * sleep_s * 0.5)


def percentile(sorted_list, p):
    if not sorted_list:
        return 0.0
    idx = int(len(sorted_list) * p)
    return sorted_list[min(idx, len(sorted_list) - 1)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", required=True, help="Vote service URL e.g. http://node:31000")
    parser.add_argument("--concurrency", type=int, default=10, help="VUs")
    parser.add_argument("--duration", type=int, default=120, help="seconds")
    parser.add_argument("--sleep", type=float, default=0.5, help="sleep between requests per VU")
    parser.add_argument("--output", default="-", help="JSON output file or '-' for stdout-only")
    parser.add_argument("--scenario", default="custom", help="scenario name in JSON output")
    args = parser.parse_args()

    print(f"[load_runner] URL={args.url} VU={args.concurrency} duration={args.duration}s sleep={args.sleep}")
    stats = Stats()
    stop = threading.Event()
    stats.start_ts = time.time()
    t0 = time.perf_counter()

    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        for _ in range(args.concurrency):
            pool.submit(worker, args.url, stats, stop, args.sleep)
        # ramp progress
        end = t0 + args.duration
        while time.perf_counter() < end:
            elapsed = int(time.perf_counter() - t0)
            with stats.lock:
                cnt = stats.success + stats.errors
                rps = cnt / elapsed if elapsed > 0 else 0
                err = stats.errors / cnt * 100 if cnt > 0 else 0
            print(f"  [{elapsed:4d}s] requests={cnt:6d} rps={rps:6.1f} errors={err:5.2f}%", flush=True)
            time.sleep(15)
        stop.set()

    duration = time.perf_counter() - t0
    sorted_lat = sorted(stats.latencies_ms)
    total = stats.success + stats.errors
    summary = {
        "scenario": args.scenario,
        "url": args.url,
        "concurrency": args.concurrency,
        "duration_s": round(duration, 1),
        "total_requests": total,
        "success": stats.success,
        "errors": stats.errors,
        "error_rate_pct": round(stats.errors / total * 100, 2) if total else 0,
        "rps_avg": round(total / duration, 1) if duration > 0 else 0,
        "latency_ms": {
            "avg": round(statistics.mean(sorted_lat), 1) if sorted_lat else 0,
            "min": round(min(sorted_lat), 1) if sorted_lat else 0,
            "p50": round(statistics.median(sorted_lat), 1) if sorted_lat else 0,
            "p90": round(percentile(sorted_lat, 0.90), 1),
            "p95": round(percentile(sorted_lat, 0.95), 1),
            "p99": round(percentile(sorted_lat, 0.99), 1),
            "max": round(max(sorted_lat), 1) if sorted_lat else 0,
        },
        "started_at": stats.start_ts,
        "ended_at": time.time(),
    }

    print()
    print("=" * 56)
    print(f"=== {args.scenario.upper()} SUMMARY ===")
    print(json.dumps(summary, indent=2))
    print("=" * 56)

    if args.output != "-":
        with open(args.output, "w") as f:
            json.dump(summary, f, indent=2)
        print(f"Saved to {args.output}")

    if total > 0 and stats.errors / total > 0.50:
        print("WARN: error rate > 50%")
        sys.exit(2)


if __name__ == "__main__":
    main()
