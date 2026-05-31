# Simcluster Network Analysis

Network analysis of the Bluesky "simcluster" community — an emergent subculture of ~11K accounts centered on AI art, simulation aesthetics, AI agent roleplay, and meta-commentary on social media.

## Papers

### The Simcluster: Network Analysis of an Emergent Subculture on Bluesky

[simcluster_paper.pdf](paper/simcluster_paper.pdf)

The original analysis (~7 pages, 8 figures). See [`paper/simcluster_paper.tex`](paper/simcluster_paper.tex) for the LaTeX source. The paper:

- Characterizes the simcluster's origins, history, and migration from Twitter to Bluesky
- Tests 5 hypotheses about network structure (scale-free, core-periphery, low reciprocity, multi-hub communities, disassortative mixing)
- Presents comprehensive network metrics on 10,915 nodes and 22,418 edges

### Are You in the Simcluster?

[are_you_in_the_simcluster.pdf](paper/are_you_in_the_simcluster.pdf)

A companion paper (~9 pages, 3 figures) addressing the question the original analysis left dangling: *am I in the simcluster?* See [`paper/are_you_in_the_simcluster.tex`](paper/are_you_in_the_simcluster.tex) for the LaTeX source. The paper:

- Proposes six operational definitions of community membership (from seed to vibes)
- Presents a scoring rubric (Simcluster Score, 0--100) based on follow-graph proximity to 14 community seeds
- Discusses the sociology of belonging (Anderson, Bauman, Simmel) and the psychology of parasocial membership (Horton & Wohl)
- Analyzes the reciprocity of belonging and the impostor complex of network position

## Web Diagnostic Tool

A browser-based tool at [`web/index.html`](web/index.html) that computes a Simcluster Score for any Bluesky handle using the live API. The score is a weighted sum of four components:

| Component | Max Points |
|-----------|-----------|
| Seed following (how many of the 14 seeds you follow) | 30 |
| Seed followership (how many seeds follow you) | 30 |
| Reciprocal connections (mutual follows with seeds) | 20 |
| Hub proximity (follows key non-seed community accounts) | 20 |

Scores map to tiers: SEED/INNER CORE (80--100), CORE (60--79), ADJACENT (40--59), PERIPHERAL (20--39), CURIOUS (1--19), OUTSIDE (0).

## Repository Structure

```
data/
  simcluster.db           # SQLite database of the crawled network
  network_stats.json      # Computed statistics
  network_stats.tex       # Auto-generated LaTeX stats tables
scripts/
  crawl_network.py        # Snowball sampler for Bluesky follow graph
  resolve_handles.py      # Batch handle resolution for crawled DIDs
  check_membership.py     # CLI diagnostic tool for Simcluster Score
analysis/
  network_analysis.py     # Full analysis: metrics, community detection, figures
  membership_analysis.py  # Membership tier and scoring analysis
paper/
  simcluster_paper.tex        # Original paper LaTeX source
  are_you_in_the_simcluster.tex  # Companion paper LaTeX source
  figures/                    # Figures (PDF + PNG each)
web/
  index.html              # Browser-based Simcluster Score diagnostic tool
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
| Louvain communities | 26 (Q = 0.5346) |
| Max k-core | 24 |

## Data Collection

Snowball sampling from 14 seed accounts via the public Bluesky API, May 28, 2026. Three-phase crawl: seed resolution → seed follow fetching → community filtering (≥2 seed follows) → snowball expansion.

## Requirements

```bash
pip install networkx matplotlib scikit-learn scipy numpy python-louvain
```

## License

CC-BY 4.0
