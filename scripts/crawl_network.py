#!/usr/bin/env python3
"""
Snowball sampler for Bluesky simcluster network.

Crawls follows/followers starting from seed accounts using the public
Bluesky API (bsky.social appview). Builds a directed graph of accounts
in the simcluster community.

Strategy:
1. Start with seed accounts known to be in the simcluster
2. Fetch their followers and following (public API, rate-limited)
3. Filter: keep accounts that follow at least K other sampled accounts
   (this keeps us inside the community rather than escaping to the whole network)
4. BFS-style expansion until target size or convergence
"""

import json
import time
import sys
import os
import sqlite3
import urllib.request
import urllib.error
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"
DATA_DIR.mkdir(exist_ok=True)

# Public Bluesky API
PUBLIC_API = "https://public.api.bsky.app/xrpc"

# Seed accounts — known simcluster members
# These come from Samantha's follows, the @simcluster Twitter account migration,
# and the simcluster.ai/simcluster.social ecosystem
SEEDS = [
    "abeliansoup.bsky.social",
    "solarapparition.bsky.social",
    "prer.at",
    "movail.bsky.social",
    "yielding.bsky.social",
    "ganweaving.bsky.social",
    "simcluster.social",
    "samantha.wiki",
    "clamclaw.pds.samantha.wiki",
]

# Expand seeds by looking at more known simcluster accounts
# These discovered from the Bisk dashboard, simcluster museum, and profile cross-references
DISCOVERED_SEEDS = [
    "norvid-studies.bsky.social",
    "void.comind.network",
    "kira.pds.witchcraft.systems",
    "seasaltshrimp.bsky.social",
    "ai.bsky.art",
]

ALL_SEEDS = SEEDS + DISCOVERED_SEEDS

# Rate limiting (public API is generous but be polite)
REQUEST_DELAY = 0.3  # seconds between requests
BATCH_SIZE = 25  # ATProto max


def resolve_handle(handle: str) -> str | None:
    """Resolve a handle to a DID via public API."""
    url = f"{PUBLIC_API}/com.atproto.identity.resolveHandle?handle={handle}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            return json.loads(r.read())["did"]
    except Exception as e:
        print(f"  resolve failed for {handle}: {e}", file=sys.stderr)
        return None


