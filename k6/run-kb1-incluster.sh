#!/bin/bash
# run-kb1-incluster.sh - KB1 Baseline 5 rounds với k6 TRONG CLUSTER
# Dùng Pod YAML manifest thay vì --overrides để tránh quoting issues

NODE_IP="${1:-}"
if [ -z "$NODE_IP" ]; then
  NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}' 2>/dev/null)
fi
VOTE_URL_INTERNAL="http://vote:8080"
PROM_URL="http://${NODE_IP}:30301"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DATE=$(date +%Y-%m-%d)
RESULTS_BASE="${SCRIPT_DIR}/results/kb1-incluster-${DATE}"
COLLECT="${SCRIPT_DIR}/collect-metrics.sh"

mkdir -p "$RESULTS_BASE"

echo "╔══════════════════════════════════════════════════════════╗"
echo "║    KB1 – In-Cluster: 5 Rounds                            ║"
echo "║    k6 location  : IN CLUSTER (Pod in default ns)         ║"
echo "║    Vote URL     : ${VOTE_URL_INTERNAL}                   "
echo "║    Prom URL     : ${PROM_URL}                            "
echo "╚══════════════════════════════════════════════════════════╝"

# Pre-flight
if [ -z "$NODE_IP" ]; then echo "ERROR: NODE_IP empty"; exit 1; fi
echo "[pre-flight] NODE_IP=$NODE_IP"

# ConfigMap với script
kubectl create configmap k6-incluster-script \
  --from-file=script.js="${SCRIPT_DIR}/stress-incluster.js" \
  -n default --dry-run=client -o yaml | kubectl apply -f - || true

echo "[pre-flight] vote ClusterIP=$(kubectl get svc vote -n default -o jsonpath='{.spec.clusterIP}' 2>/dev/null):8080"

# Snapshot trước test
kubectl get pods -n default -o wide > "${RESULTS_BASE}/pod-state-before.txt" 2>/dev/null || true

# Cấu hình 5 rounds
NORMAL_VUS=(0 30 60 100 150 200)
MEDIUM_VUS=(0 75 120 180 250 350)
SPIKE_VUS=(0 200 300 450 600 800)
NORMAL_SLEEP=(0 600 400 300 200 150)
MEDIUM_SLEEP=(0 400 300 200 150 100)
SPIKE_SLEEP=(0 100 80 60 50 40)

