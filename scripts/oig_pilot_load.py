#!/usr/bin/env python3
"""Load one app drop into Okta OIG as entitlement grants (pilot: NA Saturn ComSat).

This is the OIG-native half of the future-state design: the same CSV the reconciliation
reads also populates entitlements inside Okta, so campaigns can certify WHAT someone holds
in an app rather than merely THAT they hold it.

Requires the target app to have Entitlement Management enabled (settings.emOptInStatus
= ENABLED). That flag is UI-only — no public API sets it (PUT /api/v1/apps returns 200 and
silently ignores the field), so this script verifies it and refuses to run rather than
producing a confusing 404 from the governance API.

Structural limit, stated rather than hidden: entitlements attach to PRINCIPALS (Okta users).
App accounts with no Okta identity — the orphans, the largest finding bucket — cannot be
represented in OIG at all and stay with the external reconciliation.

Usage: oig_pilot_load.py [--drop PATH] [--apply]   (default is a dry run)
"""
import csv
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJ = Path(__file__).parent.parent
ORG = "https://demo-beige-haddock-4684.okta.com"
ORGID = "00o159zwmhz6L5eo4698"
TOKEN_FILE = Path.home() / ".secrets" / "claude_3rd_party.txt"
DEFAULT_DROP = PROJ / "bi-weekly term and app list" / "NA Saturn ComSat" / "NA_Saturn_ComSat_users_20260723.csv"


def _token():
    line = TOKEN_FILE.read_text().strip().splitlines()[0].strip()
    return line.split("=", 1)[1].strip() if "=" in line else line


def call(path, method="GET", body=None, ok404=False):
    req = urllib.request.Request(ORG + path, method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"SSWS {_token()}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        if e.code == 404 and ok404:
            return 404, {}
        try:
            return e.code, json.loads(e.read().decode(errors="replace"))
        except Exception:
            return e.code, {}


def main():
    args = sys.argv[1:]
    apply_changes = "--apply" in args
    drop = Path(args[args.index("--drop") + 1]) if "--drop" in args else DEFAULT_DROP

    app = json.loads((PROJ / "oig_pilot_app.json").read_text())
    ent = json.loads((PROJ / "oig_pilot_entitlement.json").read_text())

    code, live = call(f"/api/v1/apps/{app['id']}")
    opt_in = live.get("settings", {}).get("emOptInStatus")
    if opt_in != "ENABLED":
        raise SystemExit(f"app {app['label']} has emOptInStatus={opt_in!r}; enable Entitlement "
                         "Management in the Admin Console first (no API can set it)")

    code, vals = call(f"/governance/api/v1/entitlements/{ent['id']}/values")
    valmap = {v["name"]: v["id"] for v in vals["data"]}

    # Existing grants make the run idempotent: re-running a cycle must not duplicate grants.
    orn = f"orn:okta:idp:{ORGID}:apps:{app['name']}:{app['id']}"
    code, existing = call("/governance/api/v1/grants?filter="
                          + urllib.parse.quote(f'targetResourceOrn eq "{orn}"') + "&limit=200")
    have = {g["targetPrincipal"]["externalId"] for g in existing.get("data", [])}

    rows = list(csv.DictReader(drop.open(newline="", encoding="utf-8")))
    stats = {"rows": len(rows), "no_email": 0, "no_okta_user": 0,
             "already_granted": 0, "granted": 0, "unknown_role": 0, "errors": 0}
    orphans, granted = [], []

    for row in rows:
        email = row["email"].strip().lower()
        if not email:
            stats["no_email"] += 1
            orphans.append(row["account_id"])
            continue
        code, user = call(f"/api/v1/users/{urllib.parse.quote(email)}", ok404=True)
        if code == 404:
            stats["no_okta_user"] += 1
            orphans.append(row["account_id"])
            continue
        value_id = valmap.get(row["app_role"].strip())
        if not value_id:
            stats["unknown_role"] += 1
            continue
        if user["id"] in have:
            stats["already_granted"] += 1
            continue
        if not apply_changes:
            stats["granted"] += 1
            granted.append((row["account_id"], row["app_role"]))
            continue
        code, res = call("/governance/api/v1/grants", "POST", {
            "grantType": "CUSTOM",
            "target": {"externalId": app["id"], "type": "APPLICATION"},
            "targetPrincipal": {"externalId": user["id"], "type": "OKTA_USER"},
            "action": "ALLOW",
            "entitlements": [{"id": ent["id"], "values": [{"id": value_id}]}]})
        if code in (200, 201):
            stats["granted"] += 1
            granted.append((row["account_id"], row["app_role"]))
        else:
            stats["errors"] += 1
            print(f"  ERROR {email}: {code} {json.dumps(res)[:160]}", file=sys.stderr)

    print(("APPLIED" if apply_changes else "DRY RUN") + f" — {drop.name}")
    for k, v in stats.items():
        print(f"  {k:<18} {v}")
    print(f"  orphans (no Okta identity, NOT representable in OIG): {len(orphans)}")
    if orphans[:5]:
        print(f"    e.g. {orphans[:5]}")
    return stats


if __name__ == "__main__":
    main()
