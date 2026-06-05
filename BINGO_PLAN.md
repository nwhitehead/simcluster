# Bingo Card Creator — Plan

## Overview
A standalone single-file HTML page at `web/bingo.html` that lets a user build a bingo card from Bluesky posts. No build step, no server. Talks directly to the Bluesky public API at `public.api.bsky.app`, which supports CORS for browser-side fetches.

Lives here as a side project / piece of repo performance art, unrelated to the rest of the analysis code.

## UI Layout
- **Title bar**: editable text input at the top of the page (default value `BINGO`).
- **Mode badge**: shows the current click mode + a one-line hint describing what clicking will do.
- **Toolbar**: contains a **Shuffle all** button and a small keyboard cheat-sheet.
- **5×5 grid** of square cells. The center cell is pre-rendered as a **FREE** space and counts as already marked.
- **Status line**: per-cell loading spinners and transient error messages.

## Click Modes (single-letter keyboard toggles)
| Key | Mode    | Behavior on filled cell                            | On blank cell        |
|-----|---------|-----------------------------------------------------|----------------------|
| F   | Fill    | No-op                                               | Open handle-entry modal |
| T   | Toggle  | Add/remove big X overlay                            | No-op                |
| E   | Erase   | Clear cell back to blank                            | No-op                |
| R   | Repick  | Re-fetch + pick a different random post from handle | No-op                |

- `F` is the default mode at startup.
- The active mode is reflected in the badge and changes the cell cursor style for clarity.

## Cell Data Model
```js
{
  handle, did,
  postUri, postCid,        // used by Repick to avoid picking the same post twice
  type: "text" | "image",
  text,                    // short phrase, if type === "text"
  imageUrl,                // CDN URL, if type === "image"
  marked: false            // big X overlay
}
```

