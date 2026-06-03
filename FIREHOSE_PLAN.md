# Firehose Active User Collection

Collect a database of currently active Bluesky users by watching the Jetstream firehose,
then looking up their profile info (handle, follower count, following count).

## Script: `scripts/firehose_collect.py`

### Architecture

```
Jetstream WebSocket
       |
       v
 FirehoseThread --INSERT OR IGNORE--> write_queue ---> DBWriter --> SQLite
  (in-mem dedup set)                                    |            ^
   (cursor saves bypass via direct WAL write)            |            |
       |                                                 v            |
       |                                          DispatcherThread --+-- SELECT WHERE profiled_at IS NULL AND error_count < 5
       |                                                 |
       v                                                 v
 did_queue (thread-safe) <-------------------------------+
       |
  ProfileWorkers (N threads) <-- did_queue.get()
   (batch getProfiles, 25/request) ---> write_queue ---> DBWriter
       |
  SharedRateLimiter + SharedBackoff
```

Single process, concurrent threads, one Ctrl+C to stop. **All SQLite writes go through
a single `DBWriter` thread** fed by one `write_queue` with typed messages. Workers and
the firehose thread both push to this same queue. Workers never touch the DB directly.

Messages in `write_queue` are tuples tagged by type:

```python
("new_did", did, first_seen_at)
("new_did_with_handle", did, handle, first_seen_at)   # from identity events
("profile", did, handle, followers_count, follows_count)
("error", did, error_type)
```

### SQLite Schema

```sql
CREATE TABLE IF NOT EXISTS users (
    did TEXT PRIMARY KEY,
    handle TEXT,
    followers_count INTEGER,
    follows_count INTEGER,
    first_seen_at TEXT,
    profiled_at TEXT,
    error_count INTEGER DEFAULT 0,
    last_error TEXT
);
CREATE TABLE IF NOT EXISTS crawl_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE INDEX IF NOT EXISTS idx_users_unprofiled
    ON users(profiled_at) WHERE profiled_at IS NULL AND error_count < 5;
```

All timestamps stored as ISO 8601 strings (`YYYY-MM-DDTHH:MM:SSZ`), matching the
convention in `crawl_active_users.py`. The firehose's `time_us` (microseconds since epoch)
is converted to ISO 8601 before insertion.

DB initialization must set these PRAGMAs (same as `crawl_active_users.py`):

```python
conn.execute("PRAGMA journal_mode=WAL")
conn.execute("PRAGMA synchronous=NORMAL")
conn.execute("PRAGMA wal_autocheckpoint=50000")
```

### Components

1. **`FirehoseThread`** -- Connects to `wss://jetstream2.us-east.bsky.network/subscribe`,
   reads JSON events, extracts `did` from each event. Maintains an in-memory set to skip
   redundant inserts. New DIDs are pushed to `write_queue` (consumed by `DBWriter`).
   Stores `time_us` cursor in `crawl_state` every 10 seconds for resume. **Cursor saves
   bypass the write queue** and write directly to SQLite via `INSERT OR REPLACE` on the
   shared connection. This is safe because cursor writes are infrequent (every 10s), tiny
   (one row), and WAL mode handles concurrent readers + one writer. Going through the
   write queue would risk losing the cursor if the queue is backed up with profile updates.
   Auto-reconnects on disconnect, rewinding cursor 5 seconds
   (i.e., `cursor_us - 5_000_000` microseconds).

   Reconnection handles three cases:
   - `websockets.ConnectionClosed` (clean close) -- reconnect immediately
   - `ConnectionResetError` / `OSError` (network error) -- wait 2s, then reconnect
   - Generic `Exception` -- log the error, wait 5s, then reconnect

2. **`DispatcherThread`** -- Periodically queries `SELECT did FROM users WHERE profiled_at IS NULL AND error_count < 5 ORDER BY did LIMIT N` and feeds DIDs into a `did_queue`. This avoids the race condition of multiple workers independently SELECTing the same unprofiled rows. Workers pick DIDs from the shared queue, guaranteeing each DID is fetched at most once per pass.

3. **`ProfileWorker`** (N threads) -- Each worker loops: pull up to 25 DIDs from
   `did_queue`, call `getProfiles`, push results to `write_queue` as typed messages
   (consumed by `DBWriter`). Uses a shared `RateLimiter` (e.g., 300 req/min) and
   `SharedBackoff` for 429 responses. DIDs missing from the `getProfiles` response
   (deleted/deactivated accounts) are recorded as `("error", did, ...)` messages,
   not retried indefinitely.

