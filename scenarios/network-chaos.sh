#!/usr/bin/env bash
# Kịch bản 3: Sự cố mạng có kiểm soát (Network Chaos)
# Phương pháp: Tạo privileged pod trên cùng node với vote, dùng nsenter + tc netem
# để inject packet loss và delay vào network namespace của vote pod
#
# Tham số:
#   DELAY_MS  : độ trễ thêm vào (ms), mặc định 200ms
#   LOSS_PCT  : packet loss (%), mặc định 5%
#   CHAOS_DURATION : thời gian giữ chaos (s), mặc định 60s
#   TARGET    : deployment target, mặc định vote
#   PUSHGW    : Pushgateway URL

set -euo pipefail

TARGET="${TARGET:-vote}"
NS="${NS:-default}"
PUSHGW="${PUSHGW:-http://localhost:9091}"
DELAY_MS="${DELAY_MS:-200}"
LOSS_PCT="${LOSS_PCT:-5}"
CHAOS_DURATION="${CHAOS_DURATION:-60}"
TS=$(date +%Y%m%d-%H%M%S)
OUT_DIR="results/network-chaos-${TARGET}-${TS}"
mkdir -p "$OUT_DIR"

log() { echo "[$(date -Iseconds)] $*" | tee -a "$OUT_DIR/scenario.log"; }

log "=== Kịch bản 3: Network Chaos target=$TARGET delay=${DELAY_MS}ms loss=${LOSS_PCT}% duration=${CHAOS_DURATION}s ==="

# --- Step 1: Baseline state ---
log "Step 1: Lưu baseline state"
kubectl get pods -n "$NS" -o wide > "$OUT_DIR/01-pods-before.txt"
NODE=$(kubectl get pod -n "$NS" -l "app=$TARGET" -o jsonpath='{.items[0].spec.nodeName}')
VOTE_POD=$(kubectl get pod -n "$NS" -l "app=$TARGET" -o jsonpath='{.items[0].metadata.name}')
log "Vote pod: $VOTE_POD on node: $NODE"

# Lấy metrics TCP retransmission baseline
PROM_URL="http://localhost:9090"
RETX_BEFORE=$(curl -s "${PROM_URL}/api/v1/query" \
  --data-urlencode 'query=rate(node_netstat_Tcp_RetransSegs[1m])/rate(node_netstat_Tcp_OutSegs[1m])*100' \
  2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); r=d['data']['result']; print(round(float(r[0]['value'][1]),4) if r else 0)" 2>/dev/null || echo "N/A")
log "TCP retransmission rate BEFORE chaos: ${RETX_BEFORE}%"

# Lấy container ID của vote pod
CONTAINER_ID=$(kubectl get pod "$VOTE_POD" -n "$NS" \
  -o jsonpath='{.status.containerStatuses[0].containerID}' | sed 's|containerd://||')
log "Container ID: $CONTAINER_ID"

# --- Step 2: Deploy chaos pod ---
log "Step 2: Deploy privileged chaos pod trên node $NODE"
CHAOS_POD_NAME="network-chaos-$(echo $TS | tr -d '-' | tr -d ':')-$$"

cat <<EOF | kubectl apply -f - 2>&1 | tee -a "$OUT_DIR/scenario.log"
apiVersion: v1
kind: Pod
metadata:
  name: ${CHAOS_POD_NAME}
  namespace: ${NS}
  labels:
    app: network-chaos
spec:
  nodeName: ${NODE}
  hostPID: true
  hostNetwork: true
  restartPolicy: Never
  tolerations:
  - operator: Exists
  containers:
  - name: chaos
    image: ubuntu:22.04
    securityContext:
      privileged: true
    command:
    - /bin/bash
    - -c
    - |
      apt-get install -y iproute2 -qq 2>/dev/null
      # Tìm PID của vote container qua cgroup
      CGROUP_PATH="/sys/fs/cgroup"
      CONTAINER_SHORT="${CONTAINER_ID:0:12}"
      VOTE_PID=\$(find /proc -maxdepth 3 -name cgroup 2>/dev/null | xargs grep -l "\${CONTAINER_SHORT}" 2>/dev/null | head -1 | cut -d/ -f3)
      if [ -z "\$VOTE_PID" ]; then
        echo "WARN: cannot find vote container PID via cgroup, applying tc to node eth0 instead"
        IFACE=\$(ip route | grep default | awk '{print \$5}' | head -1)
        echo "Injecting on node interface: \$IFACE"
        tc qdisc add dev \$IFACE root netem delay ${DELAY_MS}ms loss ${LOSS_PCT}% || true
        sleep ${CHAOS_DURATION}
        tc qdisc del dev \$IFACE root || true
      else
        echo "Found vote PID=\$VOTE_PID"
        nsenter -t \$VOTE_PID -n -- tc qdisc add dev eth0 root netem delay ${DELAY_MS}ms loss ${LOSS_PCT}% || true
        sleep ${CHAOS_DURATION}
        nsenter -t \$VOTE_PID -n -- tc qdisc del dev eth0 root || true
      fi
      echo "CHAOS_DONE"
    resources:
      requests:
        cpu: "10m"
        memory: "32Mi"
EOF

# Đợi pod Running
log "Step 2: Đợi chaos pod khởi động..."
kubectl wait pod "$CHAOS_POD_NAME" -n "$NS" --for=condition=Ready --timeout=60s 2>&1 || true
T0=$(date +%s)
log "Step 2: Chaos bắt đầu tại T0=$T0 (delay=${DELAY_MS}ms, loss=${LOSS_PCT}%)"

