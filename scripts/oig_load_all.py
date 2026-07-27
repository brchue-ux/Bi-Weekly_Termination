#!/usr/bin/env python3
"""Load every governable app's drop into Okta OIG as entitlements + grants.

Generalises the proven single-app pilot (oig_pilot_load.py) across all apps in oig_apps.json.
Per app it: (1) ensures the app is opted into Entitlement Management — skips with a clear note
if not, since the opt-in is UI-only; (2) ensures a `Role` entitlement whose values are that
app's OWN distinct app_role strings (Corp legitimately has 4, not 5 — a shared taxonomy would be
wrong); (3) grants each resolvable user their role. Granting a CUSTOM entitlement also assigns
the principal to the app, so this is the step that "adds the users".

MULTI-ACCOUNT / CONFLICTING ROLES (the defect the 2026-07-26 rework fixed):
A drop routinely has several rows for the same person (multiple accounts in one app), and those
rows can carry DIFFERENT roles — e.g. one person is Power User on one account and Administrator
on another. The old loader did first-row-wins: it granted whatever the first row said and skipped
the rest, so it could grant "Power User" and silently drop the "Administrator". For a SOX
privilege-certification control that is exactly the masking the control exists to catch.

Fix = HIGHEST-PRIVILEGE WINS (Option B+). Aggregate every distinct role a person holds in the
app, then grant the single most-privileged one (Administrator > Power User > Standard User >
Read Only > Service Account). Privilege can never be hidden behind a lower role. Per-account /
per-row detail is not lost — it lives in the biweekly reconciliation, per the two-control split.

Why single-value (not multiValue "show all roles"): probed on the live tenant — a grant's value
cannot be PATCHed (400), a single-value entitlement's multiValue flag cannot be flipped in place
(400), and POSTing a new value for an existing principal REPLACES the value (proven). So the
clean, deletion-free correction is to re-POST each principal's highest role, overwriting any
wrong first-row-wins value. (Grants cannot be individually deleted — DELETE grant → 400 — so an
in-place overwrite is the only correction available anyway.)

Idempotent: existing entitlements are reused; a principal already carrying exactly the correct
highest-privilege value is left untouched; only wrong/absent values are (re-)POSTed. Safe to
re-run.

Hardening applied 2026-07-26:
  * THE DRY RUN IS NOW A REAL PLAN. Current grants were only fetched under --apply, so a dry
    run compared against {} and reported every principal as "granted" — you could not review
    what the run would change before running it, which is the entire point of the gate.
  * A failed HTTP call is no longer silently rendered as "EM not enabled" (see oig_common.
    em_enabled), and a truncated grants page no longer masquerades as "this principal holds
    nothing" and triggers a mass re-POST.
  * An unrankable role is fatal before any write, not silently sorted lowest.
  * Every mutating call is recorded to logs/<run_id>.changes.jsonl; the run ends non-zero if
    anything failed or any row was left uncertified.

Usage: oig_load_all.py [--only "NA Orion"] [--apply] [--yes] [--verbose]   (default: dry run)
"""
import argparse
import sys
import urllib.parse
from collections import defaultdict

import biterm_config
import okta_oauth
import biterm_creds
import biterm_domain as domain
import biterm_http
import biterm_runlog as runlog
import oig_common

log = None


def ensure_entitlement(client, app, apply_changes):
    """Return (entitlement_id, {role name: value id}, note). Creates `Role` if absent."""
    ent_id, valmap = oig_common.entitlement_values(client, app)
    if ent_id is not None:
        return ent_id, valmap, "existing"
    if not apply_changes:
        return "(dry-run)", {r: "(dry-run)" for r in app["roles"]}, "would-create"
    payload = {
        "name": "Role", "externalValue": "role", "dataType": "string",
        "multiValue": False, "required": False,
        "description": f"App role held in {app['tab']}",
        "parent": {"externalId": app["app_id"], "type": "APPLICATION"},
        "values": [{"name": r, "externalValue": r.lower().replace(" ", "_")}
                   for r in app["roles"]],
    }
    _, created, _ = client.request("POST", "/governance/api/v1/entitlements", payload)
    ent_id, valmap = oig_common.entitlement_values(client, app)
    if ent_id is None:
        raise biterm_http.OktaApiError("POST", "/governance/api/v1/entitlements", 200,
                                       {"error": "entitlement created but not readable back",
                                        "response": created})
    return ent_id, valmap, "created"


