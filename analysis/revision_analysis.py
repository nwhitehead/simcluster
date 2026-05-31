#!/usr/bin/env python3
"""
Revision analysis for simcluster peer review.
Implements: CSN power-law fit, seed-excluded centrality, seed-proximity
correlation, reciprocity sensitivity, handle-resolution bias check.
"""
import sqlite3, json, sys, math
from pathlib import Path
from collections import defaultdict, Counter

import networkx as nx
import numpy as np
from scipy import stats
from scipy.special import zeta as zeta_func
from scipy.optimize import minimize_scalar

DATA_DIR = Path(__file__).parent.parent / "data"
FIG_DIR = Path(__file__).parent.parent / "paper" / "figures"
FIG_DIR.mkdir(exist_ok=True)

# ── DATA LOAD ─────────────────────────────────────────────────────────

def load_graph(db_path):
    db = sqlite3.connect(db_path)
    G = nx.DiGraph()
    for src, tgt in db.execute("SELECT source, target FROM follows").fetchall():
        G.add_edge(src, tgt)
    for row in db.execute(
        "SELECT did, handle, display_name, description, follows_count, "
        "followers_count, posts_count, is_seed FROM actors WHERE handle IS NOT NULL"
    ):
        did, handle, dn, desc, fc, fwc, pc, is_seed = row
        if did in G:
            nd = G.nodes[did]
            nd["handle"], nd["display_name"], nd["description"] = handle, dn or "", desc or ""
            nd["follows_count"], nd["followers_count"], nd["posts_count"] = fc or 0, fwc or 0, pc or 0
            nd["is_seed"] = bool(is_seed)
    db.close()
    # Remove isolates and self-loops
    G.remove_nodes_from([n for n in G.nodes if G.degree(n) == 0])
    G.remove_edges_from(list(nx.selfloop_edges(G)))
    return G


# ── 1. CLAUSET-SHALIZI-NEWMAN POWER-LAW FIT ───────────────────────────

def csn_fit(data, xmin_candidates=None):
    """MLE fit + KS goodness-of-fit + LR test vs log-normal."""
    data = np.array([x for x in data if x > 0], dtype=float)
    n = len(data)
    if n < 20:
        return None

    unique_vals = np.unique(data)
    if xmin_candidates is None:
        xmin_candidates = unique_vals[unique_vals <= np.percentile(data, 95)]

    best_xmin, best_alpha, best_ks = None, None, float('inf')
    best_fit_info = None

    for xmin in xmin_candidates:
        tail = data[data >= xmin]
        nt = len(tail)
        if nt < 10:
            continue
        # MLE for continuous power law: α = 1 + n / Σ ln(x_i / xmin)
        alpha = 1 + nt / np.sum(np.log(tail / xmin))
        if alpha <= 1:
            continue
        # KS statistic
        tail_sorted = np.sort(tail)
        empirical_cdf = np.arange(1, nt + 1) / nt
        theoretical_cdf = 1 - (tail_sorted / xmin) ** (1 - alpha)
        ks = np.max(np.abs(empirical_cdf - theoretical_cdf))
        if ks < best_ks:
            best_ks = ks
            best_xmin = xmin
            best_alpha = alpha
            best_fit_info = {'n_tail': nt, 'xmin': xmin, 'alpha': alpha, 'ks': ks}

    if best_fit_info is None:
        return None

    # GOF via bootstrap
    n_bootstrap = 500
    ks_bootstrap = []
    tail_size = best_fit_info['n_tail']
    alpha_hat = best_fit_info['alpha']
    xmin_hat = best_fit_info['xmin']

    for _ in range(n_bootstrap):
        synthetic = ((np.random.random(tail_size) ** (1/(1-alpha_hat))) * xmin_hat)
        synthetic_tail = synthetic[synthetic >= xmin_hat]
        if len(synthetic_tail) < 10:
            ks_bootstrap.append(0)
            continue
        alpha_bs = 1 + len(synthetic_tail) / np.sum(np.log(synthetic_tail / xmin_hat))
        synthetic_sorted = np.sort(synthetic_tail)
        emp_bs = np.arange(1, len(synthetic_tail)+1) / len(synthetic_tail)
        theo_bs = 1 - (synthetic_sorted / xmin_hat) ** (1 - alpha_bs)
        ks_bootstrap.append(np.max(np.abs(emp_bs - theo_bs)))

    p_value = np.mean(np.array(ks_bootstrap) >= best_ks)

    # LR test vs log-normal
    tail = data[data >= xmin_hat]
    mu_ln = np.mean(np.log(tail))
    sigma_ln = np.std(np.log(tail), ddof=1)
    ll_power = np.sum(np.log((alpha_hat - 1) / xmin_hat) - alpha_hat * np.log(tail / xmin_hat))
    ll_ln = np.sum(stats.lognorm.logpdf(tail, sigma_ln, scale=np.exp(mu_ln)))
    lr = ll_power - ll_ln
    # Vuong-like: compute variance of log-likelihood ratio
    lr_i = (np.log((alpha_hat - 1) / xmin_hat) - alpha_hat * np.log(tail / xmin_hat)
            - stats.lognorm.logpdf(tail, sigma_ln, scale=np.exp(mu_ln)))
    se = np.sqrt(len(tail)) * np.std(lr_i, ddof=1)
    r_stat = lr / se if se > 0 else 0
    # Two-sided p: if r_stat > 0, power law favored; if < 0, log-normal favored
    lr_p = 2 * (1 - stats.norm.cdf(abs(r_stat))) if se > 0 else 1.0

    return {
        'alpha': float(alpha_hat), 'xmin': int(xmin_hat), 'n_tail': tail_size,
        'ks': float(best_ks), 'gof_p': float(p_value),
        'lr_vs_lognormal': float(r_stat),
        'lr_p': float(lr_p),
        'power_law_favored': r_stat > 0 and lr_p < 0.05,
        'log_normal_favored': r_stat < 0 and lr_p < 0.05,
        'conclusion': 'power_law' if (r_stat > 0 and lr_p < 0.05)
                      else 'log_normal' if (r_stat < 0 and lr_p < 0.05)
                      else 'indeterminate'
    }


