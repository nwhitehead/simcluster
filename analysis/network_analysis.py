#!/usr/bin/env python3
"""
Simcluster network analysis: metrics, community detection, hypotheses, figures.

Produces:
  - Degree distributions (in, out, total) with power-law fit
  - Core-periphery structure analysis
  - Community detection (Louvain)
  - Centrality measures (betweenness, eigenvector, pagerank)
  - Reciprocity and transitivity
  - k-core decomposition
  - Handle-based subgraph of top nodes
  - LaTeX-formatted stats table
"""

import sqlite3
import json
import sys
from pathlib import Path
from collections import defaultdict, Counter
import math

import networkx as nx
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch
import matplotlib.ticker as ticker
from scipy import stats
from sklearn.linear_model import LinearRegression

DATA_DIR = Path(__file__).parent.parent / "data"
FIG_DIR = Path(__file__).parent.parent / "paper" / "figures"
FIG_DIR.mkdir(exist_ok=True)

# Publication-quality style
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


def load_graph(db_path: str) -> nx.DiGraph:
    """Load the simcluster follow graph from SQLite."""
    db = sqlite3.connect(db_path)
    
    # Build graph from follows table
    G = nx.DiGraph()
    
    # Load follows
    follows = db.execute("SELECT source, target FROM follows").fetchall()
    G.add_edges_from(follows)
    
    # Add node attributes from actors table
    actors = db.execute(
        "SELECT did, handle, display_name, description, follows_count, "
        "followers_count, posts_count, is_seed FROM actors "
        "WHERE handle IS NOT NULL"
    ).fetchall()
    
    for row in actors:
        did, handle, display_name, description, fc, fwc, pc, is_seed = row
        if did in G:
            G.nodes[did]["handle"] = handle
            G.nodes[did]["display_name"] = display_name or ""
            G.nodes[did]["description"] = description or ""
            G.nodes[did]["follows_count"] = fc or 0
            G.nodes[did]["followers_count"] = fwc or 0
            G.nodes[did]["posts_count"] = pc or 0
            G.nodes[did]["is_seed"] = bool(is_seed)
    
    db.close()
    return G


def to_clean_undirected(G):
    """Convert to undirected graph without self-loops."""
    UG = G.to_undirected()
    UG.remove_edges_from(nx.selfloop_edges(UG))
    return UG


def fig1_degree_distribution(G):
    """Figure 1: Degree distributions (in, out, total) with CCDF."""
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    
    in_degrees = [d for _, d in G.in_degree()]
    out_degrees = [d for _, d in G.out_degree()]
    total_degrees = [d for _, d in G.degree()]
    
    # Histogram (log-log)
    ax = axes[0]
    bins = np.logspace(0, np.log10(max(total_degrees) + 1), 40)
    for label, data, color, marker in [
        ("In-degree", in_degrees, "#2196F3", "o"),
        ("Out-degree", out_degrees, "#FF5722", "s"),
        ("Total", total_degrees, "#4CAF50", "^"),
    ]:
        counts, edges = np.histogram(data, bins=bins)
        centers = (edges[:-1] + edges[1:]) / 2
        mask = counts > 0
        ax.loglog(centers[mask], counts[mask], marker=marker, markersize=3,
                  linestyle="none", color=color, alpha=0.7, label=label)
    ax.set_xlabel("Degree k")
    ax.set_ylabel("Count")
    ax.set_title("Degree Distribution (binned)")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    # CCDF
    ax = axes[1]
    for label, data, color in [
        ("In-degree", in_degrees, "#2196F3"),
        ("Out-degree", out_degrees, "#FF5722"),
        ("Total", total_degrees, "#4CAF50"),
    ]:
        sorted_data = np.sort(data)
        ccdf = 1.0 - np.arange(len(sorted_data)) / len(sorted_data)
        ax.loglog(sorted_data, ccdf, color=color, linewidth=1.5, label=label, alpha=0.8)
    ax.set_xlabel("Degree k")
    ax.set_ylabel("P(K ≥ k)")
    ax.set_title("Complementary CDF")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    fig.suptitle("Figure 1: Simcluster Follow-Network Degree Distributions", y=1.01)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig1_degree_distribution.pdf")
    fig.savefig(FIG_DIR / "fig1_degree_distribution.png")
    plt.close(fig)
    print("  [fig1] Degree distribution saved")


