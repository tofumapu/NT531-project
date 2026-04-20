#!/bin/bash
# run-kb2-5rounds.sh - Chạy KB2 (Pod Failure + Bad Deployment) 5 lần liên tiếp
# Lưu toàn bộ log + kết quả vào một folder duy nhất

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATE=$(date +%Y-%m-%d)
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
RESULTS_BASE="${SCRIPT_DIR}/results/kb2-5rounds-${DATE}"
PUSHGW="${PUSHGW:-http://localhost:9091}"
TARGET="${TARGET:-vote}"
NS="${NS:-default}"
ROUNDS=5

mkdir -p "$RESULTS_BASE"
MASTER_LOG="${RESULTS_BASE}/run.log"

log() { echo "[$(date '+%Y-%m-%dT%H:%M:%S')] $*" | tee -a "$MASTER_LOG"; }

echo "╔══════════════════════════════════════════════════════════╗"
echo "║    KB2 – 5 Rounds: Pod Failure + Bad Deployment          ║"
echo "║    Target      : ${TARGET}                               "
echo "║    Pushgateway : ${PUSHGW}                               "
echo "║    Results     : ${RESULTS_BASE}                         "
echo "╚══════════════════════════════════════════════════════════╝"

# ── Pre-flight ────────────────────────────────────────────────
log "=== PRE-FLIGHT CHECK ==="

# Pushgateway
if ! curl -sf "${PUSHGW}/-/ready" > /dev/null 2>&1; then
  log "ERROR: Pushgateway không reachable tại ${PUSHGW}"
  log "Chạy: kubectl port-forward svc/pushgateway 9091:9091 -n monitoring &"
  exit 1
fi
log "Pushgateway: OK"

# Vote pod
POD_CHECK=$(kubectl get pods -n "$NS" -l "app=${TARGET}" --no-headers 2>/dev/null | grep Running | wc -l)
if [ "$POD_CHECK" -eq 0 ]; then
  log "ERROR: Không có pod ${TARGET} Running"
  exit 1
fi
log "Vote pod: Running (${POD_CHECK} replica)"

GOOD_IMAGE=$(kubectl get deployment "$TARGET" -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}')
log "Good image: ${GOOD_IMAGE}"

# Snapshot trạng thái trước khi chạy
kubectl get pods -n "$NS" -o wide > "${RESULTS_BASE}/00-pods-before.txt" 2>/dev/null || true
log "Saved pods snapshot: 00-pods-before.txt"

# ── Kết quả tổng hợp ──────────────────────────────────────────
declare -a POD_MTTR=()
declare -a BAD_DETECTION=()
declare -a BAD_MTTR=()
declare -a BAD_ROLLBACK=()

# ── 5 Rounds ──────────────────────────────────────────────────
for ROUND in $(seq 1 $ROUNDS); do
  echo ""
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  log "  ROUND ${ROUND} / ${ROUNDS}"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  ROUND_DIR="${RESULTS_BASE}/round${ROUND}"
  mkdir -p "$ROUND_DIR"

  # ── 2A: Pod Failure ─────────────────────────────────────────
  log "  [R${ROUND}] 2A: Pod Failure"

  POD=$(kubectl get pods -n "$NS" -l "app=${TARGET}" -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)
  if [ -z "$POD" ]; then
    log "  ERROR: Không tìm thấy pod ${TARGET}, bỏ qua round này"
    continue
  fi

  kubectl get pods -n "$NS" -o wide > "${ROUND_DIR}/2a-pods-before.txt" 2>/dev/null || true

  T0_2A=$(date +%s)
  kubectl delete pod "$POD" -n "$NS" --grace-period=0 --force \
    >> "${ROUND_DIR}/2a-kill.log" 2>&1 || true

  # Đợi pod mới Ready
  kubectl wait --for=condition=Ready pod -l "app=${TARGET}" -n "$NS" \
    --timeout=300s >> "${ROUND_DIR}/2a-wait.log" 2>&1
  T1_2A=$(date +%s)
  MTTR_2A=$((T1_2A - T0_2A))

  kubectl get pods -n "$NS" -o wide > "${ROUND_DIR}/2a-pods-after.txt" 2>/dev/null || true

  # Push to Pushgateway
  cat <<EOF | curl -sS --data-binary @- \
    "${PUSHGW}/metrics/job/kb2_round${ROUND}_pod_failure/target/${TARGET}" \
    >> "${ROUND_DIR}/2a-push.log" 2>&1 || true
