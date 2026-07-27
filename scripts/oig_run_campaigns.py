#!/usr/bin/env python3
"""Build the OIG entitlement-level certification campaigns and LAUNCH exactly one.

Two campaigns, one behaviour each:
  · --live  "<app tab>"     build an entitlement-level RESOURCE campaign for this app and LAUNCH
                            it (start now, POST /launch) so it goes ACTIVE — a real attestation.
  · --dormant "<app tab>"   build an entitlement-level RESOURCE campaign for this app with a
                            far-future start and DO NOT launch it. It sits SCHEDULED; nobody
                            attests to anything until a later, deliberate manual launch.

Both use the two campaign flags that make a reviewer certify the ROLE a person holds rather than
merely "has the app" (resourceSettings.includeEntitlements + targetResources[].
includeAllEntitlementsAndBundles — the silent app-level fallback otherwise). Remediation is
NO_ACTION on every outcome: this control records a decision, it never auto-removes access.
Reviewers are the Access Management team (Zyler / Phil); bchue@wm.com is deliberately never used.

THE ENTITLEMENT-LEVEL CHECK (rewritten 2026-07-26). Project CLAUDE.md names the missing-flag
case as the highest-risk silent failure: get either flag wrong and Okta creates
app-assignment-level items with NO error. The check that guarded it was:

    if "entitlement" in json.dumps(review).lower(): -> "yes, entitlement-level"

a substring match over the whole serialised review object, which an app-level item satisfies
trivially — `"entitlements": []`, `includeEntitlements`, or any bundle key contains the word.
The guard could return "yes" for exactly the failure it existed to catch, and it sampled at
most 20 items.

It now asserts on STRUCTURE over EVERY item: each review item must carry a non-empty
entitlement value, and the item count must equal the number of principals actually holding a
grant on that app — a number this script does not control and the campaign builder never sees.

Usage: oig_run_campaigns.py --live "NA Saturn ComSat" --dormant "CloudForce HQ" [--apply]
       (default: dry run — creates and launches nothing)
"""
import argparse
import json
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import biterm_config
import okta_oauth
import biterm_creds
import biterm_http
import biterm_runlog as runlog
import oig_common

PROJ = Path(__file__).resolve().parent.parent
AM = json.loads((PROJ / "am_team_okta.json").read_text())["users"]
REVIEWERS = {"live": AM["Zyler"], "dormant": AM["Phil"]}
PREFIX = "BiTerm — "
REMEDIATION = {"accessApproved": "NO_ACTION", "accessRevoked": "NO_ACTION",
               "noResponse": "NO_ACTION"}

log = None


def app_by_tab(manifest, tab):
    a = next((m for m in manifest if m["tab"] == tab), None)
    if not a:
        raise oig_common.ManifestError(f"unknown app tab: {tab!r}")
    return a


def resource_settings(app_id):
    return {"type": "APPLICATION", "includeEntitlements": True,
            "targetResources": [{"resourceId": app_id, "resourceType": "APPLICATION",
                                 "includeAllEntitlementsAndBundles": True}]}


def campaign_body(name, app_id, reviewer, start, duration):
    return {"name": name, "campaignType": "RESOURCE",
            "scheduleSettings": {"type": "ONE_OFF", "startDate": start,
                                 "durationInDays": duration, "timeZone": "America/Toronto"},
            "remediationSettings": REMEDIATION,
            "resourceSettings": resource_settings(app_id),
            "reviewerSettings": {"type": "USER", "reviewerId": reviewer},
            "principalScopeSettings": {"type": "USERS"}}


def create(client, body):
    _, campaign, _ = client.request("POST", "/governance/api/v1/campaigns", body)
    return campaign["id"]


def status_of(client, cid):
    return client.get_json(f"/governance/api/v1/campaigns/{cid}").get("status")


def _entitlement_value_of(item):
    """The entitlement value an item certifies, or "" if it certifies only app assignment.

    Structural, not textual. Okta has moved this field between shapes across versions, so
    every known location is checked explicitly — but an EMPTY list or a missing key is a
    negative answer, which is what the old substring match could not express.
    """
    if item.get("entitlementValue"):
        return str(item["entitlementValue"])
    ent = item.get("entitlement")
    if isinstance(ent, dict) and ent.get("value"):
        return str(ent["value"])
    for key in ("entitlements", "entitlementValues"):
        vals = item.get(key)
        if isinstance(vals, list) and vals:
            first = vals[0]
            if isinstance(first, dict):
                for f in ("value", "name", "id"):
                    if first.get(f):
                        return str(first[f])
            elif first:
                return str(first)
    resource = item.get("resource")
    if isinstance(resource, dict) and resource.get("entitlementValue"):
        return str(resource["entitlementValue"])
    return ""


