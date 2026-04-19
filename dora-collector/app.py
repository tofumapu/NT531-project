"""
DORA Metrics Collector - NT531 Nhóm 17

Service expose 4 DORA metrics tại /metrics:
  - dora_deployments_total              (Counter, label=status)
  - dora_deployment_frequency_24h       (Gauge)
  - dora_lead_time_seconds              (Gauge, label=pipeline)
  - dora_lead_time_seconds_p50/p95      (Gauge)
  - dora_change_failure_rate_24h        (Gauge, 0..1)
  - dora_failed_deployments_total       (Counter)
  - dora_mttr_seconds_latest            (Gauge)
  - dora_mttr_seconds_avg_24h           (Gauge)
  - dora_collector_last_run_timestamp   (Gauge)
  - dora_collector_errors_total         (Counter, label=source)

Nguồn dữ liệu:
  - Azure DevOps REST API: pipeline runs, finishTime, sourceVersion (commit SHA)
  - Git repo (qua Azure DevOps API): commit committer date
  - Prometheus API: alertmanager-style alerts để tính MTTR
  - Pushgateway: nhận deployment events từ pipeline (push thời điểm deploy thành công)
"""

import asyncio
import logging
import os
import statistics
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from dateutil import parser as date_parser
from fastapi import FastAPI, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    generate_latest,
)