## Handle-Entry Modal
An in-page modal (not the browser's native `prompt()`) opens when a blank cell is clicked in Fill mode. It contains:
- A text input for the bsky handle.
- Submit and Cancel buttons.
- Closes on Enter (submit) or Escape (cancel).

The modal is styled to match the card.

## Bluesky Fetch Flow (per cell)
1. **Get handle** from the modal input. Strip a leading `@` if present.
2. **Resolve DID**:
   - If input starts with `did:plc:` (or `did:web:`), use it directly.
   - Otherwise call `GET https://public.api.bsky.app/xrpc/com.atproto.identity.resolveHandle?handle={h}`.
3. **Fetch feed**: `GET https://public.api.bsky.app/xrpc/app.bsky.feed.getAuthorFeed?actor={did}&limit=50`.
4. **Filter to original posts only**: keep entries where
   - `reply == null` (not a reply), AND
   - `reason == undefined` (not a repost), AND
   - `post.author.did == did` (own post, not a quoted/reposted author).
5. **Pick**: random index from the filtered list. If the cell already has a `postUri` (i.e. Repick), exclude that one to avoid showing the same post twice in a row. If exclusion would leave the list empty (single-post handle after filtering), just re-render the existing post.
6. **Render** based on `post.embed`:
   - If `embed.$type === "app.bsky.embed.images#view"` and `embed.images.length > 0` → use `images[0].thumb` (CDN URL, already square-cropped). Apply CSS `object-fit: cover` as a safety net.
   - Otherwise (text-only post, or non-image embed) → run the **Phrase Extractor** below. If the extractor returns `null`, retry with a different random post up to **5 attempts** before showing a transient error in the cell.

## Phrase Extractor (text posts)
1. Take `post.record.text`.
2. Strip: handles (`/@[\w.-]+/g`), bare URLs (`/https?:\/\/\S+/g`), leading hashtags (`/^#\w+\s+/`).
3. Split on sentence boundaries: `/[\.\!\?\n]+/`.
4. Filter clauses to those with **4–12 words** and **≤80 chars**.
5. Prefer the shortest matching clause. If multiple have the same length, take the first.
6. If no clauses match, return `null` (caller will repick).
7. Final result is trimmed and used as the cell's `text`. CSS handles wrapping via `word-break: break-word`.

## Visual / Styling
- Responsive square grid via `display: grid; grid-template-columns: repeat(5, 1fr)` with `aspect-ratio: 1` on each cell.
- **Blank cells**: faint `+` centered, hover highlight.
- **Text cells**: centered, padded, `word-break: break-word`, clamped font size for long phrases.
- **Image cells**: `<img>` with `object-fit: cover; width:100%; height:100%`.
- **Marked state**: a pseudo-element overlay drawing a thick diagonal `X` in a contrasting color, with a slight desaturate filter on the underlying content.
- **FREE cell**: rendered as already-marked with the word `FREE` instead of an X.
- **Mode badge**: small color-coded pill in the top-right of the page.
- **Per-cell spinner**: small CSS spinner while a fetch is in flight.

## BINGO Detection
After every Toggle that changes a cell's `marked` state, check the 12 possible winning lines on the 5×5 grid (5 rows, 5 columns, 2 diagonals). The FREE center cell counts as marked.

When one or more complete lines exist:
- All cells on a completed winning line switch to a **winning** visual state: the X overlay scales up (roughly 1.2–1.5×), gains a soft colored `drop-shadow` glow, and the underlying content gets a slightly stronger desaturate.
- Multiple simultaneous winning lines stack visually (a cell on two winning lines still gets one winning state, not two stacked glows).
- State is purely derived from `marked` + position; no extra field on the cell. Recompute on every toggle.
- No celebratory animation, sound, or modal — the visual upgrade is the whole signal.

When a Toggle un-marks a cell and breaks the line, the winning state drops immediately.

## Toolbar: "Shuffle all"
- Iterates over every filled non-FREE cell and triggers a Repick in parallel.
- Disabled while any fetch is in flight, to avoid hammering the API.

## Error Handling
- Handle-resolution failure (404 / `null` DID) → transient "handle not found" message in the cell, then revert to blank.
- HTTP 429 or 5xx from Bluesky → transient "rate limited, retry later" message, cell reverts to blank.
- Network failure → same path as 429.
- All transient messages auto-clear after ~3 seconds.

## Out of Scope (explicitly excluded)
- No PNG export.
- No shareable URL state encoding.
- No dark-mode toggle.
- No grid-size selector (fixed at 5×5).

## Implementation Order
1. Skeleton HTML + grid CSS + editable title.
2. Cell rendering, click handling, mode system + keyboard listener + badge.
3. In-page handle-entry modal.
4. Bluesky fetch (resolve handle → feed → filter → pick).
5. Text rendering + phrase extractor.
6. Image rendering.
7. Mark/toggle/erase/repick behaviors.
8. Shuffle-all button.
9. Error handling + transient messages.
10. BINGO detection (winning-line computation + winning X styling).
11. Polish (spinner, hover states, font sizing).

## Verification Plan
1. Load the page → 5×5 grid renders, title bar shows `BINGO`, center cell shows `FREE` and is treated as marked, all other cells are blank with a `+`.
2. Press each mode key (`F`, `T`, `E`, `R`) → badge updates, cursor on cells changes to match.
3. Fill mode + click blank cell → modal opens, focus in input, Escape closes without effect, typing handle + Enter submits.
4. Submit a known-good handle (e.g. `bsky.app`) → cell populates with a phrase or image; spinner shows during fetch; cell data records `handle`, `did`, `postUri`, `postCid`.
5. Submit a `did:plc:…` directly → skips resolveHandle, populates cell.
6. Submit a nonexistent handle (e.g. `nope-no-such-user-xyzzy.bsky.social`) → cell shows transient "handle not found" message and reverts to blank after ~3s.
7. Submit a handle with only replies in its first 50 posts → after up to 5 attempts the cell shows a transient error and reverts to blank.
8. Toggle mode + click filled cell → X overlay appears. Toggle again → X disappears. Behavior is independent for each cell.
9. Erase mode + click filled cell → cell reverts to blank, cell data cleared.
10. Repick mode + click filled cell → re-fetches, different post appears (verify postUri changed).
11. Repick on a handle that produced only one valid post → cell re-renders the same post (no error, no empty state).
12. Fill 24 cells with various handles, mark 5 in a row → all 5 cells on that row shift to the winning X state (bigger, glowing). Un-mark one → winning state drops immediately.
13. Mark both diagonals including FREE → both diagonals show winning state simultaneously.
14. Click "Shuffle all" with 24 filled cells → all 24 re-fetch; button disabled until all complete; no cell is left in a half-updated state if a fetch fails mid-shuffle.
15. Open in a mobile-width viewport → grid stays square, cells remain tappable, mode badge readable. (Keyboard mode shortcuts unavailable on touch is acceptable for now.)
16. Rapid-fire click: open Fill modal on cell A, then before submitting click cell B in another mode → modal should not stack; either cell A's modal closes or cell B's click is ignored.
17. Network panel during Shuffle-all → requests are visible; verify they go to `public.api.bsky.app` and that no credentials/cookies are sent.
18. Reload the page → state is lost (expected, given Out of Scope). Verify clean re-init.
