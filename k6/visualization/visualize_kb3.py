#!/usr/bin/env python3
"""KB3 Network Chaos — 5 gần nhất mỗi experiment | NT531 Nhóm 17"""

import io, base64, statistics
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import matplotlib.ticker as mticker
import numpy as np
from flask import Flask

plt.rcParams.update({"figure.dpi": 130, "font.size": 9,
                     "axes.spines.top": False, "axes.spines.right": False})

# ══════════════════════════════════════════════════════════════════════════════
#  DATA — từ summary.md thực tế (5 run gần nhất mỗi experiment)
#  Exp A: vote→redis | chaos: 50% loss + 800ms ± 300ms delay (300s) | 60 VUs
#  Exp B: worker→db  | chaos: 60% loss + 1.5s ± 500ms delay (300s)  | 60 VUs
# ══════════════════════════════════════════════════════════════════════════════

# ── Prometheus snapshot data (3 phases: baseline-post, chaos-end, recovery-end)
# Các giá trị từ cột tương ứng trong summary.md
EXP_A_RUNS = [
    {"run": 1, "ts": "054355",
     "prom": {"base_vote_p95": 183, "chaos_vote_p95": 206,  "rec_vote_p95": 197,
              "base_vote_rps": 58.5, "chaos_vote_rps": 54.3,  "rec_vote_rps": 52.2,
              "base_tcp": 0.25, "chaos_tcp": 0.26, "rec_tcp": 0.16,
              "base_worker_p95": 69, "chaos_worker_p95": 66, "rec_worker_p95": 68,
              "base_worker_p99": 191, "chaos_worker_p99": 346, "rec_worker_p99": 123,
              "base_cpu": 99, "chaos_cpu": 96, "rec_cpu": 93},
     "k6": {"base_rps": 54.65, "chaos_rps": 41.57, "rec_rps": 54.84,
            "base_p95_ms": 1730, "chaos_p95_ms": 3070, "rec_p95_ms": 1750,
            "base_err": 0.0, "chaos_err": 0.0, "rec_err": 0.0,
            "base_reqs": 6566, "chaos_reqs": 12474, "rec_reqs": 4946}},
    {"run": 2, "ts": "060332",
     "prom": {"base_vote_p95": 227, "chaos_vote_p95": 220,  "rec_vote_p95": 205,
              "base_vote_rps": 47.0, "chaos_vote_rps": 50.6,  "rec_vote_rps": 56.2,
              "base_tcp": 0.33, "chaos_tcp": 0.28, "rec_tcp": 0.34,
              "base_worker_p95": 83, "chaos_worker_p95": 95, "rec_worker_p95": 63,
              "base_worker_p99": 271, "chaos_worker_p99": 212, "rec_worker_p99": 211,
              "base_cpu": 95, "chaos_cpu": 99, "rec_cpu": 97},
     "k6": {"base_rps": 50.62, "chaos_rps": 40.95, "rec_rps": 54.80,
            "base_p95_ms": 1760, "chaos_p95_ms": 3060, "rec_p95_ms": 1570,
            "base_err": 0.0, "chaos_err": 0.0, "rec_err": 0.0,
            "base_reqs": 6090, "chaos_reqs": 12296, "rec_reqs": 4937}},
    {"run": 3, "ts": "062306",
     "prom": {"base_vote_p95": 220, "chaos_vote_p95": 229,  "rec_vote_p95": 213,
              "base_vote_rps": 53.2, "chaos_vote_rps": 32.7,  "rec_vote_rps": 44.7,
              "base_tcp": 0.21, "chaos_tcp": 0.50, "rec_tcp": 0.32,
              "base_worker_p95": 72, "chaos_worker_p95": 115, "rec_worker_p95": 55,
              "base_worker_p99": 225, "chaos_worker_p99": 247, "rec_worker_p99": 203,
              "base_cpu": 98, "chaos_cpu": 94, "rec_cpu": 100},
     "k6": {"base_rps": 54.63, "chaos_rps": 41.99, "rec_rps": 51.53,
            "base_p95_ms": 1730, "chaos_p95_ms": 2880, "rec_p95_ms": 1720,
            "base_err": 0.0, "chaos_err": 0.0, "rec_err": 0.0,
            "base_reqs": 6563, "chaos_reqs": 12610, "rec_reqs": 4642}},
    {"run": 4, "ts": "064243",
     "prom": {"base_vote_p95": 202, "chaos_vote_p95": 637,  "rec_vote_p95": 208,
              "base_vote_rps": 47.3, "chaos_vote_rps": 19.1,  "rec_vote_rps": 53.5,
              "base_tcp": 0.30, "chaos_tcp": 0.54, "rec_tcp": 0.20,
              "base_worker_p95": 74, "chaos_worker_p95": 200, "rec_worker_p95": 77,
              "base_worker_p99": 179, "chaos_worker_p99": 686, "rec_worker_p99": 178,
              "base_cpu": 99, "chaos_cpu": 99, "rec_cpu": 94},
     "k6": {"base_rps": 53.13, "chaos_rps": 40.46, "rec_rps": 53.24,
            "base_p95_ms": 1820, "chaos_p95_ms": 2730, "rec_p95_ms": 1630,
            "base_err": 0.0, "chaos_err": 0.0, "rec_err": 0.0,
            "base_reqs": 6385, "chaos_reqs": 12144, "rec_reqs": 4803}},
    {"run": 5, "ts": "070222",
     "prom": {"base_vote_p95": 224, "chaos_vote_p95": 637,  "rec_vote_p95": 207,
              "base_vote_rps": 53.1, "chaos_vote_rps": 16.5,  "rec_vote_rps": 45.0,
              "base_tcp": 0.32, "chaos_tcp": 0.62, "rec_tcp": 0.25,
              "base_worker_p95": 88, "chaos_worker_p95": 236, "rec_worker_p95": 61,
              "base_worker_p99": 191, "chaos_worker_p99": 748, "rec_worker_p99": 257,
              "base_cpu": 98, "chaos_cpu": 100, "rec_cpu": 100},
     "k6": {"base_rps": 52.33, "chaos_rps": 40.23, "rec_rps": 51.08,
            "base_p95_ms": 1780, "chaos_p95_ms": 2920, "rec_p95_ms": 1840,
            "base_err": 0.0, "chaos_err": 0.09, "rec_err": 0.0,
            "base_reqs": 6287, "chaos_reqs": 12095, "rec_reqs": 4613}},
]