run_scenario() {
  local ROUND=$1
  local SCENARIO=$2
  local VUS=$3
  local DURATION=$4
  local SLEEP_MS=$5
  local OUTDIR="${RESULTS_BASE}/round${ROUND}"
  mkdir -p "$OUTDIR"

  local POD_NAME="k6-r${ROUND}-${SCENARIO}"
  # Cleanup old pod nếu có
  kubectl delete pod "$POD_NAME" -n default --force --grace-period=0 2>/dev/null || true

  echo "  ┌─ [Round $ROUND / $SCENARIO] VUs=$VUS  Duration=$DURATION  Sleep=${SLEEP_MS}ms"

  # Tạo Pod YAML
  cat > "/tmp/${POD_NAME}.yaml" <<YAML
apiVersion: v1
kind: Pod
metadata:
  name: ${POD_NAME}
  namespace: default
  labels:
    app: k6-load-test
    round: "r${ROUND}"
    scenario: "${SCENARIO}"
spec:
  restartPolicy: Never
  volumes:
  - name: script
    configMap:
      name: k6-incluster-script
  containers:
  - name: k6
    image: grafana/k6:0.57.0
    imagePullPolicy: IfNotPresent
    command:
    - k6
    - run
    - -e
    - VOTE_URL=${VOTE_URL_INTERNAL}
    - -e
    - VUS=${VUS}
    - -e
    - DURATION=${DURATION}
    - -e
    - RAMP=30s
    - -e
    - SCENARIO=r${ROUND}-${SCENARIO}
    - -e
    - SLEEP_MS=${SLEEP_MS}
    - /scripts/script.js
    volumeMounts:
    - name: script
      mountPath: /scripts
    resources:
      requests:
        cpu: "50m"
        memory: "128Mi"
      limits:
        cpu: "600m"
        memory: "320Mi"
YAML

  kubectl apply -f "/tmp/${POD_NAME}.yaml" 2>/dev/null

  # Đợi pod Running
  echo -n "  │  Waiting to start"
  for i in $(seq 1 30); do
    STATUS=$(kubectl get pod "$POD_NAME" -n default --no-headers 2>/dev/null | awk '{print $3}')
    if echo "$STATUS" | grep -qE "Running|Completed|Error|CrashLoop"; then break; fi
    echo -n "."; sleep 3
  done
  echo ""

  # Đợi pod hoàn thành
  echo -n "  │  Running"
  for i in $(seq 1 120); do
    STATUS=$(kubectl get pod "$POD_NAME" -n default --no-headers 2>/dev/null | awk '{print $3}')
    if echo "$STATUS" | grep -qE "Completed|Succeeded|Error|CrashLoop"; then break; fi
    echo -n "."; sleep 10
  done
  echo " $STATUS"

  # Thu logs
  kubectl logs "$POD_NAME" -n default > "${OUTDIR}/${SCENARIO}-stdout.txt" 2>&1

  # Parse JSON từ log output
  python3 - "${OUTDIR}/${SCENARIO}-stdout.txt" "${OUTDIR}/${SCENARIO}-summary.json" "${VUS}" "${SCENARIO}" <<'PYEOF'
import sys, json, re

infile, outfile, vus, scenario = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
try:
    with open(infile) as f:
        content = f.read()
    m = re.search(r'__K6_JSON_BEGIN__\n(.+?)\n__K6_JSON_END__', content, re.DOTALL)
    if m:
        data = json.loads(m.group(1).strip())
        with open(outfile, 'w') as f:
            json.dump(data, f, indent=2)
        print(f"  │  RPS={data['rps_avg']}  p95={data['latency_ms']['p95']}ms  errors={data['error_rate_pct']}%")
    else:
        # Fallback: parse từ text output
        print("  │  WARNING: JSON block not found, creating stub")
        with open(outfile, 'w') as f:
            json.dump({"scenario": scenario, "vus_max": vus, "parse_error": True,
                       "raw": content[-500:]}, f, indent=2)
except Exception as e:
    print(f"  │  PARSE ERROR: {e}")
    with open(outfile, 'w') as f:
        json.dump({"scenario": scenario, "vus_max": vus, "error": str(e)}, f)
PYEOF

  # Prometheus metrics
  echo "  │  Collecting infra metrics..."
  bash "$COLLECT" "$PROM_URL" "r${ROUND}-${SCENARIO}-ic" "${OUTDIR}/${SCENARIO}-infra.json" 2>/dev/null

  # Pod status
  kubectl get pods -n default --no-headers >> "${OUTDIR}/pod-status.txt" 2>/dev/null || true

  # Cleanup pod
  kubectl delete pod "$POD_NAME" -n default --force --grace-period=0 2>/dev/null &

  echo "  └─ Done"
  echo ""
}

# ═══════════════════════════════════════════════════════════
for ROUND in 1 2 3 4 5; do
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
  echo "  ROUND $ROUND / 5  [IN-CLUSTER]"
  echo "  Normal=${NORMAL_VUS[$ROUND]}VU  Medium=${MEDIUM_VUS[$ROUND]}VU  Spike=${SPIKE_VUS[$ROUND]}VU"
  echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

  mkdir -p "${RESULTS_BASE}/round${ROUND}"
  bash "$COLLECT" "$PROM_URL" "r${ROUND}-before-ic" \
    "${RESULTS_BASE}/round${ROUND}/00-before-infra.json" 2>/dev/null

  run_scenario "$ROUND" "normal" "${NORMAL_VUS[$ROUND]}" "3m"    "${NORMAL_SLEEP[$ROUND]}"
  run_scenario "$ROUND" "medium" "${MEDIUM_VUS[$ROUND]}" "3m"    "${MEDIUM_SLEEP[$ROUND]}"
  run_scenario "$ROUND" "spike"  "${SPIKE_VUS[$ROUND]}"  "1m30s" "${SPIKE_SLEEP[$ROUND]}"

  echo "  [post-round $ROUND] Pod restarts:"
  kubectl get pods -n default -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{range .status.containerStatuses[*]}{.restartCount}{"\n"}{end}{end}' 2>/dev/null \
    | grep -v "^$" \
    | awk '{if(NF>=2 && $NF+0>0) print "    ⚠ RESTART: "$0; else if(NF>=1) print "    ✓ "$1"\t"$2}'
  echo ""
done
# ═══════════════════════════════════════════════════════════

kubectl get pods -n default -o wide > "${RESULTS_BASE}/pod-state-after.txt" 2>/dev/null || true
kubectl top nodes > "${RESULTS_BASE}/node-top-final.txt" 2>/dev/null || true

echo "╔══════════════════════════════════════════════════════════╗"
echo "║  All 5 rounds complete. Generating summary...            ║"
echo "╚══════════════════════════════════════════════════════════╝"

python3 - <<PYEOF
import json, os

