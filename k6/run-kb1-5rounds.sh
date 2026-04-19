#!/bin/bash
# run-kb1-5rounds.sh - Chạy KB1 Baseline 5 lần với tải tăng dần
# Mục tiêu: tìm điểm nghẽn/bottleneck của hệ thống

set -e
export PATH="$HOME/.local/bin:$PATH"

NODE_IP="${1:-$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')}"
VOTE_URL="http://${NODE_IP}:31000"
PROM_URL="http://${NODE_IP}:30301"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATE=$(date +%Y-%m-%d)
RESULTS_BASE="${SCRIPT_DIR}/results/kb1-5rounds-${DATE}"
mkdir -p "$RESULTS_BASE"

K6="$HOME/.local/bin/k6"
COLLECT="${SCRIPT_DIR}/collect-metrics.sh"
chmod +x "$COLLECT"

echo "╔══════════════════════════════════════════════════════╗"
echo "║    KB1 – Baseline Hiệu Năng: 5 Rounds               ║"
echo "║    Node IP  : ${NODE_IP}                             "
echo "║    Vote URL : ${VOTE_URL}                            "
echo "║    Prom URL : ${PROM_URL}                            "
echo "║    Results  : ${RESULTS_BASE}                        "
echo "╚══════════════════════════════════════════════════════╝"
echo ""

# Kiểm tra ứng dụng
echo "[pre-flight] Checking vote app..."
HTTP=$(curl -sf -o /dev/null -w "%{http_code}" "$VOTE_URL/" || echo "000")
if [ "$HTTP" != "200" ]; then
  echo "  ERROR: vote app not reachable ($HTTP). Abort."
  exit 1
fi
echo "  vote app: OK (HTTP $HTTP)"

# Pod restarts trước khi test
kubectl get pods -n default -o wide > "$RESULTS_BASE/pod-state-before.txt"
RESTART_BEFORE=$(kubectl get pods -n default -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.containerStatuses[*]}{.restartCount}{"\n"}{end}{end}')
echo "$RESTART_BEFORE" > "$RESULTS_BASE/restarts-before.txt"
echo ""

# ─────────────────────────────────────────────────────────────────
# Bảng cấu hình 5 rounds - tải tăng dần để tìm điểm nghẽn
# Standard_D2s_v3: 2 vCPU, 8 GB RAM
# vote: Python Flask (single worker) + Redis
# ─────────────────────────────────────────────────────────────────
declare -A NORMAL_VUS=([1]=30  [2]=60  [3]=100 [4]=150 [5]=200)
declare -A MEDIUM_VUS=([1]=75  [2]=120 [3]=180 [4]=250 [5]=350)
declare -A SPIKE_VUS=( [1]=200 [2]=300 [3]=450 [4]=600 [5]=800)

declare -A NORMAL_SLEEP=([1]=600 [2]=400 [3]=300 [4]=200 [5]=150)
declare -A MEDIUM_SLEEP=([1]=400 [2]=300 [3]=200 [4]=150 [5]=100)
declare -A SPIKE_SLEEP=( [1]=100 [2]=80  [3]=60  [4]=50  [5]=40 )

NORMAL_DUR="3m"
MEDIUM_DUR="3m"
SPIKE_HOLD="1m30s"
RAMP_TIME="30s"

run_scenario() {
  local ROUND=$1
  local SCENARIO=$2
  local VUS=$3
  local DURATION=$4
  local SLEEP_MS=$5
  local OUTDIR="${RESULTS_BASE}/round${ROUND}"
  mkdir -p "$OUTDIR"

  echo "  ┌─ [Round $ROUND / $SCENARIO] VUs=$VUS  Duration=$DURATION  Sleep=${SLEEP_MS}ms"

  cd "$OUTDIR"
  "$K6" run \
    -e VOTE_URL="$VOTE_URL" \
    -e VUS="$VUS" \
    -e DURATION="$DURATION" \
    -e RAMP="${RAMP_TIME}" \
    -e SCENARIO="${SCENARIO}-r${ROUND}" \
    -e SLEEP_MS="$SLEEP_MS" \
    --summary-export="${SCENARIO}-summary.json" \
    "${SCRIPT_DIR}/stress-parameterized.js" \
    2>&1 | tee "${SCENARIO}-stdout.txt" | grep -E "✓|✗|default|p\(9[05]\)|http_req_failed|iterations|running.*100%"

  # Collect Prometheus metrics ngay sau test
  echo "  │  Collecting infra metrics..."
  bash "$COLLECT" "$PROM_URL" "r${ROUND}-${SCENARIO}" "${OUTDIR}/${SCENARIO}-infra.json"

  # Pod status snapshot
  kubectl get pods -n default --no-headers >> "${OUTDIR}/pod-status.txt" 2>/dev/null
  echo "  └─ Done"
  echo ""
}

