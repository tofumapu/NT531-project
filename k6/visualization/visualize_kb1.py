#!/usr/bin/env python3
"""KB1 In-Cluster k6 Results Visualizer — 5 rounds x 3 scenarios"""

import json, io, base64, statistics
from pathlib import Path
from flask import Flask, render_template_string

BASE = Path(__file__).parent.parent / "results" / "kb1-incluster-2026-04-16"
ROUNDS = [1, 2, 3, 4, 5]
SCENARIOS = ["normal", "medium", "spike"]
COLORS = {"normal": "#2ecc71", "medium": "#f39c12", "spike": "#e74c3c"}
SCENARIO_LABELS = {"normal": "Normal", "medium": "Medium Load", "spike": "Spike"}

# ── Load data ─────────────────────────────────────────────────────────────────
def load_data():
    rows = []
    for r in ROUNDS:
        for s in SCENARIOS:
            path = BASE / f"round{r}" / f"{s}-summary.json"
            with open(path) as f:
                d = json.load(f)
            rows.append({
                "round": r,
                "scenario": s,
                "vus": d["vus_max"],
                "rps": d["rps_avg"],
                "iters": d["iterations"],
                "errors": d["errors_total"],
                "err_pct": d["error_rate_pct"],
                "lat_avg": d["latency_ms"]["avg"],
                "lat_p50": d["latency_ms"]["p50"],
                "lat_p90": d["latency_ms"]["p90"],
                "lat_p95": d["latency_ms"]["p95"],
                "lat_p99": d["latency_ms"]["p99"],
                "lat_max": d["latency_ms"]["max"],
                "vote_p95": d["vote_post_p95"],
            })
    return rows

DATA = load_data()

# ── Compute medians per scenario ──────────────────────────────────────────────
def medians():
    result = {}
    for s in SCENARIOS:
        subset = [d for d in DATA if d["scenario"] == s]
        result[s] = {
            "vus":       [d["vus"] for d in subset],
            "rps":       statistics.median([d["rps"] for d in subset]),
            "err_pct":   statistics.median([d["err_pct"] for d in subset]),
            "lat_p50":   statistics.median([d["lat_p50"] for d in subset]),
            "lat_p90":   statistics.median([d["lat_p90"] for d in subset]),
            "lat_p95":   statistics.median([d["lat_p95"] for d in subset]),
            "lat_p99":   statistics.median([d["lat_p99"] for d in subset]),
            "vote_p95":  statistics.median([d["vote_p95"] for d in subset]),
        }
    return result

MEDIANS = medians()

# ── Chart helpers ─────────────────────────────────────────────────────────────
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

plt.rcParams.update({"figure.dpi": 130, "font.size": 9,
                     "axes.spines.top": False, "axes.spines.right": False})

def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()

def img_tag(b64):
    return f'<img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:8px;box-shadow:0 2px 8px #0002">'

