#!/usr/bin/env bash
# Kịch bản 2A: Pod failure / Crash
# Mục đích: Đo MTTR khi pod vote/worker bị giết
#
# Các bước:
#  1. Ghi state baseline (pods, alerts)
#  2. Ghi nhận T0: thời điểm xóa pod
#  3. Theo dõi pod recreated, ready (T1)
#  4. Theo dõi alert firing → resolved (T2, T3)
#  5. Push event "pod_failure_recovered" vào Pushgateway để DORA collector ghi nhận
#  6. Lưu log vào folder results/

set -euo pipefail

TARGET="${TARGET:-vote}"      # vote | worker | result | redis | db
NS="${NS:-default}"
PUSHGW="${PUSHGW:-http://localhost:9091}"
TS=$(date +%Y%m%d-%H%M%S)
OUT_DIR="results/pod-failure-${TARGET}-${TS}"
mkdir -p "$OUT_DIR"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$OUT_DIR/scenario.log"; }

log "=== Kịch bản: Pod Failure target=$TARGET ==="

# --- Step 1: Baseline ---
log "Step 1: Lưu baseline state"
kubectl get pods -n "$NS" -o wide > "$OUT_DIR/01-pods-before.txt"
kubectl get deployment "$TARGET" -n "$NS" -o yaml > "$OUT_DIR/01-deploy-before.yaml" || true

# Chọn pod đầu tiên match label app=$TARGET
POD=$(kubectl get pods -n "$NS" -l "app=$TARGET" -o jsonpath='{.items[0].metadata.name}')
if [ -z "$POD" ]; then
  log "ERROR: không tìm thấy pod app=$TARGET"
  exit 1
fi
log "Target pod: $POD"

# --- Step 2: Kill pod ---
T0=$(date +%s)
log "Step 2: Xóa pod $POD tại T0=$T0"
kubectl delete pod "$POD" -n "$NS" --grace-period=0 --force 2>&1 | tee -a "$OUT_DIR/scenario.log"

# --- Step 3: Wait for new pod Ready ---
log "Step 3: Đợi pod mới Ready (timeout 300s)..."
kubectl wait --for=condition=Ready pod -l "app=$TARGET" -n "$NS" --timeout=300s 2>&1 | tee -a "$OUT_DIR/scenario.log"
T1=$(date +%s)
RECOVERY_SECONDS=$((T1 - T0))
log "Step 3: Pod Ready tại T1=$T1, recovery=${RECOVERY_SECONDS}s"

kubectl get pods -n "$NS" -o wide > "$OUT_DIR/03-pods-after.txt"

# --- Step 4: Lưu describe để có evidence restart count ---
log "Step 4: Mô tả deployment + pod để evidence"
kubectl describe deployment "$TARGET" -n "$NS" > "$OUT_DIR/04-deploy-describe.txt"

# --- Step 5: Push event lên Pushgateway ---
log "Step 5: Push event vào Pushgateway $PUSHGW"
cat <<EOF | curl -sS --data-binary @- "$PUSHGW/metrics/job/scenario_pod_failure/target/$TARGET" || log "WARN: Pushgateway không reachable, bỏ qua"
# TYPE pod_failure_recovery_seconds gauge
pod_failure_recovery_seconds $RECOVERY_SECONDS
# TYPE pod_failure_t0_unixtime gauge
pod_failure_t0_unixtime $T0
# TYPE pod_failure_t1_unixtime gauge
pod_failure_t1_unixtime $T1
EOF

# --- Summary ---
cat > "$OUT_DIR/SUMMARY.md" <<EOF
# Pod Failure Scenario - $TARGET

| Field | Value |
|---|---|
| Target | $TARGET |
| Namespace | $NS |
| Killed pod | $POD |
| T0 (kill) | $(date -u -d @$T0 -Iseconds) |
| T1 (Ready) | $(date -u -d @$T1 -Iseconds) |
| **Recovery (MTTR)** | **${RECOVERY_SECONDS}s** |

## Cách xác nhận
\`\`\`bash
# 1. Restart count tăng
kubectl describe deployment $TARGET -n $NS | grep -A3 'Replicas'

# 2. Alert đã firing rồi resolved trên Prometheus
# Mở Prometheus: ALERTS{alertname="VoteAppPodNotReady"}

# 3. Pushgateway có metric mới
curl http://localhost:9091/metrics | grep pod_failure
\`\`\`
EOF

log "=== DONE. MTTR = ${RECOVERY_SECONDS}s. Output: $OUT_DIR ==="