# ════════════════════════════════════════════════════════
for ROUND in 1 2 3 4 5; do
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ROUND $ROUND / 5"
  echo "  Normal=${NORMAL_VUS[$ROUND]}VU  Medium=${MEDIUM_VUS[$ROUND]}VU  Spike=${SPIKE_VUS[$ROUND]}VU"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  # Collect baseline metrics TRƯỚC khi round bắt đầu
  mkdir -p "${RESULTS_BASE}/round${ROUND}"
  bash "$COLLECT" "$PROM_URL" "r${ROUND}-before" \
    "${RESULTS_BASE}/round${ROUND}/00-before-infra.json"

  run_scenario $ROUND "normal" "${NORMAL_VUS[$ROUND]}" "$NORMAL_DUR" "${NORMAL_SLEEP[$ROUND]}"
  run_scenario $ROUND "medium" "${MEDIUM_VUS[$ROUND]}" "$MEDIUM_DUR" "${MEDIUM_SLEEP[$ROUND]}"
  run_scenario $ROUND "spike"  "${SPIKE_VUS[$ROUND]}"  "$SPIKE_HOLD" "${SPIKE_SLEEP[$ROUND]}"

  # Check pod restarts sau mỗi round
  echo "  [post-round $ROUND] Pod restarts:"
  kubectl get pods -n default -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.containerStatuses[*]}{.restartCount}{"\n"}{end}{end}' \
    | awk '{if($2>0) print "    RESTART: "$0; else print "    OK: "$0}'
  echo ""
done
# ════════════════════════════════════════════════════════

# Final pod state
kubectl get pods -n default -o wide > "$RESULTS_BASE/pod-state-after.txt"
kubectl top nodes > "$RESULTS_BASE/node-top-final.txt" 2>/dev/null || true
kubectl top pods -n default > "$RESULTS_BASE/pod-top-final.txt" 2>/dev/null || true

echo "╔══════════════════════════════════════════════════════╗"
echo "║  All 5 rounds complete. Generating summary...        ║"
echo "╚══════════════════════════════════════════════════════╝"

# Tổng hợp summary table
python3 - <<PYEOF
import json, glob, os

base = "$RESULTS_BASE"
rows = []

for r in range(1, 6):
    for sc in ["normal", "medium", "spike"]:
        sf = f"{base}/round{r}/{sc}-summary.json"
        inf = f"{base}/round{r}/{sc}-infra.json"
        if not os.path.exists(sf):
            continue
        with open(sf) as f:
            s = json.load(f)
        inf_data = {}
        if os.path.exists(inf):
            with open(inf) as f:
                inf_data = json.load(f)

        rows.append({
            "round": r,
            "scenario": sc,
            "vus": s.get("vus_max", "?"),
            "rps": s.get("rps_avg", "?"),
            "p50": s.get("latency_ms", {}).get("p50", "?"),
            "p95": s.get("latency_ms", {}).get("p95", "?"),
            "p99": s.get("latency_ms", {}).get("p99", "?"),
            "errors_pct": s.get("error_rate_pct", "?"),
            "iterations": s.get("iterations", "?"),
            "node_cpu": inf_data.get("node", {}).get("cpu_usage_pct", "?"),
            "vote_cpu": inf_data.get("pods", {}).get("vote_cpu_millicores", "?"),
            "tcp_retx": inf_data.get("tcp", {}).get("retransmission_pct", "?"),
            "app_p95": inf_data.get("app_side", {}).get("p95_latency_ms", "?"),
            "restarts": inf_data.get("pods", {}).get("restarts_total", "?"),
            "pass": s.get("thresholds_passed", "?"),
        })

# Save full summary JSON
with open(f"{base}/FULL-SUMMARY.json", "w") as f:
    json.dump(rows, f, indent=2)

# Print table
print("\n{'='*90}")
print(f"{'Round':<6} {'Scen':<8} {'VUs':<5} {'RPS':<7} {'p50':>6} {'p95':>8} {'p99':>8} {'Err%':>6} {'NodeCPU':>8} {'VoteCPU':>8} {'TCPretx':>8} {'AppP95':>8} {'Pass'}")
print("-"*105)
for r in rows:
    flag = "✓" if r["pass"] == True else "✗"
    print(f"  R{r['round']:<4} {r['scenario']:<8} {str(r['vus']):<5} {str(r['rps']):<7} "
          f"{str(r['p50']):>6} {str(r['p95']):>8} {str(r['p99']):>8} {str(r['errors_pct']):>6} "
          f"{str(r['node_cpu']):>8} {str(r['vote_cpu']):>8} {str(r['tcp_retx']):>8} "
          f"{str(r['app_p95']):>8} {flag}")
print("")
print(f"Full summary: {base}/FULL-SUMMARY.json")
PYEOF

echo ""
echo "Results saved to: $RESULTS_BASE"