# ── 2. SEED-EXCLUDED CENTRALITY ───────────────────────────────────────

def compute_seed_excluded_centrality(G):
    """Compute all three centralities on full graph AND seed-excluded subgraph."""
    seeds = {n for n in G.nodes if G.nodes[n].get("is_seed")}
    non_seeds = set(G.nodes) - seeds
    G_ns = G.subgraph(non_seeds).copy()

    results = {'seeds': sorted(seeds), 'n_seeds': len(seeds),
               'full': {}, 'seed_excluded': {}}

    def compute_all(G_sub, label):
        # Betweenness
        k_sample = min(500, len(G_sub))
        bet = nx.betweenness_centrality(G_sub, k=k_sample, seed=42)
        pr = nx.pagerank(G_sub, alpha=0.85)

        # Eigenvector on largest undirected CC
        U = G_sub.to_undirected()
        uccs = list(nx.connected_components(U))
        lcc = max(uccs, key=len)
        U_lcc = U.subgraph(lcc).copy()
        try:
            eig = nx.eigenvector_centrality_numpy(U_lcc, max_iter=200)
        except (nx.AmbiguousSolution, nx.PowerIterationFailedConvergence):
            eig = {n: pr.get(n, 0) for n in G_sub.nodes}

        def top(n_measure, n=15):
            r = []
            for node, val in sorted(n_measure.items(), key=lambda x: x[1], reverse=True)[:n]:
                h = G.nodes[node].get("handle", node[:24])
                r.append({'handle': h, 'did': node, 'value': float(val),
                          'is_seed': G.nodes[node].get('is_seed', False)})
            return r

        results[label] = {
            'betweenness': top(bet),
            'eigenvector': top(eig),
            'pagerank': top(pr),
        }

    compute_all(G, 'full')
    compute_all(G_ns, 'seed_excluded')
    return results


# ── 3. SEED PROXIMITY CORRELATION ─────────────────────────────────────