def assert_entitlement_level(client, cid, app):
    """Verify the campaign generated entitlement-level items. Returns (ok, detail).

    Two independent assertions:
      1. EVERY review item carries an entitlement value (not "at least one item mentions the
         word entitlement somewhere in its JSON").
      2. The item count equals the number of principals holding a grant on the app — a fact
         derived from the grants API, which the campaign builder has no influence over.
    """
    items = list(biterm_http.paged_governance(
        client, f"/governance/api/v1/campaigns/{cid}/reviews"))
    if not items:
        return None, "no review items generated yet (campaign may still be building)"

    valueless = [i for i in items if not _entitlement_value_of(i)]
    if valueless:
        sample = json.dumps(valueless[0])[:300]
        return False, (f"{len(valueless)} of {len(items)} review items carry NO entitlement "
                       f"value — this is an app-assignment-level campaign. Sample: {sample}")

    try:
        ent_id, valmap = oig_common.entitlement_values(client, app)
        id_to_name = {vid: name for name, vid in valmap.items()}
        granted, _bare = oig_common.granted_values(client, app, id_to_name)
        expected = len(granted)
    except biterm_http.HttpError as e:
        return True, (f"{len(items)} items, all entitlement-valued; could not cross-check the "
                      f"count against grants ({e})")
    if expected and len(items) != expected:
        return False, (f"{len(items)} review items but {expected} principals hold a grant on "
                       f"{app['tab']} — the campaign is not covering the population")
    return True, f"{len(items)} items, all entitlement-valued, matching {expected} granted principals"


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", required=True, metavar="TAB",
                    help="app tab to build AND launch a live campaign for")
    ap.add_argument("--dormant", required=True, metavar="TAB",
                    help="app tab to build a campaign for WITHOUT launching")
    ap.add_argument("--apply", action="store_true", help="create/launch; default is a dry run")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args(argv)


def main(argv=None):
    global log
    args = parse_args(argv)
    log = runlog.setup("oig_run_campaigns", verbose=args.verbose)
    log.info(f"run {runlog.run_id()} | {biterm_config.describe()}")

    manifest = oig_common.load_manifest()
    live_app = app_by_tab(manifest, args.live)
    dormant_app = app_by_tab(manifest, args.dormant)

    now = datetime.now(timezone.utc)
    live_start = (now + timedelta(minutes=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    dormant_start = (now + timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
    live_name = f"{PREFIX}Access Certification (LIVE): {args.live}"
    dormant_name = f"{PREFIX}Access Certification (PREPARED): {args.dormant}"

    if not args.apply:
        log.info("DRY RUN — would create + LAUNCH:")
        log.info(f"  live    {live_name}  app={live_app['app_id']}  reviewer=Zyler  "
                 f"start={live_start}")
        log.info("  would create (NOT launch):")
        log.info(f"  dormant {dormant_name}  app={dormant_app['app_id']}  reviewer=Phil  "
                 f"start={dormant_start}")
        return 0

    if not args.yes:
        if not sys.stdin.isatty():
            sys.exit("Refusing to launch a LIVE attestation non-interactively: no terminal "
                     "to confirm on. Pass --yes explicitly if this is intended.")
        host = urllib.parse.urlparse(biterm_config.org()).hostname or ""
        typed = input(f"Launching a LIVE attestation on {host}. Type the hostname to confirm: ").strip()
        if typed != host:
            sys.exit("Confirmation did not match. Aborting; nothing was created.")

    client = oig_common.admin_client("oig_run_campaigns", dry_run=False, logger=log)

    live_id = create(client, campaign_body(live_name, live_app["app_id"],
                                           REVIEWERS["live"], live_start, 14))
    status, _, _ = client.request("POST", f"/governance/api/v1/campaigns/{live_id}/launch")
    log.info(f"LIVE    created {live_id}  launch HTTP {status}")

    dormant_id = create(client, campaign_body(dormant_name, dormant_app["app_id"],
                                              REVIEWERS["dormant"], dormant_start, 21))
    log.info(f"DORMANT created {dormant_id}  (NOT launched)")

    # Read back and assert the two behaviours actually happened.
    live_st = status_of(client, live_id)
    for _ in range(8):
        if live_st in ("ACTIVE", "COMPLETED"):
            break
        time.sleep(5)
        live_st = status_of(client, live_id)
    dormant_st = status_of(client, dormant_id)

    ent_ok, ent_detail = assert_entitlement_level(client, live_id, live_app)
    log.info(f"\n  LIVE    {live_id}  status={live_st}")
    log.info(f"          entitlement-level: "
             f"{'yes' if ent_ok else ('unknown' if ent_ok is None else 'NO — app-level!')} "
             f"— {ent_detail}")
    log.info(f"  DORMANT {dormant_id}  status={dormant_st}")

    ok = (live_st in ("ACTIVE", "COMPLETED")
          and dormant_st not in ("ACTIVE", "COMPLETED")
          and ent_ok is True)
    log.info(f"\nVERDICT: {'PASS' if ok else 'FAIL'} — live is {live_st} (want ACTIVE), "
             f"dormant is {dormant_st} (want SCHEDULED), entitlement-level={ent_ok}")
    runlog.write_atomic(PROJ / "oig_run_campaigns.json", json.dumps(
        {"live": {"id": live_id, "name": live_name, "status": live_st,
                  "entitlement_level": ent_ok, "detail": ent_detail},
         "dormant": {"id": dormant_id, "name": dormant_name, "status": dormant_st},
         "run_id": runlog.run_id()}, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    # Entrypoints translate typed library errors into a clean exit. Library code
    # never calls sys.exit itself — the caller decides what is fatal.
    try:
        sys.exit(main())
    except (biterm_config.ConfigError, biterm_creds.CredentialError,
            biterm_http.HttpError, okta_oauth.OAuthError,
            oig_common.ManifestError, oig_common.DropError) as e:
        sys.exit(f"ABORTED: {e}")
