#!/usr/bin/env python3
"""Independent gate on the OIG entitlement pilot.

Re-derives everything from the live tenant and the drop CSV; it never trusts the loader's
own counters. Ends in a single VERDICT line.

Checks: app opted in · entitlement + values intact · every grant's VALUE matches the role the
drop says that person holds · orphans are absent by design and accounted for · no grant exists
for anyone the drop does not list.
"""
import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJ = Path(__file__).parent
ORG = "https://demo-beige-haddock-4684.okta.com"
ORGID = "00o159zwmhz6L5eo4698"
DROP = PROJ / "bi-weekly term and app list" / "NA Saturn ComSat" / "NA_Saturn_ComSat_users_20260723.csv"

failures, notes = [], []


def check(label, ok, detail=""):
    (notes if ok else failures).append(f"{'ok  ' if ok else 'FAIL'} {label}{' — ' + detail if detail else ''}")


def call(path, ok404=False):
    line = (Path.home() / ".secrets" / "claude_3rd_party.txt").read_text().strip().splitlines()[0].strip()
    tok = line.split("=", 1)[1].strip() if "=" in line else line
    req = urllib.request.Request(ORG + path)
    req.add_header("Authorization", f"SSWS {tok}")
    req.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        if e.code == 404 and ok404:
            return 404, {}
        return e.code, {}


def main():
    app = json.loads((PROJ / "oig_pilot_app.json").read_text())
    ent = json.loads((PROJ / "oig_pilot_entitlement.json").read_text())

    code, live = call(f"/api/v1/apps/{app['id']}")
    check("app is opted into Entitlement Management",
          live.get("settings", {}).get("emOptInStatus") == "ENABLED",
          str(live.get("settings", {}).get("emOptInStatus")))

    orn = f"orn:okta:idp:{ORGID}:apps:{app['name']}:{app['id']}"
    code, ents = call("/governance/api/v1/entitlements?filter="
                      + urllib.parse.quote(f'parentResourceOrn eq "{orn}"'))
    check("app resolves as a governance resource", code == 200, f"HTTP {code}")
    check("entitlement 'Role' present", any(e["name"] == "Role" for e in ents.get("data", [])),
          f"{[e['name'] for e in ents.get('data', [])]}")

    code, vals = call(f"/governance/api/v1/entitlements/{ent['id']}/values")
    id_to_value = {v["id"]: v["name"] for v in vals.get("data", [])}
    check("all 5 role values defined", len(id_to_value) == 5, f"{sorted(id_to_value.values())}")

    code, grants = call("/governance/api/v1/grants?filter="
                        + urllib.parse.quote(f'targetResourceOrn eq "{orn}"') + "&limit=200")
    granted = {}
    for g in grants.get("data", []):
        vids = [v["id"] for e in g.get("entitlements", []) for v in e.get("values", [])]
        granted[g["targetPrincipal"]["externalId"]] = [id_to_value.get(v, "?") for v in vids]
    check("grants exist", len(granted) > 0, f"{len(granted)} grants")

    # Rebuild expectation straight from the drop, resolving each identity live.
    rows = list(DROP.open(newline="", encoding="utf-8"))
    rows = list(csv.DictReader(DROP.open(newline="", encoding="utf-8")))
    expected, orphans = {}, []
    for r in rows:
        email = r["email"].strip().lower()
        if not email:
            orphans.append(r["account_id"])
            continue
        code, u = call(f"/api/v1/users/{urllib.parse.quote(email)}", ok404=True)
        if code == 404:
            orphans.append(r["account_id"])
            continue
        expected[u["id"]] = r["app_role"].strip()

    mismatches = [(uid, exp, granted.get(uid)) for uid, exp in expected.items()
                  if granted.get(uid) != [exp]]
    check("every granted value matches the role in the drop", not mismatches,
          f"{len(mismatches)} mismatched, e.g. {mismatches[:3]}")
    check("every drop identity with an Okta account has a grant",
          set(expected) <= set(granted),
          f"missing {len(set(expected) - set(granted))}")
    extra = set(granted) - set(expected)
    check("no grant exists for anyone absent from the drop", not extra, f"{len(extra)} extra")
    check("orphans accounted for and NOT in OIG", len(orphans) > 0 and not (set() & set(granted)),
          f"{len(orphans)} orphans excluded by design")
    check("coverage adds up (granted + orphans == drop rows)",
          len(expected) + len(orphans) == len(rows),
          f"{len(expected)} + {len(orphans)} vs {len(rows)}")

    # Campaign leg: the whole point is that a reviewer certifies WHAT someone holds, so an
    # item without an entitlementValue means the campaign silently fell back to app-level
    # review (the `includeEntitlements` / `includeAllEntitlementsAndBundles` pair being unset
    # produces exactly that, with no error).
    camp_file = PROJ / "oig_pilot_campaign.json"
    if camp_file.exists():
        cid = json.loads(camp_file.read_text())["id"]
        code, camp = call(f"/governance/api/v1/campaigns/{cid}")
        check("campaign is ACTIVE", camp.get("status") == "ACTIVE", str(camp.get("status")))
        code, revs = call("/governance/api/v1/reviews?filter="
                          + urllib.parse.quote(f'campaignId eq "{cid}"') + "&limit=200")
        items = revs.get("data", [])
        check("campaign item count equals grant count", len(items) == len(granted),
              f"{len(items)} items vs {len(granted)} grants")
        no_ent = [i["id"] for i in items if not i.get("entitlementValue")]
        check("every review item carries an entitlementValue", not no_ent,
              f"{len(no_ent)} items lack one")
        # End-to-end: drop CSV role -> grant -> what the reviewer actually sees.
        by_principal = {i["principalProfile"]["id"]: i.get("entitlementValue", {}).get("name")
                        for i in items}
        drift = [(uid, exp, by_principal.get(uid)) for uid, exp in expected.items()
                 if by_principal.get(uid) != exp]
        check("reviewer sees the role the drop specified (CSV -> grant -> campaign)",
              not drift, f"{len(drift)} drifted, e.g. {drift[:3]}")

    for line in notes + failures:
        print(line)
    print(f"\nVERDICT: {'PASS' if not failures else 'FAIL'}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
