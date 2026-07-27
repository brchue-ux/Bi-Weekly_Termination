#!/usr/bin/env python3
"""
Config-driven runner for okta_bookmark_sync.py.

Run this yourself, locally -- it is not meant to be executed by an AI
assistant against a real tenant. It only needs stdlib + PyJWT + the sibling
modules. No network calls happen until you run it.

Setup (one time):
  1. cp config.example.json config.json
  2. Edit config.json: real "org" URL, the OAuth client_id + private_key_file
     for your scoped Okta API Services app, real roster directory, and the
     list of {label, export} pairs for your apps.
  3. Point "private_key_file" at the PEM registered on that API Services
     app, permissions 600 (owner read/write only). This script refuses to
     run if that file is group/other readable.
  4. Put each app's roster CSV/XLSX in "roster_dir", named to match the
     "export" field per app in config.json.

Usage:
  python3 run_all.py                 # dry-run every app in config.json
  python3 run_all.py --apply         # actually assign/unassign, after the
                                     # blast-radius guard and a typed confirmation
  python3 run_all.py --only "Efax (Bi-Weekly Term Test)"   # limit to one app

THIS IS THE ONLY ENTRYPOINT THAT CAN REMOVE ACCESS. Two guards were added 2026-07-26:

  * BLAST RADIUS. `to_remove` was applied with no ceiling whatsoever. Combined with the
    fail-open resolution and the broken xlsx parser (both fixed in okta_bookmark_sync.py),
    a bad parse meant "unassign everyone from this app". `guard_removals` now refuses any
    change set beyond a configured ceiling unless the exact expected count is passed on the
    command line, and always prints the identities before asking.

  * CONFIRMATION. The prompt tested `if typed not in org` — a SUBSTRING check. Typing a
    single character "o" passed it, and so did the empty string. It is now an exact
    hostname match, and it is shown the computed add/remove counts first, so the human
    confirms the blast radius rather than the URL.
"""

import argparse
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).parent))
import biterm_config  # noqa: E402
import biterm_creds  # noqa: E402
import biterm_http  # noqa: E402
import okta_bookmark_sync as sync  # noqa: E402
import okta_oauth  # noqa: E402


class AbortRun(RuntimeError):
    """Refusing to proceed. Nothing has been written."""


def load_config(path):
    if not Path(path).exists():
        sys.exit(
            f"Missing {path}. Copy config.example.json to config.json and "
            f"fill in your real org/token/roster paths first."
        )
    with open(path) as f:
        return json.load(f)


def check_private_key_perms(private_key_file):
    ok, detail = biterm_creds.check_readable(private_key_file, "private key")
    if not ok:
        sys.exit(f"Refusing to run: {detail}")


def guard_removals(label, to_remove, current, resolved, expect):
    """Refuse an implausibly large removal set.

    Rationale in one line: every catastrophic version of this script ends with a large,
    unintended `to_remove`. The ceiling is deliberately low — a legitimate mass removal is
    rare enough to be worth typing the exact number for.
    """
    guard = biterm_config.get("removal_guard", default={})
    max_abs = guard.get("max_absolute", 10)
    max_frac = guard.get("max_fraction", 0.10)
    ceiling = max(max_abs, int(len(current) * max_frac)) if current else max_abs
    if len(to_remove) <= ceiling:
        return
    if expect is not None and expect == len(to_remove):
        print(f"  [{label}] removal count {len(to_remove)} matches --expect-removals; proceeding.")
        return
    raise AbortRun(
        f"[{label}] refusing to unassign {len(to_remove)} of {len(current)} assignees "
        f"(ceiling {ceiling} = max({max_abs}, {max_frac:.0%} of current)).\n"
        f"        A removal set this large is usually a parse or resolution fault, not an "
        f"instruction.\n"
        f"        If it is genuinely intended, re-run with --expect-removals "
        f"{len(to_remove)} after reviewing the list above.\n"
        f"        Resolved from roster: {len(resolved)}; currently assigned: {len(current)}.")


