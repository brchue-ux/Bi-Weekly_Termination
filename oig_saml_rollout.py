#!/usr/bin/env python3
"""Create the remaining 9 term-review apps as custom SAML apps so they can be governed.

Bookmark apps cannot hold entitlements (settings.emOptInStatus is unavailable on that type,
user-confirmed in the Console). Governing an app therefore means re-creating it as a SAML/custom
app — a rebuild, not a toggle. This mirrors the proven NA Saturn ComSat pilot for the other 9
tabs and records an oig_apps.json manifest that the load/verify tooling consumes.

What this does NOT do — on purpose:
  * It does not enable Entitlement Management. `settings.emOptInStatus` is UI-only; PUT ignores it
    (200, silently). An admin flips it per app in the Console. The manifest is the checklist.
  * It does not create entitlements, grants or campaigns. Those come after the UI toggle, via
    oig_load_all.py.
  * It does not touch the `BiTerm - <tab>` BOOKMARK apps — the reconciliation still reads those
    via okta_state(); label prefix `BiTerm OIG - ` keeps the two populations disjoint.

Idempotent: an app whose label already exists is reused, not duplicated.

Usage: oig_saml_rollout.py [--apply]     (default: dry run, creates nothing)
"""
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJ = Path(__file__).parent
ORG = "https://demo-beige-haddock-4684.okta.com"
TOKEN_FILE = Path.home() / ".secrets" / "claude_3rd_party.txt"
DROPS = PROJ / "bi-weekly term and app list"
MANIFEST = PROJ / "oig_apps.json"

# The full 10-app governable set. ComSat is the proven pilot and already exists; it is carried
# in the manifest so load/verify treat all ten uniformly, but rollout never recreates it.
TABS = [
    "CloudForce Canada", "CloudForce HQ", "NA Apollo", "NA Orion", "NA Saturn Central",
    "NA Saturn Corp", "NA Saturn East", "NA Saturn West", "NA Stellar", "NA Saturn ComSat",
]


def slug(tab):
    return "".join(c for c in tab.lower() if c.isalnum())


def _token():
    line = TOKEN_FILE.read_text().strip().splitlines()[0].strip()
    return line.split("=", 1)[1].strip() if "=" in line else line


def call(path, method="GET", body=None):
    for attempt in range(6):
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
            if e.code == 429:  # rate limited: wait for the window to reset, then retry
                reset = e.headers.get("x-rate-limit-reset")
                wait = max(1, int(reset) - int(time.time())) if reset and reset.isdigit() else 5
                time.sleep(min(wait + 1, 30))
                continue
            try:
                return e.code, json.loads(e.read().decode(errors="replace"))
            except Exception:
                return e.code, {}
    return 429, {}


def saml_app_payload(tab):
    """Mirror the ComSat pilot's custom-SAML shape, one distinct SP slug per app."""
    s = slug(tab)
    base = f"https://biterm.example.com"
    return {
        "label": f"BiTerm OIG - {tab}",
        "signOnMode": "SAML_2_0",
        "visibility": {"autoLaunch": False, "autoSubmitToolbar": False,
                       "hide": {"iOS": False, "web": False}},
        "settings": {"signOn": {
            "ssoAcsUrl": f"{base}/sso/{s}",
            "idpIssuer": "http://www.okta.com/${org.externalKey}",
            "audience": f"{base}/{s}",
            "recipient": f"{base}/sso/{s}",
            "destination": f"{base}/sso/{s}",
            "subjectNameIdTemplate": "${user.userName}",
            "subjectNameIdFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified",
            "responseSigned": True,
            "assertionSigned": True,
            "signatureAlgorithm": "RSA_SHA256",
            "digestAlgorithm": "SHA256",
            "honorForceAuthn": True,
            "authnContextClassRef": "urn:oasis:names:tc:SAML:2.0:ac:classes:PasswordProtectedTransport",
        }},
    }


def existing_biterm_oig_apps():
    code, apps = call("/api/v1/apps?limit=200")
    if code != 200:
        raise SystemExit(f"cannot list apps: HTTP {code}")
    return {a["label"]: a for a in apps if a.get("label", "").startswith("BiTerm OIG - ")}


def app_role_values(tab):
    """Distinct app_role values as the app's OWN export names them — derived per app, never a
    shared taxonomy. Corp legitimately lacks 'Power User'; that difference must survive."""
    import csv
    csvf = sorted((DROPS / tab).glob("*.csv"))[0]
    rows = list(csv.DictReader(csvf.open(newline="", encoding="utf-8")))
    return sorted({r["app_role"].strip() for r in rows if r["app_role"].strip()}), csvf.name


def main():
    apply_changes = "--apply" in sys.argv[1:]
    existing = existing_biterm_oig_apps()
    manifest, created, reused = [], 0, 0

    for tab in TABS:
        label = f"BiTerm OIG - {tab}"
        roles, dropname = app_role_values(tab)
        if label in existing:
            app = existing[label]
            reused += 1
            action = "reuse"
        elif not apply_changes:
            app = {"id": "(dry-run)", "name": "(dry-run)",
                   "settings": {"emOptInStatus": "NONE"}}
            action = "would-create"
        else:
            code, app = call("/api/v1/apps", "POST", saml_app_payload(tab))
            if code not in (200, 201):
                print(f"  ERROR creating {label}: {code} {json.dumps(app)[:200]}", file=sys.stderr)
                continue
            created += 1
            action = "created"

        em = app.get("settings", {}).get("emOptInStatus", "NONE")
        manifest.append({
            "tab": tab, "label": label, "slug": slug(tab),
            "app_id": app["id"], "app_name": app["name"],
            "drop": f"bi-weekly term and app list/{tab}/{dropname}",
            "roles": roles, "emOptInStatus": em,
        })
        print(f"  [{action:>12}] {label:<32} id={app['id']:<22} em={em}  roles={len(roles)}")

    if apply_changes:
        MANIFEST.write_text(json.dumps(manifest, indent=1))
        print(f"\nwrote {MANIFEST.name} ({len(manifest)} apps)")
    print(f"\n{'APPLIED' if apply_changes else 'DRY RUN'} — created={created} reused={reused}")
    not_enabled = [m["tab"] for m in manifest if m["emOptInStatus"] != "ENABLED"]
    if not_enabled:
        print("\nNEXT (UI-only, no API can do it): enable Entitlement Management on:")
        for t in not_enabled:
            print(f"    · BiTerm OIG - {t}")


if __name__ == "__main__":
    main()