def compute_seed_proximity(G):
    """Correlate centrality vs hop-distance from nearest seed."""
    seeds = {n for n in G.nodes if G.nodes[n].get("is_seed")}
    non_seeds = set(G.nodes) - seeds

    # BFS from all seeds simultaneously
    UG = G.to_undirected()
    hop_distance = {}
    from collections import deque
    q = deque()
    for s in seeds:
        if s in UG:
            hop_distance[s] = 0
            q.append(s)

    while q:
        u = q.popleft()
        for v in UG.neighbors(u):
            if v not in hop_distance:
                hop_distance[v] = hop_distance[u] + 1
                q.append(v)

    # Centralities
    bet = nx.betweenness_centrality(G, k=500, seed=42)
    pr = nx.pagerank(G, alpha=0.85)

    U = G.to_undirected()
    uccs = list(nx.connected_components(U))
    lcc = max(uccs, key=len)
    U_lcc = U.subgraph(lcc).copy()
    try:
        eig = nx.eigenvector_centrality_numpy(U_lcc, max_iter=200)
    except:
        eig = {n: pr.get(n, 0) for n in G.nodes}

    # Correlate for non-seed nodes
    measures = {'betweenness': bet, 'eigenvector': eig, 'pagerank': pr}
    results = {}
    for name, measure in measures.items():
        nodes_with_both = [n for n in non_seeds if n in measure and n in hop_distance]
        if len(nodes_with_both) < 10:
            results[name] = {'n': 0, 'spearman_r': 0, 'spearman_p': 1}
            continue
        dists = [hop_distance[n] for n in nodes_with_both]
        vals = [measure[n] for n in nodes_with_both]
        r, p = stats.spearmanr(dists, vals)
        results[name] = {'n': len(nodes_with_both), 'spearman_r': float(r),
                         'spearman_p': float(p)}

    # Distribution of hop distances
    from collections import Counter
    dist_counts = dict(Counter(hop_distance.values()))
    return {'hop_distribution': dist_counts, 'correlations': results,
            'nodes_mapped': len(hop_distance), 'fraction_mapped': len(hop_distance) / G.number_of_nodes()}


# ── 4. RECIPROCITY SENSITIVITY ────────────────────────────────────────

def reciprocity_sensitivity(G):
    """Compute reciprocity on full graph vs truncation-free subset."""
    full_recip = nx.overall_reciprocity(G)

    max_fetch_limit = 300
    truncated = set()
    non_truncated = set()
    for n in G.nodes:
        fc = G.nodes[n].get('follows_count', 0)
        if fc >= max_fetch_limit:
            truncated.add(n)
        else:
            non_truncated.add(n)

    # Subset where NEITHER side is truncated
    edges_clean = [(u, v) for u, v in G.edges()
                   if u in non_truncated and v in non_truncated]
    G_clean = nx.DiGraph()
    G_clean.add_edges_from(edges_clean)

    # Also: subset where source is non-truncated (outgoing edges are complete)
    edges_src_clean = [(u, v) for u, v in G.edges() if u in non_truncated]
    G_src_clean = nx.DiGraph()
    G_src_clean.add_edges_from(edges_src_clean)

    results = {
        'full_graph': {'reciprocity': float(full_recip), 'n_edges': G.number_of_edges()},
        'neither_truncated': {
            'reciprocity': float(nx.overall_reciprocity(G_clean)) if G_clean.number_of_edges() > 0 else 0,
            'n_edges': G_clean.number_of_edges(),
            'n_truncated_skipped': len(truncated),
        },
        'source_not_truncated': {
            'reciprocity': float(nx.overall_reciprocity(G_src_clean)) if G_src_clean.number_of_edges() > 0 else 0,
            'n_edges': G_src_clean.number_of_edges(),
        }
    }
    # Bounded estimate: lower bound = full (deflated by truncation), upper bound = neither_truncated
    results['lower_bound'] = results['full_graph']['reciprocity']
    results['upper_bound'] = results['neither_truncated']['reciprocity']
    return results


# ── 5. HANDLE RESOLUTION BIAS ─────────────────────────────────────────

def handle_resolution_bias(G):
    """Compare resolved vs unresolved nodes on degree, centrality, seed-proximity."""
    resolved = {n for n in G.nodes if G.nodes[n].get("handle")}
    unresolved = set(G.nodes) - resolved
    seeds = {n for n in G.nodes if G.nodes[n].get("is_seed")}

    # Degree stats
    def deg_stats(nodeset):
        in_d = [d for n, d in G.in_degree() if n in nodeset]
        out_d = [d for n, d in G.out_degree() if n in nodeset]
        return {
            'n': len(nodeset),
            'mean_in_deg': float(np.mean(in_d)) if in_d else 0,
            'mean_out_deg': float(np.mean(out_d)) if out_d else 0,
            'median_in_deg': float(np.median(in_d)) if in_d else 0,
        }

    # Seed proximity
    from collections import deque
    UG = G.to_undirected()
    hop_distance = {}
    q = deque()
    for s in seeds:
        if s in UG:
            hop_distance[s] = 0
            q.append(s)

    while q:
        u = q.popleft()
        for v in UG.neighbors(u):
            if v not in hop_distance:
                hop_distance[v] = hop_distance[u] + 1
                q.append(v)

    def hop_stats(nodeset):
        dists = [hop_distance.get(n, float('inf')) for n in nodeset]
        finite = [d for d in dists if d < float('inf')]
        return {
            'n': len(nodeset),
            'mean_hop': float(np.mean(finite)) if finite else None,
            'fraction_reachable': len(finite) / len(nodeset) if nodeset else 0,
            'seed_fraction': len(nodeset & seeds) / len(nodeset) if nodeset else 0,
        }

    return {
        'resolved': {'degree': deg_stats(resolved), 'hop': hop_stats(resolved),
                     'n': len(resolved)},
        'unresolved': {'degree': deg_stats(unresolved), 'hop': hop_stats(unresolved),
                       'n': len(unresolved)},
    }