def plan_app(client, app, emails):
    """Compute the full change plan for one app WITHOUT writing anything.

    Identical in dry-run and apply mode — that is what makes the dry run reviewable.
    """
    ok, detail = oig_common.em_enabled(client, app)
    if not ok:
        return {"tab": app["tab"], "skipped": f"emOptInStatus={detail} (enable EM in Console)"}

    expected, stats = oig_common.expected_grants(app, emails)
    ent_id, valmap = oig_common.entitlement_values(client, app)
    st = {"tab": app["tab"], "entitlement": ent_id, "valmap": valmap, "expected": expected,
          "must_create_entitlement": ent_id is None, **stats}

    if ent_id is None:
        # Nothing is granted yet by definition; the whole expected set is new.
        st.update(current={}, to_grant=sorted(expected), to_correct=[], unchanged=0, bare=0)
        return st

    id_to_name = {vid: name for name, vid in valmap.items()}
    current, bare = oig_common.granted_values(client, app, id_to_name)
    to_grant, to_correct, unchanged = [], [], 0
    for uid, winner in expected.items():
        have = current.get(uid, [])
        if have == [winner]:
            unchanged += 1
        elif have:
            to_correct.append((uid, have, winner))
        else:
            to_grant.append(uid)
    st.update(current=current, to_grant=sorted(to_grant), to_correct=to_correct,
              unchanged=unchanged, bare=bare,
              extra=sorted(set(current) - set(expected)))
    return st


def apply_app(client, app, st):
    """Execute the plan. Returns counts; every failure is logged and counted, never swallowed."""
    ent_id = st["entitlement"]
    valmap = st["valmap"]
    if st["must_create_entitlement"]:
        ent_id, valmap, note = ensure_entitlement(client, app, True)
        log.info(f"    entitlement {note}: {ent_id}")
        st["entitlement"], st["valmap"] = ent_id, valmap

    granted = corrected = err = 0
    targets = [(uid, None) for uid in st["to_grant"]] + \
              [(uid, have) for uid, have, _ in st["to_correct"]]
    for uid, have in targets:
        winner = st["expected"][uid]
        if winner not in valmap:
            log.error(f"    ERROR {uid}: role {winner!r} has no entitlement value id")
            err += 1
            continue
        try:
            # POSTing a value REPLACES the principal's current value (proven on the tenant);
            # grants cannot be PATCHed or individually deleted, so this is the only lever.
            client.request("POST", "/governance/api/v1/grants", {
                "grantType": "CUSTOM",
                "target": {"externalId": app["app_id"], "type": "APPLICATION"},
                "targetPrincipal": {"externalId": uid, "type": "OKTA_USER"},
                "action": "ALLOW",
                "entitlements": [{"id": ent_id, "values": [{"id": valmap[winner]}]}]})
            if have:
                corrected += 1
            else:
                granted += 1
        except biterm_http.HttpError as e:
            err += 1
            log.error(f"    ERROR {uid}: {e}")
    return granted, corrected, err


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", metavar="TAB", help="limit to one app tab from oig_apps.json")
    ap.add_argument("--apply", action="store_true", help="write; default is a dry-run plan")
    ap.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    ap.add_argument("--verbose", action="store_true")
    return ap.parse_args(argv)


def confirm(args):
    if args.yes:
        return
    if not sys.stdin.isatty():
        sys.exit("Refusing a non-interactive write run: no terminal to confirm on. "
                 "Pass --yes explicitly if this is a scheduled/unattended run.")
    host = urllib.parse.urlparse(biterm_config.org()).hostname or ""
    typed = input(f"About to WRITE grants to {host}. Type the hostname to continue: ").strip()
    if typed != host:
        sys.exit("Confirmation did not match. Aborting; nothing was written.")


