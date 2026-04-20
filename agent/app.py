"""
AI Agent — NT531 Nhóm 17 (Part 4 / KB4)

Nhận Alertmanager webhook → enrich từ Prometheus → gọi LLM (OpenClaw + 9Router)
→ quyết định remediation → ghi decision-log → optional kubectl action.

Endpoints:
  POST /webhook/alert
  GET  /healthz
  GET  /metrics

LLM integration: thay TODO_LLM_CALL bằng OpenClaw client (9Router routing).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from kubernetes import client as k8s_client, config as k8s_config
from kubernetes.client.rest import ApiException
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Histogram,
    generate_latest,
)
from pydantic import BaseModel

# ----- config -----
DECISION_LOG_URL = os.getenv("DECISION_LOG_URL", "http://decision-log.monitoring:8000")
PROMETHEUS_URL   = os.getenv("PROMETHEUS_URL",   "http://prom-stack-kube-prometheus-prometheus.monitoring:9090")
ALERTMANAGER_URL = os.getenv("ALERTMANAGER_URL", "http://prom-stack-kube-prometheus-alertmanager.monitoring:9093")
# LLM gateway (9Router, OpenAI-compatible). Không cần API key — 9Router đã
# tự handle credential cho từng combo phía sau.
# Default trỏ tới IP gateway WSL→Windows host (vì 9Router chạy bên Windows).
# Khi chạy agent ngay trên Windows: override = http://localhost:20128/v1.
# Khi deploy lên AKS: tunnel ra public + override = https://<tunnel-host>/v1.
LLM_BASE_URL     = os.getenv("LLM_BASE_URL", "http://172.22.240.1:20128/v1")
LLM_MODEL        = os.getenv("LLM_MODEL", "devops-test-agent")
LLM_TIMEOUT      = float(os.getenv("LLM_TIMEOUT_SECONDS", "30"))
MAX_ACTIONS_PER_MIN = int(os.getenv("MAX_ACTIONS_PER_MINUTE", "3"))
MIN_CONFIDENCE      = float(os.getenv("MIN_CONFIDENCE_FOR_ACTION", "0.7"))
DRY_RUN             = os.getenv("AGENT_DRY_RUN", "false").lower() == "true"
# Sau khi agent thực hiện action, chờ tối đa N giây xem alert có resolved không.
# Nếu không resolved trong khoảng đó → escalate qua Telegram (cần human).
REMEDIATION_TIMEOUT = int(os.getenv("REMEDIATION_TIMEOUT_SECONDS", "180"))

# Telegram notifier (optional). Để trống cả 2 var → bỏ qua notify.
TELEGRAM_BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID    = os.getenv("TELEGRAM_CHAT_ID", "").strip()
TELEGRAM_API_BASE   = os.getenv("TELEGRAM_API_BASE", "https://api.telegram.org")
TELEGRAM_NOTIFY_RESOLVED = os.getenv("TELEGRAM_NOTIFY_RESOLVED", "true").lower() == "true"

# Azure DevOps integration (optional — dùng cho pipeline_rerun action).
# PAT nên được mount qua K8s Secret agent-azdo.
AZDO_ORG         = os.getenv("AZDO_ORG", "tofucut3")
AZDO_PROJECT     = os.getenv("AZDO_PROJECT", "votingApp")
AZDO_PIPELINE_ID = int(os.getenv("AZDO_PIPELINE_ID", "4"))
AZDO_BRANCH      = os.getenv("AZDO_BRANCH", "dependabot/npm_and_yarn/result/express-4.19.2")
AZDO_PAT         = os.getenv("AZDO_PAT", "").strip()

# ArgoCD integration
ARGOCD_APP_NAME  = os.getenv("ARGOCD_APP_NAME", "voteapp-service")
ARGOCD_NAMESPACE = os.getenv("ARGOCD_NAMESPACE", "argocd")

VALID_DECISIONS = {
    "rollback", "scale", "restart", "restart_daemonset",
    "patch_service_monitor", "argocd_sync", "pipeline_rerun",
    "suggest", "no_action",
}
VALID_ROOT_CAUSES = {
    "transient_crash", "bad_image", "resource_exhaustion",
    "network_dependency", "upstream_dependency_down", "flapping",
    "infra_config", "unknown",
}

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("agent")

# ----- metrics -----
m_decisions = Counter(
    "ai_agent_decisions_total",
    "Total decisions made by the agent",
    ["decision", "root_cause"],
)
m_actions = Counter(
    "ai_agent_actions_taken_total",
    "Total kubectl actions actually executed by the agent",
    ["action", "result"],   # result=success|failed|skipped_dry_run|skipped_low_confidence
)
m_confidence = Histogram(
    "ai_agent_decision_confidence",
    "Confidence score per decision",
    buckets=[0.1, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99],
)
m_llm_latency = Histogram(
    "ai_agent_llm_request_duration_seconds",
    "End-to-end LLM call duration",
    buckets=[0.1, 0.5, 1, 2, 3, 5, 8, 13, 21],
)
m_telegram = Counter(
    "ai_agent_telegram_notifications_total",
    "Telegram bridge notifications",
    ["status", "phase"],   # phase=firing|action_taken|resolved|escalation|self_heal; status=sent|skipped_*|failed
)
m_remediation = Counter(
    "ai_agent_remediation_outcome_total",
    "Outcome của lifecycle remediation tracker",
    ["outcome"],   # resolved_in_time | escalated | self_healed
)

# ----- safety: rate limiter -----
_action_history: deque[float] = deque()

def _can_act_now() -> bool:
    now = time.time()
    while _action_history and now - _action_history[0] > 60:
        _action_history.popleft()
    return len(_action_history) < MAX_ACTIONS_PER_MIN

def _record_action(now: float | None = None) -> None:
    _action_history.append(now or time.time())

# ----- helpers -----
async def query_prometheus(client: httpx.AsyncClient, expr: str) -> float:
    try:
        r = await client.get(
            f"{PROMETHEUS_URL}/api/v1/query",
            params={"query": expr},
            timeout=5.0,
        )
        r.raise_for_status()
        result = r.json()["data"]["result"]
        return float(result[0]["value"][1]) if result else 0.0
    except Exception as e:
        log.warning("prometheus query failed (%s): %s", expr, e)
        return 0.0


async def enrich_alert(client: httpx.AsyncClient, alert: dict) -> dict[str, float]:
    """Lấy context metrics quanh alert."""
    labels = alert.get("labels", {})
    pod = labels.get("pod", "")
    ns = labels.get("namespace", "default")
    queries = {
        "pod_restarts_5m": f'increase(kube_pod_container_status_restarts_total{{namespace="{ns}",pod=~"{pod}.*"}}[5m])',
        "pod_ready":       f'kube_pod_status_ready{{namespace="{ns}",pod=~"{pod}.*",condition="true"}}',
        "deploy_replicas_available": f'kube_deployment_status_replicas_available{{namespace="{ns}"}}',
        "deploy_replicas_desired":   f'kube_deployment_spec_replicas{{namespace="{ns}"}}',
        # Signal quan trọng cho KB2 Bad Deploy: đếm số container đang Waiting với
        # reason ImagePullBackOff/ErrImagePull/CrashLoopBackOff — phân biệt bad_image
        # vs transient_crash. Pod ImagePullBackOff thường KHÔNG tăng pod_restarts_5m
        # vì container chưa khởi động được.
        "pod_waiting_image_pull": (
            f'sum(kube_pod_container_status_waiting_reason{{namespace="{ns}",'
            f'reason=~"ImagePullBackOff|ErrImagePull"}})'
        ),
        "pod_waiting_crashloop": (
            f'sum(kube_pod_container_status_waiting_reason{{namespace="{ns}",'
            f'reason="CrashLoopBackOff"}})'
        ),
        "vote_5xx_rate1m": 'sum(rate(vote_http_requests_total{status=~"5.."}[1m]))',
        "vote_p95_ms":     '1000*histogram_quantile(0.95,sum(rate(vote_http_request_duration_seconds_bucket[1m]))by(le))',
        # Signal KB3 NetworkChaos: worker processing latency p95 (pod-level, capture
        # được chaos worker→db mà alert node-level không thấy).
        "worker_p95_ms": (
            '1000*histogram_quantile(0.95,sum(rate('
            'worker_vote_processing_duration_seconds_bucket[1m]))by(le))'
        ),
        "node_cpu_pct":    '100*(1-avg(rate(node_cpu_seconds_total{mode="idle"}[1m])))',
        "node_mem_avail_pct": '100*node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes',
        "tcp_retrans_pct": '100*sum(rate(node_netstat_Tcp_RetransSegs[1m]))/clamp_min(sum(rate(node_netstat_Tcp_OutSegs[1m])),1)',
        "worker_throughput": "sum(rate(worker_vote_processing_duration_seconds_count[1m]))",
    }
    results = await asyncio.gather(*[query_prometheus(client, q) for q in queries.values()])
    return {k: round(v, 4) for k, v in zip(queries, results)}


# ===== LLM CALL — gọi 9Router/OpenClaw qua OpenAI-compatible API =====
# 9Router (combo `devops-test-agent`) hiện KHÔNG honor `response_format.json_schema`,
# nên ép schema bằng system prompt + few-shot và parse defensively (extract JSON
# block đầu tiên trong text trả về, kể cả khi model wrap trong ```json fence).
SYSTEM_PROMPT = f"""Bạn là AI SRE Agent giám sát cluster Kubernetes của ứng dụng
Voting App (vote → redis → worker → db → result). Khi nhận alert + context
metrics, phân tích root cause và quyết định remediation.

