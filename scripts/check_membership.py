#!/usr/bin/env python3
"""
Are you in the simcluster? — CLI diagnostic tool.

Given a Bluesky handle (or DID), queries the public Bluesky API to compute
the user's Simcluster Score (0-100) and membership tier.

Usage:
    python check_membership.py <handle_or_did>

Examples:
    python check_membership.py abeliansoup.bsky.social
    python check_membership.py did:plc:kbdtfnqrqpwwf62f6eekr7m2
"""

import json
import sys
import time
import urllib.request
import urllib.error

PUBLIC_API = "https://public.api.bsky.app/xrpc"
REQUEST_DELAY = 0.3

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
    "norvid-studies.bsky.social",
    "void.comind.network",
    "kira.pds.witchcraft.systems",
    "seasaltshrimp.bsky.social",
    "ai.bsky.art",
]

HUB_ACCOUNTS = [
    "abeliansoup.bsky.social",
    "vibe-coded.com",
    "vgel.me",
    "cee.wtf",
    "carbonadoks.com",
    "joshuashew.bsky.social",
    "godoglyness.bsky.social",
    "tbabb.bsky.social",
    "croissanthology.com",
    "astrra.space",
    "isolyth.dev",
    "tautologer.com",
    "moskov.goodventures.org",
    "minormobius.bsky.social",
    "dave.9000ish.uk",
]

TIER_NAMES = {
    (80, 101): ("SEED / INNER CORE", "\033[95m"),
    (60, 80):  ("CORE", "\033[94m"),
    (40, 60):  ("ADJACENT", "\033[92m"),
    (20, 40):  ("PERIPHERAL", "\033[93m"),
    (1, 20):   ("CURIOUS", "\033[90m"),
    (0, 1):    ("OUTSIDE", "\033[37m"),
}


def api_get(endpoint, params=None):
    url = f"{PUBLIC_API}/{endpoint}"
    if params:
        query = "&".join(f"{k}={v}" for k, v in params.items())
        url += f"?{query}"
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        if e.code == 400:
            return None
        raise


def resolve_handle(handle):
    data = api_get("com.atproto.identity.resolveHandle", {"handle": handle})
    if data and "did" in data:
        return data["did"]
    return None


def resolve_did_to_handle(did):
    data = api_get("app.bsky.actor.getProfile", {"actor": did})
    if data and "handle" in data:
        return data["handle"]
    return None


def get_profile(actor):
    return api_get("app.bsky.actor.getProfile", {"actor": actor})


def get_follows(did, limit=500):
    follows = []
    cursor = None
    while len(follows) < limit:
        params = {"actor": did, "limit": min(100, limit - len(follows))}
        if cursor:
            params["cursor"] = cursor
        data = api_get("app.bsky.graph.getFollows", params)
        if not data or "follow" not in data:
            break
        for f in data["follow"]:
            follows.append(f["subject"]["did"])
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(REQUEST_DELAY)
    return follows


def get_followers(did, limit=500):
    followers = []
    cursor = None
    while len(followers) < limit:
        params = {"actor": did, "limit": min(100, limit - len(followers))}
        if cursor:
            params["cursor"] = cursor
        data = api_get("app.bsky.graph.getFollowers", params)
        if not data or "followers" not in data:
            break
        for f in data["followers"]:
            followers.append(f["did"])
        cursor = data.get("cursor")
        if not cursor:
            break
        time.sleep(REQUEST_DELAY)
    return followers


def resolve_seeds():
    seed_dids = {}
    for handle in SEEDS:
        did = resolve_handle(handle)
        if did:
            seed_dids[did] = handle
        time.sleep(0.15)
    return seed_dids


def resolve_hubs():
    hub_dids = {}
    for handle in HUB_ACCOUNTS:
        if handle in [h for h in SEEDS]:
            continue
        did = resolve_handle(handle)
        if did:
            hub_dids[did] = handle
        time.sleep(0.15)
    return hub_dids


def score_seed_following(count):
    if count >= 8:
        return 30
    elif count >= 5:
        return 25
    elif count >= 3:
        return 20
    elif count >= 2:
        return 12
    elif count >= 1:
        return 5
    return 0


def score_seed_followership(count):
    if count >= 3:
        return 30
    elif count >= 2:
        return 20
    elif count >= 1:
        return 10
    return 0


def score_reciprocal(count):
    if count >= 3:
        return 20
    elif count >= 2:
        return 15
    elif count >= 1:
        return 8
    return 0


def score_hub_proximity(count):
    if count >= 3:
        return 20
    elif count >= 1:
        return 10
    return 0


def get_tier(score):
    for (lo, hi), (name, color) in TIER_NAMES.items():
        if lo <= score < hi:
            return name, color
    return "SEED / INNER CORE", "\033[95m"