def fig2_powerlaw_fit(G):
    """Figure 2: Power-law fit to in-degree distribution."""
    in_degrees = np.array([d for _, d in G.in_degree() if d > 0])
    
    # Fit power law via MLE on log-transformed data
    # P(k) ∝ k^(-α), estimate α from tail
    k_min = np.percentile(in_degrees, 50)  # fit on upper half
    tail = in_degrees[in_degrees >= k_min]
    
    # Log-log linear fit for α
    counts, edges = np.histogram(tail, bins=50)
    centers = (edges[:-1] + edges[1:]) / 2
    mask = counts > 0
    
    X = np.log10(centers[mask]).reshape(-1, 1)
    y = np.log10(counts[mask])
    reg = LinearRegression().fit(X, y)
    alpha_fit = -reg.coef_[0]
    
    fig, ax = plt.subplots(figsize=(6, 5))
    
    # Full data
    counts_all, edges_all = np.histogram(in_degrees, bins=80)
    centers_all = (edges_all[:-1] + edges_all[1:]) / 2
    mask_all = counts_all > 0
    ax.loglog(centers_all[mask_all], counts_all[mask_all], "o", markersize=2,
              color="#2196F3", alpha=0.5, label="Observed")
    
    # Fit line
    x_fit = np.logspace(np.log10(k_min), np.log10(max(in_degrees)), 100)
    y_fit = 10 ** reg.predict(np.log10(x_fit).reshape(-1, 1))
    ax.loglog(x_fit, y_fit, "r--", linewidth=2,
              label=f"Power-law fit: α = {alpha_fit:.2f}")
    
    ax.axvline(k_min, color="gray", linestyle=":", alpha=0.5,
               label=f"k_min = {k_min:.0f}")
    ax.set_xlabel("In-degree k")
    ax.set_ylabel("P(k)")
    ax.set_title(f"Figure 2: Power-Law Fit (α ≈ {alpha_fit:.2f})")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig2_powerlaw_fit.pdf")
    fig.savefig(FIG_DIR / "fig2_powerlaw_fit.png")
    plt.close(fig)
    
    return float(alpha_fit), int(k_min)


def fig3_community_structure(G):
    """Figure 3: Community detection via Louvain."""
    import community as community_louvain
    
    # Work with undirected giant component for community detection
    UG = G.to_undirected()
    components = list(nx.connected_components(UG))
    giant = max(components, key=len)
    UG_giant = UG.subgraph(giant).copy()
    
    partition = community_louvain.best_partition(UG_giant, random_state=42)
    n_communities = len(set(partition.values()))
    
    community_sizes = Counter(partition.values())
    sizes = sorted(community_sizes.values(), reverse=True)
    
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    
    # Community size distribution
    ax = axes[0]
    ax.bar(range(1, len(sizes) + 1), sizes, color="#7C4DFF", alpha=0.8, edgecolor="white", linewidth=0.3)
    ax.set_xlabel("Community rank")
    ax.set_ylabel("Size (nodes)")
    ax.set_title(f"Community Sizes ({n_communities} communities)")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.3, axis="y")
    
    # Modularity by community size
    ax = axes[1]
    # Sub-communities: split giant into its communities
    comm_nodes = defaultdict(list)
    for node, comm in partition.items():
        comm_nodes[comm].append(node)
    
    # Calculate internal density for top communities
    top_comms = sorted(comm_nodes.keys(), key=lambda c: len(comm_nodes[c]), reverse=True)[:10]
    densities = []
    labels = []
    for comm in top_comms:
        nodes = comm_nodes[comm]
        subg = UG_giant.subgraph(nodes)
        if len(nodes) > 1:
            max_edges = len(nodes) * (len(nodes) - 1) / 2
            density = subg.number_of_edges() / max_edges if max_edges > 0 else 0
        else:
            density = 0
        densities.append(density)
        labels.append(f"C{comm}")
    
    colors = plt.cm.viridis(np.linspace(0.2, 0.9, len(top_comms)))
    ax.barh(range(len(top_comms)), densities, color=colors, edgecolor="white", linewidth=0.3)
    ax.set_yticks(range(len(top_comms)))
    ax.set_yticklabels(labels, fontsize=7)
    ax.set_xlabel("Internal edge density")
    ax.set_title("Top 10 Communities: Internal Density")
    ax.grid(True, alpha=0.3, axis="x")
    
    fig.suptitle(f"Figure 3: Louvain Community Detection (modularity={community_louvain.modularity(partition, UG_giant):.3f})",
                 y=1.01)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig3_community_structure.pdf")
    fig.savefig(FIG_DIR / "fig3_community_structure.png")
    plt.close(fig)
    
    return n_communities, sizes, community_louvain.modularity(partition, UG_giant)