⚠️ NGÔN NGỮ BẮT BUỘC: trường `reasoning` PHẢI viết HOÀN TOÀN bằng TIẾNG VIỆT.
KHÔNG dùng tiếng Anh, KHÔNG mix song ngữ. Dùng thuật ngữ kỹ thuật tiếng Anh
trong dấu nháy/code khi cần (vd: `pod_restarts_5m`, `replica`, `rollout`),
nhưng câu văn diễn giải PHẢI là tiếng Việt. Nếu trả tiếng Anh, output bị
coi là sai format và sẽ bị reject.

🎯 TRIẾT LÝ HÀNH ĐỘNG — ĐỌC KỸ TRƯỚC KHI QUYẾT ĐỊNH:
1. **TỰ XỬ LÝ TRƯỚC**: nếu có action khả thi và an toàn qua K8s API → chọn action
   đó ngay (restart / rollback / scale / restart_daemonset), KHÔNG dùng "suggest".
2. **CHỈ "suggest" KHI THỰC SỰ BỊ CHẶN**: root cause nằm ngoài tầm K8s API (network
   infra, cloud config, thiếu CRD), HOẶC rủi ro action > rủi ro chờ human.
3. **BÁO CÁO ĐẦY ĐỦ**: mỗi decision đều giải thích đã làm gì / tại sao trong `reasoning`.
   Khi tự action, ghi rõ "Agent sẽ [action] để [mục đích]".

