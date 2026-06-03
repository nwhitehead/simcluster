#!/usr/bin/env python3
"""
Collect active Bluesky users from the Jetstream firehose and fetch their profiles.

Connects to a Jetstream WebSocket to discover active DIDs in real time, then
fetches profile metadata (handle, follower/following counts) via the public
AppView API. Everything runs concurrently in a single process.

Usage:
  python scripts/firehose_collect.py
  python scripts/firehose_collect.py --duration 60 --max-users 100000
  python scripts/firehose_collect.py --workers 10 --rate-limit 500
"""

import argparse
import asyncio
import json
import queue
import signal
import sqlite3
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import websockets

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

PUBLIC_API = "https://public.api.bsky.app/xrpc"

CURSOR_SAVE_INTERVAL = 10
DB_COMMIT_BATCH = 1000
DB_COMMIT_INTERVAL = 5
DISPATCHER_BATCH = 5000
DID_QUEUE_MAXSIZE = 50_000
ERROR_SKIP_THRESHOLD = 5
MAX_RETRIES = 3
PROFILE_BATCH_SIZE = 25
REPORT_INTERVAL = 10
WRITE_QUEUE_MAXSIZE = 200_000

SCHEMA_SQL = """
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
"""


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
    def __init__(self):
        self.start_time = time.monotonic()
        self.total_events = 0
        self.new_dids = 0
        self.profiled = 0
        self.errors = 0
        self.errors_by_type: dict[str, int] = {}
        self.lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._report_loop, daemon=True)
        self._get_write_queue_depth = lambda: 0
        self._get_did_queue_depth = lambda: 0
        self._get_active_workers = lambda: 0

    def start(self, get_write_queue_depth=None, get_did_queue_depth=None,
              get_active_workers=None):
        self._get_write_queue_depth = get_write_queue_depth or (lambda: 0)
        self._get_did_queue_depth = get_did_queue_depth or (lambda: 0)
        self._get_active_workers = get_active_workers or (lambda: 0)
        self._thread.start()

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=15)

    def add_events(self, n: int):
        with self.lock:
            self.total_events += n

    def add_new_dids(self, n: int):
        with self.lock:
            self.new_dids += n

    def add_profiled(self, n: int):
        with self.lock:
            self.profiled += n

    def add_error(self, error_type: str):
        with self.lock:
            self.errors += 1
            self.errors_by_type[error_type] = self.errors_by_type.get(error_type, 0) + 1

    def _report_loop(self):
        while not self._stop.wait(timeout=REPORT_INTERVAL):
            self._print_status()

    def _print_status(self):
        with self.lock:
            elapsed = time.monotonic() - self.start_time
            events = self.total_events
            dids = self.new_dids
            profiled = self.profiled
            errors = self.errors
            err_breakdown = dict(self.errors_by_type)
        ev_rate = events / elapsed if elapsed > 0 else 0
        did_rate = dids / elapsed if elapsed > 0 else 0
        prof_rate = profiled / elapsed if elapsed > 0 else 0
        wq = self._get_write_queue_depth()
        dq = self._get_did_queue_depth()
        workers = self._get_active_workers()
        print(
            f"  [{elapsed:.0f}s] events={events:,} ({ev_rate:.0f}/s) | "
            f"new_dids={dids:,} ({did_rate:.0f}/s) | "
            f"profiled={profiled:,} ({prof_rate:.1f}/s) | "
            f"write_q={wq:,} did_q={dq:,} workers={workers} | "
            f"errors={errors} {err_breakdown}",
            file=sys.stderr,
        )

    def print_summary(self):
        with self.lock:
            elapsed = time.monotonic() - self.start_time
            events = self.total_events
            dids = self.new_dids
            profiled = self.profiled
            errors = self.errors
            err_breakdown = dict(self.errors_by_type)
        ev_rate = events / elapsed if elapsed > 0 else 0
        did_rate = dids / elapsed if elapsed > 0 else 0
        prof_rate = profiled / elapsed if elapsed > 0 else 0
        print(f"\n{'='*60}", file=sys.stderr)
        print(f"  FIREHOSE COLLECTION COMPLETE", file=sys.stderr)
        print(f"  Events: {events:,} ({ev_rate:.0f}/s)", file=sys.stderr)
        print(f"  New DIDs: {dids:,} ({did_rate:.0f}/s)", file=sys.stderr)
        print(f"  Profiled: {profiled:,} ({prof_rate:.1f}/s)", file=sys.stderr)
        print(f"  Errors: {errors} ({err_breakdown})", file=sys.stderr)
        print(f"  Elapsed: {elapsed:.0f}s", file=sys.stderr)
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


