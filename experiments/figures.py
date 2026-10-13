"""Draw the stress-suite figures from experiments/results/stress.json.

    python3 -m experiments.figures

Writes PNGs to experiments/figures/. Needs matplotlib. Reads only the saved
results, so redrawing never retrains anything.

Every arm keeps the same color in every figure, and every mark is also
labeled in text - color is never the only way to tell arms apart.
"""

import json
from pathlib import Path

import matplotlib
import matplotlib.ticker

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import LinearSegmentedColormap  # noqa: E402

# Same path stress.py writes to - not imported from it, so redrawing doesn't
# pull in torch.
RESULTS = Path(__file__).parent / "results" / "stress.json"
OUT = Path(__file__).parent / "figures"

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"

# Fixed slot per arm (reference palette order), so an arm is the same color
# everywhere. accrue takes slot 1.
COLORS = {
    "accrue": "#2a78d6",
    "GRU: raw sequence": "#eb6834",
    "MLP: + running total": "#1baf7a",
    "MLP: + full ledger state": "#eda100",
    "MLP: + call position": "#e87ba4",
    "MLP: amount only": "#008300",
    "stateless Cedar": "#8a8984",
}

# One-hue sequential ramp (blue 100 -> 600) for the accuracy grids.
SEQ = LinearSegmentedColormap.from_list("seq", ["#cde2fb", "#86b6ef", "#2a78d6", "#184f95"])

plt.rcParams.update({
    "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
    "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
    "axes.edgecolor": GRID, "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold",
    "axes.titlelocation": "left", "axes.spines.top": False, "axes.spines.right": False,
    # Labels are full of dollar signs; don't read "$1 to $9,999" as math.
    "text.parse_math": False,
})


def _dollars(v, _pos=None):
    return f"${v / 1e6:g}M" if v >= 1e6 else f"${v / 1e3:g}k"


def _short(name):
    return name.replace("MLP: ", "MLP ").replace("GRU: raw sequence", "GRU (raw sequence)")


def accuracy_grid(path, title, subtitle, columns, cells):
    """Rows = arms, columns = conditions. Cell color = balanced accuracy
    (0.5 coin flip -> 1.0 perfect); text = caught / clean blocked."""
    arms = [a for a in COLORS if any(cells.get((a, c)) for c in columns)]
    fig, ax = plt.subplots(figsize=(1.9 * len(columns) + 2.6, 0.55 * len(arms) + 1.6))
    for i, arm in enumerate(arms):
        for j, col in enumerate(columns):
            r = cells.get((arm, col))
            if r is None:
                ax.add_patch(plt.Rectangle((j, i), 0.96, 0.92, color=GRID))
                ax.text(j + 0.48, i + 0.46, "no policy yet", ha="center", va="center",
                        color=INK_2, fontsize=8.5)
                continue
            bal = (r["detected"] + 1 - r["false_flagged"]) / 2
            shade = max(0.0, (bal - 0.5) / 0.5)
            ax.add_patch(plt.Rectangle((j, i), 0.96, 0.92, color=SEQ(shade)))
            ink = "white" if shade > 0.55 else INK
            ax.text(j + 0.48, i + 0.32, f"{bal:.2f}", ha="center", va="center",
                    color=ink, fontsize=10.5, fontweight="bold")
            ax.text(j + 0.48, i + 0.64, f"{r['detected']:.0%} caught · {r['false_flagged']:.0%} blocked",
                    ha="center", va="center", color=ink, fontsize=7.5)
    ax.set_xlim(0, len(columns))
    ax.set_ylim(len(arms), 0)
    ax.set_xticks([j + 0.48 for j in range(len(columns))], columns)
    ax.xaxis.tick_top()
    ax.set_yticks([i + 0.46 for i in range(len(arms))], [_short(a) for a in arms])
    ax.tick_params(length=0)
    for side in ax.spines.values():
        side.set_visible(False)
    fig.suptitle(title, x=0.01, ha="left", fontweight="bold", fontsize=12)
    fig.text(0.01, 0.01, subtitle, ha="left", va="bottom", color=INK_2, fontsize=8.5)
    fig.tight_layout(rect=(0, 0.06, 1, 0.97))
    fig.savefig(path, dpi=200)
    plt.close(fig)


def classes(res):
    cols = list(res["classes"])
    cells = {(arm, c): r for c in cols for arm, r in res["classes"][c]["rows"].items()}
    labels = {c: f"{c}\n~{res['classes'][c]['mean_calls']:.0f} calls" for c in cols}
    accuracy_grid(
        OUT / "classes.png", "Each violation needs its own piece of state",
        "Balanced accuracy: mean of violating sessions caught and clean sessions passed. "
        "0.50 = coin flip, 1.00 = perfect.",
        [labels[c] for c in cols],
        {(a, labels[c]): r for (a, c), r in cells.items()},
    )