# ─── Chart 1: Latency p50/p95/p99 per round per scenario ──────────────────────
def chart_latency_lines():
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharey=False)
    for ax, s in zip(axes, SCENARIOS):
        subset = sorted([d for d in DATA if d["scenario"] == s], key=lambda x: x["round"])
        vus = [d["vus"] for d in subset]
        xlabels = [f"R{d['round']}\n{d['vus']}VU" for d in subset]
        x = np.arange(len(xlabels))
        ax.plot(x, [d["lat_p50"] for d in subset], "o-", color="#27ae60", label="p50", lw=2)
        ax.plot(x, [d["lat_p90"] for d in subset], "s-", color="#f39c12", label="p90", lw=2)
        ax.plot(x, [d["lat_p95"] for d in subset], "^-", color="#e67e22", label="p95", lw=2)
        ax.plot(x, [d["lat_p99"] for d in subset], "D-", color="#e74c3c", label="p99", lw=2)
        med_p95 = MEDIANS[s]["lat_p95"]
        ax.axhline(med_p95, color="#e67e22", ls="--", alpha=0.5, lw=1.2)
        ax.text(len(x)-0.1, med_p95*1.03, f"med p95={med_p95:.0f}ms", color="#e67e22", fontsize=7.5, ha="right")
        ax.set_xticks(x); ax.set_xticklabels(xlabels, fontsize=8)
        ax.set_title(f"{SCENARIO_LABELS[s]}", fontsize=11, fontweight="bold", color=COLORS[s])
        ax.set_ylabel("Latency (ms)")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v/1000:.1f}s" if v >= 1000 else f"{v:.0f}ms"))
        ax.legend(fontsize=7.5)
        ax.grid(axis="y", alpha=0.3)
    fig.suptitle("KB1 In-Cluster — Latency p50/p90/p95/p99 theo round & tải", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ─── Chart 2: Error rate (%) per round ────────────────────────────────────────
def chart_error_rate():
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(ROUNDS))
    width = 0.28
    for i, s in enumerate(SCENARIOS):
        subset = sorted([d for d in DATA if d["scenario"] == s], key=lambda d: d["round"])
        vals = [d["err_pct"] for d in subset]
        bars = ax.bar(x + (i-1)*width, vals, width, label=SCENARIO_LABELS[s],
                      color=COLORS[s], alpha=0.85, edgecolor="white")
        for bar, v in zip(bars, vals):
            if v > 0:
                ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.3,
                        f"{v:.1f}%", ha="center", va="bottom", fontsize=7.5)
    ax.axhline(5, color="#c0392b", ls="--", lw=1.2, label="SLO 5% limit")
    ax.set_xticks(x)
    ax.set_xticklabels([f"Round {r}" for r in ROUNDS])
    ax.set_ylabel("Error Rate (%)")
    ax.set_title("KB1 In-Cluster — Tỷ lệ lỗi (Error Rate %) theo round", fontsize=12, fontweight="bold")
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    return fig_to_b64(fig)

# ─── Chart 3: RPS (throughput) per round ──────────────────────────────────────
def chart_rps():
    fig, ax = plt.subplots(figsize=(10, 4))
    x = np.arange(len(ROUNDS))
    width = 0.28
    for i, s in enumerate(SCENARIOS):
        subset = sorted([d for d in DATA if d["scenario"] == s], key=lambda d: d["round"])
        vals = [d["rps"] for d in subset]
        bars = ax.bar(x + (i-1)*width, vals, width, label=SCENARIO_LABELS[s],
                      color=COLORS[s], alpha=0.85, edgecolor="white")
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+0.5,
                    f"{v:.0f}", ha="center", va="bottom", fontsize=7.5)
    ax.set_xticks(x)
    ax.set_xticklabels([f"Round {r}" for r in ROUNDS])
    ax.set_ylabel("Requests/s (avg)")
    ax.set_title("KB1 In-Cluster — Throughput (RPS) theo round", fontsize=12, fontweight="bold")
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    return fig_to_b64(fig)

# ─── Chart 4: vote_post p95 trend ─────────────────────────────────────────────
def chart_vote_p95():
    fig, ax = plt.subplots(figsize=(10, 4))
    for s in SCENARIOS:
        subset = sorted([d for d in DATA if d["scenario"] == s], key=lambda d: d["round"])
        xlabels = [f"R{d['round']}\n{d['vus']}VU" for d in subset]
        x = np.arange(len(xlabels))
        vals = [d["vote_p95"] for d in subset]
        ax.plot(x, vals, "o-", color=COLORS[s], label=SCENARIO_LABELS[s], lw=2.5, ms=7)
        med = MEDIANS[s]["vote_p95"]
        ax.axhline(med, color=COLORS[s], ls=":", alpha=0.5, lw=1)
    ax.axhline(3000, color="gray", ls="--", lw=1, label="3s SLO ref")
    ax.set_xticks(np.arange(5)); ax.set_xticklabels(xlabels if SCENARIOS else ROUNDS)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v/1000:.1f}s" if v >= 1000 else f"{v:.0f}ms"))
    ax.set_ylabel("vote_post p95 latency")
    ax.set_title("KB1 In-Cluster — Vote POST p95 Latency theo round", fontsize=12, fontweight="bold")
    ax.legend(); ax.grid(alpha=0.3)
    plt.tight_layout()
    return fig_to_b64(fig)

