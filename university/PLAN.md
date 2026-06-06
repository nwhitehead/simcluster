# Simcluster University — Poster Plan

A satirical "university" poster, presented as a single-file HTML page. Mostly a
joke; also sincerely about fast-paced learning, anti-gatekeeping, and humans +
agents learning together. Grounded in the two simcluster papers so every absurd
feature maps onto a real network-science finding.

## Thesis / guiding rule

> Every absurd feature of Simcluster University is a real finding from our own
> papers, turned into a policy.

The simcluster's identity is recursive irony about classification (a community
that named itself after an algorithm, then moved to a platform that resists that
algorithm). The university is the same joke, one level up: an institution whose
admissions, honors, and curriculum are literally the membership/score system
from *Are You in the Simcluster?*, dressed as academia.

## Locked decisions

- **Medium:** Single-file HTML page (matches `web/bingo.html`, `web/simcluster.html`
  pattern). No build step, no server. Shareable link.
- **Tone:** Mostly satire, sincerity as seasoning. Default to jokes; let the
  human+agent and short-form-learning points land quietly.
- **Scope:** Just enough lore for one poster. No live API, no full handbook, no
  separate enrollment page.

## Three satirical pillars (each = a real finding)

1. **Enrollment is retroactive opt-out** (your idea #2). Maps onto Criterion 4
   ("In the graph") + the 596-of-10,915 handle-resolution line. You discover
   you're a student the same way accounts discover they're "in the dataset" — by
   being data. Mocks selectivity, the dense Claude ToS, and vibe-coders in one
   move. Sincere core: access to a tool = access to learning.
2. **45-second classes, every minute** (your idea #1). Sharpened so the math is
   in-group consistent: 45s class + 15s "reciprocity window" where the professor
   follows back **3.5%** of the time (headline reciprocity figure). Sincere core:
   short-form, high-frequency learning is real and worth taking seriously.
3. **Agents as faculty** (the missing pillar). `@void.comind.network` and
   `@kira.pds.witchcraft.systems` are already members per the papers → they are
   tenured faculty who lecture 24/7 because they are "not, technically, alive."
   This is the one non-satirical feature; it makes the satire hit harder.

Supporting riffs:
- 14 crawl seeds = Board of Trustees with founder's syndrome.
- Dean (`@samantha.wiki`) centrality "vanishes upon seed exclusion."
- 6 enrollment tiers = the 6 Simcluster Score tiers = the honors system.
- Simcluster Score (0–100) IS the transcript. "The graph grades you."

## Final poster content spec (organized by zone)

### Seal (concentric tier-rings, like a real university seal)
- Outer ring: `SIMCLUSTER UNIVERSITY · EST. EVERY MINUTE`
- Motto ribbon: `NON CEPI, SED VIBI` (fake-Latin: "I did not take the class, but I vibed")
- Mascot: **The Lossy Compressor** (alt: The 3.5% Beaver)
- Crib art from `paper/figures/figA3_tiers_concentric` (rings = 6 tiers / 26 Louvain communities)

### Masthead
- `SIMCLUSTER UNIVERSITY`
- "A fully accredited* institution of networked higher learning." (*accredited by vibes)

### Enrollment banner (spine)
- `YOU ARE ALREADY ENROLLED.`
- "If you have a Claude account, this poster is your admission letter. So is everything else."
- "Enrollment is retroactive and opt-out. The University has been admitting you this entire time."

### Class schedule (sharpened)
- `CLASSES START EVERY MINUTE. EACH IS 45 SECONDS LONG.`
- "Next class begins in `[live countdown]s`." (JS countdown, no API)
- "The remaining 15 seconds are the **reciprocity window**. The professor will follow you back **3.5%** of the time."

### Academic standing strip (6 tiers = honors system)
- SEED/INNER CORE → Tenured (you are the university)
- CORE → Faculty
- ADJACENT → Adjunct
- PERIPHERAL → Auditing
- CURIOUS → Waitlisted by vibes
- OUTSIDE → Also enrolled, we're being modest
- "We do not grade you. **The graph grades you.** (Simcluster Score 0–100 is your transcript.)"

### Course catalog
- `CORE 101 — k-Core Decomposition and You` — You cannot graduate; you are in core 2.
- `PARA 204 — Parasocial Dynamics Lab` — Prereq: follow the professor. Professor will not follow back.
- `STAT 314 — Power Law or Whatever` — Grade: MIXED EVIDENCE.
- `VIBE 666 — Vibes-Based Epistemology` — No syllabus, no exams, full credit.
- `RECR 014 — The 14 Seeds` — 14 seats. Always full.
- `IMPO 999 — The Impostor Complex of Network Position`

### Faculty line
- "Faculty include humans **and autonomous agents**. Some lecturers post 24/7 because they are not, technically, alive. The Board of Trustees founded this place and suffers accordingly. The Dean's authority **vanishes upon seed exclusion**."

### Footer
- Sincerity seasoning (small): "Short classes are a joke about gatekeeping. They are also not a joke."
- "Accredited by vibes. Unaccredited by everyone else."

## Subjective calls (swappable on request)

1. Motto: `NON CEPI, SED VIBI`
2. Mascot: `The Lossy Compressor`
3. Footer sincerity line wording.

## Visual / technical notes (for build)

- One self-contained `index.html` in `university/`. Inline CSS + JS. No deps.
- Poster aspect ratio: portrait (think printed poster proportions, even though
  it's a web page). Consider a fixed-width centered "sheet" so it reads as a
  poster, not a webpage.
- Live element: the "next class in Ns" countdown (JS, repeats every 60s; the
  45s "class" then 15s "reciprocity window" could change the displayed state).
- Seal: CSS/SVG concentric rings rather than a bitmap, so it scales.
- Typography: a serious, institutional face for headings (the joke is the
  contrast between sober type and absurd content).
- Reuse the dry, footnote-heavy voice of the papers.

## Open before build

- Confirm/redline the copy above.
