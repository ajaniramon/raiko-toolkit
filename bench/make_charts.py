"""Render the benchmark leaderboard charts used in the README.

Numbers come from bench/results/*/report*.md (104-task basic tier + advanced /
circuit / hardcore tiers). Regenerate with:  python bench/make_charts.py
Outputs PNGs into ../assets/.
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager  # noqa: F401

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "assets")
os.makedirs(OUT, exist_ok=True)

# palette (matches the TUI / backoffice)
INK, PANEL, GRID = "#13111c", "#1b1830", "#352d52"
TEXT, MUTED = "#ece8f7", "#9b93b8"
GOLD, GREEN, PINK, BLUE, PURPLE = "#e7b94e", "#5fe0a0", "#f472b6", "#46c2f5", "#b394ff"

plt.rcParams.update({
    "figure.facecolor": INK, "axes.facecolor": INK, "savefig.facecolor": INK,
    "text.color": TEXT, "axes.labelcolor": TEXT, "xtick.color": MUTED, "ytick.color": MUTED,
    "axes.edgecolor": GRID, "font.family": "DejaVu Sans", "font.size": 11,
})


def _style(ax):
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    ax.grid(axis="x", color=GRID, linewidth=0.6, alpha=0.6)
    ax.set_axisbelow(True)


# ---- 1) overall leaderboard (basic tier, 104 tasks) ----
runs = [
    ("qwythos · think", 94.9, GOLD),
    ("qwen3.5-9b · nothink", 94.7, GREEN),
    ("gemma4-12b · nothink", 92.9, BLUE),
    ("hauhau · nothink", 92.6, PURPLE),
    ("qwythos · nothink", 89.4, MUTED),
    ("qwen3.5-9b · think", 83.8, MUTED),
    ("gemma4-12b · think", 71.4, MUTED),
    ("hauhau · think", 61.9, MUTED),
]
runs = runs[::-1]
fig, ax = plt.subplots(figsize=(9, 4.6))
labels = [r[0] for r in runs]
vals = [r[1] for r in runs]
cols = [r[2] for r in runs]
bars = ax.barh(labels, vals, color=cols, height=0.66, edgecolor=INK)
for b, v in zip(bars, vals):
    ax.text(v - 1.5, b.get_y() + b.get_height() / 2, f"{v:.1f}", va="center", ha="right",
            color=INK, fontweight="bold", fontsize=10)
ax.set_xlim(0, 100)
ax.set_title("Overall leaderboard — basic tier (104 read-only tool tasks)",
             color=TEXT, fontweight="bold", loc="left", pad=12)
ax.set_xlabel("final score  (70% correctness · 20% tool-selection · 10% efficiency − penalties)",
              color=MUTED, fontsize=9)
_style(ax)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "leaderboard.png"), dpi=150)
plt.close(fig)

# ---- 2) thinking ON vs OFF per model ----
models = ["qwythos", "qwen3.5-9b", "gemma4-12b", "hauhau"]
nothink = [89.4, 94.7, 92.9, 92.6]
think = [94.9, 83.8, 71.4, 61.9]
x = range(len(models))
w = 0.38
fig, ax = plt.subplots(figsize=(9, 4.4))
b1 = ax.bar([i - w / 2 for i in x], nothink, w, label="thinking OFF", color=GREEN, edgecolor=INK)
b2 = ax.bar([i + w / 2 for i in x], think, w, label="thinking ON", color=PINK, edgecolor=INK)
for bars in (b1, b2):
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1, f"{b.get_height():.0f}",
                ha="center", color=TEXT, fontsize=9)
for i, (n, t) in enumerate(zip(nothink, think)):
    d = t - n
    ax.annotate(f"{d:+.0f}", (i, max(n, t) + 6), ha="center",
                color=GREEN if d > 0 else PINK, fontweight="bold", fontsize=11)
ax.set_xticks(list(x))
ax.set_xticklabels(models)
ax.set_ylim(0, 108)
ax.set_ylabel("final score")
ax.set_title("Thinking helps only Qwythos (+5.5); it hurts every other model",
             color=TEXT, fontweight="bold", loc="left", pad=12)
ax.legend(frameon=False, loc="lower right", labelcolor=TEXT)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
ax.grid(axis="y", color=GRID, linewidth=0.6, alpha=0.6)
ax.set_axisbelow(True)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "thinking_impact.png"), dpi=150)
plt.close(fig)

# ---- 3) top-3 finalists across all four tiers ----
tiers = ["basic\n(104)", "advanced\n(24)", "circuit\n(3)", "hardcore\n(20)"]
data = {
    "qwen3.5-9b · nothink": ([94.7, 98.1, 98.7, 82.0], GREEN),
    "gemma4-12b · nothink": ([92.9, 97.5, 98.7, 79.0], BLUE),
    "qwythos · think":      ([94.9, 95.9, 98.7, 71.6], GOLD),
}
x = range(len(tiers))
w = 0.26
fig, ax = plt.subplots(figsize=(9, 4.4))
for k, (name, (vals, col)) in enumerate(data.items()):
    off = (k - 1) * w
    bars = ax.bar([i + off for i in x], vals, w, label=name, color=col, edgecolor=INK)
    for b in bars:
        ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1, f"{b.get_height():.0f}",
                ha="center", color=TEXT, fontsize=8)
ax.set_xticks(list(x))
ax.set_xticklabels(tiers)
ax.set_ylim(0, 110)
ax.set_ylabel("final score")
ax.set_title("Top-3 finalists across all four tiers (they flake under real pressure)",
             color=TEXT, fontweight="bold", loc="left", pad=12)
ax.legend(frameon=False, loc="lower left", labelcolor=TEXT, ncol=3, fontsize=9)
for s in ("top", "right"):
    ax.spines[s].set_visible(False)
ax.grid(axis="y", color=GRID, linewidth=0.6, alpha=0.6)
ax.set_axisbelow(True)
fig.tight_layout()
fig.savefig(os.path.join(OUT, "tiers.png"), dpi=150)
plt.close(fig)

print("wrote:", ", ".join(sorted(os.listdir(OUT))))