def set_state(conn: sqlite3.Connection, key: str, value: str,
              write_lock=None):
    if write_lock:
        with write_lock:
            conn.execute("INSERT OR REPLACE INTO crawl_state (key, value) VALUES (?, ?)", (key, value))
            conn.commit()
    else:
        conn.execute("INSERT OR REPLACE INTO crawl_state (key, value) VALUES (?, ?)", (key, value))
        conn.commit()


def us_to_iso(us: int) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(us / 1_000_000))


class FirehoseThread:
    def __init__(self, jetstream_url: str, write_queue: queue.Queue,
                 conn: sqlite3.Connection, stop_event: threading.Event,
                 reporter: ProgressReporter, max_users: int = 0,
                 db_write_lock=None):
        self.jetstream_url = jetstream_url
        self.write_queue = write_queue
        self.conn = conn
        self.stop_event = stop_event
        self.reporter = reporter
        self.max_users = max_users
        self.db_write_lock = db_write_lock
        self.seen_dids: set[str] = set()
        self.last_cursor_us: int | None = None
        self._last_cursor_save = 0.0
        self.done_event = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def join(self, timeout: float = None):
        self._thread.join(timeout=timeout)

    def _get_connect_url(self) -> str:
        url = self.jetstream_url
        cursor_us = get_state(self.conn, "firehose_cursor")
        if cursor_us:
            try:
                rewind = int(cursor_us) - 5_000_000
                url += f"?cursor={rewind}"
            except ValueError:
                pass
        return url

    def _save_cursor(self, cursor_us: int):
        set_state(self.conn, "firehose_cursor", str(cursor_us), self.db_write_lock)

    def _maybe_save_cursor(self, cursor_us: int):
        now = time.monotonic()
        if now - self._last_cursor_save >= CURSOR_SAVE_INTERVAL:
            self._save_cursor(cursor_us)
            self._last_cursor_save = now

    def save_final_cursor(self):
        if self.last_cursor_us is not None:
            self._save_cursor(self.last_cursor_us)

    def _run(self):
        while not self.stop_event.is_set() and not self.done_event.is_set():
            try:
                asyncio.run(self._connect_loop())
            except websockets.ConnectionClosed:
                pass
            except (ConnectionResetError, OSError):
                if not self.stop_event.is_set():
                    time.sleep(2)
            except Exception as e:
                print(f"  [firehose] error: {e}", file=sys.stderr)
                if not self.stop_event.is_set():
                    time.sleep(5)

    async def _connect_loop(self):
        url = self._get_connect_url()
        print(f"  [firehose] connecting to {url}", file=sys.stderr)

        async with websockets.connect(url) as ws:
            async for raw in ws:
                if self.stop_event.is_set() or self.done_event.is_set():
                    break

                try:
                    event = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                cursor_us = event.get("time_us")
                if cursor_us:
                    self.last_cursor_us = cursor_us
                    self._maybe_save_cursor(cursor_us)

                did = event.get("did")
                if not did:
                    continue

                self.reporter.add_events(1)

                if did in self.seen_dids:
                    continue
                self.seen_dids.add(did)
                self.reporter.add_new_dids(1)

                kind = event.get("kind", "")
                time_us = event.get("time_us", int(time.time() * 1_000_000))
                first_seen = us_to_iso(time_us)

                if kind == "identity":
                    handle = event.get("identity", {}).get("handle")
                    if handle:
                        try:
                            self.write_queue.put_nowait(
                                ("new_did_with_handle", did, handle, first_seen)
                            )
                        except queue.Full:
                            pass
                        continue

                try:
                    self.write_queue.put_nowait(("new_did", did, first_seen))
                except queue.Full:
                    pass

                if self.max_users > 0 and len(self.seen_dids) >= self.max_users:
                    print(
                        f"  [firehose] reached --max-users ({self.max_users:,}), stopping firehose",
                        file=sys.stderr,
                    )
                    self.done_event.set()
                    break