4. **`DBWriter`** -- Single thread that drains `write_queue` and processes typed messages
   (`new_did`, `new_did_with_handle`, `profile`, `error`). Batches writes and commits
   periodically (every 1000 rows or 5 seconds, whichever comes first). This ensures
   only one thread writes to SQLite at a time, preventing `database is locked` under
   high concurrency.

5. **`ProgressReporter`** -- Prints stats every 10 seconds: total DIDs seen, profiled
   count, raw events/sec, unique DIDs/sec, profile queue depth, error breakdown.

6. **Signal handler** -- On Ctrl+C, sets a `stop_event`. Shutdown proceeds in strict
   order to guarantee no data is lost:

   1. Signal handler sets `stop_event`
   2. `FirehoseThread` stops reading events, saves final cursor directly to DB, exits
   3. `DispatcherThread` stops querying, exits
   4. `ProfileWorkers` drain `did_queue`, push final results to `write_queue`, exit
   5. `DBWriter` keeps running until `write_queue` is empty **and** all workers have
      joined, then flushes and commits any remaining batch, exits

   The main thread orchestrates this by joining each stage in order. `DBWriter` is always
   the last thread to stop.

### Resume Behavior

- **Firehose**: Reads last cursor from `crawl_state`, reconnects from
  `cursor_us - 5_000_000` (5 seconds back in microseconds) for gapless playback
- **Profiles**: Dispatcher queries `WHERE profiled_at IS NULL AND error_count < 5`
  -- naturally picks up where it left off, and skips permanently failed accounts

### Identity Event Bonus

Jetstream `identity` events include `handle` directly. The firehose thread can populate
`handle` immediately for those events, saving a profile API call later.

### Error Handling

Workers track errors per-DID through the result queue. The `DBWriter` increments
`error_count` and records `last_error` for each failed profile fetch. DIDs with
`error_count >= 5` are permanently skipped by the dispatcher and excluded from the
unprofiled index. Error types:

- `deleted` -- account gone (404/410 from API, or DID missing from getProfiles response)
- `bad_request` -- malformed DID (400)
- `max_retries` -- exhausted retries on transient errors
- `http_NNN` -- unexpected HTTP status

### CLI Args

| Flag | Default | Description |
|------|---------|-------------|
| `--workers` | 5 | Profile worker threads |
| `--rate-limit` | 300 | AppView requests/min |
| `--db` | `data/firehose_users.db` | SQLite path |
| `--jetstream` | `wss://jetstream2.us-east.bsky.network/subscribe` | Jetstream URL |
| `--duration` | 0 | Stop after N seconds (0 = run forever until Ctrl+C) |
| `--max-users` | 0 | Stop firehose after N unique DIDs seen (0 = unlimited) |

### Dependencies

- `websockets` (pip) -- for Jetstream WebSocket. Requires Python >= 3.8.
- Python stdlib only otherwise (sqlite3, json, threading, urllib, etc.)
- Minimum Python version: 3.10 (for `X | Y` union type hints matching existing codebase)

### Expected Performance

- Raw firehose: ~100-200K events/sec (varies by time of day)
- Unique new DIDs: ~1-3M/day (the in-mem dedup set filters the vast majority of events)
- Profile API: 300 req/min x 25 = 7,500 profiles/min = 450K/hr
- 2M users profiled in ~4.5 hours (profiling continues after firehose collection stops)
- DB size: ~200-300 MB for 2M rows (schema is lean: ~100 bytes/row + indexes)

### Implementation Notes

Follow the patterns from `scripts/crawl_active_users.py` (reuse `RateLimiter`,
`SharedBackoff`, `ProgressReporter`, `http_get`, `api_request` patterns) but simplified --
no follow-graph crawling, just the lightweight profile collection.

Key differences from `crawl_active_users.py`:
- Single `DBWriter` thread instead of direct writes from workers (firehose produces
  much higher write volume than the relay-based enumerator)
- `DispatcherThread` feeds workers via `did_queue` instead of workers independently
  querying the DB (avoids duplicate profile fetches)
- No separate phases -- firehose collection and profiling run concurrently in one process
