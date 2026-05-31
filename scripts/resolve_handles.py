#!/usr/bin/env python3
"""
Batch resolve handles for actors in the database.
Profiles the top N actors (by in-degree) that are missing handles.
"""

import json
import sqlite3
import time
import sys
import urllib.request
import urllib.error
from pathlib import Path

DATA_DIR = Path(__file__).parent.parent / "data"
PUBLIC_API = "https://public.api.bsky.app/xrpc"
REQUEST_DELAY = 0.25


def resolve_handle(did: str) -> str | None:
    """Get handle for a DID."""
    url = f"{PUBLIC_API}/app.bsky.actor.getProfile?actor={did}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read())
            return data.get("handle")
    except Exception:
        return None


def main():
    db = sqlite3.connect(str(DATA_DIR / "simcluster.db"))
    db.execute("PRAGMA journal_mode=WAL")

    # Find actors with NULL handle
    nulls = db.execute("""
        SELECT a.did, COUNT(f.target) as indeg
        FROM actors a
        LEFT JOIN follows f ON f.target = a.did
        WHERE a.handle IS NULL
        GROUP BY a.did
        ORDER BY indeg DESC
    """).fetchall()

    print(f"{len(nulls)} actors with NULL handles, resolving top 500 by in-degree")

    for i, (did, indeg) in enumerate(nulls[:500]):
        handle = resolve_handle(did)
        if handle:
            db.execute(
                "UPDATE actors SET handle=? WHERE did=?",
                (handle, did),
            )
            if i % 50 == 0:
                db.commit()
                print(f"  [{i}/{min(500, len(nulls))}] {handle} (in-deg={indeg})")
        time.sleep(REQUEST_DELAY)

    db.commit()

    # Also get profile info for the top handles
    print("\nFetching profile details for top 200 by in-degree...")
    top = db.execute("""
        SELECT a.did, COUNT(f.target) as indeg
        FROM actors a
        JOIN follows f ON f.target = a.did
        GROUP BY a.did
        ORDER BY indeg DESC
        LIMIT 200
    """).fetchall()

    for i, (did, indeg) in enumerate(top):
        url = f"{PUBLIC_API}/app.bsky.actor.getProfile?actor={did}"
        try:
            with urllib.request.urlopen(url, timeout=10) as r:
                data = json.loads(r.read())
            db.execute(
                """UPDATE actors SET
                   handle=?, display_name=?, description=?,
                   follows_count=?, followers_count=?, posts_count=?
                   WHERE did=?""",
                (
                    data.get("handle", ""),
                    data.get("displayName", ""),
                    data.get("description", ""),
                    data.get("followsCount", 0),
                    data.get("followersCount", 0),
                    data.get("postsCount", 0),
                    did,
                ),
            )
            if i % 25 == 0:
                db.commit()
                print(f"  [{i}/{len(top)}] {data.get('handle', 'N/A')}")
        except Exception:
            pass
        time.sleep(REQUEST_DELAY)

    db.commit()
    resolved = db.execute(
        "SELECT COUNT(*) FROM actors WHERE handle IS NOT NULL"
    ).fetchone()[0]
    print(f"\nDone. {resolved}/{db.execute('SELECT COUNT(*) FROM actors').fetchone()[0]} actors have handles")
    db.close()


if __name__ == "__main__":
    main()
