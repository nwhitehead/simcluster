#!/usr/bin/env python3
"""
Crawl active Bluesky users and their follow graphs into a SQLite database.

Enumerates recently-active Bluesky users via relay's listRepos endpoint,
fetches their profiles, and crawls their outgoing follow edges.

Phases:
  1. enumerate  - Scan relay for DIDs with recent repo activity
  2. profile    - Fetch profile metadata for active DIDs
  3. crawl-follows - Paginate outgoing follows for each active DID

Usage:
  python scripts/crawl_active_users.py --phase enumerate
  python scripts/crawl_active_users.py --phase profile
  python scripts/crawl_active_users.py --phase crawl-follows
  python scripts/crawl_active_users.py --phase all
"""

import argparse
import json
import os
import queue
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

RELAY_API = "https://bsky.network/xrpc"
PUBLIC_API = "https://public.api.bsky.app/xrpc"

BASE32_CHARS = "234567abcdefghijklmnopqrstuvwxyz"

BATCH_COMMIT_SIZE = 50_000
QUEUE_MAXSIZE = 200_000
MAX_RETRIES = 3
ERROR_SKIP_THRESHOLD = 5

REPORT_INTERVAL = 10

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS actors (
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

CREATE INDEX IF NOT EXISTS idx_actors_handle ON actors(handle);
CREATE INDEX IF NOT EXISTS idx_actors_profiled ON actors(profiled) WHERE profiled = 0 AND is_active = 1;
CREATE INDEX IF NOT EXISTS idx_actors_follows_crawled ON actors(follows_crawled) WHERE follows_crawled = 0 AND is_active = 1;

CREATE TABLE IF NOT EXISTS follows (
    follower_did TEXT,
    followee_did TEXT,
    PRIMARY KEY (follower_did, followee_did)
);

CREATE INDEX IF NOT EXISTS idx_follows_followee ON follows(followee_did);

CREATE TABLE IF NOT EXISTS crawl_state (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""


def decode_tid(tid_str: str) -> int:
    val = 0
    for c in tid_str:
        val = (val << 5) | BASE32_CHARS.index(c)
    return val >> 11


class RateLimiter:
    def __init__(self, requests_per_minute: int):
        self.min_interval = 60.0 / requests_per_minute
        self.lock = threading.Lock()
        self.last_time = 0.0

    def wait(self):
        with self.lock:
            now = time.monotonic()
            elapsed = now - self.last_time
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self.last_time = time.monotonic()


class SharedBackoff:
    def __init__(self):
        self.lock = threading.Lock()
        self.paused_until = 0.0

    def check_wait(self):
        with self.lock:
            wait = max(0.0, self.paused_until - time.monotonic())
        if wait > 0:
            time.sleep(wait)

    def signal_backoff(self, seconds: float):
        with self.lock:
            self.paused_until = max(self.paused_until, time.monotonic() + seconds)


class ProgressReporter:
    def __init__(self, phase_name: str):
        self.phase_name = phase_name
        self.start_time = time.monotonic()
        self.rows_processed = 0
        self.error_count = 0
        self.errors_by_type = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._report_loop, daemon=True)

    def start(self, get_queue_depth=None, get_active_workers=None):
        self._get_queue_depth = get_queue_depth or (lambda: 0)
        self._get_active_workers = get_active_workers or (lambda: 0)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=15)

    def add_rows(self, n: int):
        with self.lock:
            self.rows_processed += n

    def add_error(self, error_type: str):
        with self.lock:
            self.error_count += 1
            self.errors_by_type[error_type] = self.errors_by_type.get(error_type, 0) + 1

    def _report_loop(self):
        while not self._stop.wait(timeout=REPORT_INTERVAL):
            self._print_status()

    def _print_status(self):
        with self.lock:
            elapsed = time.monotonic() - self.start_time
            rows = self.rows_processed
            errors = self.error_count
        rate = rows / elapsed if elapsed > 0 else 0
        q_depth = self._get_queue_depth()
        workers = self._get_active_workers()
        print(
            f"  [{self.phase_name}] {elapsed:.0f}s | {rows:,} rows | "
            f"{rate:.1f} rows/s | queue={q_depth:,} | workers={workers} | "
            f"errors={errors}",
            file=sys.stderr,
        )

    def print_summary(self):
        with self.lock:
            elapsed = time.monotonic() - self.start_time
            rows = self.rows_processed
            errors = self.error_count
            err_breakdown = dict(self.errors_by_type)
        rate = rows / elapsed if elapsed > 0 else 0
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  {self.phase_name} COMPLETE", file=sys.stderr)
        print(f"  Total: {rows:,} rows in {elapsed:.0f}s ({rate:.1f} rows/s)", file=sys.stderr)
        print(f"  Errors: {errors} ({err_breakdown})", file=sys.stderr)
        print(f"{'='*60}\n", file=sys.stderr)


