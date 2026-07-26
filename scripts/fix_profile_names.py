#!/usr/bin/env python3
"""Repair scrambled firstName/lastName on seeded @bitermtest.com users.

seed_tenant.py copied SFDC FirstName/LastName columns whose values were
scrambled by the de-identification pass, so ~5,449 users have names that
don't match their email. Canonical rule (identical to seed_tenant's
name derivation): names come from the login local part.

Only logins ending in @bitermtest.com are touched — the org's 18
pre-existing demo users (incl. bchue@wm.com) are never modified.
Idempotent: profiles already matching the canonical name are skipped,
so a second run reports FIXED 0.

Lists all users once up front, computes the mismatch set locally, then
issues the POST updates from 6 worker threads (the org-wide rate bucket
is the ceiling; more workers would only pile onto 429s).

Usage: fix_profile_names.py   (token: ~/.secrets/claude_3rd_party.txt)
"""
import concurrent.futures
import json
import random
import sys
import threading
import time
import urllib.error
import urllib.request

from seed_tenant import ORG, TOKEN_FILE, paged  # 429-safe pagination for the single listing pass

SEEDED_DOMAIN = "@bitermtest.com"
WORKERS = 6
TOKEN = TOKEN_FILE.read_text().strip()  # read once before spawning; shared read-only

_lock = threading.Lock()  # guards the fixed counter across workers
_fixed = 0


def canonical(login: str) -> tuple[str, str]:
    """login local part -> (firstName, lastName) per the seeding name rule."""
    parts = login.split("@")[0].replace("_", ".").split(".")
    return parts[0].title(), (parts[-1].title() if len(parts) > 1 else "User")


def post_update(uid: str, first: str, last: str):
    """Partial profile update (POST keeps other attributes) with per-thread 429 backoff."""
    global _fixed
    body = json.dumps({"profile": {"firstName": first, "lastName": last}}).encode()
    for _ in range(8):
        req = urllib.request.Request(f"{ORG}/api/v1/users/{uid}", method="POST", data=body)
        req.add_header("Authorization", f"SSWS {TOKEN}")
        req.add_header("Accept", "application/json")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                resp.read()
            with _lock:
                _fixed += 1
                if _fixed % 500 == 0:
                    print(f"  fixed {_fixed}", file=sys.stderr)
            return
        except urllib.error.HTTPError as e:
            if e.code == 429:
                reset = int(e.headers.get("X-Rate-Limit-Reset", time.time() + 30))
                # jitter spreads the 6 threads out so they don't thundering-herd the reset
                time.sleep(max(reset - time.time(), 1) + random.uniform(0, 2))
                continue
            raise RuntimeError(f"POST users/{uid} -> {e.code}: {e.read().decode()[:300]}") from e
    raise RuntimeError(f"POST users/{uid}: rate-limited past retries")


def main():
    mismatched, skipped, total = [], 0, 0
    for u in paged("/api/v1/users?limit=200"):
        login = (u["profile"].get("login") or "").lower()
        if not login.endswith(SEEDED_DOMAIN):  # hard guard: never touch non-seeded users
            continue
        total += 1
        first, last = canonical(login)
        if u["profile"].get("firstName") == first and u["profile"].get("lastName") == last:
            skipped += 1
        else:
            mismatched.append((u["id"], first, last))
    print(f"listed {total} seeded users; {len(mismatched)} need updates", file=sys.stderr)

    with concurrent.futures.ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = [pool.submit(post_update, *m) for m in mismatched]
        for f in concurrent.futures.as_completed(futures):
            f.result()  # re-raise the first worker failure instead of finishing silently

    print(f"FIXED {_fixed} SKIPPED {skipped} TOTAL {total}")


if __name__ == "__main__":
    main()