EXP_B_RUNS = [
    {"run": 1, "ts": "055345",
     "prom": {"base_vote_p95": 189, "chaos_vote_p95": 227,  "rec_vote_p95": 188,
              "base_vote_rps": 49.3, "chaos_vote_rps": 48.3,  "rec_vote_rps": 56.9,
              "base_tcp": 0.37, "chaos_tcp": 0.25, "rec_tcp": 0.22,
              "base_worker_p95": 88, "chaos_worker_p95": 58,  "rec_worker_p95": 77,
              "base_worker_p99": 262, "chaos_worker_p99": 228, "rec_worker_p99": 219,
              "base_cpu": 91, "chaos_cpu": 98, "rec_cpu": 96},
     "k6": {"base_rps": 53.02, "chaos_rps": 39.27, "rec_rps": 54.35,
            "base_p95_ms": 1750, "chaos_p95_ms": 3990, "rec_p95_ms": 1760,
            "base_err": 0.0, "chaos_err": 0.0, "rec_err": 0.0,
            "base_reqs": 6369, "chaos_reqs": 11792, "rec_reqs": 4899}},
    {"run": 2, "ts": "061320",
     "prom": {"base_vote_p95": 229, "chaos_vote_p95": 215,  "rec_vote_p95": 198,
              "base_vote_rps": 45.8, "chaos_vote_rps": 38.3,  "rec_vote_rps": 60.0,
              "base_tcp": 0.31, "chaos_tcp": 0.34, "rec_tcp": 0.22,
              "base_worker_p95": 65, "chaos_worker_p95": 86,  "rec_worker_p95": 77,
              "base_worker_p99": 172, "chaos_worker_p99": 330, "rec_worker_p99": 308,
              "base_cpu": 96, "chaos_cpu": 100, "rec_cpu": 97},
     "k6": {"base_rps": 52.07, "chaos_rps": 40.75, "rec_rps": 45.11,
            "base_p95_ms": 1830, "chaos_p95_ms": 3040, "rec_p95_ms": 2050,
            "base_err": 0.0, "chaos_err": 0.0, "rec_err": 0.34,
            "base_reqs": 6259, "chaos_reqs": 12230, "rec_reqs": 4072}},
    {"run": 3, "ts": "063255",
     "prom": {"base_vote_p95": 200, "chaos_vote_p95": 454,  "rec_vote_p95": 208,
              "base_vote_rps": 57.1, "chaos_vote_rps": 22.2,  "rec_vote_rps": 49.3,
              "base_tcp": 0.50, "chaos_tcp": 0.34, "rec_tcp": 0.28,
              "base_worker_p95": 74, "chaos_worker_p95": 186, "rec_worker_p95": 90,
              "base_worker_p99": 191, "chaos_worker_p99": 433, "rec_worker_p99": 225,
              "base_cpu": 99, "chaos_cpu": 96, "rec_cpu": 99},
     "k6": {"base_rps": 53.06, "chaos_rps": 40.81, "rec_rps": 53.86,
            "base_p95_ms": 1780, "chaos_p95_ms": 3350, "rec_p95_ms": 1770,
            "base_err": 0.0, "chaos_err": 0.0, "rec_err": 0.0,
            "base_reqs": 6374, "chaos_reqs": 12267, "rec_reqs": 4851}},
    {"run": 4, "ts": "065231",
     "prom": {"base_vote_p95": 183, "chaos_vote_p95": 702,  "rec_vote_p95": 217,
              "base_vote_rps": 53.6, "chaos_vote_rps": 12.2,  "rec_vote_rps": 51.6,
              "base_tcp": 0.22, "chaos_tcp": 0.42, "rec_tcp": 0.18,
              "base_worker_p95": 62, "chaos_worker_p95": 226, "rec_worker_p95": 99,
              "base_worker_p99": 128, "chaos_worker_p99": 674, "rec_worker_p99": 358,
              "base_cpu": 96, "chaos_cpu": 100, "rec_cpu": 99},
     "k6": {"base_rps": 52.96, "chaos_rps": 41.42, "rec_rps": 51.49,
            "base_p95_ms": 1780, "chaos_p95_ms": 2950, "rec_p95_ms": 1770,
            "base_err": 0.0, "chaos_err": 0.0, "rec_err": 0.0,
            "base_reqs": 6359, "chaos_reqs": 12437, "rec_reqs": 4650}},
    {"run": 5, "ts": "071212",
     "prom": {"base_vote_p95": 205, "chaos_vote_p95": 738,  "rec_vote_p95": 235,
              "base_vote_rps": 43.1, "chaos_vote_rps": 16.0,  "rec_vote_rps": 44.4,
              "base_tcp": 0.34, "chaos_tcp": 0.73, "rec_tcp": 0.30,
              "base_worker_p95": 75, "chaos_worker_p95": 254, "rec_worker_p95": 115,
              "base_worker_p99": 193, "chaos_worker_p99": 816, "rec_worker_p99": 599,
              "base_cpu": 99, "chaos_cpu": 100, "rec_cpu": 100},
     "k6": {"base_rps": 53.88, "chaos_rps": 42.99, "rec_rps": 46.65,
            "base_p95_ms": 1880, "chaos_p95_ms": 2610, "rec_p95_ms": 1780,
            "base_err": 0.0, "chaos_err": 0.03, "rec_err": 0.0,
            "base_reqs": 6481, "chaos_reqs": 12996, "rec_reqs": 4294}},
]