def main(argv=None):
    global log
    args = parse_args(argv)
    log = runlog.setup("oig_load_all", verbose=args.verbose)
    log.info(f"run {runlog.run_id()} by {runlog.actor()} | {biterm_config.describe()}")
    if args.apply:
        confirm(args)

    client = oig_common.admin_client("oig_load_all", dry_run=not args.apply, logger=log)
    manifest = oig_common.load_manifest(args.only)

    log.info("paging Okta users…")
    emails = oig_common.users_by_email(client)
    log.info(f"  {len(emails)} users")

    agg = defaultdict(int)
    skipped, failures, uncertified = [], [], []
    for app in manifest:
        try:
            st = plan_app(client, app, emails)
        except (oig_common.DropError, domain.UnknownRoleError) as e:
            failures.append(f"{app['tab']}: {e}")
            log.error(f"  FAIL  {app['tab']:<20} {e}")
            continue
        except biterm_http.HttpError as e:
            failures.append(f"{app['tab']}: {e}")
            log.error(f"  ERROR {app['tab']:<20} {e}")
            continue

        if "skipped" in st:
            skipped.append((st["tab"], st["skipped"]))
            log.info(f"  SKIP  {st['tab']:<20} {st['skipped']}")
            continue

        granted = corrected = err = 0
        if args.apply:
            granted, corrected, err = apply_app(client, app, st)
        else:
            granted, corrected = len(st["to_grant"]), len(st["to_correct"])

        if st["unknown_role_rows"]:
            uncertified.append(f"{st['tab']}: {st['unknown_role_rows']} row(s) carry roles not "
                               f"in this app's entitlement values {st['unknown_roles']}")
        for k, v in (("granted", granted), ("corrected", corrected), ("unchanged", st["unchanged"]),
                     ("orphan_rows", st["orphan_rows"]), ("unknown_role_rows", st["unknown_role_rows"]),
                     ("conflicted", st["conflicted"]), ("err", err),
                     ("extra", len(st.get("extra", [])))):
            agg[k] += v
        log.info(f"  {st['tab']:<20} principals={st['principals']:>4} granted={granted:>4} "
                 f"corrected={corrected:>3} unchanged={st['unchanged']:>4} "
                 f"conflicted={st['conflicted']:>3} orphan_rows={st['orphan_rows']:>4} "
                 f"unknown_role_rows={st['unknown_role_rows']} err={err}")
        if st.get("to_correct") and not args.apply:
            for uid, have, winner in st["to_correct"][:5]:
                log.info(f"      would correct {uid}: {have} -> {winner}")

    log.info(f"\n{'APPLIED' if args.apply else 'DRY RUN (plan only — no writes)'} — "
             + " ".join(f"{k}={agg[k]}" for k in
                        ("granted", "corrected", "unchanged", "conflicted", "orphan_rows",
                         "unknown_role_rows", "extra", "err")))
    if skipped:
        log.info(f"\n{len(skipped)} app(s) skipped (EM not yet enabled):")
        for t, why in skipped:
            log.info(f"    · {t}: {why}")
    if uncertified:
        log.warning("\nROWS LEFT UNCERTIFIED (a role with no entitlement value cannot be "
                    "reviewed — fix the drop or add the role):")
        for u in uncertified:
            log.warning(f"    · {u}")
    if failures:
        log.error(f"\n{len(failures)} app(s) FAILED — these were NOT loaded:")
        for f in failures:
            log.error(f"    · {f}")
    if args.apply:
        log.info(f"\nchange log: {runlog.change_log_path()}")

    # Non-zero when anything was left in an unknown or failed state. A load that silently
    # exits 0 while apps were skipped by an auth error is how coverage gaps go unnoticed.
    return 1 if (failures or agg["err"] or uncertified) else 0


if __name__ == "__main__":
    # Entrypoints translate typed library errors into a clean exit. Library code
    # never calls sys.exit itself — the caller decides what is fatal.
    try:
        sys.exit(main())
    except (biterm_config.ConfigError, biterm_creds.CredentialError,
            biterm_http.HttpError, okta_oauth.OAuthError,
            oig_common.ManifestError, oig_common.DropError) as e:
        sys.exit(f"ABORTED: {e}")
