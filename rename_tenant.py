"""
In-place Okta re-identity of the remaining seeded users (user-authorized
2026-07-23, after SFDC removal): every seeded user's login/email/firstName/
lastName moves to the fresh identity from reidentity_map_20260723.json, in
lockstep with the rewritten worksheets.

Why in-place (not delete+reseed): profile update preserves user ids and app
assignments, so the tenant's assignment topology — and every fate planted by the
original seeding — survives untouched. Only identity strings change.

Guards:
  - only manifest-listed users (the 18 pre-existing org users are not in the
    manifest and can never be touched);
  - every remaining user MUST resolve in the map — an unmapped login aborts the
    run before any write (all-or-nothing on the plan, resumable on execution);
  - new locals are pairwise-unique by construction (distinct anchors -> distinct
    name combos) and disjoint from all old names (pool filter), so renames cannot
    collide in any order.

Writes seed_manifest.json re-keyed to the new logins (fates/ids preserved).
NOT self-verifying: run verify_reidentity_tenant.py afterwards.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from reidentity import MAP_OUT, split_local, mimic_case

ORG = "https://demo-beige-haddock-4684.okta.com"
TOKEN = (Path.home() / ".secrets" / "claude_3rd_party.txt").read_text().strip()
PROJ = Path(__file__).parent
MANIFEST = PROJ / "seed_manifest.json"
SCRATCH = Path("/tmp/claude-1000/-home-bchue/aed82bac-638b-4d9e-a003-38abeaa2d620/scratchpad")
DELETED = SCRATCH / "sfdc_removal_done.txt"
PROGRESS = SCRATCH / "rename_done.txt"
DOMAIN = "bitermtest.com"


def call(method, path, body=None):
    for _ in range(8):
        req = urllib.request.Request(ORG + path, method=method,
                                     data=json.dumps(body).encode() if body else None)
        req.add_header("Authorization", f"SSWS {TOKEN}")
        req.add_header("Accept", "application/json")
        if body:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as e:
            if e.code == 429:
                reset = int(e.headers.get("X-Rate-Limit-Reset", time.time() + 30))
                time.sleep(max(reset - time.time(), 1) + 1)
                continue
            raise SystemExit(f"{method} {path} -> {e.code}: {e.read().decode(errors='replace')[:300]}")
    raise SystemExit(f"{method} {path}: exhausted retries")


def new_identity(old_login, mapping):
    """old login -> (new_login, first, last) via the worksheet map; None if unmapped."""
    base, digits, cfca = split_local(old_login)
    if base not in mapping:
        return None
    nf, nl = mapping[base]["first"], mapping[base]["last"]
    core = old_login.split("@")[0]
    core = core[:len(core) - len(digits) - len(cfca)]
    return f"{mimic_case(core, nf, nl)}{digits}{cfca}@{DOMAIN}", nf, nl


def main():
    mapping = json.load(open(MAP_OUT))
    manifest = json.load(open(MANIFEST))
    deleted = set(DELETED.read_text().split()) if DELETED.exists() else set()

    # plan first, abort before any write if anything is unmapped
    plan, unmapped = {}, []
    for login, u in manifest["users"].items():
        if not isinstance(u, dict) or not u.get("id") or u["id"] in deleted:
            continue
        if u.get("fate") == "absent":
            ident = new_identity(login, mapping)      # manifest-only rename, no API call
            plan[login] = (u, None if not ident else ident)
            continue
        ident = new_identity(login, mapping)
        (plan.__setitem__(login, (u, ident)) if ident else unmapped.append(login))
    if unmapped:
        sys.exit(f"REFUSING: {len(unmapped)} logins unmapped, e.g. {unmapped[:5]}")

    done = set(PROGRESS.read_text().split()) if PROGRESS.exists() else set()
    live = {l: p for l, p in plan.items() if p[1] and plan[l][0].get("fate") != "absent"}
    print(f"{len(plan)} users in plan ({len(live)} live renames, "
          f"{len(plan) - len(live)} manifest-only), {len(done)} already done", flush=True)

    n = 0
    with open(PROGRESS, "a") as prog:
        for login, (u, ident) in sorted(plan.items()):
            if login in done or u.get("fate") == "absent" or not ident:
                continue
            new_login, nf, nl = ident
            call("POST", f"/api/v1/users/{u['id']}",
                 {"profile": {"login": new_login, "email": new_login,
                              "firstName": nf, "lastName": nl}})
            prog.write(login + "\n")
            prog.flush()
            n += 1
            if n % 250 == 0:
                print(f"  {n}/{len(live)} renamed", flush=True)

    new_users = {}
    for login, (u, ident) in plan.items():
        new_users[ident[0] if ident else login] = u
    manifest["users"] = new_users
    manifest["reidentity"] = "2026-07-23 map applied; SFDC users/app removed"
    json.dump(manifest, open(MANIFEST, "w"), indent=1)
    print(f"renamed {n} live users; manifest re-keyed ({len(new_users)} users). "
          "NOT VERIFIED — run verify_reidentity_tenant.py", flush=True)


if __name__ == "__main__":
    main()
