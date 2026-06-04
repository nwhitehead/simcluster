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
  --title-font PATH        TTF/OTF file for title. REQUIRED if --title is given
                           (PIL's default bitmap font ignores --title-size and
                           will produce a tiny unreadable title).
  --title-size N           Title font size in px (default: 64)
  --title-color COLOR      Title color (default: black)
  --center-image PATH      Image to place in center cell (row 2, col 2)
  --seed N                 RNG seed for reproducible output
  --mark-x ROW,COL         Mark a square with an X. Repeatable. 0-indexed.
                           Safe to combine with --center-image on the same cell;
                           X is drawn on top of the pasted center image.
  --x-color COLOR          Color of the X marks (default: red)
  --x-width N              Stroke width of X marks in px (default: 12)
  --cell-size N            Px size of each square cell (default: 200)
  --padding N              Px padding around grid + between cells (default: 8)
  --background COLOR       Background color (default: white)
  --grid-line COLOR        Color of grid lines (default: black)
  --output PATH            Output PNG path (default: bingo_card.png)
  --exts .jpg,.png,...     Comma-sep list of allowed extensions
                           (default: .jpg,.jpeg,.png,.gif,.webp,.bmp;
                           case-insensitive; leading dot required)
  --quiet                  Suppress summary output

Directory scanning is non-recursive (top level of <image_dir> only).
```

Color args accept any PIL-recognized string: `"red"`, `"#FF0000"`, `"rgb(255,0,0)"`, etc.

## Decisions Resolved
- **No BINGO column letters** — no header row, no side markers; just the grid + optional title
- **X coordinates**: 0-indexed `row,col` pairs, repeatable flag; drawn last (on top of pasted
  images, grid lines, and center image)
- **Center square**: random image by default; only replaced if `--center-image` set. When set,
  24 images are sampled and placed at positions `[0..11, 13..24]`; position 12 is reserved for
  the center image (so no random image is "wasted" by being overwritten).
- **Library**: Pillow (PIL). New top-level dependency; add to README `## Requirements` line.
- **Canvas mode**: `RGB`. All source images converted via `Image.convert("RGB")` after EXIF
  transpose, so PNGs with alpha channels composite correctly onto the background.
- **EXIF orientation**: always call `ImageOps.exif_transpose(img)` after `Image.open`, so
  phone-camera JPEGs render upright.
- **Title font**: `--title-font` is required when `--title` is given. PIL's `load_default()`
  ignores `--title-size` and would produce unreadable output; rejecting the combination is
  cleaner than silently rendering a 10px title.
- **Title strip sizing**: height and width are computed from `font.getbbox(title)` (actual
  rendered metrics), not from `--title-size` alone. If title is wider than the grid, the strip
  width grows to fit (canvas width = `max(grid_width, title_width + 2*padding)`); the grid is
  centered horizontally within the wider canvas.
- **Output**: `--output PATH`, default `bingo_card.png` in current directory
- **No title strip** when `--title` is omitted — canvas is just the grid
- **Image scarcity**: no duplicates; if dir has fewer than needed, use what's there and leave
  remaining cells as blank background-colored squares with a stderr warning
- **Directory scan**: non-recursive (`os.listdir` on `<image_dir>` only); files in subdirectories
  are ignored. Keeps the common case simple; users with nested dirs can flatten first.
- **README repo-structure block**: this block is already stale (missing `firehose_collect.py`
  and `crawl_active_users.py`). Update it to reflect *all* current scripts, including
  `make_bingo.py`, rather than appending one more line to an incomplete list.

## Implementation Outline

1. **Argparse** — flags above; `--mark-x` uses `action='append'` with a small `row,col` parser
   (raise `argparse.ArgumentTypeError` on malformed input, range-check 0-4). Validation:
   if `args.title` is set and `args.title_font` is None, exit with a clear error message
   ("--title requires --title-font").
2. **Seed** — `random.seed(args.seed)` immediately after parse (works fine when seed is None;
   seeds from system time).
3. **Scan directory** — `os.listdir(image_dir)`, filter by extension (case-insensitive, leading
   dot required, against the parsed `--exts` set), sort for deterministic order before sampling
   (so seed is meaningful across runs). Non-recursive: skip subdirectories.
4. **Sample** — need `N` = 25 slots (24 if `--center-image` given).
   `sample = random.sample(images, min(N, available))`. Error only if zero images found.
   Print stderr warning if `available < N`.
5. **Load and crop-to-square each image** — for each path, use `with Image.open(p) as img:` to
   avoid file-descriptor leaks, then in order:
   - `img = ImageOps.exif_transpose(img)` (apply camera orientation)
   - `img = img.convert("RGB")` (flatten alpha onto white; ensures RGBA PNGs composite cleanly)
   - Center-crop to square via `min(w,h)`
   - Resize to `cell_size x cell_size` (`Image.Resampling.LANCZOS`)