def run_one(client, app_cfg, roster_dir, column, apply_, expect):
    label = app_cfg["label"]
    export_path = str(Path(roster_dir) / app_cfg["export"])
    if not Path(export_path).exists():
        print(f"[{label}] SKIPPED -- roster file not found: {export_path}")
        return {"label": label, "skipped": True}

    print(f"\n=== {label} ===")
    app = sync.find_bookmark_app(client, label)
    app_exists = app is not None
    if not app_exists:
        print(f"No existing Bookmark app found for '{label}'.")
        if apply_:
            app = sync.create_bookmark_app(
                client, label, app_cfg.get("app_url", "https://internal.example/placeholder"))
        else:
            print("(dry-run) would create it here")

    identifiers = sync.parse_export(export_path, column)
    print(f"Parsed {len(identifiers)} identifiers from {export_path}")

    # Raises UnsafeSyncError if ANY identifier could not be resolved because the API did
    # not answer — those must never fall through into the removal set.
    resolved, unmatched = sync.resolve_all(client, identifiers)

    current = sync.get_current_assignments(client, app["id"]) if (app_exists or apply_) else {}
    to_add = set(resolved) - set(current)
    to_remove = set(current) - set(resolved)

    print(f"To add: {len(to_add)}  To remove: {len(to_remove)}  Unmatched: {len(unmatched)}")
    for ident in unmatched:
        print(f"  unmatched: {ident}")
    if to_remove:
        print("  removals:")
        for uid in sorted(to_remove):
            u = current[uid].get("profile") or {}
            print(f"    - {uid} {u.get('login') or u.get('email') or ''}")

    guard_removals(label, to_remove, current, resolved, expect)

    if not apply_:
        return {"label": label, "to_add": len(to_add), "to_remove": len(to_remove)}

    for uid in sorted(to_add):
        status, _, _ = client.request("POST", f"/api/v1/apps/{app['id']}/users",
                                      {"id": uid, "scope": "USER"})
        print(f"  assign {resolved[uid]} ({uid}): {status}")
    for uid in sorted(to_remove):
        status, _, _ = client.request("DELETE", f"/api/v1/apps/{app['id']}/users/{uid}",
                                      ok_statuses=(200, 204))
        print(f"  unassign {uid}: {status}")
    return {"label": label, "added": len(to_add), "removed": len(to_remove)}


def build_client(cfg):
    """Bearer client that refreshes its own token — a long run outlives one token."""
    import time
    org = cfg["org"].rstrip("/")
    state = {"token": None, "expires": 0.0}

    def token():
        if state["token"] is None or time.time() >= state["expires"]:
            payload = okta_oauth.fetch_token(
                org, cfg["client_id"], cfg["private_key_file"],
                scopes=cfg.get("scopes", okta_oauth.DEFAULT_SCOPES),
                kid=cfg.get("kid", "term-revamp-key-1"))
            lifetime = int(payload.get("expires_in", 3600))
            state["token"] = payload["access_token"]
            state["expires"] = time.time() + lifetime - min(300, lifetime // 2)
        return state["token"]

    return biterm_http.Client(org, biterm_http.bearer(token),
                              error_class=biterm_http.OktaApiError)


def main():
    ap = argparse.ArgumentParser(description=__doc__, allow_abbrev=False,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).parent.parent / "config.json"))
    ap.add_argument("--apply", action="store_true", help="actually write; default is dry-run for every app")
    ap.add_argument("--only", help="limit to a single app label from config.json")
    ap.add_argument("--expect-removals", type=int, metavar="N",
                    help="acknowledge a removal set larger than the guard ceiling; must equal "
                         "the computed count exactly")
    args = ap.parse_args()

    cfg = load_config(args.config)
    check_private_key_perms(cfg["private_key_file"])
    client = build_client(cfg)

    org = cfg["org"].rstrip("/")
    apps = cfg["apps"]
    if args.only:
        apps = [a for a in apps if a["label"] == args.only]
        if not apps:
            sys.exit(f"No app labeled '{args.only}' in {args.config}")

    if args.apply:
        # Dry-run first so the human confirms an actual change set, not just a hostname.
        print(f"Computing the change set for {len(apps)} app(s) against {org} before writing…")
        preview = []
        for app_cfg in apps:
            preview.append(run_one(client, app_cfg, cfg["roster_dir"], cfg.get("column"),
                                   False, args.expect_removals))
        adds = sum(p.get("to_add", 0) for p in preview)
        removes = sum(p.get("to_remove", 0) for p in preview)
        host = urlparse(org).hostname or ""
        print(f"\nABOUT TO APPLY: {adds} assignment(s) and {removes} REMOVAL(s) "
              f"across {len(apps)} app(s) on {host}.")
        typed = input(f"Type the org hostname exactly ({host}) to confirm: ").strip()
        if typed != host:          # exact match; `in` accepted any single character
            sys.exit("Confirmation did not match. Aborting, nothing was changed.")

    for app_cfg in apps:
        run_one(client, app_cfg, cfg["roster_dir"], cfg.get("column"), args.apply,
                args.expect_removals)

    if not args.apply:
        print("\nDry-run only for all apps above. Re-run with --apply to make changes.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (AbortRun, sync.RosterError, sync.UnsafeSyncError,
            biterm_http.HttpError, okta_oauth.OAuthError, biterm_creds.CredentialError) as e:
        sys.exit(f"\nABORTED: {e}")