def fig4_centrality_correlation(G):
    """Figure 4: Centrality measures and correlations."""
    # Compute on largest WCC
    wccs = list(nx.weakly_connected_components(G))
    largest_wcc = max(wccs, key=len)
    H = G.subgraph(largest_wcc).copy()
    
    # Betweenness (approximate with k=500 for speed)
    betweenness = nx.betweenness_centrality(H, k=min(500, len(H)), seed=42)
    pagerank = nx.pagerank(H, alpha=0.85)
    # Eigenvector centrality: use largest undirected CC to avoid AmbiguousSolution
    UH = H.to_undirected()
    uccs = list(nx.connected_components(UH))
    largest_ucc = max(uccs, key=len)
    UH_lcc = UH.subgraph(largest_ucc).copy()
    try:
        eigenvector = nx.eigenvector_centrality_numpy(UH_lcc, max_iter=200)
    except (nx.AmbiguousSolution, nx.PowerIterationFailedConvergence):
        print("  [fig4] Eigenvector centrality failed even on LCC, using PageRank as substitute")
        eigenvector = {n: pagerank.get(n, 0) for n in UH.nodes}
    
    # Top nodes by each measure
    top_bet = sorted(betweenness.items(), key=lambda x: x[1], reverse=True)[:20]
    top_eig = sorted(eigenvector.items(), key=lambda x: x[1], reverse=True)[:20]
    top_pr = sorted(pagerank.items(), key=lambda x: x[1], reverse=True)[:20]
    
    fig, axes = plt.subplots(1, 3, figsize=(12, 4))
    
    pairs = [
        (axes[0], betweenness, eigenvector, "Betweenness", "Eigenvector"),
        (axes[1], betweenness, pagerank, "Betweenness", "PageRank"),
        (axes[2], eigenvector, pagerank, "Eigenvector", "PageRank"),
    ]
    
    for ax, m1, m2, l1, l2 in pairs:
        common = set(m1.keys()) & set(m2.keys())
        xs = [m1[n] for n in common]
        ys = [m2[n] for n in common]
        r, p = stats.pearsonr(xs, ys)
        ax.scatter(xs, ys, s=2, alpha=0.3, color="#E91E63", edgecolors="none")
        ax.set_xlabel(l1, fontsize=9)
        ax.set_ylabel(l2, fontsize=9)
        ax.set_title(f"r = {r:.3f} (p = {p:.1e})" if p > 1e-10 else f"r = {r:.3f} (p < 1e-10)",
                     fontsize=9)
        ax.loglog()
        ax.grid(True, alpha=0.3)
    
    fig.suptitle("Figure 4: Centrality Measure Correlations", y=1.01)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig4_centrality_correlation.pdf")
    fig.savefig(FIG_DIR / "fig4_centrality_correlation.png")
    plt.close(fig)
    
    # Return top handles
    def top_handles(measure_dict, n=10):
        result = []
        for node, val in sorted(measure_dict.items(), key=lambda x: x[1], reverse=True)[:n]:
            handle = G.nodes[node].get("handle", node[:20])
            result.append((handle, val))
        return result
    
    return {
        "betweenness": top_handles(betweenness),
        "eigenvector": top_handles(eigenvector),
        "pagerank": top_handles(pagerank),
    }