# ── MAIN ──────────────────────────────────────────────────────────────

def main():
    db_path = str(DATA_DIR / "simcluster.db")
    print(f"Loading graph from {db_path}...")
    G = load_graph(db_path)
    print(f"  {G.number_of_nodes():,} nodes, {G.number_of_edges():,} edges")

    results = {}

    # 1. CSN power-law fit
    print("\n=== 1. CSN Power-Law Fit ===")
    in_degrees = np.array([d for _, d in G.in_degree() if d > 0])
    csn = csn_fit(in_degrees)
    if csn:
        print(f"  α = {csn['alpha']:.3f}, xmin = {csn['xmin']}, n_tail = {csn['n_tail']}")
        print(f"  GOF p = {csn['gof_p']:.4f}, LR vs log-normal: R = {csn['lr_vs_lognormal']:.3f}, p = {csn['lr_p']:.4f}")
        print(f"  Conclusion: {csn['conclusion']}")
    else:
        print("  CSN fit failed — insufficient data")
    results['csn_powerlaw'] = csn

    # 2. Seed-excluded centrality
    print("\n=== 2. Seed-Excluded Centrality ===")
    cent = compute_seed_excluded_centrality(G)
    for label in ['full', 'seed_excluded']:
        print(f"\n  {label.upper()}:")
        for measure in ['betweenness', 'eigenvector', 'pagerank']:
            print(f"    {measure}:")
            for entry in cent[label][measure][:5]:
                seed_tag = " [SEED]" if entry.get('is_seed') else ""
                print(f"      {entry['handle']:<35s} {entry['value']:.6f}{seed_tag}")
    results['seed_excluded_centrality'] = cent

    # 3. Seed proximity correlation
    print("\n=== 3. Seed Proximity Correlation ===")
    prox = compute_seed_proximity(G)
    print(f"  Nodes mapped: {prox['nodes_mapped']} ({prox['fraction_mapped']:.1%})")
    for measure, r in prox['correlations'].items():
        print(f"  {measure}: Spearman r = {r['spearman_r']:.4f}, p = {r['spearman_p']:.4e}, n = {r['n']}")
    print(f"  Hop distribution: {dict(sorted(prox['hop_distribution'].items())[:10])}")
    results['seed_proximity'] = prox

    # 4. Reciprocity sensitivity
    print("\n=== 4. Reciprocity Sensitivity ===")
    recip = reciprocity_sensitivity(G)
    print(f"  Full graph:        ρ = {recip['full_graph']['reciprocity']:.4f} ({recip['full_graph']['n_edges']:,} edges)")
    print(f"  Neither truncated: ρ = {recip['neither_truncated']['reciprocity']:.4f} ({recip['neither_truncated']['n_edges']:,} edges)")
    print(f"  Source non-trunc:  ρ = {recip['source_not_truncated']['reciprocity']:.4f} ({recip['source_not_truncated']['n_edges']:,} edges)")
    print(f"  Bounded estimate: [{recip['lower_bound']:.4f}, {recip['upper_bound']:.4f}]")
    results['reciprocity_sensitivity'] = recip

    # 5. Handle resolution bias
    print("\n=== 5. Handle Resolution Bias ===")
    hb = handle_resolution_bias(G)
    for label in ['resolved', 'unresolved']:
        info = hb[label]
        d = info['degree']
        h = info['hop']
        print(f"  {label} ({info['n']} nodes):")
        print(f"    Degree: mean_in={d['mean_in_deg']:.2f}, mean_out={d['mean_out_deg']:.2f}")
        print(f"    Hop: mean={h['mean_hop']:.2f}, reachable={h['fraction_reachable']:.1%}, seed_frac={h['seed_fraction']:.1%}")
    results['handle_resolution_bias'] = hb

    # Save results
    out_path = DATA_DIR / "revision_results.json"
    with open(out_path, 'w') as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")

    return results


if __name__ == "__main__":
    main()