class DispatcherThread:
    def __init__(self, conn: sqlite3.Connection, did_queue: queue.Queue,
                 stop_event: threading.Event):
        self.conn = conn
        self.did_queue = did_queue
        self.stop_event = stop_event
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def join(self, timeout: float = None):
        self._thread.join(timeout=timeout)

    def _run(self):
        dispatched: set[str] = set()
        offset = 0
        while not self.stop_event.is_set():
            dids = [
                r[0] for r in self.conn.execute(
                    "SELECT did FROM users "
                    "WHERE profiled_at IS NULL AND error_count < ? "
                    "ORDER BY did LIMIT ? OFFSET ?",
                    (ERROR_SKIP_THRESHOLD, DISPATCHER_BATCH, offset),
                ).fetchall()
            ]
            if not dids:
                offset = 0
                dispatched.clear()
                self.stop_event.wait(timeout=5)
                continue

            new_dids = [d for d in dids if d not in dispatched]
            if new_dids:
                for did in new_dids:
                    if self.stop_event.is_set():
                        break
                    dispatched.add(did)
                    self.did_queue.put(did)
            offset += DISPATCHER_BATCH

            if len(dispatched) > 1_000_000:
                dispatched.clear()
                offset = 0


class ProfileWorker:
    def __init__(self, did_queue: queue.Queue, write_queue: queue.Queue,
                 rate_limiter: RateLimiter, backoff: SharedBackoff,
                 stop_event: threading.Event, reporter: ProgressReporter,
                 active_count: list[int], active_lock: threading.Lock):
        self.did_queue = did_queue
        self.write_queue = write_queue
        self.rate_limiter = rate_limiter
        self.backoff = backoff
        self.stop_event = stop_event
        self.reporter = reporter
        self.active_count = active_count
        self.active_lock = active_lock
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def join(self, timeout: float = None):
        self._thread.join(timeout=timeout)

    def _run(self):
        with self.active_lock:
            self.active_count[0] += 1
        try:
            while not self.stop_event.is_set():
                batch_dids = []
                for _ in range(PROFILE_BATCH_SIZE):
                    try:
                        did = self.did_queue.get_nowait()
                        batch_dids.append(did)
                    except queue.Empty:
                        break

                if not batch_dids:
                    if self.stop_event.is_set():
                        break
                    self.stop_event.wait(timeout=0.5)
                    continue

                actors_param = "&actors=".join(batch_dids)
                url = f"{PUBLIC_API}/app.bsky.actor.getProfiles?actors={actors_param}"

                data, err = api_request(url, self.rate_limiter, self.backoff)

                if data is not None:
                    profiles = {}
                    for p in data.get("profiles", []):
                        did = p.get("did", "")
                        if did:
                            profiles[did] = p

                    for did in batch_dids:
                        p = profiles.get(did)
                        if p:
                            self.write_queue.put((
                                "profile", did,
                                p.get("handle", ""),
                                p.get("followersCount", 0),
                                p.get("followsCount", 0),
                            ))
                        else:
                            self.write_queue.put(("error", did, "no_profile_data"))
                            self.reporter.add_error("no_profile_data")
                else:
                    if err == "deleted":
                        for did in batch_dids:
                            self.write_queue.put(("error", did, "deleted"))
                    else:
                        for did in batch_dids:
                            self.write_queue.put(("error", did, err or "unknown"))
                        self.reporter.add_error(err or "unknown")
        finally:
            with self.active_lock:
                self.active_count[0] -= 1


