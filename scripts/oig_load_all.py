#!/usr/bin/env python3
"""Load every governable app's drop into Okta OIG as entitlements + grants.

Generalises the proven single-app pilot (oig_pilot_load.py) across all apps in oig_apps.json.
Per app it: (1) ensures the app is opted into Entitlement Management — skips with a clear note
if not, since the opt-in is UI-only; (2) ensures a `Role` entitlement whose values are that
app's OWN distinct app_role strings (Corp legitimately has 4, not 5 — a shared taxonomy would be
wrong); (3) grants each resolvable user the value the drop says they hold. Granting a CUSTOM
entitlement also assigns the principal to the app, so this is the step that "adds the users".

Structural limit, stated not hidden: entitlements attach to Okta users, so accounts with no Okta
identity (the orphans) cannot be represented here and stay with the reconciliation.

Idempotent: existing entitlements are reused, already-granted principals are skipped. Safe to
re-run after enabling EM on more apps.

Usage: oig_load_all.py [--only "NA Orion"] [--apply]     (default: dry run)
"""
import csv
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PROJ = Path(__file__).parent.parent
ORG = "https://demo-beige-haddock-4684.okta.com"
ORGID = "00o159zwmhz6L5eo4698"
TOKEN_FILE = Path.home() / ".secrets" / "claude_3rd_party.txt"
MANIFEST = PROJ / "oig_apps.json"


def _token():
    line = TOKEN_FILE.read_text().strip().splitlines()[0].strip()
    return line.split("=", 1)[1].strip() if "=" in line else line


def call(path, method="GET", body=None, ok404=False):
    for _ in range(6):
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
            if e.code == 429:
                reset = e.headers.get("x-rate-limit-reset")
                wait = max(1, int(reset) - int(time.time())) if reset and reset.isdigit() else 5
                time.sleep(min(wait + 1, 30))
                continue
            try:
                return e.code, json.loads(e.read().decode(errors="replace"))
            except Exception:
                return e.code, {}
    return 429, {}


def all_users_by_email():
    """Page every Okta user once; resolve drop emails locally (thousands of GETs otherwise)."""
    emails, url = {}, ORG + "/api/v1/users?limit=200"
    while url:
        req = urllib.request.Request(url)
        req.add_header("Authorization", f"SSWS {_token()}")
        req.add_header("Accept", "application/json")
        with urllib.request.urlopen(req) as resp:
            for u in json.loads(resp.read()):
                e = (u.get("profile", {}).get("email") or "").strip().lower()
                if e:
                    emails[e] = u["id"]
            url = None
            for h in resp.headers.get_all("Link") or []:
                if 'rel="next"' in h:
                    url = re.search(r"<([^>]+)>", h).group(1)
    return emails


def ensure_entitlement(app, roles, apply_changes):
    """Return (entitlement_id, {role_name: value_id}). Create `Role` if absent."""
    orn = f"orn:okta:idp:{ORGID}:apps:{app['app_name']}:{app['app_id']}"
    code, ents = call("/governance/api/v1/entitlements?filter="
                      + urllib.parse.quote(f'parentResourceOrn eq "{orn}"'))
    if code != 200:
        return None, {}, f"entitlements query HTTP {code}"
    role_ent = next((e for e in ents.get("data", []) if e["name"] == "Role"), None)

    if role_ent is None:
        if not apply_changes:
            return "(dry-run)", {r: "(dry-run)" for r in roles}, "would-create"
        payload = {
            "name": "Role", "externalValue": "role", "dataType": "string",
            "multiValue": False, "required": False,
            "description": f"App role held in {app['tab']}",
            "parent": {"externalId": app["app_id"], "type": "APPLICATION"},
            "values": [{"name": r, "externalValue": r.lower().replace(" ", "_")} for r in roles],
        }
        code, role_ent = call("/governance/api/v1/entitlements", "POST", payload)
        if code not in (200, 201):
            return None, {}, f"create HTTP {code}: {json.dumps(role_ent)[:160]}"

    code, vals = call(f"/governance/api/v1/entitlements/{role_ent['id']}/values")
    valmap = {v["name"]: v["id"] for v in vals.get("data", [])}
    return role_ent["id"], valmap, "ok"


