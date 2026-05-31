#!/usr/bin/env python3
"""
Membership analysis for "Are You in the Simcluster?" followup paper.

Generates figures from existing JSON data (network_stats.json, revision_results.json).
No database required.

Produces:
  - figA1_membership_funnel.pdf/png: The narrowing criteria funnel
  - figA2_hop_distance.pdf/png: Hop distance from seeds
  - figA3_tiers_concentric.pdf/png: Concentric tier visualization
  - figA4_score_sketch.pdf/png: Conceptual score distribution
"""

import json
import math
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.collections import PatchCollection

DATA_DIR = Path(__file__).parent.parent / "data"
FIG_DIR = Path(__file__).parent.parent / "paper" / "figures"
FIG_DIR.mkdir(exist_ok=True)

plt.rcParams.update({
    "figure.dpi": 150,
    "savefig.dpi": 300,
    "font.family": "serif",
    "font.size": 10,
    "axes.titlesize": 12,
    "axes.labelsize": 11,
    "figure.figsize": (6, 4),
    "savefig.bbox": "tight",
    "savefig.pad_inches": 0.05,
})


def load_data():
    with open(DATA_DIR / "network_stats.json") as f:
        stats = json.load(f)
    with open(DATA_DIR / "revision_results.json") as f:
        revision = json.load(f)
    return stats, revision


def figA1_membership_funnel(stats, revision):
    fig, ax = plt.subplots(figsize=(7, 5))

    categories = [
        ("\"I feel simcluster-adjacent\"\n(The Vibes Criterion)", None, "#E8E8E8"),
        ("Your DID is in\nour dataset", stats["n_nodes"], "#B0BEC5"),
        ("Within 1 hop\nof a seed", revision["seed_proximity"]["hop_distribution"]["1"], "#64B5F6"),
        ("Followed by\n≥2 seeds", 92, "#42A5F5"),
        ("You are\na seed", 14, "#1565C0"),
    ]

    y_positions = list(range(len(categories) - 1, -1, -1))
    bar_height = 0.6

    for i, (label, count, color) in enumerate(categories):
        y = y_positions[i]
        if count is None:
            width = 10
            count_label = "∞"
        else:
            width = math.log10(count + 1) * 2
            count_label = f"{count:,}"

        rect = FancyBboxPatch(
            (0.5, y - bar_height / 2), width, bar_height,
            boxstyle="round,pad=0.1", facecolor=color, edgecolor="white",
            linewidth=1.5, alpha=0.9
        )
        ax.add_patch(rect)

        text_color = "white" if i >= 3 else "#333333"
        fontweight = "bold" if i >= 3 else "normal"

        ax.text(0.5 + width / 2, y + 0.08, label,
                ha="center", va="center", fontsize=8.5, color=text_color,
                fontweight=fontweight)
        ax.text(0.5 + width / 2, y - 0.18, count_label,
                ha="center", va="center", fontsize=11, color=text_color,
                fontweight="bold")

    for i in range(len(categories) - 1):
        y_from = y_positions[i] - bar_height / 2
        y_to = y_positions[i + 1] + bar_height / 2
        ax.annotate("", xy=(3.5, y_to + 0.05), xytext=(3.5, y_from - 0.05),
                     arrowprops=dict(arrowstyle="-|>", color="#999999", lw=1.5))

    ax.set_xlim(-0.5, 12)
    ax.set_ylim(-1, len(categories))
    ax.axis("off")
    ax.set_title("The Membership Funnel: Six Ways to Be In", fontsize=13, pad=15,
                  fontweight="bold")

    fig.tight_layout()
    fig.savefig(FIG_DIR / "figA1_membership_funnel.pdf")
    fig.savefig(FIG_DIR / "figA1_membership_funnel.png")
    plt.close(fig)
    print("  [figA1] Membership funnel saved")


def figA2_hop_distance(stats, revision):
    hops = revision["seed_proximity"]["hop_distribution"]
    labels = [f"Hop {k}" for k in sorted(hops.keys(), key=int)]
    counts = [hops[k] for k in sorted(hops.keys(), key=int)]
    colors = ["#1565C0", "#42A5F5", "#90CAF9"]
    annotations = [
        f"Seeds\n({counts[0]} accounts)",
        f"Seed-adjacent\n({counts[1]:,} accounts)",
        f"Periphery\n({counts[2]:,} accounts)",
    ]

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))

    ax = axes[0]
    bars = ax.bar(labels, counts, color=colors, edgecolor="white", linewidth=1.5, width=0.6)
    for bar, ann in zip(bars, annotations):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width() / 2, height + 200, ann,
                ha="center", va="bottom", fontsize=8, color="#555555")
    ax.set_ylabel("Number of accounts")
    ax.set_title("Hop Distance from Seeds")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.3, axis="y")

    ax = axes[1]
    total = sum(counts)
    fracs = [c / total * 100 for c in counts]
    wedges, texts, autotexts = ax.pie(
        counts, labels=None, colors=colors, autopct="%1.1f%%",
        startangle=90, pctdistance=0.65,
        wedgeprops=dict(edgecolor="white", linewidth=2)
    )
    for t in autotexts:
        t.set_fontsize(9)
        t.set_fontweight("bold")
    ax.legend(
        [f"Hop 0 ({counts[0]})", f"Hop 1 ({counts[1]:,})", f"Hop 2 ({counts[2]:,})"],
        loc="lower left", fontsize=8, framealpha=0.9
    )
    ax.set_title("Distribution of Hop Distances")

    fig.suptitle("How Far Are You From the Center?", fontsize=13, fontweight="bold", y=1.02)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "figA2_hop_distance.pdf")
    fig.savefig(FIG_DIR / "figA2_hop_distance.png")
    plt.close(fig)
    print("  [figA2] Hop distance saved")