def fig5_core_periphery(G):
    """Figure 5: k-core decomposition and shell structure."""
    # k-core on undirected version (remove self-loops first)
    UG = G.to_undirected()
    UG.remove_edges_from(nx.selfloop_edges(UG))
    kcores = nx.core_number(UG)
    
    core_counts = Counter(kcores.values())
    max_core = max(kcores.values())
    
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    
    # k-core distribution
    ax = axes[0]
    ks = sorted(core_counts.keys())
    counts = [core_counts[k] for k in ks]
    ax.bar(ks, counts, color="#00BCD4", alpha=0.8, edgecolor="white", linewidth=0.3)
    ax.set_xlabel("k-core number")
    ax.set_ylabel("Node count")
    ax.set_title(f"k-Core Distribution (max core: {max_core})")
    ax.set_yscale("log")
    ax.grid(True, alpha=0.3, axis="y")
    
    # Shell structure: fraction of nodes at each core
    ax = axes[1]
    total = sum(counts)
    cumulative = np.cumsum([0] + counts)
    ax.fill_between(range(len(ks) + 1), 0, cumulative / total * 100,
                    step="post", color="#00BCD4", alpha=0.7)
    ax.set_xlabel("k-core number")
    ax.set_ylabel("Cumulative % of nodes")
    ax.set_title("Shell Structure (Cumulative)")
    ax.grid(True, alpha=0.3)
    
    fig.suptitle("Figure 5: k-Core Decomposition", y=1.01)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig5_core_periphery.pdf")
    fig.savefig(FIG_DIR / "fig5_core_periphery.png")
    plt.close(fig)
    
    return max_core, dict(core_counts)


def fig6_reciprocity_transitivity(G):
    """Figure 6: Reciprocity analysis."""
    # Overall reciprocity
    recip = nx.overall_reciprocity(G)
    
    # Reciprocity by degree bin
    out_deg = dict(G.out_degree())
    in_deg = dict(G.in_degree())
    
    # For each node, fraction of outgoing that are reciprocal
    bins = [0, 2, 5, 10, 20, 50, 100, 500, 10000]
    bin_labels = ["1", "2-5", "6-10", "11-20", "21-50", "51-100", "101-500", "500+"]
    bin_recip = []
    
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i+1]
        nodes = [n for n, d in out_deg.items() if lo < d <= hi]
        if nodes:
            recips = []
            for n in nodes:
                successors = set(G.successors(n))
                predecessors = set(G.predecessors(n))
                if successors:
                    recips.append(len(successors & predecessors) / len(successors))
            bin_recip.append(np.mean(recips) if recips else 0)
        else:
            bin_recip.append(0)
    
    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    
    # Bar chart
    ax = axes[0]
    colors = plt.cm.RdYlGn(np.linspace(0.3, 0.9, len(bin_labels)))
    ax.bar(range(len(bin_labels)), bin_recip, color=colors, edgecolor="white", linewidth=0.3)
    ax.set_xticks(range(len(bin_labels)))
    ax.set_xticklabels(bin_labels, fontsize=8)
    ax.set_xlabel("Out-degree range")
    ax.set_ylabel("Mean reciprocity")
    ax.set_title(f"Reciprocity by Out-degree (global: {recip:.3f})")
    ax.axhline(recip, color="red", linestyle="--", alpha=0.5, linewidth=1)
    ax.grid(True, alpha=0.3, axis="y")
    
    # Clustering coefficient distribution
    ax = axes[1]
    UG = G.to_undirected()
    clustering = nx.clustering(UG)
    cc_values = list(clustering.values())
    ax.hist(cc_values, bins=50, color="#9C27B0", alpha=0.7, edgecolor="white", linewidth=0.3)
    ax.set_xlabel("Clustering coefficient")
    ax.set_ylabel("Count")
    ax.set_title(f"Clustering Coefficient (mean: {np.mean(cc_values):.3f})")
    ax.grid(True, alpha=0.3, axis="y")
    
    fig.suptitle("Figure 6: Reciprocity and Transitivity", y=1.01)
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig6_reciprocity_transitivity.pdf")
    fig.savefig(FIG_DIR / "fig6_reciprocity_transitivity.png")
    plt.close(fig)
    
    return recip, np.mean(cc_values), np.mean(bin_recip)