def load_app(app, emails, apply_changes):
    code, live = call(f"/api/v1/apps/{app['app_id']}")
    em = live.get("settings", {}).get("emOptInStatus")
    if em != "ENABLED":
        return {"tab": app["tab"], "skipped": f"emOptInStatus={em} (enable EM in Console)"}

    roles = app["roles"]
    ent_id, valmap, note = ensure_entitlement(app, roles, apply_changes)
    if ent_id is None:
        return {"tab": app["tab"], "skipped": f"entitlement error: {note}"}

    orn = f"orn:okta:idp:{ORGID}:apps:{app['app_name']}:{app['app_id']}"
    code, existing = call("/governance/api/v1/grants?filter="
                          + urllib.parse.quote(f'targetResourceOrn eq "{orn}"') + "&limit=200")
    have = {g["targetPrincipal"]["externalId"] for g in existing.get("data", [])}

    rows = list(csv.DictReader((PROJ / app["drop"]).open(newline="", encoding="utf-8")))
    st = {"rows": len(rows), "orphan": 0, "already": 0, "granted": 0, "unknown_role": 0, "err": 0}
    for r in rows:
        email = r["email"].strip().lower()
        uid = emails.get(email)
        if not uid:
            st["orphan"] += 1
            continue
        vid = valmap.get(r["app_role"].strip())
        if not vid:
            st["unknown_role"] += 1
            continue
        if uid in have:
            st["already"] += 1
            continue
        if not apply_changes:
            st["granted"] += 1
            continue
        code, res = call("/governance/api/v1/grants", "POST", {
            "grantType": "CUSTOM",
            "target": {"externalId": app["app_id"], "type": "APPLICATION"},
            "targetPrincipal": {"externalId": uid, "type": "OKTA_USER"},
            "action": "ALLOW",
            "entitlements": [{"id": ent_id, "values": [{"id": vid}]}]})
        if code in (200, 201):
            st["granted"] += 1
            have.add(uid)
        else:
            st["err"] += 1
            print(f"    ERROR {email}: {code} {json.dumps(res)[:140]}", file=sys.stderr)
    st["tab"] = app["tab"]
    return st


def main():
    args = sys.argv[1:]
    apply_changes = "--apply" in args
    only = args[args.index("--only") + 1] if "--only" in args else None
    manifest = json.loads(MANIFEST.read_text())
    if only:
        manifest = [m for m in manifest if m["tab"] == only]

    print("paging Okta users…")
    emails = all_users_by_email()
    print(f"  {len(emails)} users\n")

    agg = {"granted": 0, "already": 0, "orphan": 0, "unknown_role": 0, "err": 0}
    skipped = []
    for app in manifest:
        st = load_app(app, emails, apply_changes)
        if "skipped" in st:
            skipped.append((st["tab"], st["skipped"]))
            print(f"  SKIP  {st['tab']:<20} {st['skipped']}")
            continue
        for k in agg:
            agg[k] += st.get(k, 0)
        print(f"  {st['tab']:<20} granted={st['granted']:>4} already={st['already']:>4} "
              f"orphan={st['orphan']:>4} unknown_role={st['unknown_role']} err={st['err']}")

    print(f"\n{'APPLIED' if apply_changes else 'DRY RUN'} — "
          + " ".join(f"{k}={v}" for k, v in agg.items()))
    if skipped:
        print(f"\n{len(skipped)} app(s) skipped (EM not yet enabled):")
        for t, why in skipped:
            print(f"    · {t}: {why}")


if __name__ == "__main__":
    main()
