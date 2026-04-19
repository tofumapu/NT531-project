# NT531 Nhóm 17 — AI-Augmented Voting App on AKS

Dự án môn **NT531 — Quản trị mạng nâng cao** (Nhóm 17).  
Mở rộng [example-voting-app](https://github.com/dockersamples/example-voting-app) của Docker với hệ thống giám sát đầy đủ, chaos engineering, và **AI SRE Agent** có khả năng tự động phát hiện và xử lý sự cố trên Kubernetes (AKS).

---

## Kiến trúc tổng quan

```
                    ┌─────────────────────────────────────────────────────┐
                    │                   AKS Cluster                        │
                    │                                                       │
  Browser ──────── │► vote (Flask:8080) ──► Redis ──► worker (.NET)       │
                    │                                       │               │
  Browser ──────── │► result (Node:8081) ◄──── PostgreSQL ◄┘               │
                    │                                                       │
                    │  ┌─── Monitoring (namespace: monitoring) ──────────┐ │
                    │  │  Prometheus ◄── ServiceMonitor                  │ │
                    │  │  Grafana (dashboards)                           │ │
                    │  │  Alertmanager ──webhook──► AI SRE Agent ────────┼─┼──► kubectl actions
                    │  │  Decision Log (audit trail)                     │ │         (rollback/restart/suggest)
                    │  │  DORA Collector (Azure DevOps → DORA metrics)   │ │
                    │  │  PushGateway                                    │ │
                    │  └────────────────────────────────────────────────┘ │
                    └─────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer | Công nghệ |
|---|---|
| Voting frontend | Python 3 + Flask |
| Results frontend | Node.js + Express + Socket.IO |
| Worker | .NET 8 |
| Queue | Redis |
| Database | PostgreSQL |
| Container orchestration | Kubernetes (AKS) |
| Monitoring | Prometheus + Grafana + Alertmanager (kube-prometheus-stack) |
| AI SRE Agent | Python 3 + FastAPI + kubernetes-client |
| LLM Gateway | OpenAI-compatible API (9Router) |
| Chaos Engineering | Chaos Mesh (NetworkChaos, PodChaos) |
| Load Testing | k6 |
| CI/CD | Azure Pipelines → ACR → AKS |
| DORA Metrics | Azure DevOps REST API |

---

## Cấu trúc thư mục

```
example-voting-app/
├── vote/                        # Python Flask — giao diện bỏ phiếu
├── result/                      # Node.js — giao diện kết quả
├── worker/                      # .NET — xử lý vote từ Redis → PostgreSQL
├── agent/                       # AI SRE Agent (FastAPI)
│   ├── app.py                   #   webhook handler + LLM logic + kubectl actions
│   ├── requirements.txt
│   └── Dockerfile
├── decision-log/                # Audit log service (FastAPI)
├── dora-collector/              # DORA metrics collector (Azure DevOps)
├── k8s-specifications/          # Kubernetes manifests (deployments, services, RBAC)
├── monitoring/                  # Helm values, PrometheusRules, Alertmanager config
├── grafana-dashboards/          # Grafana dashboard JSON
├── scenarios/                   # Chaos & test automation scripts
│   ├── pod-failure.sh           #   KB2a — pod kill simulation
│   ├── bad-deployment.sh        #   KB2b — bad image rollout
│   ├── network-chaos.sh         #   KB3 — Chaos Mesh NetworkChaos
│   └── chaosmesh/               #   Chaos Mesh YAML resources
├── k6/                          # k6 load test scripts
│   ├── baseline-normal.js
│   ├── baseline-medium.js
│   ├── baseline-spike.js
│   └── stress-parameterized.js
├── healthchecks/                # Service health check helpers
├── docker-compose.yml           # Local development (Docker Compose)
├── docker-compose.images.yml    # Compose với pre-built images
├── docker-stack.yml             # Docker Swarm
├── azure-pipelines-aks.yml      # CI/CD pipeline (build → push ACR → deploy AKS)
├── .env.example                 # Template biến môi trường cần cấu hình
└── .gitignore
```

---

## Yêu cầu hệ thống

- Docker Desktop (cho local dev)
- `kubectl` + `kubeconfig` trỏ vào AKS cluster
- Azure CLI (`az`) — để login ACR, quản lý AKS
- Helm 3
- k6 — `brew install k6` hoặc từ [k6.io](https://k6.io/docs/get-started/installation/)
- Chaos Mesh (cài trên cluster nếu chạy KB3)

---

## Chạy local (Docker Compose)

```bash
docker compose up
```

- **Vote app:** http://localhost:8080
- **Results app:** http://localhost:8081

---

## Deploy lên Kubernetes (AKS)

### 1. Cài monitoring stack

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update
helm install prom-stack prometheus-community/kube-prometheus-stack \
  -n monitoring --create-namespace \
  -f monitoring/prometheus-values.yaml
```

### 2. Tạo secrets bắt buộc

```bash
# ACR image pull secret
kubectl create secret docker-registry acr-secret \
  --docker-server=<acr>.azurecr.io \
  --docker-username=<service-principal-id> \
  --docker-password=<service-principal-secret> \
  -n default

kubectl create secret docker-registry acr-secret \
  --docker-server=<acr>.azurecr.io \
  --docker-username=<service-principal-id> \
  --docker-password=<service-principal-secret> \
  -n monitoring

# Azure DevOps PAT (cho agent pipeline_rerun action)
kubectl -n monitoring create secret generic agent-azdo \
  --from-literal=AZDO_PAT="<your-pat>"

# Telegram notifications (optional)
kubectl -n monitoring create secret generic agent-telegram \
  --from-literal=TELEGRAM_BOT_TOKEN="<token>" \
  --from-literal=TELEGRAM_CHAT_ID="<chat-id>"
```

### 3. Deploy ứng dụng

```bash
# Core services (default namespace)
kubectl apply -f k8s-specifications/

# AI Agent + Monitoring services (monitoring namespace)
kubectl apply -f k8s-specifications/agent-deployment.yaml
kubectl apply -f k8s-specifications/decision-log-deployment.yaml
kubectl apply -f k8s-specifications/dora-collector-deployment.yaml
```

### 4. Truy cập

```bash
# Vote app (NodePort 31000)
kubectl get nodes -o wide   # lấy External IP

# Grafana
kubectl -n monitoring port-forward svc/prom-stack-grafana 3000:80
# → http://localhost:3000 (admin / prom-operator)
```

---

## Cấu hình AI SRE Agent

Agent nhận webhook từ Alertmanager, truy vấn Prometheus để làm giàu context, sau đó gọi LLM để quyết định hành động remediation.

Các biến môi trường quan trọng (xem `.env.example`):

| Biến | Mô tả |
|---|---|
| `LLM_BASE_URL` | Endpoint LLM gateway (OpenAI-compatible) |
| `LLM_MODEL` | Model/combo cần dùng (vd. `devops-test-agent`) |
| `MIN_CONFIDENCE_FOR_ACTION` | Ngưỡng confidence để agent thực thi (mặc định 0.7) |
| `AGENT_DRY_RUN` | `true` để test không thực thi kubectl |
| `MAX_ACTIONS_PER_MINUTE` | Rate limit an toàn |

**Actions được hỗ trợ:**

| Action | Mô tả |
|---|---|
| `restart` | Delete pod bị lỗi → K8s tự tạo lại |
| `rollback` | `kubectl rollout undo deployment/<name>` |
| `suggest` | Log suggestion, không thực thi |
| `no_action` | Không làm gì, ghi log |

---

## Thí nghiệm (Knowledge Base — KB)

### KB1 — Baseline Performance
Load test 3 kịch bản × 5 rounds: Normal (30VU), Medium (60VU), Spike (100VU)

| Kịch bản | p95 Latency (median) | RPS (median) |
|---|---|---|
| Normal | ~1,686 ms | ~19.5 req/s |
| Medium | ~3,587 ms | ~16.4 req/s |
| Spike | ~5,354 ms | ~16.2 req/s |

### KB2 — Failure Recovery (MTTR)
- **KB2a — Pod Failure:** Kill vote pod → đo MTTR. Median MTTR = **5 giây**
- **KB2b — Bad Deployment:** Deploy image lỗi → detect + rollback. Median tổng = **79 giây** (detect ~77s + rollback ~2s)

### KB3 — Network Chaos (Chaos Mesh)
NetworkChaos inject lên pod network namespace:
- **Exp A** (vote → redis): 50% packet loss + 800ms delay → vote_p95_chaos median = **229ms**
- **Exp B** (worker → db): 60% packet loss + 1.5s delay, 60VUs → vote_p95_chaos median = **454ms**

### KB4 — AI Agent Impact
4 kịch bản × 5 runs mỗi kịch bản:

| Scenario | Mô tả | Kết quả chính |
|---|---|---|
| B (Baseline) | Pod failure không có agent | MTTR median = 60s |
| C (Suggest) | Agent chỉ đề xuất, không thực thi | Decision Accuracy = 100%, LLM latency median = 6.49s |
| D (Restart) | Agent tự restart pod | Decision Accuracy = 100%, Pod MTTR = 4s |
| E (Rollback) | Agent tự rollback bad deployment | MTTR median = 91s (R4=40s, R5=39s khi warm) |

---

## Load Testing

```bash
# Chạy baseline 3 kịch bản (local port-forward)
cd k6 && bash run-all-baseline.sh

# Chạy KB1 incluster
bash run-kb1-incluster.sh

# Chạy với VU tùy chỉnh
k6 run --vus 60 --duration 120s k6/baseline-medium.js
```

Kết quả lưu tại `k6/results/` (excluded khỏi git — chỉ script được commit).

---

## Visualizations (Dashboard Python/Flask)

Scripts phân tích tại `k6/visualization/`. Yêu cầu: `pip install flask matplotlib`.

```bash
cd k6/visualization

python3 visualize_kb1.py &        # KB1 — Baseline incluster    → http://localhost:5050
python3 visualize_kb3.py &        # KB3 — Network Chaos          → http://localhost:5051
python3 visualize_kb2.py &        # KB2 — MTTR Pod/Deploy        → http://localhost:5052
python3 visualize_compare.py &    # KB1 — 5rounds vs incluster   → http://localhost:5053
python3 visualize_kb4.py &        # KB4 — AI Agent Impact        → http://localhost:5054
```

> **Lưu ý:** KB1 (`visualize_kb1.py`) đọc dữ liệu từ `k6/results/kb1-incluster-2026-04-16/` (gitignored).
> Các script còn lại có dữ liệu nhúng sẵn — chạy được ngay sau khi clone.

---

## Bảo mật

- Tất cả secret (PAT, Telegram token, ACR password) được lưu làm **Kubernetes Secret** — không commit vào repo.
- Biến môi trường nhạy cảm: xem `.env.example` để biết danh sách, **không** dùng file này trực tiếp.
- `agentpool/` (Azure DevOps agent runtime) bị loại khỏi git bằng `.gitignore`.

---

## Credits

Base project: [dockersamples/example-voting-app](https://github.com/dockersamples/example-voting-app) — Apache License 2.0  
Extended by: NT531 Nhóm 17, UIT 2026