def figA3_tiers_concentric(stats, revision):
    fig, ax = plt.subplots(figsize=(8, 8))

    tiers = [
        (4.0, "#E8EAF6", "TIER 5: Not in the graph\n(The Vibes Criterion)", "#333333"),
        (3.2, "#C5CAE9", "TIER 4: In the graph, hop 2\n(10,915 accounts)", "#333333"),
        (2.3, "#7986CB", "TIER 3: Hop 1 from seeds\n(1,852 accounts)", "#FFFFFF"),
        (1.5, "#3F51B5", "TIER 2: Followed by ≥2 seeds\n(~92 accounts)", "#FFFFFF"),
        (0.7, "#1A237E", "TIER 1: Seed accounts\n(14 accounts)", "#FFFFFF"),
    ]

    for radius, color, label, text_color in tiers:
        circle = plt.Circle((0, 0), radius, facecolor=color, edgecolor="white",
                            linewidth=2, alpha=0.9)
        ax.add_patch(circle)

    for radius, color, label, text_color in tiers:
        y_offset = -radius + 0.25 if radius > 1 else 0
        ax.text(0, y_offset, label, ha="center", va="center",
                fontsize=9, color=text_color, fontweight="bold",
                bbox=dict(boxstyle="round,pad=0.3", facecolor=color, alpha=0.7,
                         edgecolor="none"))

    ax.annotate(
        "you are\nhere\n(maybe)",
        xy=(1.8, 1.8), xytext=(3.2, 3.2),
        fontsize=9, ha="center", color="#666666", fontstyle="italic",
        arrowprops=dict(arrowstyle="->", color="#999999", lw=1.5,
                        connectionstyle="arc3,rad=0.2")
    )

    ax.set_xlim(-4.5, 4.5)
    ax.set_ylim(-4.5, 4.5)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.set_title("The Concentric Simcluster\n(Which Ring Are You In?)", fontsize=13,
                  fontweight="bold", pad=15)

    fig.tight_layout()
    fig.savefig(FIG_DIR / "figA3_tiers_concentric.pdf")
    fig.savefig(FIG_DIR / "figA3_tiers_concentric.png")
    plt.close(fig)
    print("  [figA3] Concentric tiers saved")


def figA4_score_sketch(stats, revision):
    fig, ax = plt.subplots(figsize=(8, 4.5))

    np.random.seed(42)
    n = 10915

    scores = np.zeros(n)
    hop_dist = revision["seed_proximity"]["hop_distribution"]
    n_hop0 = hop_dist["0"]
    n_hop1 = hop_dist["1"]
    n_hop2 = hop_dist["2"]

    scores[:n_hop0] = np.random.beta(8, 2, n_hop0) * 25 + 75
    scores[n_hop0:n_hop0 + n_hop1] = np.random.beta(3, 3, n_hop1) * 40 + 25
    scores[n_hop0 + n_hop1:] = np.random.beta(2, 5, n_hop2) * 30 + 0

    scores = np.clip(scores, 0, 100)

    tier_colors = {
        "SEED": "#1A237E",
        "CORE": "#3F51B5",
        "ADJACENT": "#7986CB",
        "PERIPHERAL": "#C5CAE9",
        "OUTSIDE": "#E8EAF6",
    }

    ax.hist(scores, bins=60, color="#7986CB", edgecolor="white", linewidth=0.3, alpha=0.8)

    for x, label, color in [
        (90, "SEED", tier_colors["SEED"]),
        (70, "CORE", tier_colors["CORE"]),
        (45, "ADJACENT", tier_colors["ADJACENT"]),
        (20, "PERIPHERAL", tier_colors["PERIPHERAL"]),
    ]:
        ax.axvline(x, color=color, linestyle="--", linewidth=1.5, alpha=0.7)
        ax.text(x + 1, ax.get_ylim()[1] * 0.9 if ax.get_ylim()[1] > 0 else 1000,
                label, fontsize=7, color=color, fontweight="bold", rotation=90, va="top")

    ax.set_xlabel("Simcluster Score (0-100)")
    ax.set_ylabel("Number of accounts")
    ax.set_title("Simulated Score Distribution\n(\"Where Do You Fall?\")", fontweight="bold")
    ax.grid(True, alpha=0.3, axis="y")

    fig.tight_layout()
    fig.savefig(FIG_DIR / "figA4_score_sketch.pdf")
    fig.savefig(FIG_DIR / "figA4_score_sketch.png")
    plt.close(fig)
    print("  [figA4] Score distribution sketch saved")


def main():
    print("Loading data...")
    stats, revision = load_data()
    print(f"  Network: {stats['n_nodes']:,} nodes, {stats['n_edges']:,} edges")

    print("\nGenerating figures...")
    figA1_membership_funnel(stats, revision)
    figA2_hop_distance(stats, revision)
    figA3_tiers_concentric(stats, revision)
    figA4_score_sketch(stats, revision)

    print("\nDone. Figures in paper/figures/")


if __name__ == "__main__":
    main()
