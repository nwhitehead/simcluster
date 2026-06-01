# Scripts

Crawling and diagnostic tools for Bluesky network data.

## crawl_active_users.py

Enumerates recently-active Bluesky users and their follow graphs into a SQLite database (`data/active_users.db`). This produces a separate database from `simcluster.db` with a different schema (`follower_did`/`followee_did` columns, activity tracking fields).

The crawl runs in three phases that can be executed individually or sequentially. Each phase is resumable — crash or interrupt and re-run the same command to pick up where it left off.

### Prerequisites

No pip dependencies — the script uses `urllib.request` from the standard library.

Verify relay access before starting Phase 1:

```bash
curl -s 'https://bsky.network/xrpc/com.atproto.sync.listRepos?limit=1' | python3 -m json.tool
```

If this returns an auth error, Phase 1 cannot proceed. Phases 2–3 use the public AppView API which does not require authentication.

### Quick Start

```bash
# Run all three phases sequentially (enumerate → profile → crawl-follows)
python scripts/crawl_active_users.py --phase all

# Or run phases individually
python scripts/crawl_active_users.py --phase enumerate
python scripts/crawl_active_users.py --phase profile
python scripts/crawl_active_users.py --phase crawl-follows
```

### Phases

**Phase 1: `enumerate`** (2–4 hours)

Scans the relay's `listRepos` endpoint across all ~30M repos, decoding each repo's `rev` TID to a timestamp, and inserts DIDs with recent activity into the database. The relay returns repos ordered by creation time, not recency, so the entire dataset must be scanned — there is no early termination.

- Cursor state is persisted in `crawl_state` for resumption
- Rate-limited to `--relay-rate-limit` requests/min (default 100)

**Phase 2: `profile`** (4–12 hours)

Fetches profile metadata (handle, display name, bio, follow/follower/post counts) for active DIDs discovered in Phase 1. Uses the public `getProfiles` endpoint with batches of 25.

- Multi-threaded with `--workers` threads (default 10)
- Shared rate limit of `--rate-limit` requests/min (default 500)
- Resumes by querying actors where `profiled = 0 AND is_active = 1`

**Phase 3: `crawl-follows`** (3–14 days)

Paginates outgoing follow edges for each active DID via the `getFollows` endpoint. Followees are inserted as "ghost" actors (`is_active = 0`) with DID only — they are excluded from profiling and follow-crawling on resume via partial indexes.

- Same worker pool and rate limit as Phase 2
- Capped at `--max-follows-per-user` edges per user (default 1000)
- Resumes by querying actors where `follows_crawled = 0 AND is_active = 1`

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--phase` | (required) | `enumerate`, `profile`, `crawl-follows`, or `all` |
| `--days` | 2 | Lookback window for active repos (Phase 1) |
| `--workers` | 10 | Worker threads for Phases 2–3 |
| `--db` | `data/active_users.db` | SQLite database path |
| `--max-follows-per-user` | 1000 | Follow-edge cap per user (Phase 3) |
| `--rate-limit` | 500 | AppView requests/min (Phases 2–3) |
| `--relay-rate-limit` | 100 | Relay requests/min (Phase 1) |
| `--include-ghosts` | off | Also profile non-active followees (Phase 2) |

### Resuming After Interruption

All phases are resumable. Just re-run the same command:

```bash
# Phase 1 saves its pagination cursor in crawl_state
python scripts/crawl_active_users.py --phase enumerate

# Phases 2 and 3 resume by querying uncompleted actors
python scripts/crawl_active_users.py --phase profile
python scripts/crawl_active_users.py --phase crawl-follows
```

Actors that fail repeatedly (5+ errors) are automatically skipped on resume. The `error_count` and `last_error` columns in the `actors` table track per-actor failures.

### Profiling Ghost Followees

Phase 3 inserts followees as ghost actors (`is_active = 0`) without profile data. By default, Phase 2 only profiles active users. To also profile these ghost actors, run Phase 2 with `--include-ghosts` after Phase 3 completes:

```bash
python scripts/crawl_active_users.py --phase crawl-follows
python scripts/crawl_active_users.py --phase profile --include-ghosts
```

### Storage Requirements

The database can grow to 50–250 GB depending on how many active users are found and their average follow count. Budget an additional 20–30 GB for WAL checkpoints during bulk inserts.

| Scenario | Active Users | Avg Follows | Total Size |
|----------|-------------|-------------|------------|
| Low | 2M | 100 | ~50 GB |
| Mid | 3M | 200 | ~150 GB |
| High | 5M | 200 | ~250 GB |

Reduce storage by lowering `--max-follows-per-user` (e.g., `--max-follows-per-user 500` brings the high estimate to ~80–100 GB).

### Speeding Up Phase 3

Phase 3 is the bottleneck (3–14 days). To speed it up, increase workers and rate limit proportionally:

```bash
python scripts/crawl_active_users.py --phase crawl-follows --workers 20 --rate-limit 1000
```

This roughly halves the wall time but requires monitoring for HTTP 429 rate-limit responses.

### Estimated Timeline

| Phase | Time | Storage (mid) |
|-------|------|---------------|
| Enumerate | 2–4 hours | ~800 MB |
| Profile | 4–12 hours | ~800 MB |
| Crawl follows | 3–14 days | ~150 GB |

### Error Handling

The script handles different HTTP errors automatically:

| Error | Behavior |
|-------|----------|
| 429 (rate limit) | All workers pause, exponential backoff |
| 404/410 (deleted) | Actor marked complete with `last_error = 'deleted'` |
| 5xx (server error) | 3 retries with backoff, then increment `error_count` |
| Timeout / connection | 3 retries, then increment `error_count` |
| Malformed JSON | Skip and increment `error_count` |

---

## crawl_network.py

Snowball sampler that crawls the Bluesky follow graph starting from known simcluster seed accounts. Produces `data/simcluster.db`. Three-phase BFS crawl: seed resolution → follow fetching → community filtering (≥2 seed follows) → snowball expansion.

```bash
python scripts/crawl_network.py
```

No CLI flags. Edit `SEEDS` and `DISCOVERED_SEEDS` lists in the script to change seed accounts.

## resolve_handles.py

Batch-resolves handles for actors in `simcluster.db` that are missing handle data. Profiles the top 500 by in-degree, then fetches full profile details for the top 200.

```bash
python scripts/resolve_handles.py
```

## check_membership.py

Interactive CLI diagnostic that computes a Simcluster Score (0–100) for a given Bluesky handle or DID. Scores are based on follow-graph proximity to 14 community seed accounts.

```bash
python scripts/check_membership.py <handle_or_did>
```