# ── Medians từ AGGREGATE.md ──────────────────────────────────────────────────
# Exp A chaos-end medians: vote_p95=229, vote_p99=494, vote_rps=32.7,
#   worker_p99=346, tcp=0.50, cpu=99%; baseline: vote_p95=211, rps=51.8
# Exp B chaos-end medians: vote_p95=454, vote_p99=950, vote_rps=22.2,
#   worker_p99=433, tcp=0.34, cpu=100%; baseline: vote_p95=201, rps=49.8

MEDIANS = {
    "A": {
        "base_vote_p95": 211, "chaos_vote_p95": 229, "rec_vote_p95": 206,
        "base_vote_rps":  51.8, "chaos_vote_rps": 32.7, "rec_vote_rps": 50.3,
        "base_tcp": 0.28, "chaos_tcp": 0.50, "rec_tcp": 0.25,
        "base_worker_p99": 211, "chaos_worker_p99": 346, "rec_worker_p99": 194,
        "base_worker_tp": 6.61, "chaos_worker_tp": 5.76, "rec_worker_tp": 6.76,
        "base_cpu": 98, "chaos_cpu": 99, "rec_cpu": 97,
    },
    "B": {
        "base_vote_p95": 201, "chaos_vote_p95": 454, "rec_vote_p95": 209,
        "base_vote_rps":  49.8, "chaos_vote_rps": 22.2, "rec_vote_rps": 52.5,
        "base_tcp": 0.35, "chaos_tcp": 0.34, "rec_tcp": 0.24,
        "base_worker_p99": 189, "chaos_worker_p99": 433, "rec_worker_p99": 342,
        "base_worker_tp": 6.63, "chaos_worker_tp": 4.82, "rec_worker_tp": 6.53,
        "base_cpu": 96, "chaos_cpu": 100, "rec_cpu": 98,
    }
}

C_BASE   = "#2ecc71"   # green — baseline
C_CHAOS  = "#e74c3c"   # red — during chaos
C_REC    = "#3498db"   # blue — recovery
C_MED    = "#9b59b6"   # purple — median line

# ══════════════════════════════════════════════════════════════════════════════
def fig_to_b64(fig):
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight")
    plt.close(fig)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()

def img(b64):
    return f'<img src="data:image/png;base64,{b64}" style="max-width:100%;border-radius:8px;box-shadow:0 2px 10px #0002;margin:6px 0">'

