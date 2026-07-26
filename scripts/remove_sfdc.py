"""
One-time removal of the SFDC 3rd Party (DocuSign) app + its EXCLUSIVE seeded users
(user-confirmed 2026-07-23). Guards, in order:

  1. Only user ids from the precomputed exclusive list (SFDC-assigned MINUS anyone
     also assigned to a BiTerm app — 14 overlap users survive).
  2. Every id must appear in seed_manifest.json's users (verified 5478/5478 before
     this script was run; re-checked here) — the 18 pre-existing org users can never
     match both conditions.

Deactivate -> delete per user (Okta's two-step), 429 backoff, resumable via a
progress file. App itself is deactivated + deleted only after every user is done.
Independent state re-verification happens in verify_seed.py (updated separately),
not by trusting this script's own output.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ORG = "https://demo-beige-haddock-4684.okta.com"
TOKEN = (Path.home() / ".secrets" / "claude_3rd_party.txt").read_text().strip()
SCRATCH = Path("/tmp/claude-1000/-home-bchue/aed82bac-638b-4d9e-a003-38abeaa2d620/scratchpad")
EXCLUSIVE = SCRATCH / "sfdc_exclusive_ids.json"
PROGRESS = SCRATCH / "sfdc_removal_done.txt"
SFDC_APP_ID = "0oa15iclhmjSuXlIa698"


def call(method, path, tolerate=(404,)):
    """Admin call; 429 backoff; tolerated codes return None instead of raising."""
    for _ in range(8):
        req = urllib.request.Request(ORG + path, method=method)
        req.add_header("Authorization", f"SSWS {TOKEN}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                body = resp.read()
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            if e.code in tolerate:
                return None
            if e.code == 429:
                reset = int(e.headers.get("X-Rate-Limit-Reset", time.time() + 30))
                time.sleep(max(reset - time.time(), 1) + 1)
                continue
            raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:200]}")
    raise SystemExit(f"{method} {path}: exhausted retries")


def main():
    ids = set(json.load(open(EXCLUSIVE)))
    manifest = json.load(open(Path(__file__).parent.parent / "seed_manifest.json"))
    seeded = {u["id"] for u in manifest["users"].values() if isinstance(u, dict) and u.get("id")}
    strays = ids - seeded
    if strays:
        sys.exit(f"REFUSING: {len(strays)} ids not in seed manifest, e.g. {sorted(strays)[:3]}")

    done = set(PROGRESS.read_text().split()) if PROGRESS.exists() else set()
    todo = sorted(ids - done)
    print(f"{len(ids)} exclusive, {len(done)} already done, {len(todo)} to delete", flush=True)

    with open(PROGRESS, "a") as prog:
        for i, uid in enumerate(todo, 1):
            # deactivate is a 400 on already-DEPROVISIONED users - tolerated
            call("POST", f"/api/v1/users/{uid}/lifecycle/deactivate?sendEmail=false", tolerate=(400, 404))
            call("DELETE", f"/api/v1/users/{uid}")           # permanent delete
            prog.write(uid + "\n")
            prog.flush()
            if i % 250 == 0:
                print(f"  {i}/{len(todo)} deleted", flush=True)

    call("POST", f"/api/v1/apps/{SFDC_APP_ID}/lifecycle/deactivate", tolerate=(400, 404))
    call("DELETE", f"/api/v1/apps/{SFDC_APP_ID}")
    print("app deactivated + deleted; user deletions complete "
          "(claim pending independent verify_seed.py run)", flush=True)


if __name__ == "__main__":
    main()
