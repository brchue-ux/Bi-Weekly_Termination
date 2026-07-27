"""Shared derivation for the OIG entitlement load and its verifier.

Previously the privilege table, the email join, the per-principal role aggregation and the
grants reader existed as verbatim copies in `oig_load_all.py` and `oig_verify_all.py`. The
verifier's docstring said it "trusts nothing the loader reported" — true of the loader's
OUTPUT, but not of its LOGIC: any shared wrong assumption (email as the identity key, the
privilege ordering, the normalisation) passed both checks identically, because it was the
same code twice.

Copy-pasting it did not create independence; it created two places to fix a bug and the
illusion of a second opinion. The derivation now lives here once, and the verifier's
independence is stated honestly for what it actually is — a fresh read of the live tenant,
re-derived from the source drops, not a second implementation. Where genuine independence
matters, the verifier cross-checks against facts the loader never computes (row/principal
coverage arithmetic, grants present for principals absent from the drop).
"""
import csv
from collections import defaultdict
from pathlib import Path

import biterm_config
import biterm_creds
import biterm_domain as domain
import biterm_http

PROJ = Path(__file__).resolve().parent.parent
MANIFEST = PROJ / "oig_apps.json"
DROP_COLUMNS = ("email", "app_role")


class ManifestError(RuntimeError):
    """oig_apps.json does not contain what the caller asked for."""


class DropError(RuntimeError):
    """An app drop CSV cannot be trusted (missing columns, unrankable roles)."""


class DuplicateIdentityError(RuntimeError):
    """Two Okta users share a join key. Last-wins would leave one uncertified."""


def admin_client(script, dry_run=True, logger=None):
    """SSWS-authenticated Okta client — privileged scaffolding, never the control.

    The control uses the OAuth service app (okta_client.py). Everything in the OIG
    entitlement path is deliberately admin-token scaffolding that SCIM retires per app.
    """
    import biterm_runlog as runlog
    token_file = biterm_config.require("admin_token_file")
    return biterm_http.okta_client(
        biterm_http.ssws(lambda: biterm_creds.api_token(token_file)),
        on_write=runlog.change_recorder(script, dry_run=dry_run),
        logger=logger)


def load_manifest(only=None):
    import json
    manifest = json.loads(MANIFEST.read_text())
    if only:
        manifest = [m for m in manifest if m["tab"] == only]
        if not manifest:
            raise ManifestError(f"no app with tab {only!r} in {MANIFEST}")
    return manifest


def app_orn(app):
    org_id = biterm_config.require("org_id")
    return f"orn:okta:idp:{org_id}:apps:{app['app_name']}:{app['app_id']}"


def users_by_email(client):
    """email -> Okta user id, for the whole directory in one paged read.

    Resolving drop emails locally avoids thousands of per-row GETs. Duplicate emails now
    RAISE: the previous `emails[e] = u["id"]` silently kept the last user seen, so two
    people sharing an address collapsed into one principal and the other was never
    certified — an invisible coverage gap in an access-certification load.
    """
    emails, dupes = {}, defaultdict(list)
    for u in client.paged("/api/v1/users?limit=200"):
        e = (u.get("profile", {}).get("email") or "").strip().lower()
        if not e:
            continue
        if e in emails and emails[e] != u["id"]:
            dupes[e].append(u["id"])
        emails.setdefault(e, u["id"])
    if dupes:
        detail = "; ".join(f"{e}: {[emails[e]] + ids}" for e, ids in list(dupes.items())[:10])
        raise DuplicateIdentityError(
            f"{len(dupes)} email(s) map to more than one Okta user — the join key is not "
            f"unique and one principal per collision would go uncertified. {detail}")
    return emails


def read_drop(app):
    """Rows of an app's drop CSV, with the required columns validated up-front.

    A missing column used to raise KeyError from inside the row loop — potentially after
    the app had already been partially written.
    """
    path = PROJ / app["drop"]
    if not path.exists():
        raise DropError(f"{app['tab']}: drop file not found: {path}")
    with path.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        missing = [c for c in DROP_COLUMNS if c not in (reader.fieldnames or [])]
        if missing:
            raise DropError(f"{path}: missing required column(s) {missing}; "
                            f"found {reader.fieldnames}")
        return list(reader)


