# Crawl Active Bluesky Users to SQLite

Enumerate recently-active Bluesky users and their follow graphs into a SQLite database.

> **Note**: This script produces a separate database (`active_users.db`) from the existing
> `simcluster.db`. The schema uses `follower_did`/`followee_did` column names (instead of
> `source`/`target`) and adds `active_at`/`profiled`/`follows_crawled` tracking columns.

## Scope

- **Target**: Accounts with a repo commit (post, like, follow, repost, etc.) in the last 2 days
- **Expected volume**: 2-5M unique DIDs
- **Data collected**: Profile metadata + outgoing follow edges (who each user follows)
- **Storage estimate**: See [Storage Estimate](#storage-estimate) below

## Storage Estimate

Average Bluesky user follows ~200 accounts. The `follows` table stores each edge as:

- **Table data**: rowid (8 B) + follower_did (~44 B) + followee_did (~44 B) + overhead ≈ **100 B/row**
- **PK index** (follower_did, followee_did → rowid): ≈ **96 B/row**
- **Followee index** (followee_did → rowid): ≈ **52 B/row**
- **Total per edge**: ~248 B/row

The `actors` table averages ~400 B/row with profile data. All estimates include 20% overhead
for SQLite page fragmentation, WAL growth, and B-tree rebalancing during bulk inserts.

| Scenario | Active Users | Avg Follows | Edges | Follows (table + indexes) | Actors Table | **Total** |
|----------|-------------|-------------|-------|---------------------------|-------------|-----------|
| Low | 2M | 100 | 200M | ~50 GB | ~500 MB | **~50 GB** |
| Mid | 3M | 200 | 600M | ~150 GB | ~800 MB | **~150 GB** |
| High | 5M | 200 | 1B | ~250 GB | ~1.5 GB | **~250 GB** |

Budget an additional **20-30 GB** of free disk space for WAL checkpoints and temporary
B-tree rebalancing during bulk inserts.

If storage is a concern, use `--max-follows-per-user` (default 1000) to cap edges per user
and reduce the high end to ~80-100 GB.

## Approach

Single script (`scripts/crawl_active_users.py`) with `--phase` flags for each stage.

### Prerequisites: Verify Relay Access

Before starting, confirm that the relay endpoint is publicly accessible:

```bash
curl -s 'https://bsky.network/xrpc/com.atproto.sync.listRepos?limit=1' | python3 -m json.tool
```

If this returns an auth error or connection refusal, Phase 1 cannot proceed. Alternative relays
or an authenticated relay connection must be arranged first. Phases 2-3 use the public AppView
API (`public.api.bsky.app`) which does not require authentication.

### Phase 1: Enumerate Active DIDs (2-4 hours)

Paginate the relay's `com.atproto.sync.listRepos` endpoint, decode each repo's `rev` TID to a
timestamp, and keep only repos with rev within the last N days.

- **Endpoint**: `GET https://bsky.network/xrpc/com.atproto.sync.listRepos?limit=1000`
- **Pagination**: cursor-based, ~30K requests to cover ~30M total repos
- **TID decoding**: See [TID Decoding](#tid-decoding) below
- **Rate limit**: The relay (`bsky.network`) has separate and potentially stricter rate limits
  than the AppView. Phase 1 uses a conservative **100 req/min** single-threaded rate limit by
  default (no worker parallelism). Adjust with `--relay-rate-limit` if you know the relay's
  actual limits. Watch for HTTP 429 responses and increase the delay if hit.
- **Output**: Insert matching DIDs into `actors` table with `active_at` and `is_active = 1`
- **Resumable**: Cursor persisted in `crawl_state` table

> **Warning**: `listRepos` returns repos ordered by repo creation time, NOT by `rev` recency.
> A repo created 2 years ago that posted today will have a recent `rev` but appear early in
> pagination. You **cannot early-terminate** — you must scan ALL ~30M repos to find recently
> active ones. Do not add an optimization that stops when revs look "too old."

### Phase 2: Fetch Profiles (4-12 hours)

For each active DID from Phase 1, fetch profile metadata in batches.

- **Endpoint**: `GET https://public.api.bsky.app/xrpc/app.bsky.actor.getProfiles?actors=did:...` (batch of 25)
- **Parallelism**: Multi-threaded, configurable `--workers N` (default 10)
- **Rate limit**: Shared token-bucket at **500 req/min** (AppView allows ~3000 req/5min = 600 req/min;
  the 500 req/min budget leaves headroom for retries)
- **Fields stored**: `handle`, `display_name`, `description`, `follows_count`, `followers_count`, `posts_count`, `indexed_at`
- **Progress**: ~80K-200K batch requests for 2-5M users
- **Resumable**: Selects active actors where `profiled = 0` and processes in batches. On restart, automatically picks up unprofiled actors.

### Phase 3: Crawl Follow Lists (3-14 days)

For each active DID, paginate their outgoing follows and store as edges.

- **Endpoint**: `GET https://public.api.bsky.app/xrpc/app.bsky.graph.getFollows?actor={did}&limit=100`
- **Pagination**: cursor-based, 100 per page
- **Rate limit**: Shared token-bucket at **500 req/min** (same pool as Phase 2)
- **Parallelism**: Same multi-threaded worker pool
- **Scope**: Outgoing edges only (who each user follows). Followees are inserted into `actors`
  with DID only (`is_active = 0`) if not already present. These "ghost" actors are excluded from
  profiling and follow-crawling by the partial indexes (see schema below).
- **Cap**: `--max-follows-per-user 1000` (default). Accounts exceeding this are marked `follows_crawled = 1` after hitting the cap, preventing runaway pagination on bots/follow-back accounts.
- **Progress tracked per-DID** via `follows_crawled` flag so it resumes cleanly after crashes

**Timeline calculation**: 3M users × 200 avg follows = 6M pages at 100/page. At 500 req/min,
that's 6M / 500 = 12,000 min ≈ **8.3 days**. The range spans from the low estimate (~2.8 days)
to the high estimate (~14 days). If you need faster completion, increase `--workers` and the
rate limit budget proportionally (e.g., `--workers 20 --rate-limit 1000` cuts Phase 3 roughly
in half but requires careful monitoring for 429s).

### TID Decoding

The `rev` field in `listRepos` responses is a **TID** (Timestamp Identifier) — a 13-character
base32-sortable string encoding a microsecond-precision timestamp plus a per-clock-tick counter.
The ATProto TID spec is defined in
[`packages/atproto-server/src/tid.ts`](https://github.com/bluesky-social/atproto/blob/main/packages/atproto-server/src/tid.ts).

Decoding steps:

1. The TID is base32-encoded using the SRGB32 alphabet (`234567abcdefghijklmnopqrstuvwxyz`)
2. Decode the 13-character string to a 64-bit integer
3. Extract the top 53 bits as a microsecond-precision Unix timestamp (bottom 11 bits are the
   counter)
4. Convert to seconds: `timestamp_ms = tid_int >> 11 / 1_000_000`
5. Compare: `timestamp >= now - N days`

Example Python decoder:

```python
BASE32_CHARS = "234567abcdefghijklmnopqrstuvwxyz"

def decode_tid(tid_str: str) -> int:
    """Decode a TID string to a microsecond timestamp."""
    val = 0
    for c in tid_str:
        val = (val << 5) | BASE32_CHARS.index(c)
    return val >> 11  # strip 11-bit counter, get microsecond timestamp
```

## SQLite Schema

```sql
CREATE TABLE actors (
    did TEXT PRIMARY KEY,
    handle TEXT,
    display_name TEXT,
    description TEXT,
    follows_count INTEGER,
    followers_count INTEGER,
    posts_count INTEGER,
    active_at TEXT,
    indexed_at TEXT,
    is_active BOOLEAN DEFAULT 0,
    profiled BOOLEAN DEFAULT 0,
    follows_crawled BOOLEAN DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    last_error TEXT
);

CREATE INDEX idx_actors_handle ON actors(handle);

-- Partial indexes exclude ghost actors (is_active = 0) inserted by Phase 3.
-- This prevents resumption queries from attempting to profile or crawl
-- followees who were never detected as active.
CREATE INDEX idx_actors_profiled ON actors(profiled) WHERE profiled = 0 AND is_active = 1;
CREATE INDEX idx_actors_follows_crawled ON actors(follows_crawled) WHERE follows_crawled = 0 AND is_active = 1;

CREATE TABLE follows (
    follower_did TEXT,
    followee_did TEXT,
    PRIMARY KEY (follower_did, followee_did)
);

CREATE INDEX idx_follows_followee ON follows(followee_did);

CREATE TABLE crawl_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

**Column notes**:

- `is_active`: Set to `1` only for DIDs discovered in Phase 1 (recently active). Followees
  discovered in Phase 3 are inserted with `is_active = 0`. The partial indexes on `profiled`
  and `follows_crawled` filter on `is_active = 1` so ghost actors are never picked up for
  profiling or follow-crawling on resume.
- `error_count` / `last_error`: Track per-actor failures. After `error_count` exceeds a
  threshold (default 5), the actor is skipped on future passes. See
  [Error Handling](#error-handling) below.

## Concurrency and Write Model

SQLite serializes all writes (even in WAL mode). At 400M+ edge inserts, per-row or per-user
commits would be catastrophically slow. The threading model:

- **Writer thread**: A single dedicated thread owns the SQLite connection and performs all writes.
  Worker threads never touch the database directly.
- **Queue**: Workers push results (profile data, follow edges) to a `queue.Queue(maxsize=200_000)`.
  The bounded queue provides **backpressure**: if the writer falls behind (e.g., during a WAL
  checkpoint or disk I/O stall), workers block naturally rather than consuming unbounded memory.
  The writer thread drains the queue and bulk-inserts in transactions of 50K rows.
- **Workers**: Each worker thread makes HTTP requests only, parsing responses and enqueueing
  results. No shared state between workers.
- **Shared rate limiter**: A `threading.Semaphore` or token-bucket limits total requests
  across all workers. The public AppView rate limit is per-IP (~3000 req/5min for unauthenticated
  requests). The shared limiter enforces a global budget (e.g., 500 req/min with headroom),
  not per-worker delays. On HTTP 429, all workers pause via a shared backoff signal.
- **WAL checkpointing**: Set `PRAGMA wal_autocheckpoint = 50000` (50K pages ≈ 200 MB) to
  prevent the WAL from growing unbounded during bulk inserts. The writer thread also calls
  `PRAGMA wal_checkpoint(PASSIVE)` after every 50K-row transaction to reclaim space
  incrementally without blocking readers.

```
[Worker 1] --\
[Worker 2] ---→ [Queue (maxsize=200K)] → [Writer Thread] → SQLite (WAL)
[Worker N] --/           ↑
                [Rate Limiter (shared)]
```

## Error Handling

Different HTTP error codes require different strategies. The writer thread and workers
cooperate to handle them:

| Error | Response | Actor State |
|-------|----------|-------------|
| **HTTP 429** (rate limit) | Shared backoff signal: all workers pause for `Retry-After` seconds (or 60s default). Exponential backoff on repeated 429s. | No state change |
| **HTTP 404 / 410** (deleted/suspended) | Mark actor as skipped: set `last_error = 'deleted'` and treat as completed (set `profiled = 1` or `follows_crawled = 1`). | `last_error = 'deleted'` |
| **HTTP 400** (bad request, invalid DID) | Log warning, mark actor as errored. | `error_count += 1`, `last_error = 'bad_request'` |
| **HTTP 5xx** (server error) | Retry with exponential backoff (3 attempts, 5s/15s/45s). If all retries fail, increment error_count. | `error_count += 1`, `last_error = 'server_error'` |
| **Network timeout / connection error** | Retry with backoff (3 attempts). If all fail, increment error_count. | `error_count += 1`, `last_error = 'timeout'` |
| **Malformed JSON** | Log and skip. Increment error_count. | `error_count += 1`, `last_error = 'bad_json'` |

**Skip threshold**: Actors with `error_count >= 5` are excluded from resumption queries
(add `AND error_count < 5` to partial index lookups, or check at query time). This prevents
permanently broken actors from being retried indefinitely.

## Progress Reporting

Each phase logs progress to stderr at regular intervals:

- **Every 10 seconds**: phase name, elapsed time, rows processed, rows/sec, queue depth,
  active workers, error count
- **Per batch** (every 50K rows committed by writer thread): cumulative row count, current
  insert rate, estimated time remaining
- **Phase summary on completion**: total rows, total time, average rate, error breakdown by
  type

For `--phase all`, a brief summary is printed between phases showing total database size on
disk and actor/edge counts.

## CLI Interface

```bash
# Run individual phases
python scripts/crawl_active_users.py --phase enumerate
python scripts/crawl_active_users.py --phase profile
python scripts/crawl_active_users.py --phase crawl-follows

# Run all phases sequentially
python scripts/crawl_active_users.py --phase all

# Options
python scripts/crawl_active_users.py --days 2 --workers 10 --db data/active_users.db \
    --max-follows-per-user 1000 --rate-limit 500 --relay-rate-limit 100
```

When using `--phase all`, phases run sequentially: enumerate → profile → crawl-follows.
Note that Phase 3 inserts "ghost" followee actors (`is_active = 0`) that are not profiled.
To profile these followees, re-run `--phase profile` after `--phase crawl-follows` completes
(the profile phase will pick up actors with `profiled = 0` — use `--include-ghosts` to also
profile non-active followees, or leave the default to only profile active users).

## Design Decisions

- **TID decoding**: `rev` is a base32 TID using the SRGB32 alphabet (microsecond timestamp + 11-bit counter). Extract the timestamp to filter by recency. See [TID Decoding](#tid-decoding).
- **Full scan required**: `listRepos` is ordered by repo creation, not activity. Phase 1 must enumerate all ~30M repos — no early termination.
- **Resumable**: Every phase tracks progress in `crawl_state` or via boolean flags (`profiled`, `follows_crawled`) with partial indexes for fast lookups. Crashes and restarts pick up where they left off.
- **Rate limiting**: Shared token-bucket across all workers, enforced globally (not per-worker delays). Exponential backoff on HTTP 429 propagates a pause signal to all workers. Separate rate limits for relay (Phase 1) and AppView (Phases 2-3).
- **No external dependencies**: Uses `urllib.request` consistent with existing scripts (`crawl_network.py`). Note that the multi-threaded writer-queue architecture is substantially more complex than the sequential `crawl_network.py`; the shared library is `urllib.request` only.
- **Follows scope**: Only stores outgoing edges. The followee is inserted into `actors` with `is_active = 0` and DID only (no profile data) if not already present.
- **Follow cap**: `--max-follows-per-user` prevents spending thousands of API calls on a single bot account. Default 1000.
- **Writer thread model**: All database writes go through a single writer thread with bulk transaction batching (50K rows/txn) to maintain SQLite throughput at scale.
- **Ghost actor isolation**: Followees discovered during Phase 3 are marked `is_active = 0` and excluded from profile/crawl resumption via partial indexes. This prevents millions of ghost actors from polluting the work queue on restart.
- **Error tracking**: Per-actor `error_count` and `last_error` columns enable skipping permanently broken accounts after repeated failures, preventing infinite retry loops.

## Estimated Timeline

| Phase       | Time        | Storage (mid estimate) |
|-------------|-------------|------------------------|
| Enumerate   | 2-4 hours   | ~800 MB                |
| Profile     | 4-12 hours  | ~800 MB                |
| Crawl follows | 3-14 days | ~150 GB                |
| **Total**   | ~4-15 days  | **~150 GB**            |

Timeline assumes `--max-follows-per-user 1000`, 10 workers, and staying within public API
rate limits. The low end (3 days) assumes 2M active users with ~100 avg follows. The high
end (14 days) assumes 5M users with ~200 avg follows. If the relay endpoint requires
authentication or has stricter rate limits, Phase 1 will take longer. Increase `--workers`
and `--rate-limit` proportionally to reduce Phase 3 wall time.
