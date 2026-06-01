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

Average Bluesky user follows ~200 accounts. Edge rows in SQLite are ~100 bytes each
(two DIDs at ~44 chars + row overhead). The `followee_did` index roughly doubles the
`follows` table size.

| Scenario | Active Users | Avg Follows | Edges | Follows Table | Follows Index | Actors Table | **Total** |
|----------|-------------|-------------|-------|---------------|---------------|-------------|-----------|
| Low | 2M | 100 | 200M | ~20 GB | ~20 GB | ~500 MB | **~40 GB** |
| Mid | 3M | 200 | 600M | ~60 GB | ~60 GB | ~800 MB | **~120 GB** |
| High | 5M | 200 | 1B | ~100 GB | ~100 GB | ~1.5 GB | **~200 GB** |

If storage is a concern, use `--max-follows-per-user` (default 1000) to cap edges per user
and reduce the high end to ~60-80 GB.

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
- **TID decoding**: The `rev` field is a base32 TID encoding a microsecond timestamp. Decode and filter by `>= now - N days`
- **Output**: Insert matching DIDs into `actors` table with `active_at`
- **Resumable**: Cursor persisted in `crawl_state` table

> **Warning**: `listRepos` returns repos ordered by repo creation time, NOT by `rev` recency.
> A repo created 2 years ago that posted today will have a recent `rev` but appear early in
> pagination. You **cannot early-terminate** — you must scan ALL ~30M repos to find recently
> active ones. Do not add an optimization that stops when revs look "too old."

### Phase 2: Fetch Profiles (4-12 hours)

For each DID from Phase 1, fetch profile metadata in batches.

- **Endpoint**: `GET https://public.api.bsky.app/xrpc/app.bsky.actor.getProfiles?actors=did:...` (batch of 25)
- **Parallelism**: Multi-threaded, configurable `--workers N` (default 10)
- **Fields stored**: `handle`, `display_name`, `description`, `follows_count`, `followers_count`, `posts_count`, `indexed_at`
- **Progress**: ~80K-200K batch requests for 2-5M users
- **Resumable**: Selects actors where `profiled = 0` and processes in batches. On restart, automatically picks up unprofiled actors.

### Phase 3: Crawl Follow Lists (3-7 days)

For each DID, paginate their outgoing follows and store as edges.

- **Endpoint**: `GET https://public.api.bsky.app/xrpc/app.bsky.graph.getFollows?actor={did}&limit=100`
- **Pagination**: cursor-based, 100 per page
- **Parallelism**: Same multi-threaded worker pool
- **Scope**: Outgoing edges only (who each user follows). Followees are inserted into `actors` with DID only if not already present
- **Cap**: `--max-follows-per-user 1000` (default). Accounts exceeding this are marked `follows_crawled = 1` after hitting the cap, preventing runaway pagination on bots/follow-back accounts.
- **Progress tracked per-DID** via `follows_crawled` flag so it resumes cleanly after crashes

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
    profiled BOOLEAN DEFAULT 0,
    follows_crawled BOOLEAN DEFAULT 0
);

CREATE TABLE follows (
    follower_did TEXT,
    followee_did TEXT,
    PRIMARY KEY (follower_did, followee_did)
);

CREATE INDEX idx_follows_followee ON follows(followee_did);

-- Partial indexes for fast resumption queries on large tables
CREATE INDEX idx_actors_profiled ON actors(profiled) WHERE profiled = 0;
CREATE INDEX idx_actors_follows_crawled ON actors(follows_crawled) WHERE follows_crawled = 0;

CREATE TABLE crawl_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
```

## Concurrency and Write Model

SQLite serializes all writes (even in WAL mode). At 400M+ edge inserts, per-row or per-user
commits would be catastrophically slow. The threading model:

- **Writer thread**: A single dedicated thread owns the SQLite connection and performs all writes.
  Worker threads never touch the database directly.
- **Queue**: Workers push results (profile data, follow edges) to a `queue.Queue`. The writer
  thread drains the queue and bulk-inserts in transactions of 50K rows.
- **Workers**: Each worker thread makes HTTP requests only, parsing responses and enqueueing
  results. No shared state between workers.
- **Shared rate limiter**: A `threading.Semaphore` or token-bucket limits total requests
  across all workers. The public AppView rate limit is per-IP (~3000 req/5min for unauthenticated
  requests). The shared limiter enforces a global budget (e.g., 500 req/min with headroom),
  not per-worker delays. On HTTP 429, all workers pause via a shared backoff signal.

```
[Worker 1] --\
[Worker 2] ---→ [Queue] → [Writer Thread] → SQLite
[Worker N] --/       ↑
              [Rate Limiter (shared)]
```

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
    --max-follows-per-user 1000
```

## Design Decisions

- **TID decoding**: `rev` is a base32 TID (microsecond timestamp + counter). Extract the timestamp to filter by recency.
- **Full scan required**: `listRepos` is ordered by repo creation, not activity. Phase 1 must enumerate all ~30M repos — no early termination.
- **Resumable**: Every phase tracks progress in `crawl_state` or via boolean flags (`profiled`, `follows_crawled`) with partial indexes for fast lookups. Crashes and restarts pick up where they left off.
- **Rate limiting**: Shared token-bucket across all workers, enforced globally (not per-worker delays). Exponential backoff on HTTP 429 propagates a pause signal to all workers.
- **No external dependencies**: Uses `urllib.request` consistent with existing scripts (`crawl_network.py`).
- **Follows scope**: Only stores outgoing edges. The followee is inserted into `actors` with DID only (no profile data) if not already present.
- **Follow cap**: `--max-follows-per-user` prevents spending thousands of API calls on a single bot account. Default 1000.
- **Writer thread model**: All database writes go through a single writer thread with bulk transaction batching (50K rows/txn) to maintain SQLite throughput at scale.

## Estimated Timeline

| Phase       | Time       | Storage (mid estimate) |
|-------------|------------|------------------------|
| Enumerate   | 2-4 hours  | ~800 MB                |
| Profile     | 4-12 hours | ~800 MB                |
| Crawl follows | 3-7 days | ~120 GB                |
| **Total**   | ~4-8 days  | **~120 GB**            |

Timeline assumes `--max-follows-per-user 1000`, 10 workers, and staying within public API
rate limits. If the relay endpoint requires authentication or has stricter rate limits,
Phase 1 will take longer.