def http_get(url: str, timeout: int = 15) -> tuple[int, str | None, str | None]:
    req = urllib.request.Request(url)
    req.add_header("User-Agent", "simcluster-crawler/1.0")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, None, r.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        body = None
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            pass
        return e.code, e.headers.get("Retry-After"), body
    except Exception:
        return 0, None, None


def api_request(url: str, rate_limiter: RateLimiter, backoff: SharedBackoff,
                max_retries: int = MAX_RETRIES, timeout: int = 15) -> tuple[dict | None, str | None]:
    for attempt in range(max_retries):
        backoff.check_wait()
        rate_limiter.wait()
        status, retry_after, body = http_get(url, timeout)

        if status == 200 and body is not None:
            try:
                return json.loads(body), None
            except json.JSONDecodeError:
                return None, "bad_json"

        if status == 429:
            if retry_after:
                try:
                    wait_secs = float(retry_after)
                except ValueError:
                    wait_secs = 60.0
            else:
                wait_secs = 60.0
            wait_secs = min(wait_secs * (2 ** attempt), 300)
            backoff.signal_backoff(wait_secs)
            continue

        if status in (404, 410):
            return None, "deleted"

        if status == 400:
            return None, "bad_request"

        if status >= 500:
            wait_secs = 5 * (3 ** attempt)
            time.sleep(wait_secs)
            continue

        if status == 0:
            time.sleep(2 * (attempt + 1))
            continue

        return None, f"http_{status}"

    return None, "max_retries"


def init_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA wal_autocheckpoint=50000")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def get_state(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM crawl_state WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def set_state(conn: sqlite3.Connection, key: str, value: str):
    conn.execute("INSERT OR REPLACE INTO crawl_state (key, value) VALUES (?, ?)", (key, value))
    conn.commit()