def validate_roles(app):
    """Every role this app declares must carry a privilege ranking, checked BEFORE writes.

    An unranked role used to sort as -1 via `PRIORITY.get(r, -1)` — silently the lowest
    privilege, which is the precise masking that highest-privilege-wins exists to prevent.
    """
    unranked = sorted(r for r in app["roles"] if r not in domain.PRIVILEGE_ORDER)
    if unranked:
        raise DropError(
            f"{app['tab']}: role(s) {unranked} have no entry in PRIVILEGE_ORDER. Rank them in "
            f"biterm_domain.py — an unranked role cannot be certified as highest-privilege.")


def expected_grants(app, emails):
    """Re-derive the intended end state from the drop.

    Returns (expected {principal_id: highest role}, stats) where stats carries the row
    arithmetic: orphan rows (email resolves to no Okta user), unknown-role rows, and
    resolvable rows. Duplicate rows are EXPECTED (multi-account users), so coverage is
    counted in ROWS while the grant contract is per PRINCIPAL — conflating the two broke
    the old check on every app with duplicates.
    """
    validate_roles(app)
    rows = read_drop(app)
    valid_roles = set(app["roles"])
    roles_by_pid = defaultdict(set)
    stats = {"rows": len(rows), "orphan_rows": 0, "unknown_role_rows": 0,
             "resolvable_rows": 0, "unknown_roles": set()}
    for r in rows:
        uid = emails.get(r["email"].strip().lower())
        if not uid:
            stats["orphan_rows"] += 1
            continue
        role = r["app_role"].strip()
        if role not in valid_roles:
            stats["unknown_role_rows"] += 1
            stats["unknown_roles"].add(role)
            continue
        stats["resolvable_rows"] += 1
        roles_by_pid[uid].add(role)
    expected = {uid: domain.highest_privilege(rs) for uid, rs in roles_by_pid.items()}
    stats["conflicted"] = sum(1 for rs in roles_by_pid.values() if len(rs) > 1)
    stats["principals"] = len(expected)
    stats["unknown_roles"] = sorted(stats["unknown_roles"])
    return expected, stats


def entitlement_values(client, app):
    """(entitlement_id, {role name: value id}) for the app's `Role` entitlement, or (None, {})."""
    ents = client.get_json("/governance/api/v1/entitlements?filter="
                           + _quote(f'parentResourceOrn eq "{app_orn(app)}"'))
    role_ent = next((e for e in ents.get("data", []) if e["name"] == "Role"), None)
    if role_ent is None:
        return None, {}
    vals = client.get_json(f"/governance/api/v1/entitlements/{role_ent['id']}/values")
    return role_ent["id"], {v["name"]: v["id"] for v in vals.get("data", [])}


def granted_values(client, app, id_to_name):
    """(principal_id -> sorted granted role names, count of value-less bare grants).

    A page that fails RAISES. It used to `break`, returning a truncated map as if it were
    complete: in the loader that made already-correct principals look empty and triggered a
    mass re-POST with wrong "corrected" counts; in the verifier it manufactured mismatches.
    A paginated read that cannot finish is not a smaller answer, it is no answer.
    """
    granted, bare = defaultdict(list), 0
    path = ("/governance/api/v1/grants?filter="
            + _quote(f'targetResourceOrn eq "{app_orn(app)}"'))
    for g in biterm_http.paged_governance(client, path):
        pid = g["targetPrincipal"]["externalId"]
        names = [id_to_name.get(v["id"], f"?{v['id']}")
                 for e in g.get("entitlements", []) for v in e.get("values", [])]
        if names:
            granted[pid].extend(names)
        else:
            bare += 1
    return {p: sorted(set(v)) for p, v in granted.items()}, bare


def em_enabled(client, app):
    """(bool, detail). Distinguishes "EM is off" from "I could not ask".

    The old code discarded the HTTP status and read `settings.emOptInStatus` off an empty
    dict, so a 401/403/5xx rendered as "emOptInStatus=None (enable EM in Console)" and the
    app dropped silently out of a compliance load with the operator sent to fix a
    non-problem.
    """
    live = client.get_json(f"/api/v1/apps/{app['app_id']}")
    em = (live.get("settings") or {}).get("emOptInStatus")
    return em == "ENABLED", str(em)


def _quote(s):
    import urllib.parse
    return urllib.parse.quote(s)