OUTPUT FORMAT (BẮT BUỘC):
- Trả về DUY NHẤT một JSON object thuần, KHÔNG kèm markdown, KHÔNG ```json fence,
  KHÔNG văn bản giải thích trước/sau.
- JSON phải có đúng 4 field sau, không thêm field nào khác:
  {{
    "decision":    <string, 1 trong {sorted(VALID_DECISIONS)}>,
    "root_cause":  <string, 1 trong {sorted(VALID_ROOT_CAUSES)}>,
    "confidence":  <number trong [0,1]>,
    "reasoning":   <string TIẾNG VIỆT, ≤500 ký tự>
  }}

QUY TẮC RA QUYẾT ĐỊNH (xếp theo THỨ TỰ ƯU TIÊN — rule trên đánh bại rule dưới):

- alert.status == "resolved" → decision="no_action", confidence≥0.9.

- **Ưu tiên 1 — ImagePullBackOff/ErrImagePull**: nếu `pod_waiting_image_pull` ≥1
  HOẶC alert.labels/annotations nhắc tới "ImagePullBackOff/ErrImagePull" →
  decision="rollback", root_cause="bad_image", confidence ≥0.85. Container
  chưa chạy nên `pod_restarts_5m`=0, KHÔNG nhầm với transient_crash; restart
  vô dụng vì RS tạo pod mới cùng image hỏng.

- **Ưu tiên 2 — Replica mismatch + 5xx tăng** (`vote_5xx_rate1m`>0.1 và
  `deploy_replicas_available` < `deploy_replicas_desired`) → "rollback" +
  root_cause="bad_image", confidence ≥0.85.

- **Ưu tiên 3 — CrashLoopBackOff** (`pod_waiting_crashloop` ≥1 hoặc `pod_restarts_5m` ≥3):
  - Nếu `pod_restarts_5m` < 5: **thử "restart"** (confidence ~0.65) — crash có thể
    do transient state, xứng đáng thử 1 lần. Ghi rõ "đây là lần thử đầu tiên".
  - Nếu `pod_restarts_5m` ≥ 5: "suggest" + root_cause="flapping" — restart-loop
    vô tác dụng, cần human điều tra config/image.

- **Ưu tiên 4 — DaemonSet pod lỗi** (alertname chứa "KubeProxy", "DaemonSet", hoặc
  pod thuộc namespace `kube-system`/`monitoring` và là DaemonSet pod):
  - Nếu restart đơn lẻ có thể fix (pod đang Running nhưng crash) →
    "restart_daemonset", root_cause="transient_crash".
  - Nếu là config/scraping issue (KubeProxyDown mà pod đang Running bình thường) →
    "suggest", root_cause="infra_config".

- **Ưu tiên 5 — ArgoCD OutOfSync / Degraded / SyncFailed**
  (alertname chứa "ArgoCD" hoặc labels.argocd_app có giá trị):
  - Không có pod Pending (Insufficient CPU) → "argocd_sync", root_cause="infra_config",
    confidence ≥ 0.80. Agent trigger sync để cluster về đúng trạng thái Git.
  - Có pod Pending do thiếu CPU → "suggest": cần giảm CPU request trước rồi sync.
  Lưu ý: selfHeal=true đã bật; chỉ force sync khi selfHeal không đủ.

- **Ưu tiên 6 — Azure DevOps Pipeline Failed**
  (alertname chứa "Pipeline" hoặc context mang pipeline result=failed):
  - Lỗi "context deadline exceeded" (Helm timeout) → "pipeline_rerun",
    root_cause="transient_crash", confidence 0.70.
    (`deploymentStrategy.maxSurge=0` đã fix; retry thường pass).
  - Lỗi "namespace mismatch" / "resource not found" → "suggest": sửa manifest trước.
  - Lỗi Docker build / ACR auth → "pipeline_rerun" 1 lần; lỗi lại → "suggest".

- **Ưu tiên 7 — Network-layer issue**: `tcp_retrans_pct`>2 HOẶC `worker_p95_ms`>200ms
  mà signal app khác ổn → "suggest" + root_cause="network_dependency".

- **Ưu tiên 8 — Pod NotReady đơn lẻ**, `pod_restarts_5m`<3, KHÔNG có image_pull/
  crashloop waiting → "restart" + root_cause="transient_crash".
  Nếu ReplicaSet đang tự re-create (self-healing rõ ràng) → confidence ≤0.65.

- **Ưu tiên 9 — Node CPU** >85% kéo dài → "scale" + root_cause="resource_exhaustion".

- **Ưu tiên 10 — Prometheus Operator rejected resource** (alertname="PrometheusOperatorRejectedResources"
  hoặc lỗi chứa "scrapeTimeout greater than scrapeInterval"):
  → decision="patch_service_monitor", root_cause="infra_config", confidence ≥0.85.
  Agent sẽ scan và patch ServiceMonitor trong namespace monitoring để sửa scrapeTimeout.

- Còn lại → "suggest" + root_cause="unknown". LUÔN giải thích tại sao không action.

VÍ DỤ OUTPUT (8 mẫu — copy NGUYÊN văn phong tiếng Việt, KHÔNG copy số):

Ví dụ 1 — Bad deployment ImagePullBackOff:
{{"decision":"rollback","root_cause":"bad_image","confidence":0.9,"reasoning":"Signal `pod_waiting_image_pull`=2: container không kéo được image, `pod_restarts_5m`=0 vì container chưa khởi động. Restart vô dụng (RS tạo pod mới cùng image hỏng). Agent sẽ rollback Deployment về revision trước để khôi phục dịch vụ."}}

Ví dụ 2 — Bad deployment replica mismatch + 5xx:
{{"decision":"rollback","root_cause":"bad_image","confidence":0.92,"reasoning":"Replica available 1/3, `vote_5xx_rate1m`=0.42 sau rollout. CPU và network bình thường, loại trừ nghẽn tài nguyên. Agent sẽ rollback về revision trước — action nhanh nhất khôi phục dịch vụ."}}

Ví dụ 3 — CrashLoop lần đầu (restarts thấp):
{{"decision":"restart","root_cause":"transient_crash","confidence":0.65,"reasoning":"`pod_restarts_5m`=1.5, `pod_waiting_crashloop`=1 — crash mới phát sinh, chưa rõ nguyên nhân. Agent thử restart lần đầu: nếu là transient state lỗi sẽ tự clear. Nếu sau restart vẫn crash, sẽ escalate để human điều tra config."}}

Ví dụ 4 — Network chaos:
{{"decision":"suggest","root_cause":"network_dependency","confidence":0.85,"reasoning":"`worker_p95_ms`=412ms, `tcp_retrans_pct` cao. Vote ready, không crash, CPU thấp. Root cause ở lớp network ngoài tầm K8s API — agent không thể tự can thiệp. Đề xuất human kiểm tra network path / chaos injection."}}

Ví dụ 5 — DaemonSet kube-proxy crash:
{{"decision":"restart_daemonset","root_cause":"transient_crash","confidence":0.72,"reasoning":"Pod kube-proxy trong kube-system crash, `pod_restarts_5m`=2 (<5). DaemonSet pod có thể bị lỗi transient do node event. Agent sẽ rollout restart DaemonSet kube-proxy để khôi phục."}}

Ví dụ 6 — Prometheus Operator rejected ServiceMonitor:
{{"decision":"patch_service_monitor","root_cause":"infra_config","confidence":0.9,"reasoning":"Alert `PrometheusOperatorRejectedResources`: Prometheus Operator từ chối ServiceMonitor do `scrapeTimeout` vượt quá `scrapeInterval`. Agent sẽ scan và patch ServiceMonitor trong namespace monitoring để sửa lỗi cấu hình — không cần human can thiệp."}}

Ví dụ 7 — ArgoCD OutOfSync sau push code mới:
{{"decision":"argocd_sync","root_cause":"infra_config","confidence":0.82,"reasoning":"ArgoCD app `voteapp-service` = OutOfSync, không có pod Pending hay rollout kẹt CPU. Commit mới trên branch hợp lệ. Agent trigger sync để đồng bộ cluster về trạng thái Git mới nhất — không cần human."}}

Ví dụ 8 — Azure DevOps Pipeline failed do Helm timeout:
{{"decision":"pipeline_rerun","root_cause":"transient_crash","confidence":0.70,"reasoning":"Pipeline fail tại stage `DeployMonitoring`: Helm context deadline exceeded. `deploymentStrategy.maxSurge=0` đã được cấu hình nên Grafana không cần 2 pod cùng lúc. Lỗi likely transient — agent queue lại run mới để xác nhận fix."}}
"""

def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Tìm JSON object đầu tiên trong text. Hỗ trợ wrap ```json fence."""
    if not text:
        return None
    # Bóc ```json fence nếu có.
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    # Brace counting để bắt object đầu tiên cân bằng.
    start = text.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
            else:
                if ch == '"':
                    in_str = True
                elif ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(text[start:i + 1])
                        except json.JSONDecodeError:
                            break
        start = text.find("{", start + 1)
    return None


def _heuristic_fallback(alert: dict, context: dict[str, float]) -> tuple[str, str, float, str]:
    """Fallback khi LLM gateway không reach được hoặc trả output không parse được."""
    alertname = alert.get("labels", {}).get("alertname", "")
    status = alert.get("status", "firing")
    if status == "resolved":
        return "no_action", "transient_crash", 0.95, "Alert đã resolved — không cần action."
    # Ưu tiên ImagePullBackOff/ErrImagePull: signal pod_waiting_image_pull >=1
    if context.get("pod_waiting_image_pull", 0) >= 1:
        return ("rollback", "bad_image", 0.9,
                "Signal `pod_waiting_image_pull` ≥1 — container không kéo được image, "
                "rollback về revision trước.")
    restarts = context.get("pod_restarts_5m", 0)
    if context.get("pod_waiting_crashloop", 0) >= 1 or restarts >= 3:
        if restarts < 5:
            return ("restart", "transient_crash", 0.65,
                    "CrashLoop mới phát sinh, thử restart lần đầu — nếu vẫn crash sau đó sẽ escalate.")
        return "suggest", "flapping", 0.6, "Pod restart ≥5 lần → flapping, restart vô dụng, cần human."
    if alertname == "VoteAppPodRestarting" and context.get("pod_restarts_5m", 0) >= 3:
        return "suggest", "flapping", 0.6, "Pod restart >=3 lần trong 5m → flapping, đề xuất human inspect."
    if alertname == "VoteAppPodNotReady":
        return "restart", "transient_crash", 0.75, "Pod NotReady — thử restart để clear state."
    if alertname == "VoteAppDeploymentReplicaMismatch" and context.get("vote_5xx_rate1m", 0) > 0:
        return "rollback", "bad_image", 0.85, "Replica mismatch + 5xx → bad deploy, rollback."
    if alertname == "VoteAppNodeHighCpu":
        return "scale", "resource_exhaustion", 0.78, "Node CPU cao → scale out vote."
    if alertname in {"VoteAppHighTcpRetransmissionRate",
                     "VoteAppWorkerProcessingLatencyHigh",
                     "VoteAppVotePostLatencyHigh"}:
        return "suggest", "network_dependency", 0.6, "Signal network-layer — agent không tự can thiệp."
    if alertname == "KubePodCrashLooping":
        ns = alert.get("labels", {}).get("namespace", "default")
        pod = alert.get("labels", {}).get("pod", "")
        restarts2 = context.get("pod_restarts_5m", 0)
        if restarts2 < 5:
            return ("restart", "transient_crash", 0.65,
                    "KubePodCrashLooping, restarts <5 — thử restart lần đầu.")
        return "suggest", "flapping", 0.6, "KubePodCrashLooping restarts ≥5 → cần human."
    if "Proxy" in alertname or "DaemonSet" in alertname:
        return ("restart_daemonset", "transient_crash", 0.65,
                f"Alert {alertname} liên quan DaemonSet — thử rollout restart.")
    return "suggest", "unknown", 0.4, f"Chưa rõ root cause cho alert {alertname}."


async def call_llm(alert: dict, context: dict[str, float]) -> dict[str, Any]:
    """
    Gọi 9Router (OpenAI-compatible) → parse 4-field JSON. Khi gateway lỗi/timeout
    hoặc model trả output không parse được → fallback heuristic để không chặn pipeline.
    """
    t0 = time.perf_counter()
    user_payload = {
        "alert": {
            "name":     alert.get("labels", {}).get("alertname", "unknown"),
            "severity": alert.get("labels", {}).get("severity", "warning"),
            "status":   alert.get("status", "firing"),
            "labels":   alert.get("labels", {}),
            "annotations": alert.get("annotations", {}),
        },
        "context_metrics": context,
    }
    body = {
        "model": LLM_MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user",
             "content": "Phân tích alert sau và trả JSON đúng format.\n\n"
                        + json.dumps(user_payload, ensure_ascii=False, indent=2)},
        ],
        "temperature": 0.1,
    }

    decision = root_cause = reasoning = None
    conf = 0.0
    used_fallback = False
    fallback_reason = ""
    try:
        async with httpx.AsyncClient(timeout=LLM_TIMEOUT) as c:
            r = await c.post(f"{LLM_BASE_URL}/chat/completions", json=body,
                             headers={"Content-Type": "application/json"})
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"] or ""
        parsed = _extract_json_object(content)
        if not parsed:
            used_fallback = True
            fallback_reason = "no JSON object in response"
        else:
            decision   = parsed.get("decision")
            root_cause = parsed.get("root_cause")
            try:
                conf = float(parsed.get("confidence", 0.0))
            except (TypeError, ValueError):
                conf = 0.0
            reasoning = str(parsed.get("reasoning", ""))[:500]
            if decision not in VALID_DECISIONS or root_cause not in VALID_ROOT_CAUSES:
                used_fallback = True
                fallback_reason = f"invalid enum (decision={decision}, root_cause={root_cause})"
    except Exception as e:
        used_fallback = True
        fallback_reason = f"gateway error: {e}"

    if used_fallback:
        log.warning("LLM output unusable (%s) — falling back to heuristic", fallback_reason)
        decision, root_cause, conf, reasoning = _heuristic_fallback(alert, context)

    return {
        "decision": decision,
        "root_cause": root_cause,
        "confidence": conf,
        "reasoning": reasoning,
        "llm_latency_ms": int((time.perf_counter() - t0) * 1000),
    }
# =========================================================


# ----- Kubernetes client (lazy init) -----
_k8s_core: k8s_client.CoreV1Api | None = None
_k8s_apps: k8s_client.AppsV1Api | None = None


def _ensure_k8s() -> tuple[k8s_client.CoreV1Api, k8s_client.AppsV1Api]:
    """Nạp config trong cluster (ưu tiên) hoặc kubeconfig local; khởi tạo API client 1 lần."""
    global _k8s_core, _k8s_apps
    if _k8s_core is None or _k8s_apps is None:
        try:
            k8s_config.load_incluster_config()
            log.info("k8s: loaded in-cluster config")
        except Exception:
            try:
                k8s_config.load_kube_config()
                log.info("k8s: loaded local kubeconfig")
            except Exception as e:
                raise RuntimeError(f"k8s config not available: {e}")
        _k8s_core = k8s_client.CoreV1Api()
        _k8s_apps = k8s_client.AppsV1Api()
    return _k8s_core, _k8s_apps


def _owner_deployment(core: k8s_client.CoreV1Api, apps: k8s_client.AppsV1Api,
                      namespace: str, pod_name: str) -> str | None:
    """Lần owner chain Pod → ReplicaSet → Deployment."""
    try:
        pod = core.read_namespaced_pod(pod_name, namespace)
    except ApiException as e:
        if e.status == 404:
            return None
        raise
    for owner in (pod.metadata.owner_references or []):
        if owner.kind == "ReplicaSet":
            try:
                rs = apps.read_namespaced_replica_set(owner.name, namespace)
            except ApiException:
                continue
            for ro in (rs.metadata.owner_references or []):
                if ro.kind == "Deployment":
                    return ro.name
        if owner.kind == "Deployment":
            return owner.name
    return None


def _act_restart(namespace: str, pod: str) -> None:
    core, _ = _ensure_k8s()
    core.delete_namespaced_pod(pod, namespace, grace_period_seconds=0)
    log.info("k8s: deleted pod %s/%s (restart action)", namespace, pod)


def _act_rollback(namespace: str, deployment: str) -> None:
    """
    Rollback về revision trước: tìm ReplicaSet có revision cao nhất < current,
    copy `spec.template` sang Deployment (chính là cách `kubectl rollout undo` hoạt động
    từ K8s ≥1.16 vì subresource `deployments/rollback` bị remove).
    """
    _, apps = _ensure_k8s()
    dep = apps.read_namespaced_deployment(deployment, namespace)
    ann = (dep.metadata.annotations or {})
    current_rev = int(ann.get("deployment.kubernetes.io/revision", "0"))

    # Liệt kê toàn bộ ReplicaSet cùng ns rồi lọc theo owner = deployment này.
    rs_items = apps.list_namespaced_replica_set(namespace).items
    candidates: list[tuple[int, Any]] = []
    for rs in rs_items:
        for o in (rs.metadata.owner_references or []):
            if o.kind == "Deployment" and o.name == deployment and o.uid == dep.metadata.uid:
                rev = int((rs.metadata.annotations or {})
                          .get("deployment.kubernetes.io/revision", "0"))
                if rev < current_rev:
                    candidates.append((rev, rs))
                break
    if not candidates:
        raise RuntimeError(f"no previous revision for {namespace}/{deployment}")
    candidates.sort(key=lambda x: x[0], reverse=True)
    _, prev_rs = candidates[0]

    # Patch deployment spec.template về template của RS trước.
    body = {"spec": {"template": apps.api_client.sanitize_for_serialization(prev_rs.spec.template)}}
    apps.patch_namespaced_deployment(deployment, namespace, body)
    log.info("k8s: rolled back %s/%s → revision %s (prev RS %s)",
             namespace, deployment, candidates[0][0], prev_rs.metadata.name)


def _act_rollout_restart_daemonset(namespace: str, daemonset: str) -> None:
    """Rollout restart DaemonSet bằng cách patch annotation timestamp."""
    import datetime
    _, apps = _ensure_k8s()
    patch = {"spec": {"template": {"metadata": {"annotations": {
        "kubectl.kubernetes.io/restartedAt": datetime.datetime.utcnow().isoformat() + "Z"
    }}}}}
    apps.patch_namespaced_daemon_set(daemonset, namespace, patch)
    log.info("k8s: rollout restart daemonset %s/%s", namespace, daemonset)


def _act_patch_service_monitor(namespace: str, name: str) -> dict:
    """Patch ServiceMonitor để scrapeTimeout <= scrapeInterval.
    Trả về dict {patched: bool, old_timeout: str, new_timeout: str}.
    """
    import re as _re
    crd_api = k8s_client.CustomObjectsApi(_ensure_k8s()[0])
    sm = crd_api.get_namespaced_custom_object(
        group="monitoring.coreos.com", version="v1",
        namespace=namespace, plural="servicemonitors", name=name,
    )
    endpoints = sm.get("spec", {}).get("endpoints", [])
    if not endpoints:
        return {"patched": False, "reason": "no endpoints"}

    def _parse_seconds(s: str) -> int:
        m = _re.match(r"^(\d+)s$", s or "")
        return int(m.group(1)) if m else 0

    scrape_interval = endpoints[0].get("interval") or sm["spec"].get("interval", "15s")
    interval_s = _parse_seconds(scrape_interval)
    if interval_s == 0:
        interval_s = 15

    patches = []
    for i, ep in enumerate(endpoints):
        timeout_raw = ep.get("scrapeTimeout", "")
        timeout_s = _parse_seconds(timeout_raw)
        if timeout_s > interval_s:
            new_timeout = f"{interval_s - 1}s"
            patches.append({"index": i, "old": timeout_raw, "new": new_timeout})
            ep["scrapeTimeout"] = new_timeout

    if not patches:
        return {"patched": False, "reason": "scrapeTimeout already valid"}

    sm["spec"]["endpoints"] = endpoints
    crd_api.replace_namespaced_custom_object(
        group="monitoring.coreos.com", version="v1",
        namespace=namespace, plural="servicemonitors", name=name, body=sm,
    )
    log.info("k8s: patched servicemonitor %s/%s — %s", namespace, name, patches)
    return {"patched": True, "changes": patches}


def _act_scan_and_fix_service_monitors(target_namespace: str = "monitoring") -> list[dict]:
    """Scan tất cả ServiceMonitor trong namespace, patch các SM có scrapeTimeout > scrapeInterval."""
    crd_api = k8s_client.CustomObjectsApi(_ensure_k8s()[0])
    sms = crd_api.list_namespaced_custom_object(
        group="monitoring.coreos.com", version="v1",
        namespace=target_namespace, plural="servicemonitors",
    )
    results = []
    for sm in sms.get("items", []):
        name = sm["metadata"]["name"]
        ns = sm["metadata"]["namespace"]
        result = _act_patch_service_monitor(ns, name)
        if result.get("patched"):
            results.append({"name": name, **result})
    return results


def _act_argocd_sync(app_name: str | None = None, namespace: str | None = None) -> None:
    """Trigger ArgoCD Application sync bằng cách patch operation field qua K8s API."""
    name = app_name or ARGOCD_APP_NAME
    ns   = namespace or ARGOCD_NAMESPACE
    core, _ = _ensure_k8s()
    crd_api = k8s_client.CustomObjectsApi(core)
    patch = {
        "operation": {
            "sync": {
                "revision": "HEAD",
                "prune": True,
                "syncOptions": ["CreateNamespace=true"],
            },
            "initiatedBy": {"username": "ai-agent", "automated": False},
            "info": [{"name": "triggeredBy", "value": "OpenClaw SRE Agent"}],
        }
    }
    crd_api.patch_namespaced_custom_object(
        group="argoproj.io", version="v1alpha1",
        namespace=ns, plural="applications",
        name=name, body=patch,
    )
    log.info("argocd: triggered sync for %s/%s", ns, name)


async def _act_pipeline_rerun(client: httpx.AsyncClient) -> int:
    """Queue lại Azure DevOps Pipeline qua REST API. Trả về build ID mới."""
    if not AZDO_PAT:
        raise ValueError("AZDO_PAT not configured — cannot rerun pipeline")
    import base64
    token = base64.b64encode(f":{AZDO_PAT}".encode()).decode()
    url = (
        f"https://dev.azure.com/{AZDO_ORG}/{AZDO_PROJECT}"
        f"/_apis/build/builds?api-version=7.0"
    )
    body = {
        "definition": {"id": AZDO_PIPELINE_ID},
        "sourceBranch": f"refs/heads/{AZDO_BRANCH}",
    }
    resp = await client.post(
        url, json=body,
        headers={"Authorization": f"Basic {token}"},
        timeout=15.0,
    )
    resp.raise_for_status()
    build_id: int = resp.json()["id"]
    log.info("azdo: queued pipeline %d branch=%s → build #%d",
             AZDO_PIPELINE_ID, AZDO_BRANCH, build_id)
    return build_id


def _act_scale(namespace: str, deployment: str, delta: int = 1) -> int:
    """Scale +delta replica, giới hạn tối đa 10 để không blow up cluster."""
    _, apps = _ensure_k8s()
    scale = apps.read_namespaced_deployment_scale(deployment, namespace)
    current = scale.spec.replicas or 0
    new = min(current + delta, 10)
    if new == current:
        return current
    apps.patch_namespaced_deployment_scale(
        deployment, namespace, {"spec": {"replicas": new}}
    )
    log.info("k8s: scaled %s/%s %s → %s", namespace, deployment, current, new)
    return new


async def execute_action(client: httpx.AsyncClient, decision: dict, alert: dict) -> str:
    """
    Thực thi quyết định qua Kubernetes API. Trả về result:
      success | failed | not_found | skipped_dry_run |
      skipped_low_confidence | skipped_rate_limit | skipped_missing_target
    DRY_RUN=true → luôn skip để an toàn khi test prompt.
    """
    if DRY_RUN:
        return "skipped_dry_run"
    dec = decision["decision"]
    if dec in {"suggest", "no_action"}:
        return "skipped_low_confidence"
    if decision["confidence"] < MIN_CONFIDENCE:
        return "skipped_low_confidence"
    if not _can_act_now():
        log.warning("rate-limit hit (>%s actions/min) — skipping", MAX_ACTIONS_PER_MIN)
        return "skipped_rate_limit"

    labels = alert.get("labels", {})
    namespace = labels.get("namespace") or "default"
    pod = labels.get("pod") or ""
    deployment = labels.get("deployment") or ""

    try:
        core, apps = _ensure_k8s()
    except Exception as e:
        log.error("k8s client init failed: %s", e)
        return "failed"

    try:
        if dec == "restart":
            if not pod:
                return "skipped_missing_target"
            _act_restart(namespace, pod)
        elif dec in {"rollback", "scale"}:
            # Nếu alert không mang label deployment, derive từ pod's owner chain.
            if not deployment and pod:
                deployment = _owner_deployment(core, apps, namespace, pod) or ""
            if not deployment:
                return "skipped_missing_target"
            if dec == "rollback":
                _act_rollback(namespace, deployment)
            else:
                _act_scale(namespace, deployment, delta=1)
        elif dec == "restart_daemonset":
            # Target daemonset: ưu tiên label "daemonset", fallback từ pod name
            # (strip suffix -<hash>) hoặc alertname hint (kube-proxy → kube-proxy)
            daemonset = labels.get("daemonset") or labels.get("container") or ""
            if not daemonset and pod:
                # pod = "kube-proxy-cpdzj" → strip suffix → "kube-proxy"
                daemonset = pod.rsplit("-", 1)[0] if "-" in pod else pod
            if not daemonset:
                alertname = labels.get("alertname", "")
                if "proxy" in alertname.lower():
                    daemonset = "kube-proxy"
            if not daemonset:
                return "skipped_missing_target"
            _act_rollout_restart_daemonset(namespace, daemonset)
        elif dec == "patch_service_monitor":
            # Tìm ServiceMonitor bị reject: ưu tiên label "service_monitor",
            # fallback scan toàn bộ namespace monitoring.
            sm_name = labels.get("service_monitor") or labels.get("servicemonitor") or ""
            sm_ns   = labels.get("namespace") or "monitoring"
            if sm_name:
                result = _act_patch_service_monitor(sm_ns, sm_name)
                if not result.get("patched"):
                    log.info("patch_service_monitor: %s", result)
            else:
                fixed = _act_scan_and_fix_service_monitors("monitoring")
                log.info("patch_service_monitor scan: fixed %d SMs", len(fixed))
                if not fixed:
                    return "not_found"
        elif dec == "argocd_sync":
            # Lấy tên app từ label "argocd_app" nếu có, fallback về ARGOCD_APP_NAME
            app_name = (labels.get("argocd_app") or labels.get("app_name")
                        or ARGOCD_APP_NAME)
            argocd_ns = labels.get("argocd_namespace") or ARGOCD_NAMESPACE
            _act_argocd_sync(app_name, argocd_ns)
        elif dec == "pipeline_rerun":
            # Queue lại AzDO pipeline — AZDO_PAT bắt buộc
            if not AZDO_PAT:
                log.warning("pipeline_rerun: AZDO_PAT not set, skipping")
                return "skipped_missing_target"
            build_id = await _act_pipeline_rerun(client)
            log.info("pipeline_rerun: queued build #%d", build_id)
        else:
            return "skipped_low_confidence"
    except ApiException as e:
        if e.status == 404:
            log.warning("k8s target not found (%s): %s", dec, e.reason)
            return "not_found"
        log.exception("k8s %s failed: status=%s reason=%s", dec, e.status, e.reason)
        return "failed"
    except Exception as e:
        log.exception("k8s %s raised: %s", dec, e)
        return "failed"

    _record_action()
    return "success"


async def post_decision_log(client: httpx.AsyncClient, payload: dict) -> None:
    try:
        r = await client.post(f"{DECISION_LOG_URL}/decisions", json=payload, timeout=5.0)
        r.raise_for_status()
    except Exception as e:
        log.error("decision-log POST failed: %s", e)


# ----- Telegram notifier -----
_DECISION_EMOJI = {
    "rollback":              "↩️",
    "scale":                 "📈",
    "restart":               "🔄",
    "restart_daemonset":     "🔧",
    "patch_service_monitor": "🩹",
    "argocd_sync":           "🔁",
    "pipeline_rerun":        "▶️",
    "suggest":               "💡",
    "no_action":             "✅",
}


def _html_escape(s: str) -> str:
    """Escape tối thiểu cho Telegram parse_mode=HTML."""
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _common_header(alert: dict) -> tuple[str, str, str]:
    """Trả (target_line, summary_line, metric_block_seed) dùng chung mọi phase."""
    labels = alert.get("labels", {})
    namespace = labels.get("namespace", "")
    pod       = labels.get("pod", "")
    target = " · ".join(filter(None, [
        f"ns=<code>{_html_escape(namespace)}</code>" if namespace else "",
        f"pod=<code>{_html_escape(pod)}</code>" if pod else "",
    ]))
    annotations = alert.get("annotations", {})
    summary = (annotations.get("summary") or annotations.get("description", ""))[:300]
    summary_line = f"<i>{_html_escape(summary)}</i>" if summary else ""
    return target, summary_line, ""


def _metric_lines(context: dict[str, float]) -> list[str]:
    out = []
    # Signals được hiển thị ưu tiên: đặt image_pull/crashloop lên trước để dễ
    # phát hiện bad-deploy từ Telegram.
    for k in ("pod_waiting_image_pull", "pod_waiting_crashloop",
              "pod_restarts_5m", "vote_5xx_rate1m", "vote_p95_ms",
              "worker_p95_ms", "tcp_retrans_pct", "node_cpu_pct"):
        if k in context:
            out.append(f"  • <code>{k}</code>={context[k]}")
    return out


def _action_explanation(decision: dict, action_result: str) -> str:
    """Mô tả tiếng Việt tự nhiên cho dòng 'AGENT SẼ LÀM GÌ'."""
    d = decision["decision"]
    if action_result == "success":
        if d == "restart":
            return "🛠️ <b>Đang khắc phục:</b> agent xoá pod để ReplicaSet tạo pod mới."
        if d == "rollback":
            return "🛠️ <b>Đang khắc phục:</b> agent rollback Deployment về revision trước."
        if d == "scale":
            return "🛠️ <b>Đang khắc phục:</b> agent scale-out thêm replica."
        if d == "restart_daemonset":
            return "🔧 <b>Đang khắc phục:</b> agent rollout restart DaemonSet."
        if d == "patch_service_monitor":
            return "🩹 <b>Đang khắc phục:</b> agent đã patch ServiceMonitor (scrapeTimeout ≤ scrapeInterval)."
        if d == "argocd_sync":
            return "🔁 <b>Đang khắc phục:</b> agent trigger ArgoCD sync — cluster sẽ đồng bộ về Git."
        if d == "pipeline_rerun":
            return "▶️ <b>Đang khắc phục:</b> agent đã queue lại Azure DevOps Pipeline."
        return f"🛠️ <b>Đang khắc phục:</b> agent thực hiện <code>{d}</code>."
    if action_result == "skipped_dry_run":
        return "🧪 <b>DRY_RUN:</b> agent KHÔNG thực thi action (chế độ test)."
    if action_result == "skipped_low_confidence":
        if d == "no_action":
            return "✅ <b>Không cần action:</b> alert đã resolved hoặc hệ thống ổn định."
        if d == "suggest":
            return ("💡 <b>Gợi ý human:</b> root cause ngoài tầm K8s API "
                    "(network infra, cloud config). Agent đã ghi nhận — vui lòng kiểm tra thủ công.")
        return "⏸️ <b>Chưa action:</b> confidence dưới ngưỡng — agent sẽ tiếp tục theo dõi."
    if action_result == "skipped_rate_limit":
        return ("⏸️ <b>Tạm dừng:</b> đã đạt giới hạn action/phút. "
                "Có thể cluster đang flapping — cần human can thiệp.")
    return f"⚙️ <b>Action result:</b> <code>{_html_escape(action_result)}</code>"


def _format_firing(alert: dict, decision: dict, action_result: str,
                   context: dict[str, float], will_track: bool) -> str:
    """Phase=firing: alert mới + plan của agent (có thể tự khắc phục hoặc không)."""
    labels = alert.get("labels", {})
    alertname = labels.get("alertname", "unknown")
    severity  = labels.get("severity", "warning")
    head = "🔥" if severity == "critical" else "🚨"
    dec_em = _DECISION_EMOJI.get(decision["decision"], "•")
    target, summary_line, _ = _common_header(alert)

    parts = [f"{head} <b>{_html_escape(alertname)}</b> (firing/{_html_escape(severity)})"]
    if target: parts.append(target)
    if summary_line: parts.append(summary_line)
    parts += [
        "",
        f"{dec_em} <b>Decision:</b> <code>{decision['decision']}</code> "
        f"· <b>Root cause:</b> <code>{decision['root_cause']}</code>",
        f"📊 <b>Confidence:</b> {decision['confidence']:.2f} · LLM {decision['llm_latency_ms']}ms",
        "",
        _action_explanation(decision, action_result),
    ]
    if will_track:
        parts.append(
            f"⏱️ <b>Theo dõi tự động {REMEDIATION_TIMEOUT}s</b> — nếu alert chưa "
            f"resolved sẽ escalate để bạn can thiệp."
        )
    parts += ["", f"📝 {_html_escape(decision['reasoning'])}"]
    metrics = _metric_lines(context)
    if metrics:
        parts += ["", "<b>Context metrics:</b>"] + metrics
    return "\n".join(parts)


def _format_resolved_after_action(alert: dict, info: dict, elapsed_s: float) -> str:
    """Phase=resolved sau khi agent đã action thành công."""
    labels = alert.get("labels", {})
    alertname = labels.get("alertname", "unknown")
    target, _, _ = _common_header(alert)
    decision = info["decision"]
    dec_em = _DECISION_EMOJI.get(decision["decision"], "•")
    parts = [
        f"✅ <b>ĐÃ KHẮC PHỤC: {_html_escape(alertname)}</b>",
    ]
    if target: parts.append(target)
    parts += [
        "",
        f"⏱️ Khắc phục mất <b>{elapsed_s:.0f}s</b> kể từ lúc agent action.",
        f"{dec_em} Action đã apply: <code>{decision['decision']}</code> "
        f"(root cause <code>{decision['root_cause']}</code>, confidence {decision['confidence']:.2f}).",
    ]
    return "\n".join(parts)


def _format_action_taken(alert: dict, decision: dict) -> str:
    """Phase=action_taken: xác nhận ngay sau khi agent thực thi thành công."""
    import datetime
    labels = alert.get("labels", {})
    alertname = labels.get("alertname", "unknown")
    target, _, _ = _common_header(alert)
    dec = decision["decision"]
    dec_em = _DECISION_EMOJI.get(dec, "🛠️")
    ts = datetime.datetime.utcnow().strftime("%H:%M:%S UTC")

    action_desc = {
        "restart":           "Xoá pod để ReplicaSet tạo pod mới (rolling restart).",
        "rollback":          "Rollback Deployment về revision trước đó.",
        "scale":             "Scale-out +1 replica để giải phóng tải.",
        "restart_daemonset": "Rollout restart DaemonSet (patch annotation timestamp).",
    }.get(dec, f"Thực thi <code>{_html_escape(dec)}</code>.")

    parts = [
        f"{dec_em} <b>AGENT ĐÃ XỬ LÝ: {_html_escape(alertname)}</b>",
    ]
    if target:
        parts.append(target)
    parts += [
        "",
        f"⚙️ <b>Action:</b> <code>{_html_escape(dec)}</code> "
        f"· confidence <b>{decision['confidence']:.2f}</b> · {ts}",
        f"📋 {action_desc}",
        "",
        f"📝 {_html_escape(decision.get('reasoning', '')[:300])}",
        "",
        "⏱️ Đang theo dõi kết quả — sẽ thông báo khi alert resolved hoặc cần escalate.",
    ]
    return "\n".join(parts)


def _format_resolved_self_heal(alert: dict, decision: dict, context: dict[str, float]) -> str:
    """Phase=resolved nhưng agent KHÔNG action (K8s tự heal hoặc transient)."""
    labels = alert.get("labels", {})
    alertname = labels.get("alertname", "unknown")
    target, _, _ = _common_header(alert)
    parts = [f"🟢 <b>Resolved: {_html_escape(alertname)}</b>"]
    if target: parts.append(target)
    parts += [
        "",
        "ℹ️ Alert tự resolved (Kubernetes self-heal hoặc agent đã chọn không action).",
        "",
        f"📝 {_html_escape(decision.get('reasoning', '')[:300])}",
    ]
    return "\n".join(parts)


def _format_escalation(info: dict, timeout_s: int) -> str:
    """Phase=escalation: agent đã action mà alert vẫn firing sau timeout."""
    alert = info["alert"]
    decision = info["decision"]
    labels = alert.get("labels", {})
    alertname = labels.get("alertname", "unknown")
    target, summary_line, _ = _common_header(alert)
    dec_em = _DECISION_EMOJI.get(decision["decision"], "•")
    suggestions = []
    pod = labels.get("pod", "")
    ns = labels.get("namespace", "default")
    if pod:
        suggestions += [
            f"  • <code>kubectl describe pod -n {_html_escape(ns)} {_html_escape(pod)}</code>",
            f"  • <code>kubectl logs -n {_html_escape(ns)} {_html_escape(pod)} --previous</code>",
        ]
    if decision["decision"] == "rollback":
        suggestions.append("  • Kiểm tra image cũ có thực sự healthy không")
    if decision["decision"] == "restart":
        suggestions.append("  • Có thể là bad image — cân nhắc rollback thủ công")
    suggestions.append(f"  • <code>kubectl get events -n {_html_escape(ns)} --sort-by=.lastTimestamp</code>")

    parts = [
        f"🚨🚨 <b>ESCALATION: {_html_escape(alertname)}</b> chưa resolved sau {timeout_s}s",
    ]
    if target: parts.append(target)
    if summary_line: parts.append(summary_line)
    parts += [
        "",
        f"{dec_em} Action agent đã thử: <code>{decision['decision']}</code> "
        f"(confidence {decision['confidence']:.2f}) — KHÔNG hiệu quả.",
        "",
        "👤 <b>Cần human can thiệp ngay.</b> Gợi ý kiểm tra:",
    ] + suggestions
    return "\n".join(parts)


async def _telegram_send(client: httpx.AsyncClient, text: str, phase: str) -> str:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        m_telegram.labels(status="skipped_unconfigured", phase=phase).inc()
        return "skipped_unconfigured"
    url = f"{TELEGRAM_API_BASE}/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = await client.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }, timeout=10.0)
        if r.status_code != 200 or not r.json().get("ok"):
            log.warning("telegram sendMessage non-ok (phase=%s): %s %s",
                        phase, r.status_code, r.text[:300])
            m_telegram.labels(status="failed", phase=phase).inc()
            return "failed"
        m_telegram.labels(status="sent", phase=phase).inc()
        return "sent"
    except Exception as e:
        log.error("telegram notify failed (phase=%s): %s", phase, e)
        m_telegram.labels(status="failed", phase=phase).inc()
        return "failed"


# ----- Remediation lifecycle tracker -----
# Map fingerprint → {alert, decision, context, started_at, watcher: asyncio.Task}
_pending_remediation: dict[str, dict] = {}


def _alert_fp(alert: dict) -> str:
    """
    Fingerprint remediation tracker — dùng key **stabler hơn** Alertmanager
    fingerprint mặc định. Alertmanager fp thay đổi khi `pod` label đổi (pod
    ImagePullBackOff mỗi lần ReplicaSet re-create đều sinh pod name mới), nên
    per-pod fp gây false `resolved_in_time` ×N.

    Giải pháp: aggregate theo (alertname, namespace, deployment/app). Nếu alert
    có label `deployment` dùng luôn; nếu chỉ có `pod` thì strip hash ReplicaSet+
    pod suffix (`vote-59dbd67747-xxxxx` → `vote`) để nhiều pod cùng deployment
    map về 1 tracker.
    """
    labels = alert.get("labels", {})
    alertname = labels.get("alertname", "unknown")
    ns = labels.get("namespace", "")
    dep = labels.get("deployment") or labels.get("app") or ""
    if not dep and labels.get("pod"):
        # Strip 2 suffix cuối cùng: -<rsHash>-<podSuffix>
        parts = labels["pod"].rsplit("-", 2)
        dep = parts[0] if len(parts) == 3 else labels["pod"]
    return f"{alertname}|{ns}|{dep}"


async def _escalation_watcher(fp: str) -> None:
    """Chờ REMEDIATION_TIMEOUT giây; nếu vẫn pending → escalate."""
    try:
        await asyncio.sleep(REMEDIATION_TIMEOUT)
    except asyncio.CancelledError:
        return
    info = _pending_remediation.pop(fp, None)
    if not info:
        return  # đã resolved trong lúc chờ
    log.warning("remediation TIMEOUT for %s — escalating", fp)
    text = _format_escalation(info, REMEDIATION_TIMEOUT)
    async with httpx.AsyncClient() as c:
        await _telegram_send(c, text, phase="escalation")
    m_remediation.labels(outcome="escalated").inc()


def _track_remediation(fp: str, alert: dict, decision: dict, context: dict[str, float]) -> None:
    """Ghi nhận agent vừa action; schedule watcher để escalate nếu timeout."""
    # Cancel watcher cũ nếu cùng fingerprint (action mới đè lên cũ)
    old = _pending_remediation.get(fp)
    if old and old.get("task"):
        old["task"].cancel()
    task = asyncio.create_task(_escalation_watcher(fp))
    _pending_remediation[fp] = {
        "alert": alert, "decision": decision, "context": context,
        "started_at": time.time(), "task": task,
    }


def _finish_remediation(fp: str) -> dict | None:
    """Resolved alert đến → cancel watcher, trả info nếu có tracking."""
    info = _pending_remediation.pop(fp, None)
    if info and info.get("task"):
        info["task"].cancel()
    return info


# ----- FastAPI app -----
app = FastAPI(title="NT531 AI Agent", version="0.1.0")


class HealthOut(BaseModel):
    status: str
    decision_log: bool
    prometheus: bool


@app.get("/healthz", response_model=HealthOut)
async def healthz():
    async with httpx.AsyncClient() as c:
        try:
            dl = (await c.get(f"{DECISION_LOG_URL}/healthz", timeout=2.0)).status_code == 200
        except Exception:
            dl = False
        try:
            pr = (await c.get(f"{PROMETHEUS_URL}/-/ready", timeout=2.0)).status_code == 200
        except Exception:
            pr = False
    return HealthOut(status="ok", decision_log=dl, prometheus=pr)


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


@app.post("/webhook/alert")
async def webhook(req: Request):
    """Alertmanager v2 webhook payload."""
    try:
        payload = await req.json()
    except Exception:
        raise HTTPException(400, "invalid JSON")

    alerts = payload.get("alerts", [])
    if not alerts:
        return {"received": 0}

    async with httpx.AsyncClient() as c:
        for alert in alerts:
            try:
                fp = _alert_fp(alert)
                status = alert.get("status", "firing")
                ctx = await enrich_alert(c, alert)
                with m_llm_latency.time():
                    decision = await call_llm(alert, ctx)

                if decision["decision"] not in VALID_DECISIONS:
                    decision["decision"] = "suggest"
                if decision["root_cause"] not in VALID_ROOT_CAUSES:
                    decision["root_cause"] = "unknown"

                m_decisions.labels(
                    decision=decision["decision"],
                    root_cause=decision["root_cause"],
                ).inc()
                m_confidence.observe(decision["confidence"])

                action_result = await execute_action(c, decision, alert)
                m_actions.labels(
                    action=decision["decision"],
                    result=action_result,
                ).inc()

                await post_decision_log(c, {
                    "alert_name": alert.get("labels", {}).get("alertname", "unknown"),
                    "decision": decision["decision"],
                    "confidence": decision["confidence"],
                    "reasoning": decision["reasoning"],
                    "action_taken": action_result == "success",
                    "human_override": False,
                    "llm_model": LLM_MODEL,
                    "llm_latency_ms": decision["llm_latency_ms"],
                    "context_metrics": ctx,
                })

                # ----- Lifecycle dispatch: firing → tracked / resolved → close -----
                if status == "resolved":
                    info = _finish_remediation(fp)
                    if not TELEGRAM_NOTIFY_RESOLVED:
                        pass
                    elif info:
                        elapsed = time.time() - info["started_at"]
                        text = _format_resolved_after_action(alert, info, elapsed)
                        await _telegram_send(c, text, phase="resolved")
                        m_remediation.labels(outcome="resolved_in_time").inc()
                    else:
                        text = _format_resolved_self_heal(alert, decision, ctx)
                        await _telegram_send(c, text, phase="self_heal")
                        m_remediation.labels(outcome="self_healed").inc()
                else:
                    will_track = action_result == "success"
                    text = _format_firing(alert, decision, action_result, ctx, will_track)
                    await _telegram_send(c, text, phase="firing")
                    if will_track:
                        # Gửi ngay thông báo xác nhận "đã thực thi" riêng biệt,
                        # độc lập với kết quả resolved/escalated về sau.
                        action_text = _format_action_taken(alert, decision)
                        await _telegram_send(c, action_text, phase="action_taken")
                        _track_remediation(fp, alert, decision, ctx)
            except Exception as e:
                log.exception("alert handling failed: %s", e)

    return {"received": len(alerts)}
