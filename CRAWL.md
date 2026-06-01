# Crawl Active Bluesky Users to SQLite

Enumerate recently-active Bluesky users and their follow graphs into a SQLite database.

## Scope

- **Target**: Accounts with a repo commit (post, like, follow, repost, etc.) in the last 2 days
- **Expected volume**: 2-5M unique DIDs
- **Data collected**: Profile metadata + outgoing follow edges (who each user follows)
- **Storage estimate**: 5-15 GB total

## Approach

Single script (`scripts/crawl_active_users.py`) with `--phase` flags for each stage.

### Phase 1: Enumerate Active DIDs (2-4 hours)

Paginate the relay's `com.atproto.sync.listRepos` endpoint, decode each repo's `rev` TID to a timestamp, and keep only repos with rev within the last N days.

- **Endpoint**: `GET https://bsky.network/xrpc/com.atproto.sync.listRepos?limit=1000`
- **Pagination**: cursor-based, ~30K requests to cover ~30M total repos
- **TID decoding**: The `rev` field is a base32 TID encoding a microsecond timestamp. Decode and filter by `>= now - N days`
- **Output**: Insert matching DIDs into `actors` table with `active_at`
- **Resumable**: Cursor persisted in `crawl_state` table

### Phase 2: Fetch Profiles (4-12 hours)

For each DID from Phase 1, fetch profile metadata in batches.

- **Endpoint**: `GET https://public.api.bsky.app/xrpc/app.bsky.actor.getProfiles?actors=did:...` (batch of 25)
- **Parallelism**: Multi-threaded, configurable `--workers N` (default 10)
- **Fields stored**: `handle`, `display_name`, `description`, `follows_count`, `followers_count`, `posts_count`, `indexed_at`
- **Progress**: ~80K-200K batch requests for 2-5M users

### Phase 3: Crawl Follow Lists (1-3 days)

For each DID, paginate their outgoing follows and store as edges.

- **Endpoint**: `GET https://public.api.bsky.app/xrpc/app.bsky.graph.getFollows?actor={did}&limit=100`
- **Pagination**: cursor-based, 100 per page
- **Parallelism**: Same multi-threaded worker pool
- **Scope**: Outgoing edges only (who each user follows). Followees are inserted into `actors` with DID only if not already present
- **Progress tracked per-DID** so it resumes cleanly after crashes

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

CREATE TABLE crawl_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
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
python scripts/crawl_active_users.py --days 2 --workers 10 --db data/active_users.db
```

## Design Decisions

- **TID decoding**: `rev` is a base32 TID (microsecond timestamp + counter). Extract the timestamp to filter by recency.
- **Resumable**: Every phase tracks progress in `crawl_state`. Crashes and restarts pick up where they left off.
- **Rate limiting**: Configurable `REQUEST_DELAY` per worker + exponential backoff on HTTP 429 responses.
- **No external dependencies**: Uses `urllib.request` consistent with existing scripts (`crawl_network.py`).
- **Follows scope**: Only stores outgoing edges. The followee is inserted into `actors` with DID only (no profile data) if not already present.

## Estimated Timeline

| Phase       | Time       | Storage        |
|-------------|------------|----------------|
| Enumerate   | 2-4 hours  | ~200 MB        |
| Profile     | 4-12 hours | ~500 MB - 1 GB |
| Crawl follows | 1-3 days | ~5-15 GB       |
| **Total**   | ~2-4 days  | ~5-17 GB       |
