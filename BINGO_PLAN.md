# Bingo Card Generator — Plan

## Overview
New script `scripts/make_bingo.py` that scans a directory of images, picks a random subset,
and composites them into a 5x5 PNG grid with optional title and optional X marks. Fits the
repo's existing CLI-script pattern (cf. `check_membership.py`, `firehose_collect.py`).

## CLI Surface

```
python scripts/make_bingo.py <image_dir> [options]

Positional:
  image_dir                Directory to scan for images

Options:
  --title TEXT             Title text rendered above the grid (default: none)
  --title-font PATH        TTF/OTF file for title (default: PIL default font)
  --title-size N           Title font size in px (default: 64)
  --title-color COLOR      Title color (default: black)
  --center-image PATH      Image to place in center cell (row 2, col 2)
  --seed N                 RNG seed for reproducible output
  --mark-x ROW,COL         Mark a square with an X. Repeatable. 0-indexed.
  --x-color COLOR          Color of the X marks (default: red)
  --x-width N              Stroke width of X marks in px (default: 12)
  --cell-size N            Px size of each square cell (default: 200)
  --padding N              Px padding around grid + between cells (default: 8)
  --background COLOR       Background color (default: white)
  --grid-line COLOR        Color of grid lines (default: black)
  --output PATH            Output PNG path (default: bingo_card.png)
  --exts .jpg,.png,...     Comma-sep list of allowed extensions (default: common)
  --quiet                  Suppress summary output
```

Color args accept any PIL-recognized string: `"red"`, `"#FF0000"`, `"rgb(255,0,0)"`, etc.

## Decisions Resolved
- **No BINGO column letters** — no header row, no side markers; just the grid + optional title
- **X coordinates**: 0-indexed `row,col` pairs, repeatable flag
- **Center square**: random image by default; only replaced if `--center-image` set
- **Library**: Pillow (PIL)
- **Output**: `--output PATH`, default `bingo_card.png` in current directory
- **No title strip** when `--title` is omitted — canvas is just the grid
- **Image scarcity**: no duplicates; if dir has fewer than needed, use what's there and leave
  remaining cells as blank background-colored squares with a stderr warning

## Implementation Outline

1. **Argparse** — flags above; `--mark-x` uses `action='append'` with a small `row,col` parser
   (raise `argparse.ArgumentTypeError` on malformed input, range-check 0-4).
2. **Seed** — `random.seed(args.seed)` immediately after parse.
3. **Scan directory** — `os.listdir`, filter by extension, sort for deterministic order before
   sampling (so seed is meaningful across runs).
4. **Sample** — need `N` = 25 slots (24 if `--center-image` given).
   `sample = random.sample(images, min(N, available))`. Error only if zero images found.
   Print stderr warning if `available < N`.
5. **Load and crop-to-square each image** — `Image.open`, center-crop to square via `min(w,h)`.
   Resize to `cell_size x cell_size`.
6. **Compute canvas** —
   - Grid total = `5*cell_size + 6*padding` (padding on each outer edge + between cells).
   - Title strip on top (only if `--title`): height = `title_size + padding`, text centered.
   - No title strip if `--title` omitted.
7. **Draw** —
   - Paste cells into positions 0..len(sample)-1. If `--center-image`, replace index 12 (center).
   - Remaining positions (if any) stay as background color.
   - Draw grid lines using `ImageDraw.rectangle` per cell.
   - Render title with `ImageFont.truetype(args.title_font, args.title_size)` if provided, else
     `ImageFont.load_default()`.
   - For each `--mark-x (r,c)`: draw two `ImageDraw.line` calls forming an X across that cell's
     bounding box, with `fill=args.x_color` and `width=args.x_width`.
8. **Save** — `img.save(args.output, "PNG")`.
9. **Print summary** — sample count, output path, seed used (so user can reproduce). Quiet if
   `--quiet`.

## Conventions to Match
- `#!/usr/bin/env python3` shebang
- Module docstring at top with usage examples (cf. `check_membership.py:1-14`)
- Use stdlib where possible (`os`, `argparse`, `random`, `pathlib`, `sys`); only external dep =
  `Pillow`
- Update `scripts/README.md` with a new section (cf. the `firehose_collect.py` and
  `check_membership.py` sections)
- Update top-level `README.md` repository-structure block to mention the new script

## Files to Add/Modify
| File | Change |
|------|--------|
| `scripts/make_bingo.py` | New — main script |
| `scripts/README.md` | Add `## make_bingo.py` section |
| `README.md` | Add line to repo-structure block |

## Out of Scope (could add later)
- Non-square output aspect ratio
- Multiple cards in one batch
- Title subtitle / footer
- Per-cell captions
- JPEG/WebP output (PNG only for now)

## Verification Plan
1. Create a temp dir with ~30 sample images (`/tmp/opencode/bingo_test/`).
2. Run basic: `python scripts/make_bingo.py /tmp/opencode/bingo_test/` -> check `bingo_card.png`
   exists, opens, is 5x5.
3. Run with title + seed: `--title "TEST" --seed 42` -> verify reproducible (rerun -> same pixels).
4. Run with `--mark-x 0,0 --mark-x 2,2 --x-color blue` -> verify X marks visible at correct cells.
5. Run with `--center-image <path>` -> verify center is replaced and only 24 random images used.
6. Run with too-few images -> verify warning printed and blank cells appear.
7. Run with bad `--mark-x 5,5` -> verify clean argparse error.