# --- Step 3: Run load test TRONG KHI chaos ---
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
K6_DIR="$SCRIPT_DIR/../k6"
ABS_OUT_DIR="$SCRIPT_DIR/$OUT_DIR"
log "Step 3: Chạy load test 30s TRONG KHI có network chaos..."
python3 "$K6_DIR/load_runner.py" \
  --url "http://23.100.104.161:31000" \
  --concurrency 20 \
  --duration 30 \
  --sleep 0.3 \
  --scenario "chaos-load" \
  --output "$ABS_OUT_DIR/03-load-during-chaos.json" 2>&1 | tee -a "$ABS_OUT_DIR/scenario.log" || true

# Lấy metrics TRONG khi chaos
RETX_DURING=$(curl -s "${PROM_URL}/api/v1/query" \
  --data-urlencode 'query=rate(node_netstat_Tcp_RetransSegs[1m])/rate(node_netstat_Tcp_OutSegs[1m])*100' \
  2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); r=d['data']['result']; print(round(float(r[0]['value'][1]),4) if r else 0)" 2>/dev/null || echo "N/A")
log "TCP retransmission rate DURING chaos: ${RETX_DURING}%"

# --- Step 4: Đợi chaos pod hoàn thành, cleanup ---
log "Step 4: Đợi chaos kết thúc (${CHAOS_DURATION}s)..."
kubectl wait pod "$CHAOS_POD_NAME" -n "$NS" \
  --for=jsonpath='{.status.phase}'=Succeeded --timeout=180s 2>&1 | tee -a "$OUT_DIR/scenario.log" || true
T1=$(date +%s)
CHAOS_ELAPSED=$((T1 - T0))
log "Step 4: Chaos kết thúc tại T1=$T1 (elapsed=${CHAOS_ELAPSED}s)"

# Xóa chaos pod
kubectl delete pod "$CHAOS_POD_NAME" -n "$NS" --grace-period=0 2>&1 | tee -a "$OUT_DIR/scenario.log" || true

# --- Step 5: Đo recovery ---
log "Step 5: Đo metrics sau khi chaos kết thúc..."
sleep 15
RETX_AFTER=$(curl -s "${PROM_URL}/api/v1/query" \
  --data-urlencode 'query=rate(node_netstat_Tcp_RetransSegs[1m])/rate(node_netstat_Tcp_OutSegs[1m])*100' \
  2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); r=d['data']['result']; print(round(float(r[0]['value'][1]),4) if r else 0)" 2>/dev/null || echo "N/A")
log "TCP retransmission rate AFTER chaos: ${RETX_AFTER}%"

kubectl get pods -n "$NS" -o wide > "$OUT_DIR/05-pods-after.txt"

# Load result để tính p95, error_rate
LOAD_JSON="$SCRIPT_DIR/$OUT_DIR/03-load-during-chaos.json"
LOAD_P95=$(python3 -c "import json; d=json.load(open('$LOAD_JSON')); print(d['latency_ms']['p95'])" 2>/dev/null || echo "N/A")
LOAD_RPS=$(python3 -c "import json; d=json.load(open('$LOAD_JSON')); print(d['rps_avg'])" 2>/dev/null || echo "N/A")
LOAD_ERR=$(python3 -c "import json; d=json.load(open('$LOAD_JSON')); print(d['error_rate_pct'])" 2>/dev/null || echo "N/A")

# --- Step 6: Push metrics lên Pushgateway ---
log "Step 6: Push event vào Pushgateway"
cat <<EOF | curl -sS --data-binary @- "$PUSHGW/metrics/job/scenario_network_chaos/target/$TARGET" || log "WARN: Pushgateway không reachable"
# TYPE network_chaos_delay_ms gauge
network_chaos_delay_ms ${DELAY_MS}
# TYPE network_chaos_loss_pct gauge
network_chaos_loss_pct ${LOSS_PCT}
# TYPE network_chaos_duration_seconds gauge
network_chaos_duration_seconds ${CHAOS_ELAPSED}
# TYPE network_chaos_retx_before gauge
network_chaos_retx_before ${RETX_BEFORE}
# TYPE network_chaos_retx_during gauge
network_chaos_retx_during ${RETX_DURING}
# TYPE network_chaos_retx_after gauge
network_chaos_retx_after ${RETX_AFTER}
EOF

# --- Summary ---
cat > "$OUT_DIR/SUMMARY.md" <<EOF
# Network Chaos Scenario - $TARGET

| Field | Value |
|---|---|
| Target | $TARGET |
| Chaos params | delay=${DELAY_MS}ms, loss=${LOSS_PCT}% |
| Chaos duration | ${CHAOS_ELAPSED}s |
| T0 (chaos start) | $(date -u -d @$T0 -Iseconds) |
| T1 (chaos end) | $(date -u -d @$T1 -Iseconds) |

## TCP Retransmission Rate

| Phase | Rate |
|---|---|
| **Before chaos** | ${RETX_BEFORE}% |
| **During chaos** | ${RETX_DURING}% |
| **After chaos** | ${RETX_AFTER}% |

## Load test during chaos (20 VU, 30s)

| Metric | Value |
|---|---|
| RPS | ${LOAD_RPS} |
| p95 latency | ${LOAD_P95} ms |
| Error rate | ${LOAD_ERR}% |

## Cách xác nhận
\`\`\`bash
# Prometheus: TCP retransmission tăng trong khoảng T0-T1
# Query: rate(node_netstat_Tcp_RetransSegs[1m])/rate(node_netstat_Tcp_OutSegs[1m])*100

# Load test output
cat $OUT_DIR/03-load-during-chaos.json

# Pushgateway
curl -s http://localhost:9091/metrics | grep network_chaos
\`\`\`
EOF

log "=== DONE. Retx: before=${RETX_BEFORE}% during=${RETX_DURING}% after=${RETX_AFTER}% ==="