# ---- Config từ env vars ----
AZDO_ORG = os.getenv("AZDO_ORG", "tofucut3")
AZDO_PROJECT = os.getenv("AZDO_PROJECT", "votingApp")
AZDO_PAT = os.getenv("AZDO_PAT", "")
PIPELINE_IDS = [int(x) for x in os.getenv("AZDO_PIPELINE_IDS", "1,2,3,4").split(",") if x]
PROM_URL = os.getenv("PROMETHEUS_URL", "http://prom-stack-kube-prometheus-prometheus.monitoring.svc:9090")
COLLECT_INTERVAL = int(os.getenv("COLLECT_INTERVAL_SECONDS", "60"))
LOOKBACK_DAYS = int(os.getenv("LOOKBACK_DAYS", "7"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("dora-collector")

# ---- Prometheus metrics ----
registry = CollectorRegistry()

deployments_total = Counter(
    "dora_deployments_total",
    "Tổng số deployment được phát hiện",
    ["status", "pipeline"],
    registry=registry,
)
failed_deployments_total = Counter(
    "dora_failed_deployments_total",
    "Tổng số deployment failed",
    ["pipeline"],
    registry=registry,
)
deployment_frequency_24h = Gauge(
    "dora_deployment_frequency_24h",
    "Số deployment thành công trong 24h gần nhất",
    registry=registry,
)
deployment_frequency_7d = Gauge(
    "dora_deployment_frequency_7d",
    "Số deployment thành công trong 7 ngày gần nhất",
    registry=registry,
)
lead_time_p50 = Gauge(
    "dora_lead_time_seconds_p50",
    "p50 lead time từ commit đến deploy thành công (giây)",
    registry=registry,
)
lead_time_p95 = Gauge(
    "dora_lead_time_seconds_p95",
    "p95 lead time từ commit đến deploy thành công (giây)",
    registry=registry,
)
lead_time_latest = Gauge(
    "dora_lead_time_seconds_latest",
    "Lead time của deploy gần nhất (giây)",
    registry=registry,
)
change_failure_rate = Gauge(
    "dora_change_failure_rate_24h",
    "Tỷ lệ deployment fail / tổng trong 24h (0..1)",
    registry=registry,
)
change_failure_rate_7d = Gauge(
    "dora_change_failure_rate_7d",
    "Tỷ lệ deployment fail / tổng trong 7 ngày (0..1)",
    registry=registry,
)
mttr_latest = Gauge(
    "dora_mttr_seconds_latest",
    "MTTR của incident gần nhất (giây)",
    registry=registry,
)
mttr_avg_24h = Gauge(
    "dora_mttr_seconds_avg_24h",
    "MTTR trung bình trong 24h (giây)",
    registry=registry,
)
mttr_avg_7d = Gauge(
    "dora_mttr_seconds_avg_7d",
    "MTTR trung bình trong 7 ngày (giây)",
    registry=registry,
)
incidents_count_24h = Gauge(
    "dora_incidents_count_24h",
    "Số incident trong 24h",
    registry=registry,
)
last_run_ts = Gauge(
    "dora_collector_last_run_timestamp",
    "Unix timestamp của lần collect gần nhất",
    registry=registry,
)
collector_errors = Counter(
    "dora_collector_errors_total",
    "Số lỗi khi collect dữ liệu",
    ["source"],
    registry=registry,
)
collector_up = Gauge(
    "dora_collector_up",
    "1 nếu collector đang chạy",
    registry=registry,
)
collector_up.set(1)


# ---- Helpers ----
async def azdo_get(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> dict:
    """Gọi Azure DevOps REST API với PAT auth."""
    url = f"https://dev.azure.com/{AZDO_ORG}/{AZDO_PROJECT}/{path}"
    auth = ("", AZDO_PAT)
    r = await client.get(url, params=params, auth=auth, timeout=20.0)
    r.raise_for_status()
    return r.json()


async def fetch_recent_builds(client: httpx.AsyncClient, since: datetime) -> list:
    """Lấy build runs từ tất cả pipelines từ thời điểm `since`."""
    all_builds = []
    for pid in PIPELINE_IDS:
        try:
            data = await azdo_get(
                client,
                "_apis/build/builds",
                {
                    "definitions": pid,
                    "minTime": since.isoformat(),
                    "queryOrder": "finishTimeDescending",
                    "api-version": "7.0",
                    "$top": 200,
                    "statusFilter": "completed",
                },
            )
            for b in data.get("value", []):
                b["_pipeline_id"] = pid
                all_builds.append(b)
        except Exception as e:
            log.warning("AzDO builds fetch lỗi pipeline=%s: %s", pid, e)
            collector_errors.labels(source="azdo_builds").inc()
    return all_builds


async def fetch_commit_time(client: httpx.AsyncClient, repo_id: str, commit_sha: str) -> Optional[datetime]:
    """Lấy commit author/committer time để tính lead time."""
    if not commit_sha or not repo_id:
        return None
    try:
        data = await azdo_get(
            client,
            f"_apis/git/repositories/{repo_id}/commits/{commit_sha}",
            {"api-version": "7.0"},
        )
        ts = data.get("committer", {}).get("date") or data.get("author", {}).get("date")
        if ts:
            return date_parser.isoparse(ts)
    except Exception as e:
        log.debug("Commit lookup lỗi sha=%s: %s", commit_sha[:8], e)
        collector_errors.labels(source="azdo_commits").inc()
    return None


async def fetch_prometheus_alerts(client: httpx.AsyncClient, since: datetime) -> list:
    """
    Query Prometheus ALERTS metric để lấy lịch sử alert firing.
    Dùng query_range trên ALERTS{alertstate="firing", severity="critical"}
    để tính MTTR (firing -> resolved).
    """
    try:
        end = datetime.now(timezone.utc)
        start = since
        params = {
            "query": 'ALERTS{alertstate="firing", severity="critical"}',
            "start": str(int(start.timestamp())),
            "end": str(int(end.timestamp())),
            "step": "60s",
        }
        r = await client.get(f"{PROM_URL}/api/v1/query_range", params=params, timeout=15.0)
        r.raise_for_status()
        return r.json().get("data", {}).get("result", [])
    except Exception as e:
        log.warning("Prometheus alerts fetch lỗi: %s", e)
        collector_errors.labels(source="prometheus").inc()
        return []


def compute_mttr_from_series(series_list: list) -> list[float]:
    """
    Mỗi series có values=[[ts, "1"], [ts, "1"], ...] – tìm các đoạn liên tục
    của firing rồi tính khoảng thời gian incident_end - incident_start.
    """
    durations = []
    for series in series_list:
        values = series.get("values", [])
        if not values:
            continue
        # gom đoạn liên tục (gap > 120s = incident mới)
        seg_start = float(values[0][0])
        seg_last = seg_start
        for ts_str, _ in values[1:]:
            ts = float(ts_str)
            if ts - seg_last > 120:  # gap >2 lần step => incident mới
                durations.append(seg_last - seg_start)
                seg_start = ts
            seg_last = ts
        durations.append(seg_last - seg_start)
    # bỏ các incident <30s vì có thể là noise
    return [d for d in durations if d >= 30]


async def collect_once():
    """Chu kỳ collect: gọi Azure DevOps + Prometheus, tính metrics."""
    log.info("=== Bắt đầu chu kỳ collect ===")
    now = datetime.now(timezone.utc)
    since_24h = now - timedelta(hours=24)
    since_lookback = now - timedelta(days=LOOKBACK_DAYS)

    async with httpx.AsyncClient() as client:
        # 1. Pipeline builds
        builds = await fetch_recent_builds(client, since_lookback)
        log.info("Lấy được %d builds trong %d ngày", len(builds), LOOKBACK_DAYS)

        success_24h = 0
        success_7d = 0
        failed_24h = 0
        failed_7d = 0
        lead_times: list[float] = []
        latest_lead_time = None

        # repo_id cache (mỗi pipeline dùng 1 repo, cache để giảm API call)
        repo_ids: dict[int, str] = {}

        for b in builds:
            finish_str = b.get("finishTime")
            if not finish_str:
                continue
            finish = date_parser.isoparse(finish_str)
            result = b.get("result", "")  # succeeded | failed | canceled | partiallySucceeded
            pid = str(b.get("_pipeline_id", "?"))

            if result == "succeeded":
                deployments_total.labels(status="success", pipeline=pid).inc()
                if finish >= since_24h:
                    success_24h += 1
                if finish >= since_lookback:
                    success_7d += 1

                # Lead time = finish - commit_time
                repo = b.get("repository", {})
                repo_id = repo.get("id")
                if repo_id:
                    repo_ids[b["_pipeline_id"]] = repo_id
                commit_sha = b.get("sourceVersion", "")
                commit_ts = await fetch_commit_time(client, repo_id, commit_sha)
                if commit_ts:
                    lt = (finish - commit_ts).total_seconds()
                    if lt > 0:
                        lead_times.append(lt)
                        if latest_lead_time is None:
                            latest_lead_time = lt
            elif result in ("failed", "canceled"):
                deployments_total.labels(status="failed", pipeline=pid).inc()
                failed_deployments_total.labels(pipeline=pid).inc()
                if finish >= since_24h:
                    failed_24h += 1
                if finish >= since_lookback:
                    failed_7d += 1

        # 2. Set gauges
        deployment_frequency_24h.set(success_24h)
        deployment_frequency_7d.set(success_7d)

        if lead_times:
            lead_times_sorted = sorted(lead_times)
            lead_time_p50.set(statistics.median(lead_times_sorted))
            idx95 = int(len(lead_times_sorted) * 0.95)
            lead_time_p95.set(lead_times_sorted[min(idx95, len(lead_times_sorted) - 1)])
        if latest_lead_time:
            lead_time_latest.set(latest_lead_time)

        total_24h = success_24h + failed_24h
        total_7d = success_7d + failed_7d
        if total_24h > 0:
            change_failure_rate.set(failed_24h / total_24h)
        if total_7d > 0:
            change_failure_rate_7d.set(failed_7d / total_7d)

        # 3. MTTR từ Prometheus alerts
        alert_series_24h = await fetch_prometheus_alerts(client, since_24h)
        durs_24h = compute_mttr_from_series(alert_series_24h)
        if durs_24h:
            mttr_avg_24h.set(statistics.mean(durs_24h))
            mttr_latest.set(durs_24h[-1])
            incidents_count_24h.set(len(durs_24h))
        else:
            incidents_count_24h.set(0)

        alert_series_7d = await fetch_prometheus_alerts(client, since_lookback)
        durs_7d = compute_mttr_from_series(alert_series_7d)
        if durs_7d:
            mttr_avg_7d.set(statistics.mean(durs_7d))

    last_run_ts.set(time.time())
    log.info(
        "=== Done. success_24h=%d failed_24h=%d cfr=%.2f%% mttr_24h=%.0fs ===",
        success_24h,
        failed_24h,
        (failed_24h / total_24h * 100) if total_24h else 0,
        statistics.mean(durs_24h) if durs_24h else 0,
    )


async def collector_loop():
    """Background task chạy collect_once mỗi COLLECT_INTERVAL giây."""
    while True:
        try:
            await collect_once()
        except Exception as e:
            log.error("collect_once unexpected error: %s", e, exc_info=True)
            collector_errors.labels(source="loop").inc()
        await asyncio.sleep(COLLECT_INTERVAL)


# ---- FastAPI app ----
app = FastAPI(title="DORA Collector - NT531 Nhóm 17")


@app.on_event("startup")
async def startup_event():
    log.info(
        "DORA collector starting | org=%s project=%s pipelines=%s interval=%ds",
        AZDO_ORG,
        AZDO_PROJECT,
        PIPELINE_IDS,
        COLLECT_INTERVAL,
    )
    asyncio.create_task(collector_loop())


@app.get("/metrics")
def metrics():
    return Response(generate_latest(registry), media_type=CONTENT_TYPE_LATEST)


@app.get("/healthz")
def healthz():
    return {"status": "ok", "last_run_ts": last_run_ts._value.get()}


@app.get("/")
def root():
    return {
        "service": "dora-collector",
        "project": "NT531 Nhóm 17",
        "endpoints": ["/metrics", "/healthz"],
    }
