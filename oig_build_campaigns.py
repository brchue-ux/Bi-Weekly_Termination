#!/usr/bin/env python3
"""Build (but never launch) the OIG certification campaigns over the governed apps.

Creates the canonical three archetypes, now that all 10 apps carry entitlements:
  1. Per-app entitlement-level RESOURCE campaigns — one per governed app, so a reviewer
     certifies the ROLE a person holds, not merely that they hold the app.
  2. Quarterly UAR — one RESOURCE campaign spanning all 10 governed apps.
  3. Flagged Population — a USER campaign scoped to the latest recon cycle's confirmed
     terminations (the biweekly-feed → certify-the-flagged link). Skipped if no cycle data.

"Build, don't execute": each campaign is CREATED (lands in SCHEDULED) and this script NEVER
calls /launch. The start date is set ~a year out so nothing auto-starts during demos; the script
reads each campaign back and refuses to finish if any came up ACTIVE. Launch is a deliberate
human action later.

Entitlement-level review requires BOTH resourceSettings.includeEntitlements AND each
targetResources[].includeAllEntitlementsAndBundles (silent app-level fallback otherwise — the
documented campaign landmine). Remediation is NO_ACTION on every outcome: this control never
auto-removes access; a Revoke is a recorded decision, removal is verified by the reconciliation.

Reviewers are the Access Management team (Zyler, Phil); bchue@wm.com is deliberately never used.
Campaign MANAGEMENT needs the admin token (the least-privilege service app holds .read only).

Usage: oig_build_campaigns.py [--apply]      (default: dry run, creates nothing)
"""
import glob
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJ = Path(__file__).parent
ORG = "https://demo-beige-haddock-4684.okta.com"
TOKEN_FILE = Path.home() / ".secrets" / "claude_3rd_party.txt"
MANIFEST = PROJ / "oig_apps.json"
AM = json.loads((PROJ / "am_team_okta.json").read_text())["users"]
REVIEWERS = [AM["Zyler"], AM["Phil"]]      # alternate; Bogan is manager, not a reviewer
PREFIX = "BiTerm — "
OUT = PROJ / "oig_campaigns.json"

# Far-future one-off start: built + validated, but dormant. Launch is a manual step.
START = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
SCHEDULE = {"type": "ONE_OFF", "startDate": START, "durationInDays": 21,
            "timeZone": "America/Toronto"}
REMEDIATION = {"accessApproved": "NO_ACTION", "accessRevoked": "NO_ACTION",
               "noResponse": "NO_ACTION"}


def _token():
    return TOKEN_FILE.read_text().strip().splitlines()[0].strip()