# TYPE pod_failure_recovery_seconds gauge
pod_failure_recovery_seconds ${MTTR_2A}
# TYPE pod_failure_t0_unixtime gauge
pod_failure_t0_unixtime ${T0_2A}
# TYPE pod_failure_t1_unixtime gauge
pod_failure_t1_unixtime ${T1_2A}
EOF

  POD_MTTR+=($MTTR_2A)
  log "  [R${ROUND}] 2A DONE → MTTR = ${MTTR_2A}s"

  # Ghi summary round 2A
  cat > "${ROUND_DIR}/2a-summary.json" <<EOF
{
  "round": ${ROUND},
  "scenario": "pod_failure",
  "target": "${TARGET}",
  "killed_pod": "${POD}",
  "t0": ${T0_2A},
  "t1": ${T1_2A},
  "mttr_seconds": ${MTTR_2A}
}
EOF

  # Đợi vote hoàn toàn ổn định trước khi chạy 2B
  sleep 5
  kubectl rollout status deployment/"$TARGET" -n "$NS" --timeout=60s \
    >> "${ROUND_DIR}/2a-rollout-check.log" 2>&1 || true

  # ── 2B: Bad Deployment ──────────────────────────────────────
  log "  [R${ROUND}] 2B: Bad Deployment"

  BAD_IMAGE="tofuvotingappacr.azurecr.io/${TARGET}:does-not-exist-${RANDOM}"

  kubectl get pods -n "$NS" -o wide > "${ROUND_DIR}/2b-pods-before.txt" 2>/dev/null || true

  T0_2B=$(date +%s)
  kubectl set image deployment/"$TARGET" "${TARGET}=${BAD_IMAGE}" -n "$NS" \
    >> "${ROUND_DIR}/2b-set-image.log" 2>&1

  log "  [R${ROUND}] 2B: Đợi 60s cho rollout fail..."
  sleep 60

  kubectl get pods -n "$NS" -l "app=${TARGET}" -o wide \
    > "${ROUND_DIR}/2b-pods-during.txt" 2>/dev/null || true
  kubectl describe pods -n "$NS" -l "app=${TARGET}" \
    > "${ROUND_DIR}/2b-pods-describe.txt" 2>/dev/null || true

  # Verify stuck
  ROLLOUT_OUT=$(kubectl rollout status deployment/"$TARGET" -n "$NS" \
    --timeout=10s 2>&1 || true)
  echo "$ROLLOUT_OUT" >> "${ROUND_DIR}/2b-rollout-stuck.log"
  T1_2B=$(date +%s)
  DETECTION_2B=$((T1_2B - T0_2B))

  # Rollback
  kubectl set image deployment/"$TARGET" "${TARGET}=${GOOD_IMAGE}" -n "$NS" \
    >> "${ROUND_DIR}/2b-rollback.log" 2>&1
  kubectl rollout status deployment/"$TARGET" -n "$NS" \
    --timeout=300s >> "${ROUND_DIR}/2b-rollback.log" 2>&1
  T2_2B=$(date +%s)
  MTTR_2B=$((T2_2B - T0_2B))
  ROLLBACK_2B=$((T2_2B - T1_2B))

  kubectl get pods -n "$NS" -l "app=${TARGET}" -o wide \
    > "${ROUND_DIR}/2b-pods-after.txt" 2>/dev/null || true

  # Push to Pushgateway
  cat <<EOF | curl -sS --data-binary @- \
    "${PUSHGW}/metrics/job/kb2_round${ROUND}_bad_deploy/target/${TARGET}" \
    >> "${ROUND_DIR}/2b-push.log" 2>&1 || true
# TYPE bad_deployment_total counter
bad_deployment_total 1
# TYPE bad_deployment_detection_seconds gauge
bad_deployment_detection_seconds ${DETECTION_2B}
# TYPE bad_deployment_recovery_seconds gauge
bad_deployment_recovery_seconds ${MTTR_2B}
# TYPE bad_deployment_t0_unixtime gauge
bad_deployment_t0_unixtime ${T0_2B}
EOF

  BAD_DETECTION+=($DETECTION_2B)
  BAD_MTTR+=($MTTR_2B)
  BAD_ROLLBACK+=($ROLLBACK_2B)
  log "  [R${ROUND}] 2B DONE → Detection=${DETECTION_2B}s  MTTR=${MTTR_2B}s  Rollback=${ROLLBACK_2B}s"

  cat > "${ROUND_DIR}/2b-summary.json" <<EOF
{
  "round": ${ROUND},
  "scenario": "bad_deployment",
  "target": "${TARGET}",
  "bad_image": "${BAD_IMAGE}",
  "good_image": "${GOOD_IMAGE}",
  "t0": ${T0_2B},
  "t1_detected": ${T1_2B},
  "t2_recovered": ${T2_2B},
  "detection_seconds": ${DETECTION_2B},
  "rollback_seconds": ${ROLLBACK_2B},
  "total_mttr_seconds": ${MTTR_2B}
}
EOF

  # Đợi ổn định trước round tiếp theo
  if [ "$ROUND" -lt "$ROUNDS" ]; then
    log "  [R${ROUND}] Đợi 15s trước round tiếp theo..."
    sleep 15
    kubectl rollout status deployment/"$TARGET" -n "$NS" --timeout=60s \
      >> "${MASTER_LOG}" 2>&1 || true
  fi