def phase_enumerate(conn: sqlite3.Connection, args):
    print("\n=== Phase 1: Enumerate Active DIDs ===\n", file=sys.stderr)

    days = args.days
    cutoff_us = (time.time() - days * 86400) * 1_000_000

    rate_limiter = RateLimiter(args.relay_rate_limit)
    backoff = SharedBackoff()
    reporter = ProgressReporter("enumerate")
    reporter.start()

    cursor = get_state(conn, "enumerate_cursor")
    total_scanned = int(get_state(conn, "enumerate_scanned") or "0")

    if cursor:
        print(f"  Resuming from cursor, {total_scanned:,} repos already scanned", file=sys.stderr)
    else:
        print(f"  Starting fresh scan (cutoff: {days} days)", file=sys.stderr)

    batch_actors = []
    batch_size = 0
    last_commit_scanned = total_scanned

    try:
        while True:
            url = f"{RELAY_API}/com.atproto.sync.listRepos?limit=1000"
            if cursor:
                url += f"&cursor={cursor}"

            data, err = api_request(url, rate_limiter, backoff, max_retries=5, timeout=30)

            if err == "max_retries":
                print(f"  WARNING: max retries for listRepos, saving state and stopping", file=sys.stderr)
                break

            if data is None:
                print(f"  WARNING: got error '{err}' from listRepos, stopping", file=sys.stderr)
                reporter.add_error(err or "unknown")
                break

            repos = data.get("repos", [])
            if not repos:
                print("  No more repos returned, scan complete.", file=sys.stderr)
                break

            new_cursor = data.get("cursor")
            matched = 0

            for repo in repos:
                did = repo.get("did", "")
                rev = repo.get("rev", "")
                if not did or not rev:
                    continue
                try:
                    ts_us = decode_tid(rev)
                except (ValueError, IndexError):
                    continue

                if ts_us >= cutoff_us:
                    ts_iso = time.strftime(
                        "%Y-%m-%dT%H:%M:%SZ",
                        time.gmtime(ts_us / 1_000_000),
                    )
                    batch_actors.append((did, ts_iso))
                    matched += 1

            total_scanned += len(repos)
            batch_size += matched

            if batch_size >= BATCH_COMMIT_SIZE:
                conn.executemany(
                    "INSERT OR IGNORE INTO actors (did, active_at, is_active) VALUES (?, ?, 1)",
                    batch_actors,
                )
                conn.commit()
                reporter.add_rows(batch_size)
                print(
                    f"  [batch] {total_scanned:,} scanned, {reporter.rows_processed:,} active found",
                    file=sys.stderr,
                )
                batch_actors = []
                batch_size = 0

                set_state(conn, "enumerate_cursor", new_cursor or "")
                set_state(conn, "enumerate_scanned", str(total_scanned))
                last_commit_scanned = total_scanned

            cursor = new_cursor
            if not cursor:
                print("  No cursor returned, scan complete.", file=sys.stderr)
                break

    except KeyboardInterrupt:
        print("\n  Interrupted, saving state...", file=sys.stderr)

    if batch_actors:
        conn.executemany(
            "INSERT OR IGNORE INTO actors (did, active_at, is_active) VALUES (?, ?, 1)",
            batch_actors,
        )
        conn.commit()
        reporter.add_rows(batch_size)

    set_state(conn, "enumerate_cursor", cursor or "")
    set_state(conn, "enumerate_scanned", str(total_scanned))
    conn.commit()

    reporter.stop()
    reporter.print_summary()

    active_count = conn.execute(
        "SELECT COUNT(*) FROM actors WHERE is_active=1"
    ).fetchone()[0]
    print(f"  Active DIDs in database: {active_count:,}", file=sys.stderr)