BASE     = "${RESULTS_BASE}"
BASE_OUT = "${SCRIPT_DIR}/results/kb1-5rounds-${DATE}"

VUS_MAP = {
    (1,'normal'):30,  (1,'medium'):75,  (1,'spike'):200,
    (2,'normal'):60,  (2,'medium'):120, (2,'spike'):300,
    (3,'normal'):100, (3,'medium'):180, (3,'spike'):450,
    (4,'normal'):150, (4,'medium'):250, (4,'spike'):600,
    (5,'normal'):200, (5,'medium'):350, (5,'spike'):800,
}

rows_in = []
for r in range(1,6):
    for sc in ['normal','medium','spike']:
        sf  = f"{BASE}/round{r}/{sc}-summary.json"
        inf = f"{BASE}/round{r}/{sc}-infra.json"
        if not os.path.exists(sf):
            continue
        with open(sf) as f:
            s = json.load(f)
        if s.get('parse_error') or s.get('error'):
            print(f"  WARN: R{r} {sc} parse error: {s.get('error','')}")
            continue
        inf_data = {}
        if os.path.exists(inf):
            with open(inf) as f:
                inf_data = json.load(f)
        row = {
            'round': r, 'scenario': sc,
            'vus': VUS_MAP.get((r,sc),'?'),
            'iterations':  s.get('iterations', 0),
            'rps':         s.get('rps_avg', 0),
            'p50':         s.get('latency_ms', {}).get('p50', 0),
            'p95':         s.get('latency_ms', {}).get('p95', 0),
            'p99':         s.get('latency_ms', {}).get('p99', 0),
            'error_pct':   s.get('error_rate_pct', 0),
            'node_cpu':    inf_data.get('node',{}).get('cpu_usage_pct','?'),
            'vote_cpu':    inf_data.get('pods',{}).get('vote_cpu_millicores','?'),
            'tcp_retx':    inf_data.get('tcp',{}).get('retransmission_pct','?'),
            'app_p95':     inf_data.get('app_side',{}).get('p95_latency_ms','?'),
            'restarts':    inf_data.get('pods',{}).get('restarts_total','?'),
        }
        rows_in.append(row)

with open(f"{BASE}/FULL-SUMMARY.json","w") as f:
    json.dump(rows_in, f, indent=2)

# Load outside-cluster for comparison
rows_out = []
out_f = f"{BASE_OUT}/FULL-SUMMARY.json"
if os.path.exists(out_f):
    with open(out_f) as f:
        rows_out = json.load(f)
out_map = {(r['round'],r['scenario']): r for r in rows_out}

# In-cluster table
print(f"\n{'='*105}")
print(f"  {'Rnd':<4} {'Sc':<7} {'VUs':<5} {'Iter':>7} {'RPS':>7} {'p50':>7} {'p95':>8} {'p99':>8} {'Err%':>6} | {'NodeCPU':>8} {'VoteCPU':>8} {'TCPretx':>8}")
print("-"*105)
for row in rows_in:
    flag = "⚠" if float(row['error_pct'])>1 else "✓"
    print(f"  R{row['round']:<3} {row['scenario']:<7} {str(row['vus']):<5} "
          f"{str(row['iterations']):>7} {str(row['rps']):>7} "
          f"{str(row['p50']):>7} {str(row['p95']):>8} {str(row['p99']):>8} "
          f"{str(row['error_pct']):>5}{flag} | "
          f"{str(row['node_cpu']):>8} {str(row['vote_cpu']):>8} {str(row['tcp_retx']):>8}")

# Comparison
if out_map:
    print(f"\n{'='*90}")
    print("  COMPARISON: In-Cluster vs Outside-Cluster (p95 latency ms)")
    print(f"  {'Rnd':<4} {'Sc':<7} {'VUs':<5} | {'IN-CLUSTER p95':>15} | {'OUTSIDE p95':>13} | {'Reduction':>10}")
    print("-"*90)
    for row in rows_in:
        out = out_map.get((row['round'],row['scenario']),{})
        p95_in  = float(row['p95'])  if row['p95'] else 0
        p95_out = float(out.get('p95', 0))
        if p95_out > 0 and p95_in > 0:
            red = f"-{(1 - p95_in/p95_out)*100:.0f}%"
        else:
            red = "N/A"
        print(f"  R{row['round']:<3} {row['scenario']:<7} {str(row['vus']):<5} | "
              f"{str(p95_in):>15} | {str(p95_out):>13} | {red:>10}")

print(f"\nSaved: {BASE}/FULL-SUMMARY.json")
PYEOF

echo ""
echo "Results: $RESULTS_BASE"
