#!/usr/bin/env python3
"""KB2 — Pod Failure & Bad Deployment | NT531 Nhóm 17"""

import io, base64, statistics
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import matplotlib.patches as mpatches
import numpy as np
from flask import Flask

plt.rcParams.update({"figure.dpi": 130, "font.size": 9,
                     "axes.spines.top": False, "axes.spines.right": False})

# ══════════════════════════════════════════════════════════════════════════════
#  DATA — từ all-kb-results/kb2-round{1..5}/
# ══════════════════════════════════════════════════════════════════════════════

DATA_2A = [   # Pod Failure (kubectl delete pod --force)
    {"round": 1, "pod": "vote-c7ff9ccc5-4x4f2", "mttr": 4},
    {"round": 2, "pod": "vote-c7ff9ccc5-hfd9h",  "mttr": 5},
    {"round": 3, "pod": "vote-c7ff9ccc5-c2pqk",  "mttr": 4},
    {"round": 4, "pod": "vote-c7ff9ccc5-6hdpf",  "mttr": 5},
    {"round": 5, "pod": "vote-c7ff9ccc5-nlw7x",  "mttr": 5},
]

DATA_2B = [   # Bad Deployment (image tag không tồn tại → rollback)
    {"round": 1, "bad_image": "vote:does-not-exist-16004", "detect": 77, "rollback": 2, "total": 79},
    {"round": 2, "bad_image": "vote:does-not-exist-19713", "detect": 77, "rollback": 2, "total": 79},
    {"round": 3, "bad_image": "vote:does-not-exist-573",   "detect": 78, "rollback": 2, "total": 80},
    {"round": 4, "bad_image": "vote:does-not-exist-2998",  "detect": 77, "rollback": 2, "total": 79},
    {"round": 5, "bad_image": "vote:does-not-exist-1059",  "detect": 77, "rollback": 1, "total": 78},
]

ROUNDS = [1, 2, 3, 4, 5]
MTTR_2A   = [d["mttr"]    for d in DATA_2A]
DETECT_2B = [d["detect"]  for d in DATA_2B]
ROLLBK_2B = [d["rollback"]for d in DATA_2B]
TOTAL_2B  = [d["total"]   for d in DATA_2B]

MED_2A    = statistics.median(MTTR_2A)        # 5
MED_TOTAL = statistics.median(TOTAL_2B)       # 79
MED_DET   = statistics.median(DETECT_2B)      # 77
MED_RBK   = statistics.median(ROLLBK_2B)      # 2

C_2A  = "#3498db"   # blue — pod failure
C_DET = "#e67e22"   # orange — detection
C_RBK = "#e74c3c"   # red — rollback
C_TOT = "#8e44ad"   # purple — total
C_MED = "#2c3e50"   # dark — median line

# ══════════════════════════════════════════════════════════════════════════════
def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()