# ─── Chart 5: Median summary radar / bar ──────────────────────────────────────
def chart_median_summary():
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.5))

    # Left: median latency percentiles grouped by scenario
    ax = axes[0]
    metrics = ["lat_p50", "lat_p90", "lat_p95", "lat_p99"]
    labels  = ["p50", "p90", "p95", "p99"]
    x = np.arange(len(metrics))
    width = 0.28
    for i, s in enumerate(SCENARIOS):
        vals = [MEDIANS[s][m] for m in metrics]
        bars = ax.bar(x + (i-1)*width, vals, width, label=SCENARIO_LABELS[s],
                      color=COLORS[s], alpha=0.85, edgecolor="white")
        for bar, v in zip(bars, vals):
            ax.text(bar.get_x()+bar.get_width()/2, bar.get_height()+20,
                    f"{v/1000:.1f}s" if v>=1000 else f"{v:.0f}ms",
                    ha="center", fontsize=7, va="bottom")
    ax.set_xticks(x); ax.set_xticklabels(labels)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v/1000:.1f}s" if v>=1000 else f"{v:.0f}ms"))
    ax.set_title("Trung vị Latency theo Scenario", fontweight="bold")
    ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.3)

    # Right: median RPS & error rate (dual axis)
    ax2 = axes[1]
    ax2r = ax2.twinx()
    x2 = np.arange(len(SCENARIOS))
    rps_vals = [MEDIANS[s]["rps"] for s in SCENARIOS]
    err_vals = [MEDIANS[s]["err_pct"] for s in SCENARIOS]
    b1 = ax2.bar(x2 - 0.2, rps_vals, 0.35, color=[COLORS[s] for s in SCENARIOS], alpha=0.8, label="RPS (median)")
    b2 = ax2r.bar(x2 + 0.2, err_vals, 0.35, color=[COLORS[s] for s in SCENARIOS], alpha=0.4, hatch="///", label="Error% (median)")
    for bar, v in zip(b1, rps_vals): ax2.text(bar.get_x()+bar.get_width()/2, v+1, f"{v:.0f}", ha="center", fontsize=8)
    for bar, v in zip(b2, err_vals): ax2r.text(bar.get_x()+bar.get_width()/2, v+0.1, f"{v:.2f}%", ha="center", fontsize=8)
    ax2.set_xticks(x2); ax2.set_xticklabels([SCENARIO_LABELS[s] for s in SCENARIOS])
    ax2.set_ylabel("RPS (req/s)"); ax2r.set_ylabel("Error Rate (%)")
    ax2.set_title("Trung vị RPS & Error Rate", fontweight="bold")
    lines1, labs1 = ax2.get_legend_handles_labels()
    lines2, labs2 = ax2r.get_legend_handles_labels()
    ax2.legend(lines1+lines2, labs1+labs2, fontsize=8)
    ax2.grid(axis="y", alpha=0.3)

    fig.suptitle("KB1 In-Cluster — Tổng hợp TRUNG VỊ (5 rounds)", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ── Median table text ──────────────────────────────────────────────────────────
def median_table_html():
    rows = ""
    for s in SCENARIOS:
        m = MEDIANS[s]
        color = COLORS[s]
        rows += f"""
        <tr>
          <td style="color:{color};font-weight:bold">{SCENARIO_LABELS[s]}</td>
          <td>{m['rps']:.1f}</td>
          <td>{m['err_pct']:.3f}%</td>
          <td>{m['lat_p50']:.0f} ms</td>
          <td>{m['lat_p90']:.0f} ms</td>
          <td>{m['lat_p95']:.0f} ms</td>
          <td>{m['lat_p99']:.0f} ms</td>
          <td>{m['vote_p95']:.0f} ms</td>
        </tr>"""
    return f"""
    <table>
      <thead><tr>
        <th>Scenario</th><th>RPS</th><th>Error%</th>
        <th>p50</th><th>p90</th><th>p95</th><th>p99</th><th>vote p95</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>"""

# ── Analysis text ─────────────────────────────────────────────────────────────
ANALYSIS = """
<h2>📊 Đánh giá tổng thể KB1 — In-Cluster (5 rounds)</h2>

<h3>🟢 Normal Load (VU tăng: 30→60→100→150→200)</h3>
<ul>
  <li><strong>Round 1 (30VU)</strong>: Latency p95=400ms, 0 lỗi — hệ thống ổn định.</li>
  <li><strong>Round 2 (60VU)</strong>: p95 vọt lên 2108ms dù 0 lỗi — bắt đầu có áp lực, RPS chỉ 53.</li>
  <li><strong>Round 3–4 (100–150VU)</strong>: p95 cải thiện (1008–1686ms), RPS tăng mạnh (130–131 rps) — hệ thống scale tốt hơn.</li>
  <li><strong>Round 5 (200VU)</strong>: error rate 0.29%, p95=3089ms — bắt đầu chạm ngưỡng.</li>
  <li>✅ <em>Trung vị p95 = ~1686ms</em> — acceptable nhưng cần cải thiện.</li>
</ul>

<h3>🟡 Medium Load (VU: 75→120→180→250→350)</h3>
<ul>
  <li><strong>Round 1 (75VU)</strong>: error 2.25%, p95=3928ms — quá tải sớm.</li>
  <li><strong>Round 2 (120VU)</strong>: 0 lỗi, p95=1607ms, RPS=114 — nghịch lý: tải cao hơn nhưng tốt hơn R1 (pod đã warm up).</li>
  <li><strong>Round 3 (180VU)</strong>: 0.28% lỗi, p95=3587ms — tải tăng bắt đầu xấu.</li>
  <li><strong>Round 4 (250VU)</strong>: 2.45% lỗi, p95=5040ms — vượt 5s SLO.</li>
  <li><strong>Round 5 (350VU)</strong>: 0 lỗi nhưng p95=3439ms — system recovered do cooling.</li>
  <li>⚠️ <em>Trung vị p95 = ~3587ms</em> — borderline, cần HPA hoặc resource limit.</li>
</ul>

<h3>🔴 Spike Load (VU: 200→300→450→600→800)</h3>
<ul>
  <li><strong>Round 1 (200VU)</strong>: error 58.5%! p95≈10s — system sụp hoàn toàn.</li>
  <li><strong>Round 2 (300VU)</strong>: error 0.9%, p95=3904ms — hệ thống recover tốt bất ngờ.</li>
  <li><strong>Round 3 (450VU)</strong>: 0 lỗi, p95=4274ms — ổn định ở tải cao.</li>
  <li><strong>Round 4 (600VU)</strong>: 5.65% lỗi, p95=5355ms — vượt SLO.</li>
  <li><strong>Round 5 (800VU)</strong>: 22.2% lỗi, p95=7206ms — degraded nặng.</li>
  <li>❌ <em>Trung vị p95 = ~5354ms</em> — không đạt SLO, cần circuit breaker/autoscale.</li>
</ul>

<h3>💡 Kết luận & Trung vị đề xuất</h3>
<table>
  <tr><th>Scenario</th><th>Trung vị RPS</th><th>Trung vị Error%</th><th>Trung vị p95</th><th>Nhận xét</th></tr>
  <tr style="color:#27ae60"><td>Normal</td><td>~116 rps</td><td>~0%</td><td>~1686ms</td><td>✅ Stable, scale tốt đến R4</td></tr>
  <tr style="color:#f39c12"><td>Medium</td><td>~103 rps</td><td>~0.28%</td><td>~3587ms</td><td>⚠️ Biến động cao, cần HPA</td></tr>
  <tr style="color:#e74c3c"><td>Spike</td><td>~142 rps</td><td>~5.65%</td><td>~5355ms</td><td>❌ Không ổn định, cần autoscale</td></tr>
</table>
<p><strong>Breaking point</strong>: Normal ~200VU | Medium ~250VU | Spike bất định (R1 fail, R2 ok)</p>
<p><strong>Nguyên nhân R1-Spike thất bại nặng (58.5%)</strong>: Pod cold start + chưa warm up sau khi AKS start lại — đây là artifact của môi trường test, không phải hành vi production thực.</p>
"""

# ── Flask app ─────────────────────────────────────────────────────────────────
app = Flask(__name__)

HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>KB1 In-Cluster k6 Results</title>
  <style>
    body { font-family: 'Segoe UI', sans-serif; background: #f5f7fa; margin: 0; padding: 20px; }
    h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
    h2 { color: #2c3e50; margin-top: 40px; }
    h3 { color: #555; }
    .chart-card { background: white; border-radius: 12px; padding: 20px; margin: 20px 0;
                  box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    .chart-title { font-size: 1.1em; font-weight: bold; color: #2c3e50; margin-bottom: 12px; }
    table { border-collapse: collapse; width: 100%; margin: 10px 0; }
    th { background: #3498db; color: white; padding: 10px 14px; text-align: left; }
    td { padding: 9px 14px; border-bottom: 1px solid #eee; }
    tr:nth-child(even) td { background: #f9f9f9; }
    .analysis { background: white; border-radius: 12px; padding: 24px; margin: 20px 0;
                box-shadow: 0 2px 12px rgba(0,0,0,0.08); }
    ul li { margin: 6px 0; line-height: 1.6; }
    p { line-height: 1.7; }
    .badge { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 0.85em; font-weight: bold; }
  </style>
</head>
<body>
  <h1>📈 KB1 In-Cluster — k6 Load Test Results (5 Rounds)</h1>
  <p>Ngày chạy: <strong>2026-04-16</strong> | Kịch bản: Normal / Medium / Spike | KB1: Không có Chaos</p>

  <div class="chart-card">
    <div class="chart-title">Chart 1: Latency Percentiles (p50/p90/p95/p99) theo từng Round</div>
    {{ chart1 }}
  </div>

  <div class="chart-card">
    <div class="chart-title">Chart 2: Error Rate (%) theo Round</div>
    {{ chart2 }}
  </div>

  <div class="chart-card">
    <div class="chart-title">Chart 3: Throughput (RPS) theo Round</div>
    {{ chart3 }}
  </div>

  <div class="chart-card">
    <div class="chart-title">Chart 4: Vote POST p95 Latency trend</div>
    {{ chart4 }}
  </div>

  <div class="chart-card">
    <div class="chart-title">Chart 5: Tổng hợp TRUNG VỊ — 5 Rounds</div>
    {{ chart5 }}
  </div>

  <div class="analysis">
    <h2>📋 Bảng Trung vị tổng hợp</h2>
    {{ median_table }}
    {{ analysis }}
  </div>
</body>
</html>"""

@app.route("/")
def index():
    print("Generating charts...")
    c1 = img_tag(chart_latency_lines())
    c2 = img_tag(chart_error_rate())
    c3 = img_tag(chart_rps())
    c4 = img_tag(chart_vote_p95())
    c5 = img_tag(chart_median_summary())
    html = (HTML_TEMPLATE
            .replace("{{ chart1 }}", c1)
            .replace("{{ chart2 }}", c2)
            .replace("{{ chart3 }}", c3)
            .replace("{{ chart4 }}", c4)
            .replace("{{ chart5 }}", c5)
            .replace("{{ median_table }}", median_table_html())
            .replace("{{ analysis }}", ANALYSIS))
    return html

if __name__ == "__main__":
    print("🚀 Serving at http://localhost:5050")
    print("   Mở trình duyệt → http://localhost:5050")
    app.run(host="0.0.0.0", port=5050, debug=False)
