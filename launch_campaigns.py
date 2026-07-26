"""
Relaunch the three OIG certification campaigns on the post-re-identity tenant
(2026-07-23). Ends any stale ACTIVE BiTerm campaigns, then creates + launches:

  1. Targeted Resource — NA Saturn ComSat (RESOURCE campaign, one app).
  2. Quarterly UAR — Saturn Regional (RESOURCE campaign, Saturn East/Central/West).
  3. Flagged Population (biweekly feed) — USER campaign scoped to the latest
     recon cycle's confirmed-termination Okta users (new identities).

Uses the SSWS admin token: campaign MANAGEMENT needs
okta.governance.accessCertifications.manage, which the least-privilege service
app deliberately lacks (it holds .read only, for reporting). Managing campaigns
is an administrative action — same rationale as oauth_bootstrap.py using admin.

API gotchas (hard-won, from project CLAUDE.md):
  - reviewerSettings.type = USER (not REVIEWER); reviewerId = an Okta user id.
  - RESOURCE campaign: resourceSettings.targetResources = [{resourceId: appId,
    resourceType: APPLICATION}]. USER campaign IGNORES targetResources — its
    resourceSettings must be {type: APPLICATION, targetTypes: [APPLICATION]} and
    principalScopeSettings.userIds carries the population.
  - Ended campaigns linger in listings (filter by status).
"""

import glob
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ORG = "https://demo-beige-haddock-4684.okta.com"
TOKEN = (Path.home() / ".secrets" / "claude_3rd_party.txt").read_text().strip()
REVIEWER_EMAIL = "bchue@wm.com"          # clickable queue owner
PREFIX = "BiTerm — "
APP_PREFIX = "BiTerm - "


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


def paged_gov(path):
    url = path
    while url:
        status, body = api("GET", url)
        if status != 200:
            raise SystemExit(f"{path} -> {status}: {body}")
        yield from body.get("data", [])
        nxt = (body.get("_links", {}).get("next") or {}).get("href")
        url = nxt.replace(ORG, "") if nxt else None


def app_id(label):
    status, apps = api("GET", f"/api/v1/apps?q={urllib.parse.quote(label)}&limit=20")
    a = next((x for x in apps if x["label"] == label), None)
    if not a:
        sys.exit(f"app not found: {label}")
    return a["id"]


def reviewer_id():
    status, users = api("GET", f"/api/v1/users?q={urllib.parse.quote(REVIEWER_EMAIL)}&limit=5")
    u = next((x for x in users if x["profile"]["login"] == REVIEWER_EMAIL), None)
    if not u:
        sys.exit(f"reviewer {REVIEWER_EMAIL} not found")
    return u["id"]


def end_stale():
    for c in paged_gov("/governance/api/v1/campaigns?limit=50"):
        if c.get("name", "").startswith(PREFIX) and c.get("status") == "ACTIVE":
            status, _ = api("POST", f"/governance/api/v1/campaigns/{c['id']}/end")
            print(f"ended ACTIVE: {c['name']} -> HTTP {status}")


def create(name, body):
    status, c = api("POST", "/governance/api/v1/campaigns", body)
    if status not in (200, 201):
        sys.exit(f"create failed ({name}) {status}: {json.dumps(c)[:400]}")
    cid = c["id"]
    status, _ = api("POST", f"/governance/api/v1/campaigns/{cid}/launch")
    print(f"created + launched: {name}  id={cid}  launch HTTP {status}")
    return cid


def _schedule():
    start = datetime.now(timezone.utc) + timedelta(minutes=5)
    return {"type": "ONE_OFF", "startDate": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "durationInDays": 14, "timeZone": "America/Toronto"}


REMEDIATION = {"accessApproved": "NO_ACTION", "accessRevoked": "NO_ACTION",
               "noResponse": "NO_ACTION"}


def resource_campaign(name, app_labels, rid):
    resources = [{"resourceId": app_id(lbl), "resourceType": "APPLICATION"} for lbl in app_labels]
    return create(name, {
        "name": name,
        "campaignType": "RESOURCE",
        "scheduleSettings": _schedule(),
        "remediationSettings": REMEDIATION,
        "resourceSettings": {"type": "APPLICATION", "targetResources": resources},
        "reviewerSettings": {"type": "USER", "reviewerId": rid},
        "principalScopeSettings": {"type": "USERS"},
    })


def user_campaign(name, user_ids, rid):
    return create(name, {
        "name": name,
        "campaignType": "USER",
        "scheduleSettings": _schedule(),
        "remediationSettings": REMEDIATION,
        "resourceSettings": {"type": "APPLICATION", "targetTypes": ["APPLICATION"]},
        "reviewerSettings": {"type": "USER", "reviewerId": rid},
        "principalScopeSettings": {"type": "USERS", "userIds": user_ids},
    })


def flagged_user_ids():
    cyc = sorted(glob.glob("cycles/cycle_*"))[-1]
    findings = json.load(open(f"{cyc}/state.json"))["findings"]
    upns = {f["upn"] for f in findings if f["cls"] == "ticket" and f.get("upn")}
    ids = []
    for upn in sorted(upns):
        status, users = api("GET", f"/api/v1/users?q={urllib.parse.quote(upn)}&limit=2")
        u = next((x for x in users if x["profile"]["login"] == upn), None)
        if u:
            ids.append(u["id"])
    print(f"flagged population resolved: {len(ids)}/{len(upns)} (from {cyc})")
    return ids


def main():
    rid = reviewer_id()
    end_stale()
    resource_campaign(PREFIX + "Targeted Resource Review: NA Saturn ComSat",
                      ["BiTerm - NA Saturn ComSat"], rid)
    resource_campaign(PREFIX + "Quarterly UAR: Saturn Regional",
                      ["BiTerm - NA Saturn East", "BiTerm - NA Saturn Central",
                       "BiTerm - NA Saturn West"], rid)
    ids = flagged_user_ids()
    if ids:
        user_campaign(PREFIX + "Flagged Population Review (biweekly feed)", ids, rid)
    print("\ncampaigns relaunched — verify with campaign_report.py")


if __name__ == "__main__":
    main()