6. **Compute canvas** —
   - Grid total = `5*cell_size + 6*padding` (padding on each outer edge + between cells).
   - Title strip on top (only if `--title`): load font via
     `ImageFont.truetype(args.title_font, args.title_size)`; compute rendered bbox with
     `font.getbbox(title)`; strip height = `bbox_height + 2*padding`. If title is wider than
     the grid, canvas width grows to fit: `canvas_w = max(grid_width, bbox_width + 2*padding)`,
     and the grid is horizontally centered within the wider canvas.
   - No title strip if `--title` omitted; canvas width = grid width.
   - Canvas mode: `"RGB"`, filled with `args.background`.
7. **Draw** —
   - Compute paste positions for each grid index `i` (0..24):
     `row, col = divmod(i, 5)`, then `x = grid_origin_x + padding + col*(cell_size + padding)`,
     `y = grid_origin_y + padding + row*(cell_size + padding)` where `grid_origin_x` is the
     horizontal centering offset when title is wider than grid.
   - Build a list of 25 target indices: if `--center-image`, the 24 samples go to
     `[0..11, 13..24]` and index 12 gets the center image; otherwise all 25 samples go to
     `[0..24]`. Remaining positions (when fewer samples than slots) stay as background color.
   - Paste each prepared cell at its target position.
   - Draw grid lines: 6 horizontal lines + 6 vertical lines spanning the grid bounding box
     (avoids double-thick interior lines from per-cell `rectangle` calls).
   - Render title with `draw.text(...)`, centered horizontally in the title strip.
   - For each `--mark-x (r,c)` (drawn last, on top of everything): compute the cell's bounding
     box and draw two `ImageDraw.line` calls forming an X with `fill=args.x_color` and
     `width=args.x_width`. Safe to mark the center cell when `--center-image` is set.
8. **Save** — `img.save(args.output, "PNG")`.
9. **Print summary** — sample count, output path, seed used (so user can reproduce). Quiet if
   `--quiet`.

## Conventions to Match
- `#!/usr/bin/env python3` shebang
- Module docstring at top with usage examples (cf. `check_membership.py:1-14`)
- Use stdlib where possible (`os`, `argparse`, `random`, `pathlib`, `sys`); only external dep =
  `Pillow`
- CLI argument parsing via `argparse` (cf. `firehose_collect.py:694-724` — *not*
  `check_membership.py`, which does manual `sys.argv` parsing)
- Update `scripts/README.md` with a new `## make_bingo.py` section (cf. the `firehose_collect.py`
  section's structure: Quick Start → How It Works → CLI Arguments table)
- Update top-level `README.md`:
  - Refresh the repository-structure block so it lists **all** current scripts (the block is
    already missing `firehose_collect.py` and `crawl_active_users.py`)
  - Add `Pillow` to the `## Requirements` install line

## Files to Add/Modify
| File | Change |
|------|--------|
| `scripts/make_bingo.py` | New — main script |
| `scripts/README.md` | Add `## make_bingo.py` section |
| `README.md` | Refresh repository-structure block (all scripts); add `Pillow` to Requirements |

## Out of Scope (could add later)
- Non-square output aspect ratio
- Multiple cards in one batch
- Title subtitle / footer
- Per-cell captions
- JPEG/WebP output (PNG only for now)

## Verification Plan
1. Create a temp dir with ~30 sample images (`/tmp/opencode/bingo_test/`). Include a mix of
   aspect ratios (square, wide, tall) and at least one RGBA PNG with transparency.
2. Run basic: `python scripts/make_bingo.py /tmp/opencode/bingo_test/` -> check `bingo_card.png`
   exists, opens, is 5x5.
3. Run with title + seed: `--title "TEST" --title-font <path> --seed 42` -> verify reproducible
   (rerun -> same pixels).
4. Run with `--mark-x 0,0 --mark-x 2,2 --x-color blue` -> verify X marks visible at correct cells.
5. Run with `--center-image <path>` -> verify center is replaced, only 24 random images used,
   and position 24 (bottom-right) is NOT blank.
6. Run with too-few images (e.g. 10 in dir) -> verify warning printed and blank cells appear.
7. Run with bad `--mark-x 5,5` -> verify clean argparse error.
8. Run with `--title "Hello"` but no `--title-font` -> verify clean error message refusing to
   render with PIL default font.
9. Run with a non-square source image -> verify center-crop produces a square cell (no distortion).
10. Run with `--mark-x 2,2 --center-image <path>` together -> verify X is drawn on top of the
    center image.
11. Run with an empty directory -> verify clean error (no images found).
12. Run with a directory containing non-image files (e.g. `.txt`, subdirectory) -> verify they
    are skipped silently and don't break the run.
13. Run with a camera JPEG that has EXIF orientation set -> verify image renders upright
    (not rotated/mirrored).
14. Run with a PNG that has an alpha channel -> verify it composites onto the background
    cleanly (no black boxes where transparent pixels were).
