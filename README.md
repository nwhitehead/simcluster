# Simcluster Network Analysis

Network analysis of the Bluesky "simcluster" community — an emergent subculture of ~11K accounts centered on AI art, simulation aesthetics, AI agent roleplay, and meta-commentary on social media.

## Paper

See [`paper/simcluster_paper.tex`](paper/simcluster_paper.tex) for the full LaTeX manuscript (~7 pages, 8 figures). The paper:

- Characterizes the simcluster's origins, history, and migration from Twitter to Bluesky
- Tests 5 hypotheses about network structure (scale-free, core-periphery, low reciprocity, multi-hub communities, disassortative mixing)
- Presents comprehensive network metrics on 10,915 nodes and 22,418 edges

## Repository Structure

```
data/
  simcluster.db           # SQLite database of the crawled network
  network_stats.json      # Computed statistics
  network_stats.tex       # Auto-generated LaTeX stats tables
scripts/
  crawl_network.py        # Snowball sampler for Bluesky follow graph
  resolve_handles.py      # Batch handle resolution for crawled DIDs
analysis/
  network_analysis.py     # Full analysis: metrics, community detection, figures
paper/
  simcluster_paper.tex    # LaTeX manuscript
  figures/                # 8 figures (PDF + PNG each)
```

## Key Findings

| Metric | Value |
|--------|-------|
| Nodes / Edges | 10,915 / 22,418 |
| Density | 0.00019 |
| Reciprocity | 3.45% |
| Mean clustering | 0.131 |
| Degree assortativity | -0.090 |
| Power-law α (in-degree) | 2.50 |
| Louvain communities | 26 (Q = 0.535) |
| Max k-core | 24 |

## Data Collection

Snowball sampling from 14 seed accounts via the public Bluesky API, May 28, 2026. Three-phase crawl: seed resolution → seed follow fetching → community filtering (≥2 seed follows) → snowball expansion.

## Requirements

```bash
pip install networkx matplotlib scikit-learn scipy numpy python-louvain
```

## License

CC-BY 4.0