class DBWriter:
    FLUSH_SENTINEL = ("__flush__",)

    def __init__(self, write_queue: queue.Queue, conn: sqlite3.Connection,
                 stop_event: threading.Event, reporter: ProgressReporter,
                 db_write_lock=None):
        self.write_queue = write_queue
        self.conn = conn
        self.stop_event = stop_event
        self.reporter = reporter
        self.db_write_lock = db_write_lock or threading.Lock()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self._thread.start()

    def join(self, timeout: float = None):
        self._thread.join(timeout=timeout)

    def flush(self):
        ack = threading.Event()
        self.write_queue.put(("__flush__", ack))
        ack.wait(timeout=10)

    def _run(self):
        new_did_batch: list[tuple] = []
        profile_batch: list[tuple] = []
        error_batch: list[tuple] = []
        last_commit = time.monotonic()
        total_pending = 0

        while True:
            try:
                msg = self.write_queue.get(timeout=1.0)
            except queue.Empty:
                if self.stop_event.is_set() and self.write_queue.empty():
                    break
                now = time.monotonic()
                if total_pending > 0 and now - last_commit >= DB_COMMIT_INTERVAL:
                    self._flush(new_did_batch, profile_batch, error_batch)
                    new_did_batch = []
                    profile_batch = []
                    error_batch = []
                    total_pending = 0
                    last_commit = now
                continue

            if msg[0] == "__flush__":
                if total_pending > 0:
                    self._flush(new_did_batch, profile_batch, error_batch)
                    new_did_batch = []
                    profile_batch = []
                    error_batch = []
                    total_pending = 0
                    last_commit = time.monotonic()
                if len(msg) > 1:
                    msg[1].set()
                continue

            msg_type = msg[0]

            if msg_type == "new_did":
                _, did, first_seen_at = msg
                new_did_batch.append((did, first_seen_at))
                total_pending += 1
            elif msg_type == "new_did_with_handle":
                _, did, handle, first_seen_at = msg
                new_did_batch.append((did, handle, first_seen_at))
                total_pending += 1
            elif msg_type == "profile":
                _, did, handle, followers, follows = msg
                profile_batch.append((handle, followers, follows, did))
                total_pending += 1
                self.reporter.add_profiled(1)
            elif msg_type == "error":
                _, did, error_type = msg
                error_batch.append((error_type, did))
                total_pending += 1

            if total_pending >= DB_COMMIT_BATCH:
                self._flush(new_did_batch, profile_batch, error_batch)
                new_did_batch = []
                profile_batch = []
                error_batch = []
                total_pending = 0
                last_commit = time.monotonic()

        if new_did_batch or profile_batch or error_batch:
            self._flush(new_did_batch, profile_batch, error_batch)

    def _flush(self, new_did_batch, profile_batch, error_batch):
        with self.db_write_lock:
            if new_did_batch:
                rows_plain = []
                rows_with_handle = []
                for item in new_did_batch:
                    if len(item) == 2:
                        rows_plain.append(item)
                    else:
                        rows_with_handle.append(item)

                if rows_plain:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO users (did, first_seen_at) VALUES (?, ?)",
                        rows_plain,
                    )
                if rows_with_handle:
                    self.conn.executemany(
                        "INSERT OR IGNORE INTO users (did, handle, first_seen_at) VALUES (?, ?, ?)",
                        rows_with_handle,
                    )

            if profile_batch:
                self.conn.executemany(
                    "UPDATE users SET handle=?, followers_count=?, follows_count=?, "
                    "profiled_at=strftime('%Y-%m-%dT%H:%M:%SZ','now') "
                    "WHERE did=?",
                    profile_batch,
                )

            for error_type, did in error_batch:
                if error_type == "deleted":
                    self.conn.execute(
                        "UPDATE users SET profiled_at=strftime('%Y-%m-%dT%H:%M:%SZ','now'), "
                        "last_error=? WHERE did=?",
                        (error_type, did),
                    )
                else:
                    self.conn.execute(
                        "UPDATE users SET error_count=error_count+1, last_error=? WHERE did=?",
                        (error_type, did),
                    )

            self.conn.commit()