def phase_profile(conn: sqlite3.Connection, args):
    print("\n=== Phase 2: Fetch Profiles ===\n", file=sys.stderr)

    rate_limiter = RateLimiter(args.rate_limit)
    backoff = SharedBackoff()
    result_queue = queue.Queue(maxsize=QUEUE_MAXSIZE)

    include_ghosts = args.include_ghosts

    where_clause = "profiled = 0 AND error_count < ?"
    params = [ERROR_SKIP_THRESHOLD]
    if not include_ghosts:
        where_clause += " AND is_active = 1"

    total_to_profile = conn.execute(
        f"SELECT COUNT(*) FROM actors WHERE {where_clause}", params
    ).fetchone()[0]
    print(f"  Actors to profile: {total_to_profile:,}", file=sys.stderr)

    if total_to_profile == 0:
        print("  Nothing to do.", file=sys.stderr)
        return

    stop_event = threading.Event()
    reporter = ProgressReporter("profile")
    active_workers = [0]
    active_lock = threading.Lock()

    def worker():
        with active_lock:
            active_workers[0] += 1
        try:
            while not stop_event.is_set():
                batch_dids = []
                try:
                    for _ in range(25):
                        did = did_queue.get_nowait()
                        batch_dids.append(did)
                except queue.Empty:
                    break

                if not batch_dids:
                    break

                actors_param = "&actors=".join(batch_dids)
                url = f"{PUBLIC_API}/app.bsky.actor.getProfiles?actors={actors_param}"

                data, err = api_request(url, rate_limiter, backoff)

                if data is not None:
                    profiles = {}
                    for p in data.get("profiles", []):
                        did = p.get("did", "")
                        profiles[did] = p

                    for did in batch_dids:
                        p = profiles.get(did)
                        if p:
                            result_queue.put(("profile", did, {
                                "handle": p.get("handle", ""),
                                "display_name": p.get("displayName", ""),
                                "description": p.get("description", ""),
                                "follows_count": p.get("followsCount", 0),
                                "followers_count": p.get("followersCount", 0),
                                "posts_count": p.get("postsCount", 0),
                                "indexed_at": p.get("indexedAt", ""),
                            }))
                        else:
                            result_queue.put(("profile", did, None))
                else:
                    if err == "deleted":
                        for did in batch_dids:
                            result_queue.put(("error", did, "deleted"))
                    else:
                        for did in batch_dids:
                            result_queue.put(("error", did, err))
                        reporter.add_error(err or "unknown")
        finally:
            with active_lock:
                active_workers[0] -= 1

    did_queue = queue.Queue(maxsize=100_000)

    cursor_offset = 0
    batch_size_dids = 5000
    while not stop_event.is_set():
        params_q = [ERROR_SKIP_THRESHOLD, cursor_offset, batch_size_dids]
        where_q = where_clause
        dids = [r[0] for r in conn.execute(
            f"SELECT did FROM actors WHERE {where_q} ORDER BY did LIMIT ? OFFSET ?",
            params_q,
        ).fetchall()]
        if not dids:
            break
        for d in dids:
            did_queue.put(d)
        cursor_offset += batch_size_dids

    workers = []
    for _ in range(args.workers):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        workers.append(t)

    def get_q_depth():
        return result_queue.qsize()

    def get_active():
        with active_lock:
            return active_workers[0]

    reporter.start(get_q_depth, get_active)

    committed = 0
    profile_batch = []
    error_batch = []

    try:
        while committed < total_to_profile:
            try:
                item = result_queue.get(timeout=1.0)
            except queue.Empty:
                all_done = all(not t.is_alive() for t in workers) and result_queue.empty()
                if all_done:
                    break
                continue

            msg_type, did, payload = item

            if msg_type == "profile":
                if payload is not None:
                    profile_batch.append((
                        payload["handle"],
                        payload["display_name"],
                        payload["description"],
                        payload["follows_count"],
                        payload["followers_count"],
                        payload["posts_count"],
                        payload["indexed_at"],
                        did,
                    ))
                else:
                    error_batch.append(("no_profile_data", did))
                committed += 1
            elif msg_type == "error":
                if payload == "deleted":
                    error_batch.append(("deleted", did))
                    committed += 1
                else:
                    error_batch.append((payload or "unknown", did))
                    committed += 1

            if len(profile_batch) + len(error_batch) >= BATCH_COMMIT_SIZE:
                _flush_profile_batch(conn, profile_batch, error_batch, reporter)
                reporter.add_rows(committed)
                profile_batch = []
                error_batch = []

    except KeyboardInterrupt:
        print("\n  Interrupted, saving progress...", file=sys.stderr)
        stop_event.set()

    if profile_batch or error_batch:
        _flush_profile_batch(conn, profile_batch, error_batch, reporter)
    reporter.add_rows(committed)

    stop_event.set()
    for t in workers:
        t.join(timeout=5)

    reporter.stop()
    reporter.print_summary()

    profiled_count = conn.execute(
        "SELECT COUNT(*) FROM actors WHERE profiled=1"
    ).fetchone()[0]
    print(f"  Profiled actors: {profiled_count:,}", file=sys.stderr)


def _flush_profile_batch(conn, profile_batch, error_batch, reporter):
    if profile_batch:
        conn.executemany(
            """UPDATE actors SET
               handle=?, display_name=?, description=?,
               follows_count=?, followers_count=?, posts_count=?,
               indexed_at=?, profiled=1
               WHERE did=?""",
            profile_batch,
        )
    for err_type, did in error_batch:
        if err_type in ("deleted",):
            conn.execute(
                "UPDATE actors SET profiled=1, last_error=? WHERE did=?",
                (err_type, did),
            )
        else:
            conn.execute(
                "UPDATE actors SET error_count=error_count+1, last_error=? WHERE did=?",
                (err_type, did),
            )
            if reporter:
                reporter.add_error(err_type or "unknown")
    conn.commit()