done

# ── Tổng hợp kết quả ──────────────────────────────────────────
echo ""
log "=== TỔNG HỢP 5 ROUNDS ==="

# Tính trung bình bằng Python
python3 - "${RESULTS_BASE}" <<'PYEOF'
import json, os, sys

base = sys.argv[1]

pod_mttr = []
bad_detection = []
bad_mttr = []
bad_rollback = []

for r in range(1, 6):
    rdir = f"{base}/round{r}"
    f2a = f"{rdir}/2a-summary.json"
    f2b = f"{rdir}/2b-summary.json"
    if os.path.exists(f2a):
        with open(f2a) as f: d = json.load(f)
        pod_mttr.append(d['mttr_seconds'])
    if os.path.exists(f2b):
        with open(f2b) as f: d = json.load(f)
        bad_detection.append(d['detection_seconds'])
        bad_mttr.append(d['total_mttr_seconds'])
        bad_rollback.append(d['rollback_seconds'])

def stats(arr):
    if not arr: return {'avg':0,'min':0,'max':0}
    return {'avg': round(sum(arr)/len(arr),1), 'min': min(arr), 'max': max(arr)}

s_pod  = stats(pod_mttr)
s_det  = stats(bad_detection)
s_mttr = stats(bad_mttr)
s_rb   = stats(bad_rollback)

print("\n╔══════════════════════════════════════════════════════════╗")
print("║  KB2 – Kết quả 5 Rounds                                 ║")
print("╠══════════════════════════════════════════════════════════╣")
print("║  2A – Pod Failure MTTR (s)                               ║")
header = f"  {'Round':<8} {'MTTR (s)':>10}"
print(f"║{header:<57}║")
for i, v in enumerate(pod_mttr):
    row = f"  R{i+1:<7} {v:>10}"
    print(f"║{row:<57}║")
row_avg = f"  {'AVG':<8} {s_pod['avg']:>10}   MIN={s_pod['min']}  MAX={s_pod['max']}"
print(f"║{row_avg:<57}║")
print("╠══════════════════════════════════════════════════════════╣")
print("║  2B – Bad Deployment (s)                                 ║")
header2 = f"  {'Round':<8} {'Detection':>10} {'Rollback':>10} {'MTTR':>8}"
print(f"║{header2:<57}║")
for i in range(len(bad_detection)):
    row = f"  R{i+1:<7} {bad_detection[i]:>10} {bad_rollback[i]:>10} {bad_mttr[i]:>8}"
    print(f"║{row:<57}║")
row_avg2 = f"  {'AVG':<8} {s_det['avg']:>10} {s_rb['avg']:>10} {s_mttr['avg']:>8}"
print(f"║{row_avg2:<57}║")
print("╚══════════════════════════════════════════════════════════╝")

# Lưu JSON tổng hợp
summary = {
    "rounds": 5,
    "2a_pod_failure": {
        "per_round": pod_mttr,
        "avg_mttr_s": s_pod['avg'],
        "min_mttr_s": s_pod['min'],
        "max_mttr_s": s_pod['max'],
    },
    "2b_bad_deployment": {
        "detection_per_round": bad_detection,
        "rollback_per_round": bad_rollback,
        "mttr_per_round": bad_mttr,
        "avg_detection_s": s_det['avg'],
        "min_detection_s": s_det['min'],
        "max_detection_s": s_det['max'],
        "avg_rollback_s": s_rb['avg'],
        "avg_mttr_s": s_mttr['avg'],
        "min_mttr_s": s_mttr['min'],
        "max_mttr_s": s_mttr['max'],
    }
}
with open(f"{base}/FULL-SUMMARY.json", "w") as f:
    json.dump(summary, f, indent=2)
print(f"\nSaved: {base}/FULL-SUMMARY.json")
PYEOF

log "=== ALL DONE. Results: ${RESULTS_BASE} ==="