def img(b64):
    return f'<img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:8px;box-shadow:0 2px 10px #0002;margin:6px 0">'

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 1 — MTTR Pod Failure (2a) per round + median
# ══════════════════════════════════════════════════════════════════════════════
def chart_mttr_pod():
    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = np.arange(len(ROUNDS))
    bars = ax.bar(x, MTTR_2A, color=C_2A, alpha=0.85, edgecolor="white", width=0.55, zorder=3)

    for bar, v in zip(bars, MTTR_2A):
        ax.text(bar.get_x() + bar.get_width()/2, v + 0.08,
                f"{v}s", ha="center", va="bottom", fontsize=11, fontweight="bold", color=C_2A)

    # Median line
    ax.axhline(MED_2A, color=C_MED, ls="--", lw=2, label=f"Trung vị = {MED_2A:.0f}s", zorder=5)
    ax.text(4.42, MED_2A + 0.05, f"median={MED_2A:.0f}s", color=C_MED, fontsize=9, fontweight="bold")

    # SLO reference
    ax.axhline(30, color="red", ls=":", lw=1.2, alpha=0.6, label="SLO ref 30s")

    ax.set_xticks(x)
    ax.set_xticklabels([f"Round {r}" for r in ROUNDS], fontsize=10)
    ax.set_ylabel("MTTR (giây)", fontsize=10)
    ax.set_ylim(0, max(MTTR_2A) * 2.5)
    ax.set_title("2a — MTTR Pod Failure\n(kubectl delete pod --force → pod Ready lại)", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9); ax.grid(axis="y", alpha=0.3, zorder=0)
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 2 — MTTR Bad Deploy Total per round + median
# ══════════════════════════════════════════════════════════════════════════════
def chart_mttr_deploy():
    fig, ax = plt.subplots(figsize=(9, 4.5))
    x = np.arange(len(ROUNDS))
    bars = ax.bar(x, TOTAL_2B, color=C_TOT, alpha=0.85, edgecolor="white", width=0.55, zorder=3)

    for bar, v in zip(bars, TOTAL_2B):
        ax.text(bar.get_x() + bar.get_width()/2, v + 0.5,
                f"{v}s", ha="center", va="bottom", fontsize=11, fontweight="bold", color=C_TOT)

    ax.axhline(MED_TOTAL, color=C_MED, ls="--", lw=2, label=f"Trung vị = {MED_TOTAL:.0f}s", zorder=5)
    ax.text(4.42, MED_TOTAL + 0.5, f"median={MED_TOTAL:.0f}s", color=C_MED, fontsize=9, fontweight="bold")
    ax.axhline(300, color="red", ls=":", lw=1.2, alpha=0.6, label="SLO ref 5min")

    ax.set_xticks(x)
    ax.set_xticklabels([f"Round {r}" for r in ROUNDS], fontsize=10)
    ax.set_ylabel("Total MTTR (giây)", fontsize=10)
    ax.set_ylim(0, max(TOTAL_2B) * 1.5)
    ax.set_title("2b — Total MTTR Bad Deployment\n(detect + rollback → pod Ready)", fontsize=12, fontweight="bold")
    ax.legend(fontsize=9); ax.grid(axis="y", alpha=0.3, zorder=0)
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 3 — Stacked Bar: detection + rollback per round
# ══════════════════════════════════════════════════════════════════════════════
def chart_stacked():
    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(ROUNDS))
    w = 0.55

    b1 = ax.bar(x, DETECT_2B, w, label=f"Detection (trung vị={MED_DET:.0f}s)",
                color=C_DET, alpha=0.88, edgecolor="white", zorder=3)
    b2 = ax.bar(x, ROLLBK_2B, w, bottom=DETECT_2B,
                label=f"Rollback (trung vị={MED_RBK:.0f}s)",
                color=C_RBK, alpha=0.88, edgecolor="white", zorder=3)

    for i, (d, r) in enumerate(zip(DETECT_2B, ROLLBK_2B)):
        ax.text(x[i], d/2, f"{d}s", ha="center", va="center", fontsize=10,
                fontweight="bold", color="white")
        ax.text(x[i], d + r/2, f"{r}s", ha="center", va="center", fontsize=10,
                fontweight="bold", color="white")
        ax.text(x[i], d + r + 0.8, f"Total={d+r}s", ha="center", va="bottom",
                fontsize=9, color=C_MED, fontweight="bold")

    # Median total
    ax.axhline(MED_TOTAL, color=C_MED, ls="--", lw=2, label=f"Trung vị total = {MED_TOTAL:.0f}s")
    ax.set_xticks(x)
    ax.set_xticklabels([f"Round {r}" for r in ROUNDS], fontsize=10)
    ax.set_ylabel("Thời gian (giây)", fontsize=10)
    ax.set_ylim(0, max(TOTAL_2B) * 1.45)
    ax.set_title("2b — Phân tách Detection vs Rollback per Round\n(Bad Deployment: image tag không tồn tại)",
                 fontsize=12, fontweight="bold")
    ax.legend(fontsize=9); ax.grid(axis="y", alpha=0.3, zorder=0)
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 4 — Box plot: 2a vs 2b distribution
# ══════════════════════════════════════════════════════════════════════════════
def chart_boxplot():
    fig, ax = plt.subplots(figsize=(9, 5))

    data   = [MTTR_2A, TOTAL_2B]
    labels = ["2a: Pod Failure\n(MTTR)", "2b: Bad Deploy\n(Total MTTR)"]
    colors = [C_2A, C_TOT]

    bp = ax.boxplot(data, labels=labels, patch_artist=True, widths=0.45,
                    medianprops={"color": "white", "lw": 2.5},
                    whiskerprops={"lw": 1.5},
                    capprops={"lw": 2},
                    flierprops={"marker": "o", "ms": 7})

    for patch, color in zip(bp["boxes"], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.75)

    # Scatter overlay (jitter)
    for i, (vals, color) in enumerate(zip(data, colors), start=1):
        jitter = np.random.uniform(-0.08, 0.08, len(vals))
        ax.scatter([i + j for j in jitter], vals, color=color,
                   s=80, zorder=5, edgecolors="white", lw=0.8)
        for j, v in zip(jitter, vals):
            ax.annotate(f"{v}s", (i+j, v), textcoords="offset points",
                        xytext=(8, 0), fontsize=8, color=color)

    # Median annotations
    ax.text(1, MED_2A + 0.1, f"median={MED_2A:.0f}s", ha="center", fontsize=9,
            color="white", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=C_2A, alpha=0.9))
    ax.text(2, MED_TOTAL + 0.5, f"median={MED_TOTAL:.0f}s", ha="center", fontsize=9,
            color="white", fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.3", facecolor=C_TOT, alpha=0.9))

    ax.set_ylabel("MTTR (giây)", fontsize=10)
    ax.set_title("Phân phối MTTR — 2a (Pod Failure) vs 2b (Bad Deploy)\n5 rounds mỗi scenario",
                 fontsize=12, fontweight="bold")
    ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 5 — Timeline so sánh: 2a vs 2b side-by-side
# ══════════════════════════════════════════════════════════════════════════════
def chart_comparison():
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))

    # Left: 2a MTTR values scatter + line
    ax = axes[0]
    ax.plot(ROUNDS, MTTR_2A, "o-", color=C_2A, lw=2.5, ms=10, zorder=5)
    for r, v in zip(ROUNDS, MTTR_2A):
        ax.annotate(f"{v}s", (r, v), textcoords="offset points",
                    xytext=(0, 9), ha="center", fontsize=10, fontweight="bold", color=C_2A)
    ax.axhline(MED_2A, color=C_MED, ls="--", lw=1.8, label=f"Trung vị={MED_2A:.0f}s")
    ax.fill_between(ROUNDS, [min(MTTR_2A)]*5, [max(MTTR_2A)]*5, alpha=0.08, color=C_2A)
    ax.set_xticks(ROUNDS); ax.set_xticklabels([f"R{r}" for r in ROUNDS])
    ax.set_ylabel("MTTR (s)"); ax.set_ylim(0, 12)
    ax.set_title("2a — Pod Failure MTTR\nper round (Kubernetes reschedule)", fontsize=10.5, fontweight="bold")
    ax.legend(fontsize=9); ax.grid(alpha=0.3)

    # Right: 2b Total + Detection + Rollback lines
    ax = axes[1]
    ax.plot(ROUNDS, TOTAL_2B,  "D-",  color=C_TOT, lw=2.5, ms=9, label="Total MTTR", zorder=5)
    ax.plot(ROUNDS, DETECT_2B, "s--", color=C_DET, lw=1.8, ms=7, label="Detection", zorder=4)
    ax.plot(ROUNDS, ROLLBK_2B, "^--", color=C_RBK, lw=1.8, ms=7, label="Rollback", zorder=4)
    for r, v in zip(ROUNDS, TOTAL_2B):
        ax.annotate(f"{v}s", (r, v), textcoords="offset points",
                    xytext=(0, 8), ha="center", fontsize=9, fontweight="bold", color=C_TOT)
    ax.axhline(MED_TOTAL, color=C_MED, ls="--", lw=1.8, label=f"Trung vị total={MED_TOTAL:.0f}s")
    ax.set_xticks(ROUNDS); ax.set_xticklabels([f"R{r}" for r in ROUNDS])
    ax.set_ylabel("Thời gian (s)"); ax.set_ylim(0, 100)
    ax.set_title("2b — Bad Deploy MTTR breakdown\nper round", fontsize=10.5, fontweight="bold")
    ax.legend(fontsize=8.5); ax.grid(alpha=0.3)

    fig.suptitle("KB2 — So sánh MTTR theo round: Pod Failure vs Bad Deployment", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  FLASK
# ══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>KB2 — Pod Failure & Bad Deployment</title>
  <style>
    *{box-sizing:border-box}
    body{font-family:'Segoe UI',sans-serif;background:#f0f2f5;margin:0;padding:24px;color:#2c3e50}
    h1{margin-bottom:4px}
    .sub{color:#7f8c8d;font-size:.9em;margin-bottom:20px}
    h2{margin:28px 0 8px;font-size:1.15em;border-left:4px solid #3498db;padding-left:10px}
    .card{background:#fff;border-radius:12px;padding:20px;margin:14px 0;box-shadow:0 2px 10px #0001}
    table{border-collapse:collapse;width:100%;font-size:.9em;margin:10px 0}
    th{background:#2c3e50;color:#fff;padding:9px 14px;text-align:left}
    td{padding:8px 14px;border-bottom:1px solid #eee}
    tr:nth-child(even) td{background:#f8f9fa}
    .badge{display:inline-block;padding:3px 10px;border-radius:5px;font-size:.85em;font-weight:bold}
    .ok  {background:#e8f8ef;color:#1a7c3e}
    .warn{background:#fef4e4;color:#9a6700}
    .crit{background:#fde8e8;color:#c0392b}
    .finding{background:#f8f9fa;border-radius:8px;padding:13px 17px;margin:8px 0;
             border-left:4px solid #3498db;line-height:1.75}
    .finding.ok{border-color:#2ecc71}
    .finding.w {border-color:#f39c12}
    .finding.x {border-color:#e74c3c}
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:16px}
    @media(max-width:800px){.grid2{grid-template-columns:1fr}}
    .toc{background:#fff;border-radius:8px;padding:14px 20px;margin-bottom:20px}
    .toc a{color:#2980b9;text-decoration:none;display:block;padding:3px 0;font-size:.9em}
    .toc a:hover{text-decoration:underline}
    .metric-box{text-align:center;background:#fff;border-radius:10px;padding:18px;
                box-shadow:0 2px 8px #0001}
    .metric-val{font-size:2.2em;font-weight:bold;line-height:1.1}
    .metric-lbl{font-size:.82em;color:#7f8c8d;margin-top:4px}
  </style>
</head>
<body>
<h1>KB2 — Sự cố hạ tầng: Pod Failure & Bad Deployment</h1>
<p class="sub">NT531 Nhóm 17 · AKS In-Cluster · 2026-04-17 · 5 rounds · Target: vote pod</p>

<div class="toc card">
  <strong>Mục lục</strong>
  <a href="#summary">📊 Bảng tổng hợp 5 rounds</a>
  <a href="#c1">Chart 1 — MTTR Pod Failure (2a)</a>
  <a href="#c2">Chart 2 — MTTR Bad Deploy Total (2b)</a>
  <a href="#c3">Chart 3 — Stacked: Detection vs Rollback (2b)</a>
  <a href="#c4">Chart 4 — Box Plot phân phối MTTR</a>
  <a href="#c5">Chart 5 — So sánh 2 scenario theo round</a>
  <a href="#analysis">💡 Đánh giá</a>
</div>

<!-- KPI BOXES -->
<div class="card">
  <div class="grid2" style="grid-template-columns:repeat(4,1fr)">
    <div class="metric-box">
      <div class="metric-val" style="color:#3498db">5s</div>
      <div class="metric-lbl">MTTR Pod Failure<br>(trung vị)</div>
    </div>
    <div class="metric-box">
      <div class="metric-val" style="color:#8e44ad">79s</div>
      <div class="metric-lbl">MTTR Bad Deploy<br>(trung vị)</div>
    </div>
    <div class="metric-box">
      <div class="metric-val" style="color:#e67e22">77s</div>
      <div class="metric-lbl">Detection time<br>(trung vị)</div>
    </div>
    <div class="metric-box">
      <div class="metric-val" style="color:#e74c3c">2s</div>
      <div class="metric-lbl">Rollback time<br>(trung vị)</div>
    </div>
  </div>
</div>

<!-- SUMMARY TABLE -->
<h2 id="summary">📊 Bảng tổng hợp 5 rounds</h2>
<div class="card">
  <div class="grid2">
    <div>
      <strong>2a — Pod Failure (kubectl delete --force)</strong>
      <table>
        <tr><th>Round</th><th>Pod bị kill</th><th>MTTR (s)</th><th>Đánh giá</th></tr>
        <tr><td>1</td><td>vote-...-4x4f2</td><td>4s</td><td><span class="badge ok">Tốt</span></td></tr>
        <tr><td>2</td><td>vote-...-hfd9h</td><td>5s</td><td><span class="badge ok">Tốt</span></td></tr>
        <tr><td>3</td><td>vote-...-c2pqk</td><td>4s</td><td><span class="badge ok">Tốt</span></td></tr>
        <tr><td>4</td><td>vote-...-6hdpf</td><td>5s</td><td><span class="badge ok">Tốt</span></td></tr>
        <tr><td>5</td><td>vote-...-nlw7x</td><td>5s</td><td><span class="badge ok">Tốt</span></td></tr>
        <tr style="background:#f0f8ff"><td><strong>Trung vị</strong></td><td>—</td><td><strong>5s</strong></td><td><span class="badge ok">Stable</span></td></tr>
      </table>
    </div>
    <div>
      <strong>2b — Bad Deployment (image tag lỗi → rollback)</strong>
      <table>
        <tr><th>Round</th><th>Detection</th><th>Rollback</th><th>Total MTTR</th></tr>
        <tr><td>1</td><td>77s</td><td>2s</td><td>79s</td></tr>
        <tr><td>2</td><td>77s</td><td>2s</td><td>79s</td></tr>
        <tr><td>3</td><td>78s</td><td>2s</td><td>80s</td></tr>
        <tr><td>4</td><td>77s</td><td>2s</td><td>79s</td></tr>
        <tr><td>5</td><td>77s</td><td>1s</td><td>78s</td></tr>
        <tr style="background:#f0f8ff"><td><strong>Trung vị</strong></td><td><strong>77s</strong></td><td><strong>2s</strong></td><td><strong>79s</strong></td></tr>
      </table>
    </div>
  </div>
</div>

<!-- CHARTS -->
<h2 id="c1">Chart 1 — MTTR Pod Failure (2a): T0 kill → T1 pod Ready</h2>
<div class="card">{{ c1 }}</div>

<h2 id="c2">Chart 2 — MTTR Bad Deployment Total (2b)</h2>
<div class="card">{{ c2 }}</div>

<h2 id="c3">Chart 3 — Stacked: Detection time vs Rollback time (2b)</h2>
<div class="card">{{ c3 }}</div>

<h2 id="c4">Chart 4 — Box Plot phân phối MTTR (5 rounds)</h2>
<div class="card">{{ c4 }}</div>

<h2 id="c5">Chart 5 — So sánh 2 scenario theo round (trend)</h2>
<div class="card">{{ c5 }}</div>

<!-- ANALYSIS -->
<h2 id="analysis">💡 Đánh giá KB2</h2>
<div class="card">

  <h3>2a — Pod Failure: Kubernetes self-healing</h3>
  <div class="finding ok">
    <strong>MTTR cực kỳ ổn định: 4-5s qua 5 rounds</strong><br>
    Kubernetes reschedule pod vote trong vòng 4-5 giây sau khi bị force-delete.
    Trung vị = <strong>5s</strong>, min=4s, max=5s — variance gần như bằng 0.
    Đây là hành vi expected của Deployment với <code>replicas=1</code>:
    controller phát hiện pod missing → tạo pod mới → container start → readiness probe pass.
  </div>
  <div class="finding w">
    <strong>Downtime thực tế trong 4-5s này:</strong> Với 1 replica, trong khoảng T0→T1 service vote
    không available. Nếu có HPA hoặc replicas&gt;1 với PodDisruptionBudget, downtime = 0.
    <span class="badge warn">Cải thiện: tăng replicas=2+ để zero-downtime</span>
  </div>

  <h3>2b — Bad Deployment: Detection + Rollback</h3>
  <div class="finding w">
    <strong>Detection time = 77s (median)</strong> — rất nhất quán qua 5 rounds (77-78s).<br>
    Đây là thời gian từ khi deploy bad image đến khi script phát hiện pod không Ready.
    77s ≈ <code>progressDeadlineSeconds</code> mặc định Kubernetes (600s) bị override + readiness probe
    failureThreshold × periodSeconds (ví dụ: 3 × 10s = 30s + scheduling overhead ≈ 77s).
  </div>
  <div class="finding ok">
    <strong>Rollback time = 1-2s</strong> — cực kỳ nhanh.<br>
    <code>kubectl rollout undo deployment/vote</code> switch về ReplicaSet cũ ngay lập tức.
    Image đã được pull sẵn → không cần pull lại → container start trong &lt;2s.
  </div>
  <div class="finding x">
    <strong>Total MTTR = 78-80s (median 79s)</strong> — bottleneck là detection time (97.5%).<br>
    Rollback chỉ chiếm 1.3-2.5% tổng MTTR. Để giảm MTTR cần giảm detection time:
    <ul style="margin:6px 0">
      <li>Giảm <code>readinessProbe.failureThreshold × periodSeconds</code> (hiện ~77s → target &lt;30s)</li>
      <li>Tích hợp Alertmanager → tự động trigger rollback khi alert fire</li>
      <li>ArgoCD với health check tự động rollback khi deployment degraded</li>
    </ul>
  </div>

  <h3>So sánh MTTR giữa 2 scenario</h3>
  <table>
    <tr><th>Metric</th><th>2a: Pod Failure</th><th>2b: Bad Deploy</th><th>Tỉ lệ</th></tr>
    <tr><td>MTTR median</td><td><strong>5s</strong></td><td><strong>79s</strong></td><td>2b chậm hơn <span class="badge crit">15.8x</span></td></tr>
    <tr><td>MTTR min</td><td>4s</td><td>78s</td><td>—</td></tr>
    <tr><td>MTTR max</td><td>5s</td><td>80s</td><td>—</td></tr>
    <tr><td>Variance</td><td>±1s (20%)</td><td>±1s (1.3%)</td><td>2b ổn định hơn</td></tr>
    <tr><td>Bottleneck</td><td>Container cold start</td><td>Detection time (97.5%)</td><td>—</td></tr>
    <tr><td>Error rate user</td><td>~100% trong 4-5s</td><td>~100% trong 77s</td><td>2b tệ hơn nhiều</td></tr>
    <tr><td>Auto-recovery</td><td>Kubernetes tự động</td><td>Manual rollback script</td><td>—</td></tr>
  </table>

  <div class="finding ok" style="margin-top:16px">
    <strong>Kết luận:</strong> KB2 chứng minh Kubernetes self-healing hoạt động xuất sắc với pod failure (MTTR=5s).
    Điểm yếu chính là <em>change failure detection latency</em> — 77s là quá lâu cho production SLO.
    Rollback khi phát hiện xong thì rất nhanh (1-2s). Ưu tiên cải thiện: reduce readiness probe timeout
    và implement automated rollback trigger qua Alertmanager/ArgoCD.
  </div>
</div>
</body></html>"""

@app.route("/")
def index():
    print("Rendering KB2 dashboard...")
    html = (HTML
            .replace("{{ c1 }}", img(chart_mttr_pod()))
            .replace("{{ c2 }}", img(chart_mttr_deploy()))
            .replace("{{ c3 }}", img(chart_stacked()))
            .replace("{{ c4 }}", img(chart_boxplot()))
            .replace("{{ c5 }}", img(chart_comparison())))
    return html

if __name__ == "__main__":
    print("KB2 Dashboard → http://localhost:5052")
    app.run(host="0.0.0.0", port=5052, debug=False)