def main():
    if len(sys.argv) < 2:
        print("Usage: python check_membership.py <handle_or_did>")
        print("Example: python check_membership.py abeliansoup.bsky.social")
        sys.exit(1)

    input_id = sys.argv[1].strip().rstrip("/")

    print("=" * 60)
    print("  ARE YOU IN THE SIMCLUSTER?")
    print("  A diagnostic tool")
    print("=" * 60)
    print()

    if input_id.startswith("did:"):
        did = input_id
        print(f"Looking up DID: {did}...")
        handle = resolve_did_to_handle(did)
        if handle:
            print(f"  -> Handle: @{handle}")
        else:
            print("  -> Could not resolve handle (proceeding with DID)")
            handle = did[:30] + "..."
    else:
        handle = input_id.lstrip("@")
        print(f"Resolving @{handle}...")
        did = resolve_handle(handle)
        if not did:
            print(f"  ERROR: Could not resolve handle '@{handle}'")
            print("  Is this a valid Bluesky handle?")
            sys.exit(1)
        print(f"  -> DID: {did}")

    profile = get_profile(did)
    if profile:
        display_name = profile.get("displayName", "")
        followers_count = profile.get("followersCount", "?")
        follows_count = profile.get("followsCount", "?")
        print(f"  -> Display name: {display_name or '(none)'}")
        print(f"  -> Following: {follows_count}  |  Followers: {followers_count}")
    print()

    print("Resolving seed accounts...")
    seed_dids = resolve_seeds()
    print(f"  Resolved {len(seed_dids)}/{len(SEEDS)} seeds")

    hub_dids = resolve_hubs()
    print(f"  Resolved {len(hub_dids)} hub accounts")
    print()

    is_seed = did in seed_dids
    if is_seed:
        print("*** YOU ARE A SEED ACCOUNT ***")
        print(f"  @{handle} was used as a starting point for the simcluster crawl.")
        print()

    print(f"Fetching follows for @{handle}...")
    user_follows = get_follows(did)
    user_follows_set = set(user_follows)
    print(f"  Found {len(user_follows)} follows")

    print(f"Fetching followers for @{handle}...")
    user_followers = get_followers(did)
    user_followers_set = set(user_followers)
    print(f"  Found {len(user_followers)} followers")
    print()

    seeds_followed = user_follows_set & set(seed_dids.keys())
    seeds_following_you = user_followers_set & set(seed_dids.keys())
    reciprocal_seeds = seeds_followed & seeds_following_you

    hub_did_set = set(hub_dids.keys()) | set(seed_dids.keys())
    hubs_followed = user_follows_set & (set(hub_dids.keys()))

    s_following = score_seed_following(len(seeds_followed))
    s_followership = score_seed_followership(len(seeds_following_you))
    s_reciprocal = score_reciprocal(len(reciprocal_seeds))
    s_hub = score_hub_proximity(len(hubs_followed))
    total = s_following + s_followership + s_reciprocal + s_hub

    tier_name, tier_color = get_tier(total)

    print("=" * 60)
    print("  DIAGNOSTIC RESULTS")
    print("=" * 60)
    print()

    print(f"  Score breakdown:")
    print(f"    Seed following     ({len(seeds_followed):>2}/14 seeds): {s_following:>3}/30 pts")
    print(f"    Seed followership  ({len(seeds_following_you):>2}/14 seeds): {s_followership:>3}/30 pts")
    print(f"    Reciprocal connects ({len(reciprocal_seeds):>2} mutual): {s_reciprocal:>3}/20 pts")
    print(f"    Hub proximity      ({len(hubs_followed):>2} hubs):  {s_hub:>3}/20 pts")
    print()
    print(f"  {tier_color}TOTAL SCORE: {total}/100")
    print(f"  TIER: {tier_name}\033[0m")
    print()

    if seeds_followed:
        names = [f"@{seed_dids[d]}" for d in seeds_followed if d in seed_dids]
        print(f"  Seeds you follow: {', '.join(names)}")
    else:
        print("  Seeds you follow: (none)")

    if seeds_following_you:
        names = [f"@{seed_dids[d]}" for d in seeds_following_you if d in seed_dids]
        print(f"  Seeds following you: {', '.join(names)}")
    else:
        print("  Seeds following you: (none)")

    if reciprocal_seeds:
        names = [f"@{seed_dids[d]}" for d in reciprocal_seeds if d in seed_dids]
        print(f"  Mutual with: {', '.join(names)}")
    print()

    tier_commentary = {
        "SEED / INNER CORE": "You are the simcluster. The crawl started from you (or someone very close to you). Your betweenness centrality may or may not be a sampling artifact. Try not to think about it.",
        "CORE": "You're card-carrying. Multiple seeds follow you. You show up in the centrality tables. You probably already knew this, which raises the question: why are you running this tool?",
        "ADJACENT": "You're in, but not center stage. You follow enough seeds to be structurally connected. Whether the feeling is mutual is between you and the reciprocity statistics.",
        "PERIPHERAL": "You're on the outskirts. You know someone who knows someone. In a network with 3.5% reciprocity, you are the 96.5%.",
        "CURIOUS": "You're simcluster-curious. Barely. You might have followed one seed by accident, or because they posted something funny once. The vibes criterion awaits.",
        "OUTSIDE": "No detectable connection to the simcluster. This does not mean you are not in the simcluster. It means the follow graph doesn't show it. The vibes criterion is always available.",
    }

    commentary = tier_commentary.get(tier_name, "")
    print(f"  Diagnosis: {commentary}")
    print()
    print("=" * 60)
    print("  Remember: your score is a function of who started the crawl,")
    print("  not a function of who you are. The map is not the territory.")
    print("  The simcluster is liquid. So are you.")
    print("=" * 60)


if __name__ == "__main__":
    main()
