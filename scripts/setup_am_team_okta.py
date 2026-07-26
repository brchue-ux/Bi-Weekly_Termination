"""
Access Management team in Okta (2026-07-23, user-authorized): create the three
AM staff, wire the reporting line, and make them genuine participants in the
biweekly termination flow — reviewers + governance visibility.

  Bogan Wone   (manager, top of the line)
  Zyler Bawado -> reports to Bogan
  Phil Manawan -> reports to Bogan

Actions (SSWS admin — user + role + campaign management are admin operations):
  1. Create the 3 users (ACTIVE), fake domain bitermtest.com.
  2. Reporting line via profile.managerId + profile.manager (Zyler/Phil -> Bogan).
  3. Okta group "Access Management" (mirrors the ServiceNow group) — add all 3.
  4. Governance VISIBILITY: assign Read-Only Administrator to each (see users,
     apps, and the whole certification process end to end).
  5. REVIEWER participation: reassign a slice of the active Flagged-Population
     campaign's review items to Zyler and Phil so they actually certify.

bchue@wm.com is never touched. Idempotent by login/label. Records the 3 new
user ids to am_team_okta.json so the re-identity gate can allowlist them.
"""

import json
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ORG = "https://demo-beige-haddock-4684.okta.com"
TOKEN = (Path.home() / ".secrets" / "claude_3rd_party.txt").read_text().strip()
DOMAIN = "bitermtest.com"
GROUP = "Access Management"
FLAGGED_CAMPAIGN = "ici118cvovgsMIX25697"
OUT = Path(__file__).parent.parent / "am_team_okta.json"
# Shared demo logins (same password per person in Okta + ServiceNow). In ~/.secrets
# (not the LAN share) per credential-handling rule; user will simplify the passwords.
CREDS_FILE = Path.home() / ".secrets" / "am_team_demo_logins.txt"


def gen_password():
    """Strong enough for Okta's default policy (upper/lower/digit/symbol, no login parts)."""
    body = secrets.token_urlsafe(12).replace("-", "x").replace("_", "y")
    return f"Bt{body}7!"


def load_or_make_creds():
    """person login -> password, persisted once so both scripts use the same value."""
    creds = {}
    if CREDS_FILE.exists():
        for line in CREDS_FILE.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                creds[k.strip()] = v.strip()
    changed = False
    for p in TEAM:
        login = login_of(p)
        if login not in creds:
            creds[login] = gen_password()
            changed = True
    if changed:
        CREDS_FILE.write_text(
            "# AM team demo logins — Okta org demo-beige-haddock-4684 + ServiceNow dev336362\n"
            "# same password per person in both systems; login = email local part.\n"
            + "".join(f"{k} = {v}\n" for k, v in creds.items()))
        CREDS_FILE.chmod(0o600)
    return creds

TEAM = [
    {"first": "Bogan", "last": "Wone", "manager": None},
    {"first": "Zyler", "last": "Bawado", "manager": "Bogan"},
    {"first": "Phil", "last": "Manawan", "manager": "Bogan"},
]


def api(method, path, body=None, ok=(200, 201)):
    req = urllib.request.Request(ORG + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"SSWS {TOKEN}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            return resp.status, (json.loads(data) if data else {})
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "{}")


def login_of(p):
    return f"{p['first']}.{p['last']}@{DOMAIN}".lower()


def find_user(login):
    status, users = api("GET", f"/api/v1/users?q={urllib.parse.quote(login)}&limit=5")
    return next((u for u in users if u["profile"]["login"].lower() == login), None)


def ensure_user(p, password):
    login = login_of(p)
    existing = find_user(login)
    if existing:
        # make sure a pre-existing account has the shared demo password set
        api("POST", f"/api/v1/users/{existing['id']}",
            {"credentials": {"password": {"value": password}}})
        print(f"user exists: {login} ({existing['id']}) — password reset to shared demo value")
        return existing["id"]
    status, u = api("POST", "/api/v1/users?activate=true", {
        "profile": {"firstName": p["first"], "lastName": p["last"],
                    "email": login, "login": login},
        "credentials": {"password": {"value": password}},
    })
    if status not in (200, 201):
        sys.exit(f"create {login} failed ({status}): {json.dumps(u)[:300]}")
    print(f"created: {login} ({u['id']})")
    return u["id"]


def main():
    creds = load_or_make_creds()
    ids = {}
    for p in TEAM:
        ids[p["first"]] = ensure_user(p, creds[login_of(p)])
    print(f"shared demo passwords in {CREDS_FILE}")

    # 2. reporting line
    for p in TEAM:
        if p["manager"]:
            mgr = p["manager"]
            api("POST", f"/api/v1/users/{ids[p['first']]}",
                {"profile": {"managerId": ids[mgr], "manager": f"{mgr} Wone"}})
            print(f"reports-to set: {p['first']} -> {mgr}")

    # 3. Access Management group
    status, groups = api("GET", f"/api/v1/groups?q={urllib.parse.quote(GROUP)}&limit=5")
    grp = next((g for g in groups if g["profile"]["name"] == GROUP), None)
    if not grp:
        status, grp = api("POST", "/api/v1/groups",
                          {"profile": {"name": GROUP,
                                       "description": "Biweekly termination review team"}})
        print(f"group created: {GROUP} ({grp['id']})")
    else:
        print(f"group exists: {GROUP} ({grp['id']})")
    for first, uid in ids.items():
        api("PUT", f"/api/v1/groups/{grp['id']}/users/{uid}")
        print(f"  added to group: {first}")

    # 4. governance visibility — Read-Only Administrator per user
    for first, uid in ids.items():
        status, roles = api("GET", f"/api/v1/users/{uid}/roles")
        have = {r["type"] for r in roles} if isinstance(roles, list) else set()
        if "READ_ONLY_ADMIN" not in have:
            api("POST", f"/api/v1/users/{uid}/roles", {"type": "READ_ONLY_ADMIN"})
            print(f"  role READ_ONLY_ADMIN -> {first}")
        else:
            print(f"  role already present: {first}")

    # 5. reviewer participation — split flagged-pop review items to Zyler & Phil
    q = urllib.parse.quote(f'campaignId eq "{FLAGGED_CAMPAIGN}"')
    items, url = [], f"/governance/api/v1/reviews?filter={q}&limit=200"
    while url:
        status, body = api("GET", url)
        if status != 200:
            break
        items += body.get("data", [])
        nxt = (body.get("_links", {}).get("next") or {}).get("href")
        url = nxt.replace(ORG, "").replace("-admin", "") if nxt else None
    reassignable = [it["id"] for it in items]
    half = len(reassignable) // 2
    buckets = {ids["Zyler"]: reassignable[:half], ids["Phil"]: reassignable[half:]}
    for reviewer_id, review_ids in buckets.items():
        if not review_ids:
            continue
        status, body = api("POST",
                           f"/governance/api/v1/campaigns/{FLAGGED_CAMPAIGN}/reviews/reassign",
                           {"reviewerId": reviewer_id, "reviewIds": review_ids,
                            "note": "AM team demo — routing flagged-population reviews to fulfiller"},
                           ok=(200, 202))
        print(f"  reassigned {len(review_ids)} review items -> {reviewer_id}: HTTP {status}"
              + ("" if status in (200, 202) else f" {json.dumps(body)[:200]}"))

    json.dump({"group_id": grp["id"], "users": ids}, open(OUT, "w"), indent=1)
    print(f"\nAM team (Okta) done; ids -> {OUT}")


if __name__ == "__main__":
    main()