def phase_crawl_follows(conn: sqlite3.Connection, args):
    print("\n=== Phase 3: Crawl Follow Lists ===\n", file=sys.stderr)

    rate_limiter = RateLimiter(args.rate_limit)
    backoff = SharedBackoff()
    result_queue = queue.Queue(maxsize=QUEUE_MAXSIZE)

    max_follows = args.max_follows_per_user

    total_to_crawl = conn.execute(
        "SELECT COUNT(*) FROM actors WHERE follows_crawled=0 AND is_active=1 AND error_count < ?",
        (ERROR_SKIP_THRESHOLD,),
    ).fetchone()[0]
    print(f"  Actors to crawl: {total_to_crawl:,}", file=sys.stderr)

    if total_to_crawl == 0:
        print("  Nothing to do.", file=sys.stderr)
        return

    stop_event = threading.Event()
    reporter = ProgressReporter("crawl-follows")
    active_workers = [0]
    active_lock = threading.Lock()

    def worker():
        with active_lock:
            active_workers[0] += 1
        try:
            while not stop_event.is_set():
                try:
                    did = did_queue.get_nowait()
                except queue.Empty:
                    break

                edges = []
                cursor = None
                pages = 0
                max_pages = (max_follows + 99) // 100

                while pages < max_pages:
                    url = f"{PUBLIC_API}/app.bsky.graph.getFollows?actor={did}&limit=100"
                    if cursor:
                        url += f"&cursor={cursor}"

                    data, err = api_request(url, rate_limiter, backoff)

                    if data is None:
                        if err == "deleted":
                            result_queue.put(("actor_done", did, {"error": "deleted"}))
                        else:
                            result_queue.put(("actor_done", did, {"error": err}))
                            reporter.add_error(err or "unknown")
                        break

                    follows_list = data.get("follows", [])
                    for f in follows_list:
                        followee_did = f.get("did", "")
                        if followee_did:
                            edges.append((did, followee_did))

                    pages += 1
                    cursor = data.get("cursor")
                    if not cursor or not follows_list:
                        result_queue.put(("actor_done", did, {"edges": edges}))
                        break
                else:
                    result_queue.put(("actor_done", did, {"edges": edges, "capped": True}))
        finally:
            with active_lock:
                active_workers[0] -= 1

    did_queue = queue.Queue(maxsize=50_000)

    cursor_offset = 0
    batch_size_dids = 5000
    while not stop_event.is_set():
        dids = [r[0] for r in conn.execute(
            "SELECT did FROM actors WHERE follows_crawled=0 AND is_active=1 AND error_count < ? "
            "ORDER BY did LIMIT ? OFFSET ?",
            (ERROR_SKIP_THRESHOLD, batch_size_dids, cursor_offset),
        ).fetchall()]
        if not dids:
            break
        for d in dids:
            did_queue.put(d)
        cursor_offset += batch_size_dids

    workers = []
    for _ in range(args.workers):
        t = threading.Thread(target=worker, daemon=True)
        t.start()
        workers.append(t)

    def get_q_depth():
        return result_queue.qsize()

    def get_active():
        with active_lock:
            return active_workers[0]

    reporter.start(get_q_depth, get_active)

    edge_batch = []
    ghost_batch = []
    done_batch = []
    error_batch = []
    done_actors = 0
    total_edges = 0
    last_batch_report = 0

    try:
        while done_actors < total_to_crawl:
            try:
                item = result_queue.get(timeout=1.0)
            except queue.Empty:
                all_done = all(not t.is_alive() for t in workers) and result_queue.empty()
                if all_done:
                    break
                continue

            msg_type, did, payload = item
            done_actors += 1

            if "error" in payload:
                err = payload["error"]
                if err == "deleted":
                    done_batch.append((err, did))
                else:
                    error_batch.append((err, did))
                    reporter.add_error(err)
            else:
                edges = payload.get("edges", [])
                for follower_did, followee_did in edges:
                    edge_batch.append((follower_did, followee_did))
                    ghost_batch.append((followee_did,))

                done_batch.append((None, did))
                total_edges += len(edges)

            if len(edge_batch) >= BATCH_COMMIT_SIZE or len(done_batch) >= BATCH_COMMIT_SIZE:
                _flush_follow_batch(conn, edge_batch, ghost_batch, done_batch, error_batch)
                reporter.add_rows(total_edges)
                if total_edges - last_batch_report >= BATCH_COMMIT_SIZE:
                    print(
                        f"  [batch] {done_actors:,} actors done, {total_edges:,} edges",
                        file=sys.stderr,
                    )
                    last_batch_report = total_edges
                edge_batch = []
                ghost_batch = []
                done_batch = []
                error_batch = []

    except KeyboardInterrupt:
        print("\n  Interrupted, saving progress...", file=sys.stderr)
        stop_event.set()

    if edge_batch or ghost_batch or done_batch or error_batch:
        _flush_follow_batch(conn, edge_batch, ghost_batch, done_batch, error_batch)
    reporter.add_rows(total_edges)

    stop_event.set()
    for t in workers:
        t.join(timeout=5)

    reporter.stop()
    reporter.print_summary()

    crawled_count = conn.execute(
        "SELECT COUNT(*) FROM actors WHERE follows_crawled=1"
    ).fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0]
    print(f"  Crawled actors: {crawled_count:,}", file=sys.stderr)
    print(f"  Total edges: {edge_count:,}", file=sys.stderr)