def fetch_page(url: str, max_retries: int = 3) -> dict | None:
    """Fetch an API page with retries."""
    for attempt in range(max_retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5 * (attempt + 1)
                print(f"  rate limited, waiting {wait}s...", file=sys.stderr)
                time.sleep(wait)
            else:
                print(f"  HTTP {e.code} for {url[:80]}...", file=sys.stderr)
                return None
        except Exception as e:
            print(f"  fetch error: {e}", file=sys.stderr)
            time.sleep(2)
    return None


def fetch_follows(did: str, direction: str = "follows", limit: int = 500) -> list[dict]:
    """
    Fetch follows or followers for a DID.
    direction: 'follows' or 'followers'
    Returns list of actor DIDs.
    """
    collection = "app.bsky.graph.follow"
    actors = []
    cursor = None

    while len(actors) < limit:
        url = f"{PUBLIC_API}/app.bsky.graph.get{direction.capitalize()}?actor={did}&limit={min(BATCH_SIZE, limit - len(actors))}"
        if cursor:
            url += f"&cursor={cursor}"

        data = fetch_page(url)
        if data is None:
            break

        batch = data.get("follows") or data.get("followers", [])
        actors.extend([a["did"] for a in batch])

        cursor = data.get("cursor")
        if not cursor or len(batch) == 0:
            break

        time.sleep(REQUEST_DELAY)

    return actors


def fetch_profile(did: str) -> dict | None:
    """Fetch profile info for a DID."""
    url = f"{PUBLIC_API}/app.bsky.actor.getProfile?actor={did}"
    return fetch_page(url)


def main():
    db_path = DATA_DIR / "simcluster.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")

    # Schema
    conn.execute("""
        CREATE TABLE IF NOT EXISTS actors (
            did TEXT PRIMARY KEY,
            handle TEXT,
            display_name TEXT,
            description TEXT,
            follows_count INTEGER,
            followers_count INTEGER,
            posts_count INTEGER,
            indexed_at TEXT,
            is_seed BOOLEAN DEFAULT 0,
            crawled_follows BOOLEAN DEFAULT 0,
            crawled_followers BOOLEAN DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            source TEXT,
            target TEXT,
            PRIMARY KEY (source, target)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS crawl_state (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)
    conn.commit()

    # Resolve seeds
    print("Resolving seed accounts...")
    seed_dids = {}
    for handle in ALL_SEEDS:
        did = resolve_handle(handle)
        if did:
            seed_dids[did] = handle
            conn.execute(
                "INSERT OR IGNORE INTO actors (did, handle, is_seed) VALUES (?, ?, 1)",
                (did, handle),
            )
            print(f"  {handle} -> {did}")
        time.sleep(0.2)

    conn.commit()
    print(f"Resolved {len(seed_dids)}/{len(ALL_SEEDS)} seeds\n")

    # BFS crawl: for each seed, fetch follows, then for followers that intersect
    # significantly, fetch their follows too
    queue = list(seed_dids.keys())
    crawled = set()

    # Phase 1: fetch follows for all seeds
    print("Phase 1: Fetching follows for seed accounts")
    for did in queue[:]:
        profile = fetch_profile(did)
        if profile:
            conn.execute(
                """UPDATE actors SET handle=?, display_name=?, description=?,
                   follows_count=?, followers_count=?, posts_count=?, indexed_at=?
                   WHERE did=?""",
                (
                    profile.get("handle", ""),
                    profile.get("displayName", ""),
                    profile.get("description", ""),
                    profile.get("followsCount", 0),
                    profile.get("followersCount", 0),
                    profile.get("postsCount", 0),
                    profile.get("indexedAt", ""),
                    did,
                ),
            )
            conn.commit()

        # Fetch follows
        print(f"  fetching follows for {conn.execute('SELECT handle FROM actors WHERE did=?', (did,)).fetchone()[0]}...")
        follows = fetch_follows(did, "follows", limit=500)
        for target in follows:
            conn.execute(
                "INSERT OR IGNORE INTO actors (did) VALUES (?)", (target,)
            )
            conn.execute(
                "INSERT OR IGNORE INTO follows (source, target) VALUES (?, ?)",
                (did, target),
            )
        conn.execute(
            "UPDATE actors SET crawled_follows=1 WHERE did=?", (did,)
        )
        conn.commit()
        crawled.add(did)
        time.sleep(REQUEST_DELAY)
        print(f"    got {len(follows)} follows")

    # Phase 2: which of the seed-target accounts are in the simcluster?
    # Define: accounts that are followed by >=2 seeds
    print("\nPhase 2: Expanding to in-community accounts")
    rows = conn.execute("""
        SELECT f.target, COUNT(DISTINCT f.source) as n_seeds, a.handle
        FROM follows f
        JOIN actors a ON a.did = f.target
        WHERE f.source IN ({})
        GROUP BY f.target
        HAVING n_seeds >= 2
        ORDER BY n_seeds DESC
    """.format(",".join("?" * len(seed_dids))), list(seed_dids.keys())).fetchall()

    print(f"  {len(rows)} accounts followed by >=2 seeds")
    for row in rows[:30]:
        print(f"    {row[2]} ({row[1]} seeds)")

    # Phase 3: fetch follows for top community members
    phase3 = [r[0] for r in rows[:100] if r[0] not in crawled]
    print(f"\nPhase 3: Fetching follows for {len(phase3)} community members")

    for i, did in enumerate(phase3):
        handle = conn.execute(
            "SELECT handle FROM actors WHERE did=?", (did,)
        ).fetchone()
        handle = handle[0] if handle else did

        profile = fetch_profile(did)
        if profile:
            conn.execute(
                """UPDATE actors SET handle=?, display_name=?, description=?,
                   follows_count=?, followers_count=?, posts_count=?, indexed_at=?
                   WHERE did=?""",
                (
                    profile.get("handle", ""),
                    profile.get("displayName", ""),
                    profile.get("description", ""),
                    profile.get("followsCount", 0),
                    profile.get("followersCount", 0),
                    profile.get("postsCount", 0),
                    profile.get("indexedAt", ""),
                    did,
                ),
            )
            conn.commit()

        print(f"  [{i+1}/{len(phase3)}] {handle}")
        follows = fetch_follows(did, "follows", limit=300)
        for target in follows:
            conn.execute(
                "INSERT OR IGNORE INTO actors (did) VALUES (?)", (target,)
            )
            conn.execute(
                "INSERT OR IGNORE INTO follows (source, target) VALUES (?, ?)",
                (did, target),
            )
        conn.execute(
            "UPDATE actors SET crawled_follows=1 WHERE did=?", (did,)
        )
        conn.commit()
        crawled.add(did)
        time.sleep(REQUEST_DELAY)
        print(f"    got {len(follows)} follows")

    # Save state
    conn.execute(
        "INSERT OR REPLACE INTO crawl_state VALUES ('last_crawl', ?)",
        (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),),
    )
    conn.commit()

    # Summary
    actor_count = conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0]
    follow_count = conn.execute("SELECT COUNT(*) FROM follows").fetchone()[0]
    crawled_count = conn.execute(
        "SELECT COUNT(*) FROM actors WHERE crawled_follows=1"
    ).fetchone()[0]
    print(f"\nCrawl complete: {actor_count} actors, {follow_count} follows, {crawled_count} crawled")
    conn.close()


if __name__ == "__main__":
    main()