def api(method, path, body=None):
    req = urllib.request.Request(ORG + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"SSWS {_token()}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            data = resp.read()
            return resp.status, (json.loads(data) if data else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or "{}")
        except Exception:
            return e.code, {}


def existing_campaign_names():
    """Names of campaigns not in a terminal state, so re-runs don't duplicate."""
    names, url = set(), "/governance/api/v1/campaigns?limit=50"
    while url:
        code, body = api("GET", url)
        if code != 200:
            break
        for c in body.get("data", []):
            if c.get("status") not in ("ENDED", "DELETED"):
                names.add(c.get("name"))
        nxt = (body.get("_links", {}).get("next") or {}).get("href")
        url = nxt.replace(ORG, "") if nxt else None
    return names


def resource_settings(app_ids):
    return {
        "type": "APPLICATION",
        "includeEntitlements": True,
        "targetResources": [
            {"resourceId": aid, "resourceType": "APPLICATION",
             "includeAllEntitlementsAndBundles": True} for aid in app_ids
        ],
    }


def build(name, body, results, existing, apply_changes):
    if name in existing:
        print(f"  [exists]  {name}")
        results.append({"name": name, "status": "pre-existing"})
        return
    if not apply_changes:
        print(f"  [would ]  {name}")
        results.append({"name": name, "status": "dry-run"})
        return
    code, c = api("POST", "/governance/api/v1/campaigns", body)
    if code not in (200, 201):
        print(f"  ERROR    {name}: {code} {json.dumps(c)[:200]}", file=sys.stderr)
        results.append({"name": name, "status": f"error {code}"})
        return
    # Read back and refuse to treat as built if it somehow went live.
    code, live = api("GET", f"/governance/api/v1/campaigns/{c['id']}")
    st = live.get("status")
    flag = "" if st != "ACTIVE" else "  <<< UNEXPECTEDLY ACTIVE"
    print(f"  [built ]  {name}  id={c['id']}  status={st}{flag}")
    results.append({"name": name, "id": c["id"], "status": st})


def flagged_user_ids():
    cycles = sorted(glob.glob(str(PROJ / "cycles" / "cycle_*")))
    if not cycles:
        return [], None
    state = json.loads((Path(cycles[-1]) / "state.json").read_text())
    upns = sorted({f["upn"] for f in state.get("findings", [])
                   if f.get("cls") == "ticket" and f.get("upn")})
    ids = []
    for upn in upns:
        code, users = api("GET", f"/api/v1/users?q={urllib.parse.quote(upn)}&limit=2")
        u = next((x for x in users if x.get("profile", {}).get("login") == upn), None)
        if u:
            ids.append(u["id"])
    return ids, Path(cycles[-1]).name


def main():
    apply_changes = "--apply" in sys.argv[1:]
    manifest = json.loads(MANIFEST.read_text())
    existing = existing_campaign_names()
    results = []

    print("Per-app entitlement-level certification campaigns:")
    for i, app in enumerate(manifest):
        name = f"{PREFIX}Access Certification: {app['tab']}"
        body = {"name": name, "campaignType": "RESOURCE", "scheduleSettings": SCHEDULE,
                "remediationSettings": REMEDIATION,
                "resourceSettings": resource_settings([app["app_id"]]),
                "reviewerSettings": {"type": "USER", "reviewerId": REVIEWERS[i % 2]},
                "principalScopeSettings": {"type": "USERS"}}
        build(name, body, results, existing, apply_changes)

    print("\nQuarterly UAR across all governed apps:")
    uar = f"{PREFIX}Quarterly UAR: All Governed Apps"
    build(uar, {"name": uar, "campaignType": "RESOURCE", "scheduleSettings": SCHEDULE,
                "remediationSettings": REMEDIATION,
                "resourceSettings": resource_settings([a["app_id"] for a in manifest]),
                "reviewerSettings": {"type": "USER", "reviewerId": REVIEWERS[0]},
                "principalScopeSettings": {"type": "USERS"}},
          results, existing, apply_changes)

    print("\nFlagged-population campaign (biweekly feed → certify flagged):")
    ids, cyc = flagged_user_ids() if apply_changes else ([], None)
    fname = f"{PREFIX}Flagged Population Review (biweekly feed)"
    if fname in existing:
        print(f"  [exists]  {fname}")
    elif not apply_changes:
        print(f"  [would ]  {fname}  (population resolved at --apply time)")
    elif not ids:
        print(f"  [skip  ]  {fname}  — no cycle findings to scope to")
    else:
        build(fname, {"name": fname, "campaignType": "USER", "scheduleSettings": SCHEDULE,
                      "remediationSettings": REMEDIATION,
                      "resourceSettings": {"type": "APPLICATION", "targetTypes": ["APPLICATION"]},
                      "reviewerSettings": {"type": "USER", "reviewerId": REVIEWERS[1]},
                      "principalScopeSettings": {"type": "USERS", "userIds": ids}},
              results, existing, apply_changes)
        print(f"           scoped to {len(ids)} flagged users from {cyc}")

    if apply_changes:
        OUT.write_text(json.dumps(results, indent=1))
        active = [r for r in results if r.get("status") == "ACTIVE"]
        print(f"\nwrote {OUT.name}. built={sum(1 for r in results if r.get('id'))} "
              f"active={len(active)} (MUST be 0 — none should execute)")
    else:
        print("\nDRY RUN — nothing created")


if __name__ == "__main__":
    main()