def shift(res):
    cols = list(res["shift"])
    cells = {(arm, c): r for c in cols for arm, r in res["shift"][c].items()}
    accuracy_grid(
        OUT / "shift.png", "Off the training distribution, only the ledger holds",
        "Spend guards trained on $500-4,000 calls and a $10,000 budget, then tested on each "
        "condition without retraining.",
        cols, cells,
    )


def length(res):
    pts = res["length"]
    x = [p["mean_calls"] for p in pts]
    show = ["accrue", "MLP: + full ledger state", "MLP: + running total", "GRU: raw sequence"]
    fig, ax = plt.subplots(figsize=(7.2, 4.2))
    # Reference line stops at the data so it doesn't run through the labels.
    ax.plot([x[0] * 0.9, x[-1]], [0.5, 0.5], color=INK_2, lw=1, ls=(0, (4, 3)), zorder=1)
    ax.text(x[0], 0.485, "coin flip", ha="left", va="top", color=INK_2, fontsize=8.5)
    ends = []
    for arm in show:
        y = [p["balanced"][arm] for p in pts]
        ax.plot(x, y, color=COLORS[arm], lw=2, marker="o", ms=5.5,
                markeredgecolor=SURFACE, markeredgewidth=1.5, zorder=3)
        ends.append([y[-1], arm])
    # Direct labels at the line ends, nudged apart so they don't collide.
    ends.sort()
    for k in range(1, len(ends)):
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 0.045)
    for y, arm in ends:
        ax.text(x[-1] * 1.03, y, _short(arm), color=INK, va="center", fontsize=9)
    ax.set_xscale("log")
    ax.set_xticks([5, 10, 20, 40], ["5", "10", "20", "40"])
    ax.minorticks_off()
    ax.set_xlim(x[0] * 0.9, x[-1] * 1.9)
    ax.set_ylim(0.4, 1.06)
    ax.grid(axis="y", color=GRID, lw=0.8)
    ax.set_xlabel("calls per session (same $10,000 budget, smaller calls)")
    ax.set_ylabel("balanced accuracy")
    ax.set_title("A GRU learns the running total - for sessions as long as it trained on")
    fig.tight_layout()
    fig.savefig(OUT / "length.png", dpi=200)
    plt.close(fig)


def attack(res):
    budgets = list(res["attack"])
    arms = [a for a in COLORS if a in res["attack"][budgets[0]]]
    fig, axes = plt.subplots(1, len(budgets), figsize=(5.2 * len(budgets), 0.5 * len(arms) + 1.8),
                             sharey=True)
    for ax, b in zip(axes, budgets):
        budget = int(b)
        vals = [res["attack"][b][a]["extracted"] for a in arms]
        ys = range(len(arms))
        ax.barh(ys, vals, height=0.6, color=[COLORS[a] for a in arms], zorder=3)
        ax.axvline(budget, color=INK, lw=1.2, zorder=4)
        ax.text(budget * 1.08, -0.75, f"budget ${budget:,}", ha="left", va="center",
                color=INK, fontsize=8.5)
        for y, v in zip(ys, vals):
            ax.text(v * 1.12, y, f"${v:,}  ({v / budget:.1f}x)", va="center", color=INK, fontsize=8.5)
        ax.set_xscale("log")
        ax.set_xlim(1_000, max(vals) * 12)
        ax.xaxis.set_major_formatter(matplotlib.ticker.FuncFormatter(_dollars))
        ax.set_ylim(len(arms) - 0.4, -1.1)
        ax.grid(axis="x", color=GRID, lw=0.8, zorder=0)
        ax.set_xlabel("dollars extracted in one session (log scale)")
        ax.set_title(f"Budget ${budget:,}" + ("" if budget == res["budget"] else " (not retrained)"),
                     fontsize=10.5)
        ax.tick_params(axis="y", length=0)
    axes[0].set_yticks(list(range(len(arms))), [_short(a) for a in arms])
    fig.suptitle("Adaptive attacker: free queries, any amount from $1 to $9,999",
                 x=0.01, ha="left", fontweight="bold", fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.95))
    fig.savefig(OUT / "attack.png", dpi=200)
    plt.close(fig)


def main():
    res = json.loads(RESULTS.read_text())
    OUT.mkdir(exist_ok=True)
    for draw in (classes, shift, length, attack):
        draw(res)
    print(f"wrote {', '.join(sorted(p.name for p in OUT.glob('*.png')))} to {OUT}")


if __name__ == "__main__":
    main()