def _flush_follow_batch(conn, edge_batch, ghost_batch, done_batch, error_batch):
    if ghost_batch:
        conn.executemany(
            "INSERT OR IGNORE INTO actors (did, is_active) VALUES (?, 0)",
            ghost_batch,
        )
    if edge_batch:
        conn.executemany(
            "INSERT OR IGNORE INTO follows (follower_did, followee_did) VALUES (?, ?)",
            edge_batch,
        )
    if done_batch:
        conn.executemany(
            "UPDATE actors SET follows_crawled=1, last_error=COALESCE(?, last_error) WHERE did=?",
            done_batch,
        )
    for err, did in error_batch:
        conn.execute(
            "UPDATE actors SET error_count=error_count+1, last_error=? WHERE did=?",
            (err, did),
        )
    conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
    conn.commit()


def print_db_stats(conn: sqlite3.Connection, db_path: Path):
    actor_count = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
    active_count = conn.execute(
        "SELECT COUNT(*) FROM actors WHERE is_active=1"
    ).fetchone()[0]
    profiled_count = conn.execute(
        "SELECT COUNT(*) FROM actors WHERE profiled=1"
    ).fetchone()[0]
    crawled_count = conn.execute(
        "SELECT COUNT(*) FROM actors WHERE follows_crawled=1"
    ).fetchone()[0]
    edge_count = conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0]

    db_size_mb = 0
    if db_path.exists():
        db_size_mb = db_path.stat().st_size / (1024 * 1024)

    print(f"\n  Database: {db_path}", file=sys.stderr)
    print(f"  Size on disk: {db_size_mb:.1f} MB", file=sys.stderr)
    print(f"  Actors: {actor_count:,} ({active_count:,} active, {profiled_count:,} profiled, {crawled_count:,} crawled)", file=sys.stderr)
    print(f"  Follow edges: {edge_count:,}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Crawl active Bluesky users into SQLite"
    )
    parser.add_argument(
        "--phase",
        required=True,
        choices=["enumerate", "profile", "crawl-follows", "all"],
        help="Which phase to run",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=2,
        help="Number of days to look back for active repos (default: 2)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=10,
        help="Number of worker threads for profile/follow phases (default: 10)",
    )
    parser.add_argument(
        "--db",
        type=str,
        default=str(DATA_DIR / "active_users.db"),
        help="Path to SQLite database (default: data/active_users.db)",
    )
    parser.add_argument(
        "--max-follows-per-user",
        type=int,
        default=1000,
        help="Cap on follow edges to fetch per user (default: 1000)",
    )
    parser.add_argument(
        "--rate-limit",
        type=int,
        default=500,
        help="AppView requests per minute for profile/follow phases (default: 500)",
    )
    parser.add_argument(
        "--relay-rate-limit",
        type=int,
        default=100,
        help="Relay requests per minute for enumerate phase (default: 100)",
    )
    parser.add_argument(
        "--include-ghosts",
        action="store_true",
        help="Also profile non-active (ghost) actors discovered from follow edges",
    )

    args = parser.parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening database: {db_path}", file=sys.stderr)
    conn = init_db(db_path)

    try:
        if args.phase == "enumerate":
            phase_enumerate(conn, args)
        elif args.phase == "profile":
            phase_profile(conn, args)
        elif args.phase == "crawl-follows":
            phase_crawl_follows(conn, args)
        elif args.phase == "all":
            phase_enumerate(conn, args)
            print_db_stats(conn, db_path)
            phase_profile(conn, args)
            print_db_stats(conn, db_path)
            phase_crawl_follows(conn, args)
            print_db_stats(conn, db_path)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
