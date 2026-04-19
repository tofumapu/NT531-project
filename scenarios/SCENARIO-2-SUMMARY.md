# Kịch bản 2 - Sự cố hạ tầng và ứng dụng (2026-04-15)

## 2A. Pod Failure (vote)

| Field | Value |
|---|---|
| Target | vote |
| Killed pod | vote-8469b4b4ff-84h9s |
| T0 (kill) | 2026-04-15 07:46:02 UTC |
| T1 (Ready new pod) | 2026-04-15 07:46:07 UTC |
| **MTTR** | **5 giây** |
| Method | `kubectl delete pod ... --grace-period=0 --force` |

**Kết luận:** Recovery rất nhanh (5s) – Kubernetes Deployment + ReplicaSet hoạt động đúng.

## 2B. Bad Deployment (vote)

| Field | Value |
|---|---|
| Target | vote |
| Bad image | `tofuvotingappacr.azurecr.io/vote:does-not-exist-16980` |
| Good image | `tofuvotingappacr.azurecr.io/votingapp/vote:69` |
| T0 (apply bad) | 2026-04-15 07:47:40 UTC |
| T1 (detected, rollout timeout 60s) | 2026-04-15 07:48:48 UTC |
| T2 (rollback xong) | 2026-04-15 07:48:48 UTC |
| **Detection time** | **68 giây** |
| **Total MTTR** | **67 giây** |

**Quan sát:**
- Pod mới ImagePullBackOff (image không tồn tại), pod cũ vẫn chạy → service vote không bị down hoàn toàn (rolling update strategy)
- Kubectl `set image` rồi `rollout status --timeout=60s` đã phát hiện rollout stuck
- Rollback bằng `kubectl set image` về tag cũ → hoàn tất sau ~7s

**Kết luận:** MTTR thực tế khá nhanh nhờ:
1. Default rolling strategy bảo vệ availability (pod cũ chưa down trước khi pod mới Ready)
2. Detection bằng `rollout status --timeout` rõ ràng
3. Rollback đơn giản bằng `kubectl set image`

## Bằng chứng đã ghi vào Pushgateway

```
pod_failure_recovery_seconds{target="vote"}        = 5
pod_failure_t0_unixtime{target="vote"}             = 1776239162
pod_failure_t1_unixtime{target="vote"}             = 1776239167

bad_deployment_total{target="vote"}                = 1
bad_deployment_detection_seconds{target="vote"}    = 68
bad_deployment_recovery_seconds{target="vote"}     = 67
bad_deployment_t0_unixtime{target="vote"}          = 1776239260
```

## Cách xác nhận

```bash
# 1. Xem log scenario chi tiết
ls scenarios/results/
cat scenarios/results/pod-failure-vote-20260415-144600/SUMMARY.md
cat scenarios/results/bad-deploy-vote-20260415-144738/SUMMARY.md

# 2. Verify Pushgateway có data
kubectl port-forward -n monitoring svc/pushgateway 9091:9091 &
curl -s http://localhost:9091/metrics | grep -E "(pod_failure|bad_deployment)"

# 3. Xem trên Prometheus
kubectl port-forward -n monitoring svc/prom-stack-kube-prometheus-prometheus 9090:9090 &
# Mở http://localhost:9090
# Query:
#   bad_deployment_recovery_seconds
#   pod_failure_recovery_seconds
#   ALERTS{alertname="VoteAppDeploymentReplicaMismatch"}

# 4. Xem rollout history
kubectl rollout history deployment/vote -n default

# 5. Xem trong Grafana DORA dashboard
# http://40.81.186.16:31300 → "DORA Metrics - NT531 Nhom 17"
# Panel "Bad deployments (Pushgateway)" sẽ hiển thị 1
```