def fig7_network_overview(G):
    """Figure 7: Spring-layout overview of the giant component."""
    UG = G.to_undirected()
    components = list(nx.connected_components(UG))
    giant = max(components, key=len)
    
    # Sample for visualization (too many nodes = unreadable)
    n_sample = min(500, len(giant))
    sampled = set(list(giant)[:n_sample])
    # Also add all seeds for context
    seeds = {n for n in G.nodes if G.nodes[n].get("is_seed")}
    sampled |= seeds
    sampled = {n for n in sampled if n in G}
    
    H = G.subgraph(sampled).to_undirected()
    
    # Remove isolates for cleaner viz
    isolates = [n for n in H.nodes if H.degree(n) == 0]
    H.remove_nodes_from(isolates)
    
    if len(H) < 3:
        print("  [fig7] Too few nodes for layout, skipping")
        return
    
    fig, ax = plt.subplots(figsize=(10, 10))
    
    pos = nx.spring_layout(H, k=2.5, iterations=50, seed=42, scale=2)
    
    # Node sizes by degree
    node_sizes = [10 + 3 * H.degree(n) for n in H.nodes]
    
    # Colors: seeds in red, others by degree
    node_colors = []
    for n in H.nodes:
        if G.nodes[n].get("is_seed"):
            node_colors.append("#FF1744")
        else:
            node_colors.append("#448AFF")
    
    nx.draw_networkx_edges(H, pos, alpha=0.15, edge_color="#666666", width=0.3, ax=ax)
    nx.draw_networkx_nodes(H, pos, node_size=node_sizes, node_color=node_colors,
                           alpha=0.8, linewidths=0.3, edgecolors="white", ax=ax)
    
    # Label seeds
    seed_labels = {n: G.nodes[n].get("handle", n[:12]) for n in H.nodes if G.nodes[n].get("is_seed")}
    nx.draw_networkx_labels(H, pos, labels=seed_labels, font_size=5,
                           font_color="#333333", ax=ax)
    
    ax.set_title(f"Figure 7: Simcluster Network (giant component sample, n={len(H)})")
    ax.axis("off")
    
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig7_network_overview.pdf")
    fig.savefig(FIG_DIR / "fig7_network_overview.png")
    plt.close(fig)
    print("  [fig7] Network overview saved")


def fig8_assortativity(G):
    """Figure 8: Degree assortativity and mixing patterns."""
    # Degree assortativity
    r_assort = nx.degree_assortativity_coefficient(G)
    
    # Degree mixing: mean neighbor degree by node degree
    in_deg = dict(G.in_degree())
    out_deg = dict(G.out_degree())
    
    # Bin nodes by out-degree and compute mean neighbor in-degree
    bins = [0, 2, 5, 10, 20, 50, 100, 500, 10000]
    bin_centers = []
    mean_neighbor_deg = []
    
    for i in range(len(bins) - 1):
        lo, hi = bins[i], bins[i+1]
        nodes = [n for n, d in out_deg.items() if lo < d <= hi]
        if nodes:
            bin_centers.append(np.mean([out_deg[n] for n in nodes]))
            neighbor_degs = []
            for n in nodes:
                successors = list(G.successors(n))
                if successors:
                    neighbor_degs.append(np.mean([in_deg.get(s, 0) for s in successors]))
                else:
                    neighbor_degs.append(0)
            mean_neighbor_deg.append(np.mean(neighbor_degs))
    
    fig, ax = plt.subplots(figsize=(6, 5))
    ax.plot(bin_centers, mean_neighbor_deg, "o-", color="#FF5722", markersize=6,
            linewidth=2, label=f"Degree assortment r = {r_assort:.3f}")
    ax.set_xlabel("Mean out-degree of bin")
    ax.set_ylabel("Mean in-degree of neighbors")
    ax.set_xscale("log")
    ax.set_yscale("log")
    
    # Reference line for neutral mixing
    x_ref = [min(bin_centers), max(bin_centers)]
    ax.plot(x_ref, x_ref, "k--", alpha=0.3, linewidth=1, label="Neutral mixing")
    
    ax.set_title(f"Figure 8: Degree Mixing Pattern (r = {r_assort:.3f})")
    ax.legend(fontsize=8)
    ax.grid(True, alpha=0.3)
    
    fig.tight_layout()
    fig.savefig(FIG_DIR / "fig8_assortativity.pdf")
    fig.savefig(FIG_DIR / "fig8_assortativity.png")
    plt.close(fig)
    
    return r_assort


