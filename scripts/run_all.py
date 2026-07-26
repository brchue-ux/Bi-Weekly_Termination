#!/usr/bin/env python3
"""
Config-driven runner for okta_bookmark_sync.py.

Run this yourself, locally -- it is not meant to be executed by an AI
assistant against a real tenant. It only needs stdlib + okta_bookmark_sync.py
in the same directory. No network calls happen until you run it.

Setup (one time):
  1. cp config.example.json config.json
  2. Edit config.json: real --org URL, the OAuth client_id + private_key_file
     for your scoped Okta API Services app, real roster directory, and the
     list of {label, export} pairs for your apps.
  3. Point "private_key_file" at the PEM registered on that API Services
     app, permissions 600 (owner read/write only). This script refuses to
     run if that file is group/other readable.
  4. Put each app's roster CSV/XLSX in "roster_dir", named to match the
     "export" field per app in config.json.

Usage:
  python3 run_all.py                 # dry-run every app in config.json
  python3 run_all.py --apply         # actually assign/unassign, with a
                                      # typed confirmation prompt first
  python3 run_all.py --only "Efax (Bi-Weekly Term Test)"   # limit to one app
"""

import argparse
import json
import os
import stat
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import okta_bookmark_sync as sync  # noqa: E402
import okta_oauth  # noqa: E402


def load_config(path):
    if not Path(path).exists():
        sys.exit(
            f"Missing {path}. Copy config.example.json to config.json and "
            f"fill in your real org/token/roster paths first."
        )
    with open(path) as f:
        return json.load(f)


def check_private_key_perms(private_key_file):
    p = Path(private_key_file)
    if not p.exists():
        sys.exit(f"Private key file not found: {private_key_file}")
    mode = stat.S_IMODE(os.stat(p).st_mode)
    if mode & 0o077:
        sys.exit(
            f"Refusing to run: {private_key_file} is readable by group/other "
            f"(mode {oct(mode)}). Fix with: chmod 600 {private_key_file}"
        )


def run_one(org, auth_header, app_cfg, roster_dir, column, apply_):
    label = app_cfg["label"]
    export_path = str(Path(roster_dir) / app_cfg["export"])
    if not Path(export_path).exists():
        print(f"[{label}] SKIPPED -- roster file not found: {export_path}")
        return

    print(f"\n=== {label} ===")
    app_exists = True
    app = sync.find_bookmark_app(org, auth_header, label)
    if app is None:
        app_exists = False
        print(f"No existing Bookmark app found for '{label}'.")
        if apply_:
            app = sync.create_bookmark_app(
                org, auth_header, label, app_cfg.get("app_url", "https://internal.example/placeholder")
            )
        else:
            print("(dry-run) would create it here")

    identifiers = sync.parse_export(export_path, column)
    print(f"Parsed {len(identifiers)} identifiers from {export_path}")

    resolved, unmatched = {}, []
    for ident in identifiers:
        user = sync.resolve_user(org, auth_header, ident)
        if user:
            resolved[user["id"]] = ident
        else:
            unmatched.append(ident)

    current = sync.get_current_assignments(org, auth_header, app["id"]) if (app_exists or apply_) else {}
    to_add = set(resolved) - set(current)
    to_remove = set(current) - set(resolved)

    print(f"To add: {len(to_add)}  To remove: {len(to_remove)}  Unmatched: {len(unmatched)}")
    for ident in unmatched:
        print(f"  unmatched: {ident}")

    if not apply_:
        return

    for uid in to_add:
        status, _ = sync.okta_request(org, auth_header, "POST", f"apps/{app['id']}/users", {"id": uid, "scope": "USER"})
        print(f"  assign {resolved[uid]} ({uid}): {status}")
    for uid in to_remove:
        status, _ = sync.okta_request(org, auth_header, "DELETE", f"apps/{app['id']}/users/{uid}")
        print(f"  unassign {uid}: {status}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(Path(__file__).parent.parent / "config.json"))
    ap.add_argument("--apply", action="store_true", help="actually write; default is dry-run for every app")
    ap.add_argument("--only", help="limit to a single app label from config.json")
    args = ap.parse_args()

    cfg = load_config(args.config)
    check_private_key_perms(cfg["private_key_file"])
    access_token = okta_oauth.get_access_token(
        cfg["org"].rstrip("/"), cfg["client_id"], cfg["private_key_file"],
        scopes=cfg.get("scopes", okta_oauth.DEFAULT_SCOPES), kid=cfg.get("kid", "term-revamp-key-1"),
    )
    auth_header = f"Bearer {access_token}"

    org = cfg["org"].rstrip("/")
    apps = cfg["apps"]
    if args.only:
        apps = [a for a in apps if a["label"] == args.only]
        if not apps:
            sys.exit(f"No app labeled '{args.only}' in {args.config}")

    if args.apply:
        print(f"About to APPLY changes for {len(apps)} app(s) against {org}.")
        typed = input("Type the org hostname to confirm: ").strip()
        if typed not in org:
            sys.exit("Confirmation did not match. Aborting, nothing was changed.")

    for app_cfg in apps:
        run_one(org, auth_header, app_cfg, cfg["roster_dir"], cfg.get("column"), args.apply)

    if not args.apply:
        print("\nDry-run only for all apps above. Re-run with --apply to make changes.")


if __name__ == "__main__":
    main()