def print_db_stats(conn: sqlite3.Connection, db_path: Path):
    total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    profiled = conn.execute(
        "SELECT COUNT(*) FROM users WHERE profiled_at IS NOT NULL"
    ).fetchone()[0]
    errors = conn.execute(
        "SELECT COUNT(*) FROM users WHERE error_count >= ?", (ERROR_SKIP_THRESHOLD,)
    ).fetchone()[0]
    pending = total - profiled - errors

    db_size_mb = 0.0
    if db_path.exists():
        db_size_mb = db_path.stat().st_size / (1024 * 1024)

    print(f"\n  Database: {db_path}", file=sys.stderr)
    print(f"  Size: {db_size_mb:.1f} MB", file=sys.stderr)
    print(f"  Total DIDs: {total:,}", file=sys.stderr)
    print(f"  Profiled: {profiled:,}", file=sys.stderr)
    print(f"  Pending: {pending:,}", file=sys.stderr)
    print(f"  Errored (skipped): {errors:,}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(
        description="Collect active Bluesky users from the Jetstream firehose"
    )
    parser.add_argument(
        "--workers", type=int, default=5,
        help="Profile worker threads (default: 5)",
    )
    parser.add_argument(
        "--rate-limit", type=int, default=300,
        help="AppView requests/min (default: 300)",
    )
    parser.add_argument(
        "--db", type=str, default=str(DATA_DIR / "firehose_users.db"),
        help="SQLite path (default: data/firehose_users.db)",
    )
    parser.add_argument(
        "--jetstream", type=str,
        default="wss://jetstream2.us-east.bsky.network/subscribe",
        help="Jetstream WebSocket URL",
    )
    parser.add_argument(
        "--duration", type=int, default=0,
        help="Stop after N seconds (default: 0 = run until Ctrl+C)",
    )
    parser.add_argument(
        "--max-users", type=int, default=0,
        help="Stop firehose after N unique DIDs seen (default: 0 = unlimited)",
    )

    args = parser.parse_args()
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Opening database: {db_path}", file=sys.stderr)
    conn = init_db(db_path)

    stop_event = threading.Event()
    write_queue: queue.Queue = queue.Queue(maxsize=WRITE_QUEUE_MAXSIZE)
    did_queue: queue.Queue = queue.Queue(maxsize=DID_QUEUE_MAXSIZE)
    db_write_lock = threading.Lock()
    reporter = ProgressReporter()
    rate_limiter = RateLimiter(args.rate_limit)
    backoff = SharedBackoff()
    active_workers = [0]
    active_lock = threading.Lock()

    original_sigint = signal.getsignal(signal.SIGINT)

    def sigint_handler(signum, frame):
        if stop_event.is_set():
            print("\n  Force exit.", file=sys.stderr)
            signal.signal(signal.SIGINT, original_sigint)
            return
        print("\n  Stopping... (Ctrl+C again to force)", file=sys.stderr)
        stop_event.set()

    signal.signal(signal.SIGINT, sigint_handler)

    firehose = FirehoseThread(
        args.jetstream, write_queue, conn, stop_event, reporter,
        max_users=args.max_users, db_write_lock=db_write_lock,
    )
    dispatcher = DispatcherThread(conn, did_queue, stop_event)

    workers = []
    for _ in range(args.workers):
        w = ProfileWorker(
            did_queue, write_queue, rate_limiter, backoff,
            stop_event, reporter, active_workers, active_lock,
        )
        workers.append(w)

    db_writer = DBWriter(write_queue, conn, stop_event, reporter,
                         db_write_lock=db_write_lock)

    reporter.start(
        get_write_queue_depth=lambda: write_queue.qsize(),
        get_did_queue_depth=lambda: did_queue.qsize(),
        get_active_workers=lambda: active_workers[0],
    )

    print(f"  Starting firehose collector ({args.workers} workers, {args.rate_limit} req/min)", file=sys.stderr)

    db_writer.start()
    for w in workers:
        w.start()
    dispatcher.start()
    firehose.start()

    if args.duration > 0:
        firehose.done_event.wait(timeout=args.duration)
        if not firehose.done_event.is_set():
            print(f"  --duration ({args.duration}s) reached, stopping firehose", file=sys.stderr)
            firehose.done_event.set()

    firehose.join(timeout=30)
    firehose.save_final_cursor()
    print("  [shutdown] firehose stopped", file=sys.stderr)

    db_writer.flush()

    while not stop_event.is_set():
        unprofiled = conn.execute(
            "SELECT COUNT(*) FROM users WHERE profiled_at IS NULL AND error_count < ?",
            (ERROR_SKIP_THRESHOLD,),
        ).fetchone()[0]
        with active_lock:
            num_active = active_workers[0]
        if unprofiled == 0 and num_active == 0 and did_queue.empty() and write_queue.empty():
            break
        stop_event.wait(timeout=5)

    stop_event.set()

    dispatcher.join(timeout=10)
    print("  [shutdown] dispatcher stopped", file=sys.stderr)

    for w in workers:
        w.join(timeout=60)
    print("  [shutdown] workers stopped", file=sys.stderr)

    db_writer.join(timeout=30)
    print("  [shutdown] db_writer stopped", file=sys.stderr)

    reporter.stop()
    reporter.print_summary()

    print_db_stats(conn, db_path)
    conn.close()


if __name__ == "__main__":
    main()
