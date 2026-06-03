# Scripts

## `firehose_collect.py` -- Active User Collection from the Jetstream Firehose

Collects active Bluesky user DIDs by watching the Jetstream firehose in real time, then fetches
their profile metadata (handle, follower count, following count) via the public AppView API.

### Quick Start

```bash
# Install dependency
pip install websockets

# Run until Ctrl+C -- collects DIDs and profiles concurrently
python scripts/firehose_collect.py

# Collect for 60 seconds, then continue profiling until done
python scripts/firehose_collect.py --duration 60

# Stop firehose after 10,000 unique DIDs, profile all of them
python scripts/firehose_collect.py --max-users 10000

# Resume a previous run (automatic -- reads cursor from DB)
python scripts/firehose_collect.py
```

### How It Works

1. Connects to a Jetstream WebSocket and reads events in real time
2. Extracts unique DIDs from all events (posts, likes, follows, etc.)
3. Fetches profile metadata in batches of 25 via `getProfiles`
4. Writes everything to a SQLite database

Firehose collection and profile fetching run concurrently in a single process. When the firehose
stops (--duration, --max-users, or Ctrl+C), profiling continues until all discovered DIDs are
processed.

### CLI Arguments

| Flag | Default | Description |
|------|---------|-------------|
| `--workers` | 5 | Number of profile fetcher threads |
| `--rate-limit` | 300 | Max AppView API requests per minute |
| `--db` | `data/firehose_users.db` | Path to SQLite database |
| `--jetstream` | `wss://jetstream2.us-east.bsky.network/subscribe` | Jetstream WebSocket URL |
| `--duration` | 0 | Stop firehose after N seconds (0 = run forever) |
| `--max-users` | 0 | Stop firehose after N unique DIDs (0 = unlimited) |

### Stopping

Press Ctrl+C once for a graceful shutdown. The firehose saves its cursor, workers finish their
current batches, and the DB writer flushes all pending data. Press Ctrl+C again to force exit.

### Resume

If interrupted, just run the script again with the same `--db` path. The firehose rewinds 5
seconds from its last saved cursor for gapless playback. Profile fetching naturally picks up
where it left off by querying unprofiled DIDs.

### Database Schema

The SQLite database (`data/firehose_users.db` by default) contains:

```
users
  did              TEXT PRIMARY KEY   -- Bluesky DID
  handle           TEXT               -- @handle
  followers_count  INTEGER            -- number of followers
  follows_count    INTEGER            -- number following
  first_seen_at    TEXT               -- ISO 8601, when first seen on firehose
  profiled_at      TEXT               -- ISO 8601, when profile was fetched
  error_count      INTEGER            -- number of failed profile attempts
  last_error       TEXT               -- last error type (deleted, bad_request, etc.)

crawl_state
  key              TEXT PRIMARY KEY   -- state key (e.g. "firehose_cursor")
  value            TEXT               -- state value
```

### Example Queries

```sql
-- Top 20 most-followed accounts
SELECT handle, followers_count FROM users ORDER BY followers_count DESC LIMIT 20;

-- Count profiled vs pending
SELECT
  COUNT(*) FILTER (WHERE profiled_at IS NOT NULL) AS profiled,
  COUNT(*) FILTER (WHERE profiled_at IS NULL AND error_count < 5) AS pending,
  COUNT(*) FILTER (WHERE error_count >= 5) AS errored
FROM users;

-- Accounts with more followers than following
SELECT handle, followers_count, follows_count
FROM users
WHERE followers_count > follows_count
ORDER BY followers_count DESC;
```

---

## `crawl_active_users.py` -- Relay-Based Active User Crawler

Enumerates recently-active Bluesky users via the relay's `listRepos` endpoint, fetches their
profiles, and optionally crawls their outgoing follow edges. Runs in separate phases.

```bash
# Phase 1: Find active DIDs from the last 2 days
python scripts/crawl_active_users.py --phase enumerate

# Phase 2: Fetch profiles for discovered DIDs
python scripts/crawl_active_users.py --phase profile

# Phase 3: Crawl follow edges for profiled users
python scripts/crawl_active_users.py --phase crawl-follows

# Or run all phases
python scripts/crawl_active_users.py --phase all
```

---

## `crawl_network.py` -- Snowball Follow-Graph Sampler

Seeds from a set of known community members and expands outward via their follow graphs.
Used to build the original simcluster network dataset.

## `resolve_handles.py` -- Batch Handle Resolution

Resolves human-readable handles to DIDs for accounts already in a crawl database.

## `check_membership.py` -- CLI Membership Diagnostic

Computes a Simcluster Score for a given Bluesky handle from the command line.