def compute_stats(G, powerlaw_alpha, powerlaw_kmin, n_communities, comm_sizes,
                  modularity, max_core, core_counts, reciprocity, mean_clustering,
                  mean_recip_bin, assortativity, centrality_tops):
    """Compute comprehensive network statistics."""
    stats_dict = {
        "n_nodes": G.number_of_nodes(),
        "n_edges": G.number_of_edges(),
        "density": nx.density(G),
        "reciprocity": reciprocity,
        "mean_clustering": mean_clustering,
        "assortativity": assortativity,
        "n_wcc": nx.number_weakly_connected_components(G),
        "largest_wcc_frac": len(max(nx.weakly_connected_components(G), key=len)) / G.number_of_nodes(),
        "n_scc": nx.number_strongly_connected_components(G),
        "largest_scc_frac": len(max(nx.strongly_connected_components(G), key=len)) / G.number_of_nodes(),
        "mean_in_deg": np.mean([d for _, d in G.in_degree()]),
        "mean_out_deg": np.mean([d for _, d in G.out_degree()]),
        "max_in_deg": max(d for _, d in G.in_degree()),
        "max_out_deg": max(d for _, d in G.out_degree()),
        "powerlaw_alpha": powerlaw_alpha,
        "powerlaw_kmin": powerlaw_kmin,
        "n_communities": n_communities,
        "largest_community_frac": comm_sizes[0] / sum(comm_sizes) if comm_sizes else 0,
        "modularity": modularity,
        "max_core": max_core,
        "n_cores": len(core_counts),
    }
    return stats_dict


def write_latex_stats(stats, centrality_tops):
    """Write a LaTeX file with network statistics for inclusion in paper."""
    path = DATA_DIR / "network_stats.tex"
    lines = []
    lines.append("% Auto-generated network statistics for simcluster paper")
    lines.append("")
    
    # Basic stats
    lines.append("\\begin{table}[htbp]")
    lines.append("\\centering")
    lines.append("\\caption{Network Statistics}")
    lines.append("\\label{tab:stats}")
    lines.append("\\begin{tabular}{lrlr}")
    lines.append("\\toprule")
    lines.append("Metric & Value & Metric & Value \\\\")
    lines.append("\\midrule")
    
    rows = [
        ("Nodes", f"{stats['n_nodes']:,}", "Edges", f"{stats['n_edges']:,}"),
        ("Density", f"{stats['density']:.6f}", "Reciprocity", f"{stats['reciprocity']:.4f}"),
        ("Mean clustering", f"{stats['mean_clustering']:.4f}", "Degree assortativity", f"{stats['assortativity']:.4f}"),
        ("WCC count", f"{stats['n_wcc']}", "Largest WCC", f"{stats['largest_wcc_frac']:.1%}"),
        ("SCC count", f"{stats['n_scc']}", "Largest SCC", f"{stats['largest_scc_frac']:.1%}"),
        ("Mean in-degree", f"{stats['mean_in_deg']:.1f}", "Mean out-degree", f"{stats['mean_out_deg']:.1f}"),
        ("Max in-degree", f"{stats['max_in_deg']}", "Max out-degree", f"{stats['max_out_deg']}"),
        ("Power-law α (in)", f"{stats['powerlaw_alpha']:.2f}", "k_min", f"{stats['powerlaw_kmin']}"),
        ("Louvain communities", f"{stats['n_communities']}", "Largest comm.", f"{stats['largest_community_frac']:.1%}"),
        ("Modularity Q", f"{stats['modularity']:.4f}", "Max k-core", f"{stats['max_core']}"),
    ]
    
    for left_k, left_v, right_k, right_v in rows:
        lines.append(f"  {left_k} & {left_v} & {right_k} & {right_v} \\\\")
    
    lines.append("\\bottomrule")
    lines.append("\\end{tabular}")
    lines.append("\\end{table}")
    
    # Centrality top-10 tables
    lines.append("")
    for measure, entries in centrality_tops.items():
        lines.append("\\begin{table}[htbp]")
        lines.append("\\centering")
        lines.append(f"\\caption{{Top 10 Accounts by {measure.capitalize()} Centrality}}")
        lines.append(f"\\label{{tab:centrality_{measure}}}")
        lines.append("\\begin{tabular}{rl}")
        lines.append("\\toprule")
        lines.append("Rank & Handle & Value \\\\")
        lines.append("\\midrule")
        for i, (handle, val) in enumerate(entries, 1):
            handle_escaped = handle.replace("_", "\\_").replace("&", "\\&")
            lines.append(f"  {i} & {handle_escaped} & {val:.6f} \\\\")
        lines.append("\\bottomrule")
        lines.append("\\end{tabular}")
        lines.append("\\end{table}")
    
    with open(path, "w") as f:
        f.write("\n".join(lines))
    print(f"  [latex] Stats written to {path}")