def ms_fmt(v, _): return f"{v/1000:.1f}s" if v >= 1000 else f"{v:.0f}ms"

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 1: TCP Retransmission — 5 runs x 3 phases (line per run + median bar)
# ══════════════════════════════════════════════════════════════════════════════
def chart_tcp():
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, runs, exp, med in [
        (axes[0], EXP_A_RUNS, "A: vote→redis\n(50% loss + 800ms delay)", MEDIANS["A"]),
        (axes[1], EXP_B_RUNS, "B: worker→db\n(60% loss + 1.5s delay)",   MEDIANS["B"]),
    ]:
        phases = ["Baseline", "Chaos-end", "Recovery"]
        for r in runs:
            p = r["prom"]
            vals = [p["base_tcp"], p["chaos_tcp"], p["rec_tcp"]]
            ax.plot(phases, vals, "o-", alpha=0.45, lw=1.5, ms=5, label=f"Run {r['run']}")
        # Median line
        mv = [med["base_tcp"], med["chaos_tcp"], med["rec_tcp"]]
        ax.plot(phases, mv, "D--", color=C_MED, lw=2.8, ms=9, label="MEDIAN", zorder=10)
        for i, (ph, v) in enumerate(zip(phases, mv)):
            ax.annotate(f"{v:.2f}%", (i, v), textcoords="offset points",
                        xytext=(0, 10), ha="center", fontsize=9, color=C_MED, fontweight="bold")
        ax.axvspan(0.5, 1.5, alpha=0.07, color=C_CHAOS)
        ax.set_ylabel("TCP Retransmission Rate (%)")
        ax.set_title(f"Exp {exp}", fontsize=10.5, fontweight="bold")
        ax.legend(fontsize=7.5, loc="upper left")
        ax.grid(alpha=0.3)
    fig.suptitle("TCP Retransmission Rate — 5 runs mỗi experiment (Prometheus)", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 2: Throughput (Vote RPS — Prometheus) 5 runs + median
# ══════════════════════════════════════════════════════════════════════════════
def chart_rps():
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, runs, exp, med in [
        (axes[0], EXP_A_RUNS, "A: vote→redis", MEDIANS["A"]),
        (axes[1], EXP_B_RUNS, "B: worker→db",  MEDIANS["B"]),
    ]:
        phases = ["Baseline", "Chaos-end", "Recovery"]
        for r in runs:
            p = r["prom"]
            vals = [p["base_vote_rps"], p["chaos_vote_rps"], p["rec_vote_rps"]]
            ax.plot(phases, vals, "o-", alpha=0.4, lw=1.5, ms=5, label=f"Run {r['run']}")
        mv = [med["base_vote_rps"], med["chaos_vote_rps"], med["rec_vote_rps"]]
        ax.plot(phases, mv, "D--", color=C_MED, lw=2.8, ms=9, label="MEDIAN", zorder=10)
        for i, v in enumerate(mv):
            ax.annotate(f"{v:.1f}", (i, v), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=9, color=C_MED, fontweight="bold")
        ax.axvspan(0.5, 1.5, alpha=0.07, color=C_CHAOS)
        ax.set_ylabel("Vote RPS (Prometheus rate1m)")
        ax.set_title(f"Exp {exp}\nThroughput (Vote RPS)", fontsize=10.5, fontweight="bold")
        ax.legend(fontsize=7.5, loc="lower right")
        ax.grid(alpha=0.3)
    fig.suptitle("Throughput — Vote RPS trước/trong/sau Chaos (5 runs)", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 3: Vote p95 Latency — Prometheus 5 runs + median
# ══════════════════════════════════════════════════════════════════════════════
def chart_vote_p95():
    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, runs, exp, med in [
        (axes[0], EXP_A_RUNS, "A: vote→redis", MEDIANS["A"]),
        (axes[1], EXP_B_RUNS, "B: worker→db",  MEDIANS["B"]),
    ]:
        phases = ["Baseline", "Chaos-end", "Recovery"]
        for r in runs:
            p = r["prom"]
            vals = [p["base_vote_p95"], p["chaos_vote_p95"], p["rec_vote_p95"]]
            ax.plot(phases, vals, "o-", alpha=0.4, lw=1.5, ms=5, label=f"Run {r['run']}")
        mv = [med["base_vote_p95"], med["chaos_vote_p95"], med["rec_vote_p95"]]
        ax.plot(phases, mv, "D--", color=C_MED, lw=2.8, ms=9, label="MEDIAN", zorder=10)
        for i, v in enumerate(mv):
            ax.annotate(f"{v}ms", (i, v), textcoords="offset points",
                        xytext=(0, 8), ha="center", fontsize=9, color=C_MED, fontweight="bold")
        ax.axvspan(0.5, 1.5, alpha=0.07, color=C_CHAOS)
        ax.set_ylabel("Vote p95 Latency (ms)")
        ax.set_title(f"Exp {exp}\nVote p95 Latency (Prometheus)", fontsize=10.5, fontweight="bold")
        ax.legend(fontsize=7.5)
        ax.grid(alpha=0.3)
    fig.suptitle("Vote p95 Latency — Prometheus 5 runs + Trung vị", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 4: k6 client p95 + Error Rate — 3 phases x 5 runs
# ══════════════════════════════════════════════════════════════════════════════
def chart_k6_latency_error():
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    for col, (runs, exp) in enumerate([(EXP_A_RUNS, "A: vote→redis"), (EXP_B_RUNS, "B: worker→db")]):
        # Top: k6 p95 per run per phase (grouped bar)
        ax = axes[0][col]
        x = np.arange(5)
        w = 0.28
        base_vals  = [r["k6"]["base_p95_ms"]  for r in runs]
        chaos_vals = [r["k6"]["chaos_p95_ms"] for r in runs]
        rec_vals   = [r["k6"]["rec_p95_ms"]   for r in runs]
        b1 = ax.bar(x-w, base_vals,  w, color=C_BASE,  alpha=0.82, label="Baseline")
        b2 = ax.bar(x,   chaos_vals, w, color=C_CHAOS, alpha=0.82, label="During chaos")
        b3 = ax.bar(x+w, rec_vals,   w, color=C_REC,   alpha=0.82, label="Recovery")
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(ms_fmt))
        ax.set_xticks(x); ax.set_xticklabels([f"R{r['run']}" for r in runs])
        ax.set_ylabel("k6 p95 Latency")
        ax.set_title(f"Exp {exp} — k6 p95 (client-side)", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.3)
        # Median lines
        med_b = statistics.median(base_vals);  med_c = statistics.median(chaos_vals)
        ax.axhline(med_c, color=C_MED, ls="--", lw=1.5, label=f"med chaos={med_c:.0f}ms")
        ax.text(4.4, med_c*1.02, f"med={med_c:.0f}ms", color=C_MED, fontsize=7.5)

        # Bottom: k6 error rate per run per phase
        ax = axes[1][col]
        base_err  = [r["k6"]["base_err"]  for r in runs]
        chaos_err = [r["k6"]["chaos_err"] for r in runs]
        rec_err   = [r["k6"]["rec_err"]   for r in runs]
        b1 = ax.bar(x-w, base_err,  w, color=C_BASE,  alpha=0.82, label="Baseline")
        b2 = ax.bar(x,   chaos_err, w, color=C_CHAOS, alpha=0.82, label="During chaos")
        b3 = ax.bar(x+w, rec_err,   w, color=C_REC,   alpha=0.82, label="Recovery")
        for bars, vals in [(b1,base_err),(b2,chaos_err),(b3,rec_err)]:
            for bar, v in zip(bars, vals):
                if v > 0:
                    ax.text(bar.get_x()+bar.get_width()/2, v+0.005,
                            f"{v:.2f}%", ha="center", fontsize=7.5, color="red", fontweight="bold")
        ax.axhline(1.0, color="red", ls="--", lw=1, label="1% SLO")
        ax.set_xticks(x); ax.set_xticklabels([f"R{r['run']}" for r in runs])
        ax.set_ylabel("k6 Error Rate (%)"); ax.set_ylim(0, max(max(chaos_err)+0.1, 0.5))
        ax.set_title(f"Exp {exp} — Error Rate (Timeout / 5xx)", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.3)

    fig.suptitle("k6 Client-side: p95 Latency & Error Rate — 5 runs", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 5: Worker p95 + TCP Retrans per run (showing spread)
# ══════════════════════════════════════════════════════════════════════════════
def chart_worker_tcp():
    fig, axes = plt.subplots(2, 2, figsize=(14, 9))

    for col, (runs, exp, med) in enumerate([
        (EXP_A_RUNS, "A: vote→redis", MEDIANS["A"]),
        (EXP_B_RUNS, "B: worker→db",  MEDIANS["B"]),
    ]):
        x = np.arange(5); w = 0.28

        # Worker p95 per run
        ax = axes[0][col]
        base_w  = [r["prom"]["base_worker_p95"]  for r in runs]
        chaos_w = [r["prom"]["chaos_worker_p95"] for r in runs]
        rec_w   = [r["prom"]["rec_worker_p95"]   for r in runs]
        ax.bar(x-w, base_w,  w, color=C_BASE,  alpha=0.82, label="Baseline")
        ax.bar(x,   chaos_w, w, color=C_CHAOS, alpha=0.82, label="During chaos")
        ax.bar(x+w, rec_w,   w, color=C_REC,   alpha=0.82, label="Recovery")
        med_c = statistics.median(chaos_w)
        ax.axhline(med_c, color=C_MED, ls="--", lw=1.8)
        ax.text(4.4, med_c*1.03, f"med={med_c:.0f}ms", color=C_MED, fontsize=7.5)
        ax.set_xticks(x); ax.set_xticklabels([f"R{r['run']}" for r in runs])
        ax.set_ylabel("Worker p95 (ms)")
        ax.set_title(f"Exp {exp}\nWorker p95 Latency (Prometheus)", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.3)

        # TCP Retrans per run
        ax = axes[1][col]
        base_t  = [r["prom"]["base_tcp"]  for r in runs]
        chaos_t = [r["prom"]["chaos_tcp"] for r in runs]
        rec_t   = [r["prom"]["rec_tcp"]   for r in runs]
        ax.bar(x-w, base_t,  w, color=C_BASE,  alpha=0.82, label="Baseline")
        ax.bar(x,   chaos_t, w, color=C_CHAOS, alpha=0.82, label="During chaos")
        ax.bar(x+w, rec_t,   w, color=C_REC,   alpha=0.82, label="Recovery")
        for bars, vals in [(ax.containers[0], base_t), (ax.containers[1], chaos_t), (ax.containers[2], rec_t)]:
            for bar, v in zip(bars, vals):
                ax.text(bar.get_x()+bar.get_width()/2, v+0.005, f"{v:.2f}%",
                        ha="center", fontsize=7, va="bottom")
        med_ct = statistics.median(chaos_t)
        ax.axhline(med_ct, color=C_MED, ls="--", lw=1.8)
        ax.text(4.4, med_ct*1.05, f"med={med_ct:.2f}%", color=C_MED, fontsize=7.5)
        ax.set_xticks(x); ax.set_xticklabels([f"R{r['run']}" for r in runs])
        ax.set_ylabel("TCP Retransmission (%)")
        ax.set_title(f"Exp {exp}\nTCP Retransmission Rate", fontsize=10, fontweight="bold")
        ax.legend(fontsize=8); ax.grid(axis="y", alpha=0.3)

    fig.suptitle("Worker Impact & TCP Retransmission — 5 runs per experiment", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 6: Median summary — before/during/after bar comparison
# ══════════════════════════════════════════════════════════════════════════════
def chart_median_bars():
    metrics = [
        ("Vote p95 (Prom, ms)", "base_vote_p95", "chaos_vote_p95", "rec_vote_p95"),
        ("Vote RPS (Prom)",     "base_vote_rps", "chaos_vote_rps", "rec_vote_rps"),
        ("Worker p99 (ms)",     "base_worker_p99","chaos_worker_p99","rec_worker_p99"),
        ("TCP Retrans (%×100)", None, None, None),  # special
        ("CPU %",               "base_cpu",      "chaos_cpu",      "rec_cpu"),
    ]

    fig, axes = plt.subplots(1, 2, figsize=(14, 5))
    for ax, (exp_key, exp_label) in zip(axes, [("A","Exp A: vote→redis\n(50% loss + 800ms)"),
                                                 ("B","Exp B: worker→db\n(60% loss + 1.5s)")]):
        med = MEDIANS[exp_key]
        rows = [
            ("Vote p95 Prom (ms)",  med["base_vote_p95"],  med["chaos_vote_p95"],  med["rec_vote_p95"]),
            ("Vote RPS",            med["base_vote_rps"],  med["chaos_vote_rps"],  med["rec_vote_rps"]),
            ("Worker p99 (ms)",     med["base_worker_p99"],med["chaos_worker_p99"],med["rec_worker_p99"]),
            ("TCP Retrans (×100%)", med["base_tcp"]*100,   med["chaos_tcp"]*100,   med["rec_tcp"]*100),
            ("Worker TP (/s)",      med["base_worker_tp"], med["chaos_worker_tp"], med["rec_worker_tp"]),
        ]
        y = np.arange(len(rows))
        w = 0.25
        labels = [r[0] for r in rows]
        base_v  = [r[1] for r in rows]
        chaos_v = [r[2] for r in rows]
        rec_v   = [r[3] for r in rows]
        ax.barh(y+w,   base_v,  w, color=C_BASE,  alpha=0.85, label="Baseline")
        ax.barh(y,     chaos_v, w, color=C_CHAOS, alpha=0.85, label="Chaos-end")
        ax.barh(y-w,   rec_v,   w, color=C_REC,   alpha=0.85, label="Recovery")
        ax.set_yticks(y); ax.set_yticklabels(labels)
        ax.set_title(f"Trung vị tổng hợp\n{exp_label}", fontsize=10.5, fontweight="bold")
        ax.legend(fontsize=8); ax.grid(axis="x", alpha=0.3)
        ax.axvline(0, color="black", lw=0.5)

    fig.suptitle("TRUNG VI (Median) — Tất cả metrics, 5 runs mỗi experiment", fontsize=13, fontweight="bold")
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHART 7: Per-run Vote p95 chaos-end scatter — biến động rõ
# ══════════════════════════════════════════════════════════════════════════════
def chart_scatter_chaos():
    fig, ax = plt.subplots(figsize=(11, 4.5))
    xa = np.arange(1, 6)
    xb = np.arange(1, 6) + 0.15

    ca_p95 = [r["prom"]["chaos_vote_p95"] for r in EXP_A_RUNS]
    cb_p95 = [r["prom"]["chaos_vote_p95"] for r in EXP_B_RUNS]

    ax.scatter(xa, ca_p95, color="#e74c3c", s=100, zorder=5, label="Exp A chaos vote p95")
    ax.scatter(xb, cb_p95, color="#e67e22", s=100, marker="s", zorder=5, label="Exp B chaos vote p95")
    for x, v in zip(xa, ca_p95):
        ax.annotate(f"{v}ms", (x, v), textcoords="offset points", xytext=(0, 8), ha="center", fontsize=8, color="#c0392b")
    for x, v in zip(xb, cb_p95):
        ax.annotate(f"{v}ms", (x, v), textcoords="offset points", xytext=(0, -14), ha="center", fontsize=8, color="#d35400")

    # Median lines
    med_a = statistics.median(ca_p95); med_b = statistics.median(cb_p95)
    ax.axhline(med_a, color="#e74c3c", ls="--", lw=1.5, alpha=0.7, label=f"Exp A median={med_a}ms")
    ax.axhline(med_b, color="#e67e22", ls="--", lw=1.5, alpha=0.7, label=f"Exp B median={med_b}ms")

    # Baseline bands
    base_a = statistics.median([r["prom"]["base_vote_p95"] for r in EXP_A_RUNS])
    base_b = statistics.median([r["prom"]["base_vote_p95"] for r in EXP_B_RUNS])
    ax.axhspan(base_a-20, base_a+20, alpha=0.08, color=C_BASE, label=f"A baseline ≈{base_a}ms")
    ax.axhspan(base_b-20, base_b+20, alpha=0.05, color=C_BASE)

    ax.set_xticks([1,2,3,4,5]); ax.set_xticklabels([f"Run {i}" for i in range(1,6)])
    ax.set_ylabel("Vote p95 (chaos-end, Prometheus, ms)")
    ax.set_title("Biến động Vote p95 trong Chaos — 5 runs mỗi experiment\n(runs 3-5 thể hiện degradation mạnh hơn)", fontsize=11, fontweight="bold")
    ax.legend(fontsize=8); ax.grid(alpha=0.3)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(ms_fmt))
    plt.tight_layout()
    return fig_to_b64(fig)

# ══════════════════════════════════════════════════════════════════════════════
#  CHAOS MANIFESTS
# ══════════════════════════════════════════════════════════════════════════════
MANIFESTS_HTML = """
<div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
<div>
<div class="mtitle" style="border-color:#e74c3c">Exp A — vote→redis (50% loss + 800ms delay)</div>
<pre>apiVersion: chaos-mesh.org/v1alpha1
kind: NetworkChaos
metadata:
  name: network-loss-vote-to-redis
  namespace: default
spec:
  action: loss
  mode: all
  selector:
    namespaces: [default]
    labelSelectors: {app: vote}
  loss:
    loss: "50"        # 50% packet loss
    correlation: "25"
  direction: to
  target:
    selector:
      labelSelectors: {app: redis}
    mode: all
  duration: "300s"
---
apiVersion: chaos-mesh.org/v1alpha1
kind: NetworkChaos
metadata:
  name: network-delay-vote-to-redis
spec:
  action: delay
  delay:
    latency: "800ms"  # 800ms ± 300ms
    jitter:  "300ms"
    correlation: "25"
  duration: "300s"</pre>
</div>
<div>
<div class="mtitle" style="border-color:#e67e22">Exp B — worker→db (60% loss + 1.5s delay)</div>
<pre>apiVersion: chaos-mesh.org/v1alpha1
kind: NetworkChaos
metadata:
  name: network-loss-worker-to-db
  namespace: default
spec:
  action: loss
  mode: all
  selector:
    namespaces: [default]
    labelSelectors: {app: worker}
  loss:
    loss: "60"        # 60% packet loss
    correlation: "50"
  direction: to
  target:
    selector:
      labelSelectors: {app: db}
    mode: all
  duration: "300s"
---
apiVersion: chaos-mesh.org/v1alpha1
kind: NetworkChaos
metadata:
  name: network-delay-worker-to-db
spec:
  action: delay
  delay:
    latency: "1500ms" # 1.5s ± 500ms
    jitter:  "500ms"
    correlation: "50"
  duration: "300s"</pre>
</div>
</div>"""

# ══════════════════════════════════════════════════════════════════════════════
#  FLASK
# ══════════════════════════════════════════════════════════════════════════════
app = Flask(__name__)

HTML = """<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>KB3 Chaos Mesh — 5 Runs Real Data</title>
  <style>
    *{box-sizing:border-box}
    body{font-family:'Segoe UI',sans-serif;background:#f0f2f5;margin:0;padding:24px;color:#2c3e50}
    h1{margin-bottom:4px}
    .sub{color:#7f8c8d;font-size:.9em;margin-bottom:20px}
    h2{margin:28px 0 8px;font-size:1.15em;border-left:4px solid #3498db;padding-left:10px}
    .card{background:#fff;border-radius:12px;padding:20px;margin:14px 0;box-shadow:0 2px 10px #0001}
    .leg{display:flex;gap:18px;flex-wrap:wrap;margin-bottom:12px;font-size:.88em}
    .dot{display:inline-block;width:12px;height:12px;border-radius:50%;margin-right:4px;vertical-align:middle}
    pre{background:#1a1f2e;color:#7ec8a4;padding:14px;border-radius:6px;font-size:.8em;overflow-x:auto;line-height:1.55;margin:0}
    .mtitle{font-weight:bold;font-size:.9em;padding:6px 10px;background:#f5f5f5;border-left:4px solid #ccc;border-radius:4px 4px 0 0;margin-bottom:0}
    table{border-collapse:collapse;width:100%;font-size:.88em;margin:8px 0}
    th{background:#2c3e50;color:#fff;padding:8px 12px;text-align:left}
    td{padding:7px 12px;border-bottom:1px solid #eee}
    tr:nth-child(even) td{background:#f9f9f9}
    .badge{display:inline-block;padding:2px 8px;border-radius:4px;font-size:.82em;font-weight:bold}
    .ok{background:#e8f8ef;color:#1a7c3e}.warn{background:#fef4e4;color:#9a6700}.crit{background:#fde8e8;color:#c0392b}
    .finding{background:#f8f9fa;border-radius:8px;padding:12px 16px;margin:8px 0;border-left:4px solid #3498db;line-height:1.7}
    .finding.w{border-color:#f39c12}.finding.ok{border-color:#2ecc71}.finding.x{border-color:#e74c3c}
    .toc{background:#fff;border-radius:8px;padding:14px 20px;margin-bottom:20px}
    .toc a{color:#2980b9;text-decoration:none;display:block;padding:2px 0;font-size:.9em}
    .toc a:hover{text-decoration:underline}
    .grid2{display:grid;grid-template-columns:1fr 1fr;gap:14px}
    @media(max-width:800px){.grid2{grid-template-columns:1fr}}
  </style>
</head>
<body>
<h1>KB3 — Network Chaos (Chaos Mesh) | Dữ liệu thực 5 runs gần nhất</h1>
<p class="sub">NT531 Nhóm 17 · AKS In-Cluster · 2026-04-17 · 60 VUs · 90s baseline + 270s chaos + 60s recovery</p>

<div class="toc card">
  <strong>Mục lục</strong>
  <a href="#manifests">📄 Chaos Manifests đã áp dụng</a>
  <a href="#tcp">📊 TCP Retransmission — 5 runs + trung vị</a>
  <a href="#rps">⚡ Throughput (Vote RPS) — 5 runs + trung vị</a>
  <a href="#latency">⏱ Vote p95 Latency — Prometheus 5 runs</a>
  <a href="#k6">📉 k6 client p95 + Error Rate — 5 runs</a>
  <a href="#worker">🔧 Worker p95 & TCP per run</a>
  <a href="#scatter">🎯 Biến động Vote p95 chaos-end — scatter</a>
  <a href="#median">📋 Trung vị tổng hợp</a>
  <a href="#analysis">💡 Đánh giá</a>
</div>

<div class="leg">
  <span><span class="dot" style="background:#2ecc71"></span>Baseline (pre-chaos)</span>
  <span><span class="dot" style="background:#e74c3c"></span>Chaos-end (during)</span>
  <span><span class="dot" style="background:#3498db"></span>Recovery-end</span>
  <span><span class="dot" style="background:#9b59b6"></span>MEDIAN (trung vị)</span>
</div>

<h2 id="manifests">📄 Chaos Manifests đã áp dụng</h2>
<div class="card">{{ manifests }}</div>

<h2 id="tcp">📊 TCP Retransmission Rate (Prometheus)</h2>
<div class="card">{{ c_tcp }}</div>

<h2 id="rps">⚡ Throughput — Vote RPS (Prometheus)</h2>
<div class="card">{{ c_rps }}</div>

<h2 id="latency">⏱ Vote p95 Latency (Prometheus server-side)</h2>
<div class="card">{{ c_p95 }}</div>

<h2 id="k6">📉 k6 client-side: p95 & Error Rate (Timeout/5xx)</h2>
<div class="card">{{ c_k6 }}</div>

<h2 id="worker">🔧 Worker p95 & TCP Retransmission per run</h2>
<div class="card">{{ c_worker }}</div>

<h2 id="scatter">🎯 Biến động Vote p95 chaos-end — 5 runs</h2>
<div class="card">{{ c_scatter }}</div>

<h2 id="median">📋 Trung vị tổng hợp (từ AGGREGATE.md)</h2>
<div class="card">
<table>
  <tr><th>Metric</th><th>Exp A Baseline</th><th>Exp A Chaos-end</th><th>Δ A</th><th>Exp B Baseline</th><th>Exp B Chaos-end</th><th>Δ B</th></tr>
  <tr><td>Vote p95 (Prometheus)</td><td>211ms</td><td><b>229ms</b></td><td class="badge warn">+9%</td><td>201ms</td><td><b>454ms</b></td><td class="badge crit">+126%</td></tr>
  <tr><td>Vote p99 (Prometheus)</td><td>446ms</td><td><b>494ms</b></td><td class="badge warn">+11%</td><td>413ms</td><td><b>950ms</b></td><td class="badge crit">+130%</td></tr>
  <tr><td>Vote RPS (Prometheus)</td><td>51.8</td><td><b>32.7</b></td><td class="badge crit">-37%</td><td>49.8</td><td><b>22.2</b></td><td class="badge crit">-55%</td></tr>
  <tr><td>Worker p99 (ms)</td><td>211ms</td><td><b>346ms</b></td><td class="badge crit">+64%</td><td>189ms</td><td><b>433ms</b></td><td class="badge crit">+129%</td></tr>
  <tr><td>Worker throughput (/s)</td><td>6.61</td><td><b>5.76</b></td><td class="badge warn">-13%</td><td>6.63</td><td><b>4.82</b></td><td class="badge crit">-27%</td></tr>
  <tr><td>TCP Retransmission</td><td>0.28%</td><td><b>0.50%</b></td><td class="badge crit">+79%</td><td>0.35%</td><td><b>0.34%</b></td><td class="badge ok">-3%</td></tr>
  <tr><td>Node CPU %</td><td>98%</td><td><b>99%</b></td><td class="badge ok">+1%</td><td>96%</td><td><b>100%</b></td><td class="badge warn">+4%</td></tr>
  <tr><td>Error Rate (k6)</td><td>0%</td><td><b>0.018%</b></td><td class="badge ok">OK</td><td>0%</td><td><b>0.006%</b></td><td class="badge ok">OK</td></tr>
  <tr><td>Recovery vote p95</td><td>—</td><td>—</td><td>—</td><td colspan="2"><b>209ms vs 201ms baseline</b></td><td class="badge ok">+4% clean</td></tr>
</table>
</div>

<h2 id="analysis">💡 Đánh giá chi tiết KB3</h2>
<div class="card">

<h3>Run pattern: Biến động rõ rệt (Runs 1-2 nhẹ, Runs 3-5 nặng)</h3>
<div class="finding">
  Cả 2 experiments đều có pattern: <strong>run 1-2 chịu impact nhẹ, run 3-5 tăng mạnh</strong>.
  Vote p95 chaos-end Exp A: [206, 220, 229, 637, 637ms]. Exp B: [227, 215, 454, 702, 738ms].
  Nguyên nhân có thể do Redis/DB connection pool saturation tích lũy dần qua các run liên tiếp.
  <span class="badge warn">Run-to-run variance cao — cần xem xét thứ tự execution</span>
</div>

<h3>Exp A — vote→redis: 50% loss + 800ms delay</h3>
<div class="finding ok">
  <strong>Voting SLO giữ vững: Error rate 0% trong tất cả 5 runs</strong> (trừ run 5 = 0.09% — 1 timeout).
  Vote p95 Prometheus: trung vị 229ms — tăng nhẹ từ baseline 211ms (+9%).
  k6 client p95: tăng từ ~1.75s → ~2.9s (k6 đo end-to-end bao gồm kết nối).
</div>
<div class="finding w">
  <strong>Throughput drop đáng kể: -37% (median)</strong>. Vote RPS Prometheus: 51.8 → 32.7.
  Runs 4-5 giảm tới -69% (19.1 và 16.5 RPS). Redis backlog tích lũy do 50% loss khiến
  vote app retry nhiều, giảm effective throughput.
</div>
<div class="finding w">
  <strong>TCP Retransmission tăng: 0.28% → 0.50% (median, +79%)</strong>.
  Runs 4-5 lên 0.54-0.62%. Đây là bằng chứng rõ nhất của packet loss chaos ở network level.
  <span class="badge warn">Quan sát rõ ràng nhất trong Exp A</span>
</div>
<div class="finding ok">
  <strong>Recovery sạch:</strong> vote p95 về 206ms sau chaos (baseline 211ms, Δ=-3%). ✅
</div>

<h3>Exp B — worker→db: 60% loss + 1.5s delay</h3>
<div class="finding x">
  <strong>Vote RPS giảm -55% (median)</strong>: 49.8 → 22.2. Runs 4-5: xuống 12.2-16.0 RPS.
  Điều này bất ngờ vì vote path về lý thuyết tách biệt với DB write path.
  Giải thích: worker backpressure → Redis queue đầy → vote app block khi RPUSH vào queue.
  <span class="badge crit">Throughput impact nặng hơn Exp A</span>
</div>
<div class="finding ok">
  <strong>Error rate: 0% trong chaos phase</strong>. Chỉ có 0.34% error trong recovery của run 2
  — timeout ngắn sau khi chaos gỡ.
</div>
<div class="finding w">
  <strong>TCP Retransmission KHÔNG tăng (0.35% → 0.34% median)</strong>.
  Ngược với Exp A. Lý do: Chaos Mesh inject lỗi ở pod network namespace của worker,
  nhưng metric <code>node_netstat_Tcp_RetransSegs</code> đo ở node level — không phân biệt được
  pod-level retransmission của worker pod. <span class="badge warn">Metric limitation</span>
</div>
<div class="finding w">
  <strong>Recovery worker p99 chưa hoàn toàn: 342ms vs baseline 189ms (+81%)</strong>.
  Worker cần thêm thời gian để xử lý hết backlog accumulated votes trong Redis queue.
</div>

<h3>📌 So sánh Exp A vs Exp B</h3>
<table>
  <tr><th>Tiêu chí</th><th>Exp A (vote→redis)</th><th>Exp B (worker→db)</th></tr>
  <tr><td>Voting availability (error%)</td><td class="badge ok">✅ 0% (1 timeout)</td><td class="badge ok">✅ 0% chaos</td></tr>
  <tr><td>Throughput drop (median RPS)</td><td class="badge warn">-37%</td><td class="badge crit">-55% (nặng hơn)</td></tr>
  <tr><td>Vote p95 tăng</td><td class="badge warn">+9% (median 229ms)</td><td class="badge crit">+126% (median 454ms)</td></tr>
  <tr><td>TCP Retransmission</td><td class="badge crit">+79% — rõ ràng</td><td class="badge ok">-3% — không đo được</td></tr>
  <tr><td>Worker impact</td><td class="badge warn">+64% p99</td><td class="badge crit">+129% p99</td></tr>
  <tr><td>Recovery (vote p95)</td><td class="badge ok">Clean (-3%)</td><td class="badge warn">OK nhưng worker lag +81%</td></tr>
  <tr><td>Run variance</td><td>206-637ms (cao)</td><td>215-738ms (rất cao)</td></tr>
</table>

<div class="finding ok" style="margin-top:16px">
  <strong>Kết luận kiến trúc:</strong> Vote app có khả năng chịu lỗi mạng tốt — không có error rate đáng kể
  trong cả 10 runs (5A + 5B). Tuy nhiên, <strong>throughput giảm mạnh</strong> là điểm yếu:
  Redis queue backlog lan truyền ngược lên vote submission khi loss rate cao ≥50%.
  Cần implement <em>Redis connection timeout + retry budget</em> và <em>worker DB circuit breaker</em>
  để giảm backpressure propagation.
</div>
</div>
</body></html>"""

@app.route("/")
def index():
    print("Rendering KB3 real-data dashboard...")
    html = (HTML
            .replace("{{ manifests }}", MANIFESTS_HTML)
            .replace("{{ c_tcp }}",     img(chart_tcp()))
            .replace("{{ c_rps }}",     img(chart_rps()))
            .replace("{{ c_p95 }}",     img(chart_vote_p95()))
            .replace("{{ c_k6 }}",      img(chart_k6_latency_error()))
            .replace("{{ c_worker }}",  img(chart_worker_tcp()))
            .replace("{{ c_scatter }}", img(chart_scatter_chaos()))
            .replace("{{ c_median }}",  ""))
    return html

if __name__ == "__main__":
    print("KB3 Real-Data Dashboard → http://localhost:5051")
    app.run(host="0.0.0.0", port=5051, debug=False)
