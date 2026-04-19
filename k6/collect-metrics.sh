#!/bin/bash
# collect-metrics.sh - Thu Prometheus metrics sau mỗi sub-test
# Usage: ./collect-metrics.sh <PROM_URL> <LABEL> <OUTPUT_FILE>

PROM="${1:-http://localhost:30301}"
LABEL="${2:-unknown}"
OUT="${3:-metrics-$(date +%s).json}"

q() {
  curl -sf "${PROM}/api/v1/query" --data-urlencode "query=$1" 2>/dev/null \
    | python3 -c "import sys,json; d=json.load(sys.stdin); r=d['data']['result']; print(float(r[0]['value'][1]) if r else 0)" 2>/dev/null || echo "0"
}

echo "  Collecting Prometheus metrics for [$LABEL]..."

# Metrics thu thập
NODE_CPU=$(q '100-(avg(rate(node_cpu_seconds_total{mode="idle"}[2m]))*100)')
NODE_MEM_AVAIL=$(q 'node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes*100')
NODE_MEM_USED=$(q '(1 - node_memory_MemAvailable_bytes/node_memory_MemTotal_bytes)*100')
TCP_RETX=$(q 'rate(node_netstat_Tcp_RetransSegs[2m])/clamp_min(rate(node_netstat_Tcp_OutSegs[2m]),1)*100')
TCP_ESTAB=$(q 'node_netstat_Tcp_CurrEstab')
VOTE_CPU=$(q 'sum(rate(container_cpu_usage_seconds_total{namespace="default",pod=~"vote.*",container="vote"}[2m]))*1000')
WORKER_CPU=$(q 'sum(rate(container_cpu_usage_seconds_total{namespace="default",pod=~"worker.*"}[2m]))*1000')
REDIS_CPU=$(q 'sum(rate(container_cpu_usage_seconds_total{namespace="default",pod=~"redis.*"}[2m]))*1000')
VOTE_MEM=$(q 'sum(container_memory_working_set_bytes{namespace="default",pod=~"vote.*",container="vote"})/1024/1024')
APP_P95=$(q 'histogram_quantile(0.95,sum by(le)(rate(vote_http_request_duration_seconds_bucket[2m])))*1000')
APP_P99=$(q 'histogram_quantile(0.99,sum by(le)(rate(vote_http_request_duration_seconds_bucket[2m])))*1000')
APP_RPS=$(q 'sum(rate(vote_http_request_total[2m]))')
POD_RESTARTS=$(q 'sum(kube_pod_container_status_restarts_total{namespace="default"})')
NET_TX=$(q 'sum(rate(node_network_transmit_bytes_total{device!~"lo|veth.*|cni.*|flannel.*|cbr.*|tunl.*|kube.*"}[2m]))*8/1024/1024')
NET_RX=$(q 'sum(rate(node_network_receive_bytes_total{device!~"lo|veth.*|cni.*|flannel.*|cbr.*|tunl.*|kube.*"}[2m]))*8/1024/1024')

python3 - <<PYEOF
import json, datetime

data = {
    "label": "$LABEL",
    "timestamp": datetime.datetime.utcnow().isoformat() + "Z",
    "node": {
        "cpu_usage_pct":      round(float("$NODE_CPU"), 2),
        "memory_used_pct":    round(float("$NODE_MEM_USED"), 2),
        "memory_avail_pct":   round(float("$NODE_MEM_AVAIL"), 2),
        "net_tx_mbps":        round(float("$NET_TX"), 3),
        "net_rx_mbps":        round(float("$NET_RX"), 3),
    },
    "tcp": {
        "retransmission_pct": round(float("$TCP_RETX"), 4),
        "established_conns":  int(float("$TCP_ESTAB")),
    },
    "pods": {
        "vote_cpu_millicores":   round(float("$VOTE_CPU"), 1),
        "worker_cpu_millicores": round(float("$WORKER_CPU"), 1),
        "redis_cpu_millicores":  round(float("$REDIS_CPU"), 1),
        "vote_mem_mb":           round(float("$VOTE_MEM"), 1),
        "restarts_total":        int(float("$POD_RESTARTS")),
    },
    "app_side": {
        "p95_latency_ms": round(float("$APP_P95"), 1),
        "p99_latency_ms": round(float("$APP_P99"), 1),
        "rps":            round(float("$APP_RPS"), 2),
    }
}

with open("$OUT", "w") as f:
    json.dump(data, f, indent=2)

print(f"    CPU: {data['node']['cpu_usage_pct']}%  Mem: {data['node']['memory_used_pct']}%  TCP-retx: {data['tcp']['retransmission_pct']}%")
print(f"    Vote CPU: {data['pods']['vote_cpu_millicores']}m  App-p95: {data['app_side']['p95_latency_ms']}ms  App-RPS: {data['app_side']['rps']}")
print(f"    TCP established: {data['tcp']['established_conns']}  Pod restarts: {data['pods']['restarts_total']}")
PYEOF