def main():
    db_path = str(DATA_DIR / "simcluster.db")
    print(f"Loading graph from {db_path}...")
    G = load_graph(db_path)
    print(f"  Graph: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")
    
    # Remove nodes with degree 0 in the follow network (not part of any connection)
    isolates = [n for n in G.nodes if G.degree(n) == 0]
    G.remove_nodes_from(isolates)
    print(f"  After removing {len(isolates)} isolates: {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")
    
    # Remove self-loops (accounts following themselves)
    selfloops = list(nx.selfloop_edges(G))
    G.remove_edges_from(selfloops)
    if selfloops:
        print(f"  Removed {len(selfloops)} self-loops")
    
    print("\nGenerating figures...")
    
    print("  Figure 1: Degree distributions")
    fig1_degree_distribution(G)
    
    print("  Figure 2: Power-law fit")
    alpha, kmin = fig2_powerlaw_fit(G)
    
    print("  Figure 3: Community structure")
    n_comm, comm_sizes, modularity = fig3_community_structure(G)
    
    print("  Figure 4: Centrality correlations")
    centrality_tops = fig4_centrality_correlation(G)
    
    print("  Figure 5: k-core decomposition")
    max_core, core_counts = fig5_core_periphery(G)
    
    print("  Figure 6: Reciprocity and transitivity")
    recip, mean_cc, mean_br = fig6_reciprocity_transitivity(G)
    
    print("  Figure 7: Network overview")
    fig7_network_overview(G)
    
    print("  Figure 8: Assortativity")
    assort = fig8_assortativity(G)
    
    # Compute and save stats
    print("\nComputing statistics...")
    stats = compute_stats(
        G, alpha, kmin, n_comm, comm_sizes, modularity,
        max_core, core_counts, recip, mean_cc, mean_br, assort,
        centrality_tops
    )
    
    # Print summary
    print("\n=== Network Summary ===")
    for k, v in stats.items():
        if isinstance(v, float):
            print(f"  {k:30s}: {v:.4f}")
        else:
            print(f"  {k:30s}: {v}")
    
    print("\n=== Top Accounts by Centrality ===")
    for measure, tops in centrality_tops.items():
        print(f"\n  {measure}:")
        for handle, val in tops[:5]:
            print(f"    {handle:40s} {val:.4f}")
    
    # Write LaTeX stats
    write_latex_stats(stats, centrality_tops)
    
    # Save stats as JSON
    with open(DATA_DIR / "network_stats.json", "w") as f:
        json.dump(stats, f, indent=2, default=str)
    
    print("\nDone. Figures in paper/figures/, stats in data/")


if __name__ == "__main__":
    main()
