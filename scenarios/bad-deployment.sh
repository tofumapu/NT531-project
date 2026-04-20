#!/usr/bin/env bash
# Kịch bản 2B: Bad Deployment / Change Failure
# Mục đích: Deploy bad image, observe failure → rollback. Đo MTTR.
#
# Cách gây lỗi: thay image của deployment vote thành image sai tag
# (vd: tofuvotingappacr.azurecr.io/vote:does-not-exist)
# → ImagePullBackOff → readinessProbe fail → service degraded
# → rollback bằng `kubectl rollout undo`
#
# Push event vào Pushgateway để DORA collector tính Change Failure Rate

set -euo pipefail

TARGET="${TARGET:-vote}"
NS="${NS:-default}"
PUSHGW="${PUSHGW:-http://localhost:9091}"
BAD_IMAGE="${BAD_IMAGE:-tofuvotingappacr.azurecr.io/${TARGET}:does-not-exist-${RANDOM}}"
TS=$(date +%Y%m%d-%H%M%S)
OUT_DIR="results/bad-deploy-${TARGET}-${TS}"
mkdir -p "$OUT_DIR"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$OUT_DIR/scenario.log"; }

log "=== Kịch bản: Bad Deployment target=$TARGET image=$BAD_IMAGE ==="

# Step 1: Lưu image hiện tại để rollback
log "Step 1: Lưu image hiện tại của $TARGET"
GOOD_IMAGE=$(kubectl get deployment "$TARGET" -n "$NS" -o jsonpath='{.spec.template.spec.containers[0].image}')
log "Good image: $GOOD_IMAGE"
kubectl get deployment "$TARGET" -n "$NS" -o yaml > "$OUT_DIR/01-good-deploy.yaml"

# Step 2: Deploy bad image
T0=$(date +%s)
log "Step 2: Set image = $BAD_IMAGE tại T0=$T0"
kubectl set image deployment/"$TARGET" "$TARGET=$BAD_IMAGE" -n "$NS"

# Step 3: Đợi 60s để rollout fail (probe phải fail)
log "Step 3: Đợi 60s observe failure..."
sleep 60
kubectl get pods -n "$NS" -l "app=$TARGET" -o wide > "$OUT_DIR/03-pods-during-failure.txt"
kubectl describe pods -n "$NS" -l "app=$TARGET" > "$OUT_DIR/03-pods-describe.txt"

# Verify rollout đang stuck
ROLLOUT_STATUS=$(kubectl rollout status deployment/"$TARGET" -n "$NS" --timeout=10s 2>&1 || true)
log "Rollout status (mong đợi: stuck/timeout): $ROLLOUT_STATUS"
T1=$(date +%s)
DETECTION_SECONDS=$((T1 - T0))
log "Step 3: Failure detected tại T1=$T1, detection=${DETECTION_SECONDS}s"

# Step 4: Rollback
log "Step 4: Rollback về image $GOOD_IMAGE"
kubectl set image deployment/"$TARGET" "$TARGET=$GOOD_IMAGE" -n "$NS"
# Hoặc dùng: kubectl rollout undo deployment/$TARGET -n $NS

kubectl rollout status deployment/"$TARGET" -n "$NS" --timeout=300s 2>&1 | tee -a "$OUT_DIR/scenario.log"
T2=$(date +%s)
RECOVERY_SECONDS=$((T2 - T0))
log "Step 4: Rollback xong tại T2=$T2, total MTTR=${RECOVERY_SECONDS}s"

kubectl get pods -n "$NS" -l "app=$TARGET" -o wide > "$OUT_DIR/04-pods-after-rollback.txt"

# Step 5: Push event vào Pushgateway → DORA Change Failure Rate
log "Step 5: Push event vào Pushgateway $PUSHGW"
cat <<EOF | curl -sS --data-binary @- "$PUSHGW/metrics/job/scenario_bad_deployment/target/$TARGET" || log "WARN: Pushgateway không reachable"
# TYPE bad_deployment_total counter
bad_deployment_total 1
# TYPE bad_deployment_detection_seconds gauge
bad_deployment_detection_seconds $DETECTION_SECONDS
# TYPE bad_deployment_recovery_seconds gauge
bad_deployment_recovery_seconds $RECOVERY_SECONDS
# TYPE bad_deployment_t0_unixtime gauge
bad_deployment_t0_unixtime $T0
EOF

# Summary
cat > "$OUT_DIR/SUMMARY.md" <<EOF
# Bad Deployment Scenario - $TARGET

| Field | Value |
|---|---|
| Target | $TARGET |
| Bad image | $BAD_IMAGE |
| Good image (rollback to) | $GOOD_IMAGE |
| T0 (apply bad) | $(date -u -d @$T0 -Iseconds) |
| T1 (detected) | $(date -u -d @$T1 -Iseconds) |
| T2 (rollback done) | $(date -u -d @$T2 -Iseconds) |
| **Detection time** | **${DETECTION_SECONDS}s** |
| **Total MTTR** | **${RECOVERY_SECONDS}s** |

## Cách xác nhận
\`\`\`bash
# 1. Replicaset history: thấy 2 revision (bad rồi good)
kubectl rollout history deployment/$TARGET -n $NS

# 2. Pushgateway có metrics
curl -s http://localhost:9091/metrics | grep bad_deployment

# 3. Prometheus alert đã firing trong khoảng T0-T2
# Query: ALERTS{alertname="VoteAppDeploymentReplicaMismatch"}

# 4. DORA collector sau ~60s sẽ tăng dora_change_failure_rate_24h
curl -s http://<dora-collector>:8000/metrics | grep change_failure
\`\`\`
EOF

log "=== DONE. Detection=${DETECTION_SECONDS}s, MTTR=${RECOVERY_SECONDS}s ==="
