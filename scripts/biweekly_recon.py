#!/usr/bin/env python3
"""Biweekly termination reconciliation — the detective control.

3-way join (app roster <-> HR <-> Okta) over every certifiable population, classified
into risk tiers: auto-clear, auto-actioned (ServiceNow REQ/RITM per confirmed removal),
and high-risk human review with a LOUD unknown branch (never default-to-fine).

Rules this encodes (project CLAUDE.md is the authority):
  - HR employment status is the only legitimacy authority; Okta status is enrichment.
  - The HR check runs on EVERYONE first — an exception never suppresses a termination hit.
  - Exceptions carry owner+expiry: expired -> high-risk; owner terminated -> reassignment flag.
  - The SFDC 3rd-party file is EXCLUDED (user 2026-07-22: obsolete legacy export;
    Salesforce is covered by the randomized STARS-style tabs).
  - Closure = verified disappearance on the NEXT cycle, guarded: a missing or collapsed
    export never auto-closes its findings (absence of evidence != removal).
  - Tickets are created only for auto-actioned findings (exact identity + Terminated/
    Retired), once per finding — later cycles age/escalate, never re-ticket.
  - This pipeline never removes access.

Outputs per cycle under cycles/cycle_<ts>/: report.xlsx (Summary + tab per app,
findings first), state.json (immutable snapshot incl. per-finding source rows),
three notification digests (admin / adjudication / ownership), a tickets ledger
(tickets.jsonl, appended and fsynced as each chain is created), and SHA256SUMS +
evidence_manifest.json pinning both the inputs reviewed and the outputs produced.

Hardening applied 2026-07-26 (see docs/CODE_REVIEW_2026-07-26.md):
  * Ticket creation is IDEMPOTENT. Every chain carries a deterministic correlation_id, and
    ServiceNow is asked whether that chain already exists before ordering. Previously the
    only record of a ticket was state.json, written after the whole loop — a crash at
    ticket 300 of 400 orphaned 300 chains and the next cycle ordered 300 duplicates.
  * Findings are keyed on a STABLE per-account identity, not on `upn or alias:…`. When an
    app owner backfilled a missing email, the old key vanished and a new one appeared: the
    control recorded a verified CLOSURE, wrote "REMOVAL VERIFIED" into the ticket as audit
    evidence, and opened a fresh finding, for someone who never lost access. Prior state
    written under the old scheme is still matched (see closure_pass) so the first cycle
    after this change does not emit a wall of false closures.
  * Exception expiries are PARSED to real dates and a malformed register is fatal. They
    used to be compared as raw strings, so an Excel date cell ("46234") never expired.
  * The exception register is read by HEADER NAME, not by column position.
  * Every HTTP call has a timeout and a uniform retry ladder; ServiceNow errors are typed.
  * A partially-created ticket chain is recorded rather than collapsed into "SN-ERROR".

Usage: biweekly_recon.py [--apply] [--rosters DIR | --feeds DIR [--feed-date YYYYMMDD]]
                         [--today YYYY-MM-DD] [--verbose]
       (--apply absent = DRY: everything runs except ServiceNow writes)
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
from pathlib import Path

import biterm_config
import okta_oauth
import biterm_creds
import biterm_domain as domain
import biterm_http
import biterm_runlog as runlog
from biterm_domain import LEGIT, TERM, HR_NOT_FOUND, STARS_TABS, APP_LABEL_PREFIX
from okta_client import paged  # OAuth service app (least privilege), NOT the SSWS admin token
from xlsx_min import column, column_by_suffix, find_header_row, load_workbook_rows
from xlsx_write import write_xlsx

PROJ = Path(__file__).resolve().parent.parent
CYCLES = PROJ / "cycles"
CYCLES_FEED = PROJ / "cycles_feed"   # unjoined-drop lineage, kept apart from STARS-era state

EXPIRY_WARN_DAYS = 30
ESCALATE_AGE = 2
ROSTER_SANITY_RATIO = 0.5  # export under half its previous size = anomaly, closures frozen

# ServiceNow task states that mean "the fulfiller says they are done".
SN_CLOSED_STATES = ("3", "4", "7")

log = None          # set in main()
_sn = None          # ServiceNow client, built lazily (dry runs never touch it)


class ExceptionRegisterError(RuntimeError):
    """The exception register is unusable. Fatal: every verdict depends on it."""


# ---------------------------------------------------------------- input loading

def load_rosters(rosters_dir):
    """Return (populations, hr_by_upn).

    populations[app] = [row dict: key/alias/upn/empid/hr/src] for the 10 STARS tabs;
    hr_by_upn is worst-wins across tabs, used for exception-owner lookups."""
    stars = load_workbook_rows(Path(rosters_dir) / "FAKE USERS - STARS Report.xlsx")
    populations, hr_by_upn = {}, {}
    missing_tabs = [t for t in STARS_TABS if t not in stars]
    if missing_tabs:
        raise ExceptionRegisterError(
            f"STARS workbook is missing expected tab(s) {missing_tabs}; refusing to run a "
            f"cycle over a partial population (found: {sorted(stars)})")
    for tab in STARS_TABS:
        rows = stars[tab]
        # Locate the header by content: the offset genuinely varies per tab, and a fixed
        # index silently mapped every column to the wrong header when it drifted.
        hdr_idx, headers = find_header_row(
            rows, ["TH_UPN", "TH_EmployeeStatus", "TH_EmployeeID"], sheet_name=tab)
        upn_c = column(headers, "TH_UPN", sheet_name=tab)
        st_c = column(headers, "TH_EmployeeStatus", sheet_name=tab)
        id_c = column(headers, "TH_EmployeeID", sheet_name=tab)
        alias_c = column_by_suffix(headers, ("_NetworkAlias", "_USERNAME"), sheet_name=tab)
        pop = []
        for r in rows[hdr_idx + 1:]:
            if not any(str(v).strip() for v in r.values()):
                continue
            upn = domain.normalise_upn(r.get(upn_c))
            alias = (r.get(alias_c) or "").strip()
            hr = (r.get(st_c) or "").strip()
            row = {"alias": alias, "upn": upn,
                   "empid": (r.get(id_c) or "").strip(),
                   "hr": hr, "src": {str(k): str(v) for k, v in r.items()}}
            row["key"] = domain.identity_key(row)
            row["legacy_key"] = domain.legacy_identity_key(row)
            pop.append(row)
            if upn and (hr in TERM or upn not in hr_by_upn):
                hr_by_upn[upn] = hr
        populations[tab] = pop
    return populations, hr_by_upn


EXCEPTION_HEADERS = {
    "upn": ("UPN", "User UPN", "Account UPN", "Email"),
    "owner": ("Owner", "Owner UPN", "Exception Owner", "owner_upn"),
    "expiry": ("Expiry", "Expiry Date", "Expiration", "Expires", "expiry"),
    "type": ("Type", "Exception Type", "exception_type"),
}


def load_exceptions(rosters_dir):
    """exceptions[app][upn] = {owner, expiry(date), expiry_raw, type}.

    Columns are resolved by HEADER NAME. They were previously read by position —
    r.get(6)/r.get(7)/r.get(4) — against a spreadsheet maintained by humans outside this
    repo, so one inserted column silently swapped owner, expiry and type with no error.

    A malformed or unparseable expiry aborts the cycle. Row-level guessing is not available
    here: an exception that is silently treated as unexpired is a direct control failure,
    and every verdict in the run depends on this register being trustworthy.
    """
    book = load_workbook_rows(Path(rosters_dir) / "FAKE USERS - Exception List.xlsx")
    out, problems = {}, []
    for app, rows in book.items():
        if not rows:
            continue
        try:
            hdr_idx, headers = find_header_row(
                rows, [EXCEPTION_HEADERS["upn"][0]], sheet_name=app)
        except Exception:
            hdr_idx, headers = 0, {str(v).strip(): k for k, v in (rows[0] or {}).items()
                                   if str(v).strip()}
        try:
            cols = {field: column(headers, *names, sheet_name=app)
                    for field, names in EXCEPTION_HEADERS.items()}
        except Exception as e:
            problems.append(f"{app}: {e}")
            continue
        entries = {}
        for n, r in enumerate(rows[hdr_idx + 1:], start=hdr_idx + 2):
            upn = (r.get(cols["upn"], "") or "").strip().lower()
            if not upn:
                continue
            raw = (r.get(cols["expiry"], "") or "").strip()
            try:
                expiry = domain.parse_date(raw, field=f"{app} row {n} expiry")
            except domain.DateFormatError as e:
                problems.append(str(e))
                continue
            entries[upn] = {"owner": (r.get(cols["owner"], "") or "").strip().lower(),
                            "expiry": expiry, "expiry_raw": raw,
                            "type": (r.get(cols["type"], "") or "").strip()}
        out[app] = entries
    if problems:
        raise ExceptionRegisterError(
            "exception register is not usable — fix the source file and re-run:\n  "
            + "\n  ".join(problems[:25])
            + (f"\n  … and {len(problems) - 25} more" if len(problems) > 25 else ""))
    return out


def okta_state():
    """(login->status, app label->set of assigned logins, unresolved_assignments) — live pulls."""
    users = {}
    for u in paged("/api/v1/users?limit=200"):
        users[u["profile"]["login"].lower()] = (u["status"], u["id"])
    for u in paged("/api/v1/users?limit=200&filter=status%20eq%20%22DEPROVISIONED%22"):
        users[u["profile"]["login"].lower()] = (u["status"], u["id"])
    id_to_login = {uid: login for login, (_, uid) in users.items()}
    assigns, unresolved = {}, {}
    for a in paged("/api/v1/apps?limit=200"):
        if a["label"].startswith(APP_LABEL_PREFIX):
            logins, missing = set(), []
            for au in paged(f"/api/v1/apps/{a['id']}/users?limit=200"):
                login = id_to_login.get(au["id"])
                if login:
                    logins.add(login)
                else:
                    # Previously every unresolvable assignee collapsed to the string "?",
                    # so N of them were indistinguishable from one. Keep the ids.
                    missing.append(au["id"])
            assigns[a["label"]] = logins
            if missing:
                unresolved[a["label"]] = missing
    return {l: s for l, (s, _) in users.items()}, assigns, unresolved


# ---------------------------------------------------------------- classification

def classify(populations, exceptions, okta_users, today):
    """Bucket every roster row. Returns (rows_by_app, findings).

    Finding classes: ticket (auto-actioned), exception_expired, owner_terminated,
    unknown (loud). Non-finding buckets: pass, exception_ok. Each row also carries
    its live Okta status for the 3-way callout.

    `today` is a datetime.date.
    """
    rows_by_app, findings = {}, []

    def okta_of(upn):
        return okta_users.get(upn, "NONE") if upn else "n/a"

    def finding(app, row, cls, reason):
        findings.append({"app": app, "key": row["key"], "legacy_key": row.get("legacy_key", ""),
                         "key_scheme": "v2", "cls": cls, "reason": reason,
                         "upn": row["upn"], "alias": row["alias"], "empid": row["empid"],
                         "hr": row["hr"], "okta": okta_of(row["upn"]), "snapshot": row["src"]})

    for app, pop in populations.items():
        exc = exceptions.get(app, {})
        out = []
        for row in pop:
            hr, upn = row["hr"], row["upn"]
            okta = okta_of(upn)
            # 1. HR verdict on everyone first
            if hr in TERM:
                bucket, reason = "ticket", f"{hr} in HR, access present" + \
                    (" — Okta still ACTIVE" if okta == "ACTIVE" else "")
                finding(app, row, "ticket", reason)
            # 2. exception evaluation (never reached by terminated rows)
            elif upn in exc:
                e = exc[upn]
                if domain.is_expired(e["expiry"], today):
                    bucket, reason = "exception_expired", f"exception lapsed {e['expiry'].isoformat()}"
                    finding(app, row, bucket, reason)
                else:  # owner-terminated is judged from the register in ownership_review()
                    bucket, reason = "exception_ok", f"{e['type']}, owner {e['owner'] or '(none)'}"
            # 3. legitimate / loud unknown
            elif hr in LEGIT:
                bucket, reason = "pass", hr
            elif hr == HR_NOT_FOUND or not upn:
                bucket, reason = "unknown", "Not found in TalentHub / no usable identity"
                finding(app, row, "unknown", reason)
            else:
                bucket, reason = "unknown", f"unrecognized HR status {hr!r}"
                finding(app, row, "unknown", reason)
            out.append({**row, "okta": okta, "bucket": bucket, "reason": reason})
        rows_by_app[app] = out
    return rows_by_app, findings


def ownership_review(exceptions, hr_by_upn, today):
    """Owner-terminated findings + expiring-soon warnings, from the exception register
    itself (independent of whether the excepted account was otherwise flagged)."""
    findings, warnings = [], []
    warn_before = today + dt.timedelta(days=EXPIRY_WARN_DAYS)
    for app, entries in exceptions.items():
        for upn, e in entries.items():
            if hr_by_upn.get(upn) in TERM:
                continue  # account holder terminated -> already a ticket; owner moot
            if hr_by_upn.get(e["owner"]) in TERM:
                findings.append({"app": app, "key": f"exc:{upn}", "legacy_key": upn,
                                 "key_scheme": "v2", "cls": "owner_terminated",
                                 "reason": f"owner {e['owner']} is {hr_by_upn[e['owner']]} — reassign",
                                 "upn": upn, "alias": "", "empid": "", "hr": hr_by_upn.get(upn, ""),
                                 "okta": "", "snapshot": {k: str(v) for k, v in e.items()}})
            elif today <= e["expiry"] <= warn_before:
                warnings.append(
                    f"{app}: {upn} exception expires {e['expiry'].isoformat()} (owner {e['owner']})")
    return findings, warnings


# ---------------------------------------------------------------- closure

def closure_pass(prev_state, findings, roster_counts):
    """Carry ages/tickets forward; close what verifiably disappeared.

    Matching is by the stable identity key, with a fallback to the pre-2026-07-26 key
    formula so state written under the old scheme still lines up. Without that fallback the
    first cycle after the key change would read every open finding as remediated and emit a
    full set of false closures — the exact failure the new key exists to prevent.
    """
    open_now = {(f["app"], f["key"], f["cls"]): f for f in findings}
    by_legacy = {}
    for f in findings:
        if f.get("legacy_key"):
            by_legacy.setdefault((f["app"], f["legacy_key"], f["cls"]), f)

    closures, anomalies, migrated = [], set(), 0
    if prev_state:
        for app, prev_n in prev_state.get("roster_counts", {}).items():
            cur_n = roster_counts.get(app, 0)
            if cur_n < prev_n * ROSTER_SANITY_RATIO:
                anomalies.add(app)
        for f in prev_state.get("findings", []):
            k = (f["app"], f["key"], f["cls"])
            current = open_now.get(k)
            if current is None:
                # Same account, different key: either prior state predates the key change,
                # or an identifier was backfilled between cycles. Not a removal.
                current = by_legacy.get(k)
                if current is not None:
                    migrated += 1
                    if f.get("key_scheme") == "v2" and f["key"] != current["key"]:
                        current["identity_changed"] = True
                        current["reason"] += " [IDENTITY CHANGED since last cycle — not a closure]"
            if current is not None:
                current["age"] = f.get("age", 1) + 1
                current["ticket"] = f.get("ticket", "")
                current["correlation_id"] = f.get("correlation_id", "")
                current["first_cycle"] = f.get("first_cycle", prev_state.get("cycle", ""))
            elif f["app"] in anomalies:
                open_now[k] = {**f, "reason": f["reason"] + " [UNVERIFIABLE: export anomaly]"}
            else:
                closures.append({**f, "closed_in": "current"})
    for f in findings:
        f.setdefault("age", 1)
        f.setdefault("ticket", "")
        f.setdefault("correlation_id", "")
    return list(open_now.values()), closures, anomalies, migrated


# ---------------------------------------------------------------- servicenow

def sn_client(dry_run):
    """Lazily built ServiceNow client: timeout, retry ladder, typed errors, change log.

    Built on demand so a dry run never needs the credential to exist, and so `--help`
    cannot fail on a missing secret.
    """
    global _sn
    if _sn is None:
        cfg = biterm_config.get("servicenow", default={})
        creds_file = cfg.get("credentials_file")
        _sn = biterm_http.Client(
            cfg["instance"],
            biterm_http.basic(lambda: biterm_creds.basic_auth(creds_file)),
            error_class=biterm_http.ServiceNowApiError,
            on_write=runlog.change_recorder("biweekly_recon", dry_run=dry_run),
            logger=log)
    return _sn


def sn_call(method, path, body=None, dry_run=False):
    return sn_client(dry_run).request(method, path, body)[1]


_sn_ids = {}


def sn_id(table, query):
    """Resolve-and-cache a sys_id by query; '' when the record doesn't exist."""
    if (table, query) not in _sn_ids:
        r = sn_call("GET", f"/api/now/table/{table}?sysparm_query={urllib.parse.quote(query, safe='=^')}"
                           "&sysparm_fields=sys_id&sysparm_limit=1")["result"]
        _sn_ids[(table, query)] = r[0]["sys_id"] if r else ""
    return _sn_ids[(table, query)]


def sn_find_chain(correlation_id):
    """Return an existing ticket chain for this finding, or None.

    This is the idempotency gate. State.json used to be the only record that a ticket
    existed, and it was written after the entire ticketing loop — so any crash, SystemExit
    from the API layer, or Ctrl-C left the created chains untracked and the next cycle
    ordered them all again. Asking ServiceNow directly means the answer survives losing the
    state file entirely.
    """
    ritms = sn_call("GET", "/api/now/table/sc_req_item?sysparm_query="
                           f"correlation_id={urllib.parse.quote(correlation_id, safe='')}"
                           "&sysparm_fields=sys_id,number,request&sysparm_limit=1")["result"]
    if not ritms:
        return None
    ritm = ritms[0]
    req_num = ""
    req_ref = ritm.get("request")
    req_sys = req_ref.get("value") if isinstance(req_ref, dict) else req_ref
    if req_sys:
        req = sn_call("GET", f"/api/now/table/sc_request/{req_sys}?sysparm_fields=number")["result"]
        req_num = req.get("number", "") if isinstance(req, dict) else ""
    tasks = sn_call("GET", f"/api/now/table/sc_task?sysparm_query=request_item={ritm['sys_id']}"
                           "&sysparm_fields=number&sysparm_limit=1")["result"]
    task_num = tasks[0]["number"] if tasks else ""
    return f"{req_num}/{ritm['number']}/{task_num}"


class PartialTicket(RuntimeError):
    """A chain was partially created. Carries what exists so nothing is orphaned silently."""

    def __init__(self, message, created):
        self.created = created
        super().__init__(f"{message} (created so far: {created})")


def sn_create_ticket(f, cycle_id, correlation_id):
    """Order the dedicated catalog item (variables carry the evidence), stamp the RITM,
    set requested_for to the terminated user's own SN record, and adopt the flow's
    fulfillment task — retitled and assigned to the Access Management group.

    Every partial failure raises PartialTicket carrying the identifiers that DO exist, so a
    half-built chain is recorded as evidence instead of collapsing to the string "SN-ERROR"
    and being silently re-ordered next cycle.
    """
    cfg = biterm_config.get("servicenow", default={})
    created = {"correlation_id": correlation_id}
    try:
        order = sn_call("POST", f"/api/sn_sc/servicecatalog/items/{cfg['catalog_item']}/order_now", {
            "sysparm_quantity": "1",
            "variables": {"application": f["app"], "account_alias": f["alias"], "upn": f["upn"],
                          "employee_id": f["empid"], "hr_status": f["hr"], "okta_status": f["okta"],
                          "reason": f["reason"], "cycle_id": cycle_id,
                          "correlation_id": correlation_id}})["result"]
        created["request_number"] = order.get("request_number", "")
        created["request_id"] = order.get("request_id", "")
    except biterm_http.HttpError as e:
        raise PartialTicket(f"order_now failed: {e}", created) from e

    try:
        # email keeps the full login (user_name truncates at 40)
        requested_for = sn_id("sys_user", f"email={f['upn']}") if f["upn"] else ""
        if requested_for:
            sn_call("PATCH", f"/api/now/table/sc_request/{order['request_id']}",
                    {"requested_for": requested_for})
        ritm = sn_call("GET", f"/api/now/table/sc_req_item?sysparm_query=request={order['request_id']}"
                              "&sysparm_fields=sys_id,number")["result"][0]
        created["ritm_number"] = ritm["number"]
        sn_call("PATCH", f"/api/now/table/sc_req_item/{ritm['sys_id']}", {
            # correlation_id is written FIRST-class: it is what makes a re-run idempotent.
            "correlation_id": correlation_id,
            "correlation_display": f"BiTerm {f['app']} / {f['key']}",
            "short_description": f"Remove access: {f['upn'] or f['alias']} from {f['app']}",
            "description": (f"Biweekly termination review {cycle_id}\n"
                            f"App: {f['app']}\nAccount: {f['alias'] or f['upn']}\nUPN: {f['upn']}\n"
                            f"EmployeeID: {f['empid']}\nHR status: {f['hr']}\nOkta status: {f['okta']}\n"
                            f"Reason: {f['reason']}\nFinding key: {f['key']}\n"
                            f"Correlation: {correlation_id}\n"
                            f"Remediation is manual; closure verified next cycle."),
        })
    except (biterm_http.HttpError, IndexError, KeyError) as e:
        raise PartialTicket(f"RITM stamping failed: {e}", created) from e

    # adopt the flow-generated fulfillment task (closing it drives the RITM/REQ lifecycle);
    # poll with backoff because the flow creates it async
    tasks, delay = [], 0.5
    for _ in range(8):
        tasks = sn_call("GET", f"/api/now/table/sc_task?sysparm_query=request_item={ritm['sys_id']}"
                               "&sysparm_fields=sys_id,number")["result"]
        if tasks:
            break
        time.sleep(delay)
        delay = min(delay * 2, 8)
    fill = {"assignment_group": sn_id("sys_user_group", f"name={cfg['assignment_group']}"),
            "correlation_id": correlation_id,
            "short_description": f"Remove access: {f['upn'] or f['alias']} from {f['app']}",
            "description": f"Manually remove this access in {f['app']}, then close this task. "
                           f"Closure is verified by the next review cycle."}
    # Route to the GROUP by default. A named individual compiled into the control means the
    # control breaks when that person leaves; set servicenow.assignee only if required.
    if cfg.get("assignee"):
        fill["assigned_to"] = sn_id("sys_user", f"user_name={cfg['assignee']}")
    try:
        if tasks:
            task_num = tasks[0]["number"]
            sn_call("PATCH", f"/api/now/table/sc_task/{tasks[0]['sys_id']}", fill)
        else:  # flow produced nothing in time; create the task so work is never lost
            log.warning(f"  SN flow produced no fulfillment task for {ritm['number']} — "
                        f"creating one directly (flow may be misconfigured)")
            task_num = sn_call("POST", "/api/now/table/sc_task",
                               {**fill, "request_item": ritm["sys_id"]})["result"]["number"]
    except (biterm_http.HttpError, KeyError) as e:
        raise PartialTicket(f"fulfillment task setup failed: {e}", created) from e
    created["task_number"] = task_num
    return f"{order['request_number']}/{ritm['number']}/{task_num}"


def closure_writeback(findings, closures, roster_counts, cycle_id):
    """Two-phase closure evidence, written onto the tickets themselves.

    Verified closures get a BEFORE/AFTER work note on their RITM (the ticket becomes
    self-contained audit evidence). Ticket findings whose task was closed but whose
    account is STILL in the roster are false claims: note both records, reopen the
    task, and tag the finding so the report/digest carry the reopen."""
    def parts(t):
        p = (t or "").split("/")
        return (p[1], p[2]) if len(p) == 3 else ("", "")

    verified = reopened = 0
    for c in closures:
        ritm_num, _ = parts(c.get("ticket", ""))
        if not ritm_num:
            continue
        r = sn_call("GET", f"/api/now/table/sc_req_item?sysparm_query=number={ritm_num}"
                           "&sysparm_fields=sys_id")["result"]
        if r:
            sn_call("PATCH", f"/api/now/table/sc_req_item/{r[0]['sys_id']}", {"work_notes":
                f"REMOVAL VERIFIED — {cycle_id}\n"
                f"BEFORE ({c.get('first_cycle', 'prior cycle')}): {c['upn'] or c['alias']} present "
                f"in {c['app']} roster; HR status {c['hr']}\n"
                f"AFTER ({cycle_id}): absent from fresh {c['app']} export "
                f"({roster_counts.get(c['app'], '?')} rows, passed sanity checks)"})
            verified += 1
            log.info(f"  VERIFIED  {ritm_num}: {c['upn'] or c['alias']}")

    for f in findings:
        if f["cls"] != "ticket" or f.get("age", 1) < 2:
            continue
        ritm_num, task_num = parts(f.get("ticket", ""))
        if not task_num:
            continue
        t = sn_call("GET", f"/api/now/table/sc_task?sysparm_query=number={task_num}"
                           "&sysparm_fields=sys_id,state")["result"]
        if t and t[0]["state"] in SN_CLOSED_STATES:  # closed, yet the account is still there
            note = (f"REMOVAL NOT VERIFIED — {cycle_id}\n"
                    f"Task was closed, but {f['upn'] or f['alias']} is still present in the "
                    f"fresh {f['app']} export. Reopening; finding age {f['age']} cycles.")
            sn_call("PATCH", f"/api/now/table/sc_task/{t[0]['sys_id']}",
                    {"state": "2", "work_notes": note})
            r = sn_call("GET", f"/api/now/table/sc_req_item?sysparm_query=number={ritm_num}"
                               "&sysparm_fields=sys_id")["result"]
            if r:
                sn_call("PATCH", f"/api/now/table/sc_req_item/{r[0]['sys_id']}", {"work_notes": note})
            f["reason"] += " [REOPENED: task closed without verified removal]"
            reopened += 1
            log.info(f"  REOPENED  {task_num}: {f['upn'] or f['alias']}")
    return verified, reopened


def sweep_flow_stage_tasks():
    """The OOB item flow spawns a stage-2 'deploy' task (Field Services) whenever a
    removal task closes. Irrelevant to access removal: close each as skipped with a
    note — which also lets the flow finish and close its RITM/REQ properly.

    Paginated: the previous single sysparm_limit=200 call silently left stray task 201+
    open, which blocks its RITM lifecycle forever.
    """
    cfg = biterm_config.get("servicenow", default={})
    swept, offset, page = 0, 0, 200
    while True:
        strays = sn_call("GET", "/api/now/table/sc_task?sysparm_query="
                         f"request_item.cat_item={cfg['catalog_item']}^active=true"
                         "^short_descriptionNOT%20LIKERemove%20access"
                         f"&sysparm_fields=sys_id,number&sysparm_limit={page}"
                         f"&sysparm_offset={offset}")["result"]
        if not strays:
            break
        for t in strays:
            sn_call("PATCH", f"/api/now/table/sc_task/{t['sys_id']}", {
                "state": "7", "work_notes": "Auto-skipped by reconciliation: flow stage not "
                                            "applicable to access-removal fulfillment."})
            swept += 1
            log.debug(f"  swept {t['number']}")
        if len(strays) < page:
            break
        # Closed rows drop out of the active=true filter, so the window does not advance.
        if swept > 10000:
            log.warning("  stage-task sweep exceeded 10000 records; stopping to avoid a loop")
            break
    if swept:
        log.info(f"  swept {swept} stray flow stage task(s)")
    return swept


# ---------------------------------------------------------------- outputs

BUCKET_ORDER = {"ticket": 0, "owner_terminated": 1, "exception_expired": 2, "unknown": 3,
                "exception_ok": 4, "pass": 5}
HEADER = ["Account (alias)", "UPN", "EmployeeID", "HR Status", "Okta", "Bucket", "Reason",
          "Ticket", "Age", "Finding key"]


def _unique_sheet_names(names):
    """Excel caps sheet names at 31 chars. Truncating without de-duplication produced
    duplicate sheet names — a workbook Excel may refuse to open — for any two apps sharing
    a 31-character prefix."""
    out, seen = [], {}
    for n in names:
        base = n[:31]
        if base in seen:
            seen[base] += 1
            suffix = f"~{seen[base]}"
            base = base[:31 - len(suffix)] + suffix
        else:
            seen[base] = 0
        out.append(base)
    return out


def write_report(out_dir, rows_by_app, findings, closures, anomalies, cycle_id, today,
                 okta_assigns=None, unresolved_assigns=None):
    tickets = {(f["app"], f["key"]): (f.get("ticket", ""), f.get("age", 1)) for f in findings}
    # Flagged ROWS and ticket CHAINS are different units: duplicate seats for one person on
    # one app collapse to a single finding and a single ticket. Reporting only rows under a
    # "tickets" heading makes the workbook disagree with ServiceNow's actual chain count.
    chains_by_app = {}
    for f in findings:
        if f["cls"] == "ticket":
            chains_by_app[f["app"]] = chains_by_app.get(f["app"], 0) + 1
    summary = [["Biweekly Termination Review", cycle_id], ["Run date", today.isoformat()],
               ["Run id", runlog.run_id()], ["Environment", biterm_config.describe()], [],
               ["App", "rows", "pass", "exception_ok", "ticket rows", "ticket chains",
                "expired_exc", "unknown", "no-Okta orphans", "in Okta not in roster", "closures"]]
    sheets = []
    total = dict.fromkeys(["rows", "tickets", "ticket_chains", "unknown", "orphans",
                           "closures", "okta_only"], 0)
    owner_flags = [f for f in findings if f["cls"] == "owner_terminated"]
    okta_assigns = okta_assigns or {}
    for app, rows in rows_by_app.items():
        counts = {}
        orphans = 0
        body = [HEADER]
        for r in sorted(rows, key=lambda r: BUCKET_ORDER[r["bucket"]]):
            counts[r["bucket"]] = counts.get(r["bucket"], 0) + 1
            if r["okta"] == "NONE" or (r["okta"] == "n/a" and r["bucket"] == "unknown"):
                orphans += 1
            t, age = tickets.get((app, r["key"]), ("", ""))
            body.append([r["alias"], r["upn"], r["empid"], r["hr"] or "(no HR source)",
                         r["okta"], r["bucket"], r["reason"], t, age if t else "", r["key"]])
        # The Okta assignment leg was pulled and then discarded. It answers the reverse
        # question the roster cannot: who is assigned in Okta but absent from the app export.
        roster_upns = {r["upn"] for r in rows if r["upn"]}
        okta_only = sorted(okta_assigns.get(APP_LABEL_PREFIX + app, set()) - roster_upns)
        closed_here = sum(1 for c in closures if c["app"] == app)
        summary.append([app + (" ⚠ EXPORT ANOMALY" if app in anomalies else ""),
                        len(rows), counts.get("pass", 0), counts.get("exception_ok", 0),
                        counts.get("ticket", 0), chains_by_app.get(app, 0),
                        counts.get("exception_expired", 0),
                        counts.get("unknown", 0), orphans, len(okta_only), closed_here])
        total["rows"] += len(rows); total["tickets"] += counts.get("ticket", 0)
        total["ticket_chains"] += chains_by_app.get(app, 0)
        total["unknown"] += counts.get("unknown", 0); total["orphans"] += orphans
        total["okta_only"] += len(okta_only)
        total["closures"] += closed_here
        if okta_only:
            body.append([])
            body.append(["ASSIGNED IN OKTA, ABSENT FROM THIS EXPORT (investigate: stale "
                         "assignment or truncated export)"])
            body += [[u] for u in okta_only]
        sheets.append((app, body))
    summary += [[], ["TOTAL rows", total["rows"], "ticket rows", total["tickets"],
                     "ticket chains (= ServiceNow REQs)", total["ticket_chains"], "unknown",
                     total["unknown"], "orphans", total["orphans"],
                     "in Okta not in roster", total["okta_only"],
                     "closures", total["closures"]],
                [], ["Ownership reassignment flags", len(owner_flags)],
                *[["", f"{f['app']}: {f['upn']} — {f['reason']}"] for f in owner_flags]]
    if unresolved_assigns:
        summary += [[], ["Okta app assignments that resolve to no Okta user "
                         "(id shown; investigate)"]]
        for label, ids in sorted(unresolved_assigns.items()):
            summary.append(["", f"{label}: {len(ids)} — {', '.join(ids[:10])}"
                                + (" …" if len(ids) > 10 else "")])
    names = _unique_sheet_names([n for n, _ in sheets])
    write_xlsx(out_dir / "report.xlsx",
               [("Summary", summary)] + [(n, b) for n, (_, b) in zip(names, sheets)])
    return total


def write_digests(out_dir, findings, closures, warnings, anomalies, total, cycle_id):
    new = [f for f in findings if f.get("age", 1) == 1]
    aged = [f for f in findings if f.get("age", 1) >= ESCALATE_AGE]
    changed = [f for f in findings if f.get("identity_changed")]
    adm = [f"# Admin digest — cycle {cycle_id}", "",
           f"Population {total['rows']} rows | ticket chains open {total['ticket_chains']} "
           f"(from {total['tickets']} flagged rows) "
           f"({len([f for f in new if f['cls'] == 'ticket'])} new) | verified closures {total['closures']} "
           f"| unknowns {total['unknown']} | no-Okta orphans {total['orphans']} "
           f"| assigned in Okta but absent from export {total['okta_only']}", ""]
    adm += [f"- NEW {f['app']}: {f['upn'] or f['alias']} — {f['reason']} [{f.get('ticket', 'DRY')}]"
            for f in new if f["cls"] == "ticket"]
    adm += [f"- CLOSED {c['app']}: {c['upn'] or c['alias']} — verified gone [{c.get('ticket', '')}]"
            for c in closures]
    if changed:
        adm += ["", "Identity changed since last cycle (carried forward, NOT closed):"]
        adm += [f"- {f['app']}: {f['upn'] or f['alias']} ({f['key']})" for f in changed]
    adj = [f"# Adjudication queue — cycle {cycle_id}", "",
           "Escalated (open >= 2 cycles):", *[
               f"- {f['app']}: {f['upn'] or f['alias']} — {f['reason']} (age {f['age']}, {f.get('ticket') or 'no ticket'})"
               for f in aged], "",
           "High-risk needing judgment:"]
    adj += [f"- {f['app']}: {f['upn'] or f['alias']} — [{f['cls']}] {f['reason']}"
            for f in findings if f["cls"] in ("exception_expired", "owner_terminated")]
    adj += ["", f"Loud unknowns: {total['unknown']} rows (full list in report.xlsx; "
                "adjudicate via owner registry / standing exemptions)"]
    if anomalies:
        adj += ["", "EXPORT ANOMALIES (closures frozen): " + ", ".join(sorted(anomalies))]
    own = [f"# Ownership digest — cycle {cycle_id}", "", "Reassignment required:"]
    own += [f"- {f['app']}: {f['upn']} — {f['reason']}" for f in findings if f["cls"] == "owner_terminated"]
    own += ["", "Expiring within 30 days (renew or lapse):", *[f"- {w}" for w in warnings]]
    for name, lines in [("digest_admin.md", adm), ("digest_adjudication.md", adj),
                        ("digest_ownership.md", own)]:
        runlog.write_atomic(out_dir / name, "\n".join(lines) + "\n")


# ---------------------------------------------------------------- state

def _state_payload(cycle_id, today, apply_writes, roster_counts, findings, closures,
                   anomalies, total, inputs, extra=None):
    return {
        "cycle": cycle_id, "today": today.isoformat(), "tickets_live": apply_writes,
        "run_id": runlog.run_id(), "actor": runlog.actor(),
        "key_scheme": "v2", "environment": biterm_config.describe(),
        "inputs": inputs,
        "roster_counts": roster_counts,
        "findings": _jsonable(findings), "closures": _jsonable(closures),
        "anomalies": sorted(anomalies), "summary": total, **(extra or {}),
    }


def _jsonable(obj):
    """dates -> ISO strings, so state.json round-trips through json.load unchanged."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (dt.date, dt.datetime)):
        return obj.isoformat()
    return obj


VOID_MARKER = "VOID"


def load_prev_state(lineage):
    """Most recent cycle OF RECORD (one that actually wrote tickets), plus crash warnings.

    A DRY run is a rehearsal: letting it become the baseline ages every finding on the next
    real cycle (age 2 => "escalated" and "0 new" on a first cycle) and would eventually let
    a rehearsal's roster counts drive the closure sanity check.

    A cycle directory containing a `VOID` file is skipped as a baseline regardless of its
    tickets_live flag. That is the escape hatch for a run that wrote to ServiceNow but must
    not define the closure baseline — e.g. an unintended or aborted live run. The cycle's
    evidence is deliberately NOT deleted: the VOID file records who voided it and why, so
    the audit trail shows the run happened and was excluded on purpose.
    """
    warnings = []
    prev = None
    for p in sorted(lineage.glob("cycle_*/state.json"), reverse=True):
        void = p.parent / VOID_MARKER
        if void.exists():
            warnings.append(f"{p.parent.name} is VOID — excluded as a baseline "
                            f"({void.read_text().strip().splitlines()[0][:90]})")
            continue
        try:
            candidate = json.loads(p.read_text())
        except json.JSONDecodeError:
            warnings.append(f"{p} is unreadable (truncated write?) — skipped as a baseline")
            continue
        if candidate.get("tickets_live"):
            prev = candidate
            break
    # A cycle directory holding a tickets ledger but no state.json is a crashed live run:
    # its chains exist in ServiceNow. The correlation-id gate handles it, but say so loudly.
    for ledger in sorted(lineage.glob("cycle_*/tickets.jsonl")):
        if not (ledger.parent / "state.json").exists():
            n = sum(1 for _ in ledger.open())
            warnings.append(
                f"{ledger.parent.name} has {n} ticket(s) in its ledger but no state.json — "
                f"that run did not finish. Its chains are matched by correlation_id, not lost.")
    return prev, warnings


# ---------------------------------------------------------------- main

def parse_args(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
        # No prefix matching: on a control that can file real tickets, "--appl" must be an
        # error, not a silent guess at "--apply".
        allow_abbrev=False)
    ap.add_argument("--apply", action="store_true",
                    help="perform ServiceNow writes; default is a DRY rehearsal")
    ap.add_argument("--create-tickets", dest="apply", action="store_true",
                    help=argparse.SUPPRESS)   # historical alias, kept so runbooks still work
    ap.add_argument("--rosters", metavar="DIR",
                    help="directory holding the STARS workbook + exception list")
    ap.add_argument("--feeds", metavar="DIR", help="unjoined scheduled drops (feed model)")
    ap.add_argument("--feed-date", metavar="YYYYMMDD", help="which drop to run (default: latest)")
    ap.add_argument("--today", metavar="YYYY-MM-DD", help="override the cycle date")
    ap.add_argument("--yes", action="store_true",
                    help="skip the confirmation prompt for --apply (non-interactive runs)")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.rosters and args.feeds:
        ap.error("--rosters and --feeds are mutually exclusive")
    if args.feed_date and not args.feeds:
        ap.error("--feed-date requires --feeds")
    return args


def confirm_apply(args):
    """A live run against an unrecognised (i.e. real) tenant must be confirmed by hostname.

    Hand-rolled flag parsing previously meant a typo'd `--create-ticket` silently produced a
    DRY run: the operator believed tickets were filed and none were. argparse now rejects
    the typo outright; this prompt covers the opposite error — an unintended live run.
    """
    if args.yes:
        return
    if not sys.stdin.isatty():
        # Fail CLOSED when there is nobody to ask. The first version of this guard returned
        # early on a non-tty, so any piped/automated invocation performed a full live run
        # with no confirmation at all — which is exactly how a diagnostic command created 29
        # unintended ticket chains on 2026-07-26. An unattended live run must be declared
        # explicitly with --yes, never inferred from the absence of a terminal.
        sys.exit("Refusing a non-interactive LIVE run: no terminal to confirm on. "
                 "Pass --yes explicitly if this is a scheduled/unattended run.")
    host = urllib.parse.urlparse(biterm_config.org()).hostname or ""
    sn = biterm_config.get("servicenow", "instance", default="")
    print(f"\nLIVE RUN — ServiceNow tickets will be created in {sn}\n"
          f"Okta tenant: {host}\n", file=sys.stderr)
    typed = input(f"Type the Okta hostname exactly ({host}) to continue: ").strip()
    if typed != host:                       # exact match; `in` accepted a single character
        sys.exit("Confirmation did not match. Aborting; nothing was written.")


def main(argv=None):
    global log
    args = parse_args(argv)
    log = runlog.setup("biweekly_recon", verbose=args.verbose)
    log.info(f"run {runlog.run_id()} by {runlog.actor()} | {biterm_config.describe()}")

    rosters = Path(args.rosters) if args.rosters else PROJ / "App User Lists"
    feeds = Path(args.feeds) if args.feeds else None
    inputs = []

    if feeds:
        import feed_ingest
        populations, hr_by_upn, exceptions, meta = feed_ingest.load(feeds, args.feed_date)
        today = (dt.date.fromisoformat(args.today) if args.today
                 else dt.date.fromisoformat(meta["cycle_date"]))
        inputs = meta["input_files"]
        # Feed cycles keep their own lineage: the STARS-era state under cycles/ keys findings
        # off a different join, so closure_pass would read every prior finding as remediated.
        lineage = CYCLES_FEED
    else:
        populations, hr_by_upn = load_rosters(rosters)
        exceptions = load_exceptions(rosters)
        today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
        meta = None
        inputs = [str(rosters / "FAKE USERS - STARS Report.xlsx"),
                  str(rosters / "FAKE USERS - Exception List.xlsx")]
        lineage = CYCLES

    if args.apply:
        confirm_apply(args)

    cycle_id = time.strftime("cycle_%Y%m%d_%H%M%S")
    out_dir = lineage / cycle_id
    out_dir.mkdir(parents=True)
    ledger = out_dir / "tickets.jsonl"

    if meta:
        log.info(f"{cycle_id}: feed drop {meta['stamp']} — {meta['rows']} app rows across "
                 f"{meta['apps']} apps, {meta['hr_rows']} HR rows | joined {meta['joined']}, "
                 f"no HR match {meta['no_hr_match']}, unjoinable {meta['unjoinable']}")
    log.info(f"{cycle_id}: {sum(len(p) for p in populations.values())} roster rows, "
             f"{len(populations)} apps; pulling Okta...")
    okta_users, okta_assigns, unresolved = okta_state()
    if unresolved:
        log.warning(f"  {sum(len(v) for v in unresolved.values())} Okta app assignment(s) "
                    f"resolve to no Okta user; listed in the report Summary")

    rows_by_app, findings = classify(populations, exceptions, okta_users, today)
    own_findings, warnings = ownership_review(exceptions, hr_by_upn, today)
    findings += own_findings

    prev_state, state_warnings = load_prev_state(lineage)
    for w in state_warnings:
        log.warning(f"  {w}")
    roster_counts = {app: len(rows) for app, rows in populations.items()}
    findings, closures, anomalies, migrated = closure_pass(prev_state, findings, roster_counts)
    if migrated:
        log.info(f"  {migrated} finding(s) matched to prior state via the legacy key "
                 f"(identity backfill or first run after the key change) — not closed")
    for f in findings:
        f.setdefault("first_cycle", cycle_id)  # birth cycle, for BEFORE/AFTER evidence
        f["correlation_id"] = f.get("correlation_id") or domain.correlation_id(
            f["app"], f["key"], f["cls"], f["first_cycle"])

    # Persist state immediately and after every batch of chains. If the ticket loop dies,
    # this file plus tickets.jsonl reconstruct the cycle; previously nothing reached disk
    # until the very end, so a crash mid-loop orphaned every chain created so far.
    def save_state(total=None, extra=None):
        runlog.write_atomic(out_dir / "state.json", json.dumps(_state_payload(
            cycle_id, today, args.apply, roster_counts, findings, closures, anomalies,
            total or {}, inputs, extra), indent=1))

    save_state()

    to_ticket = [f for f in findings
                 if f["cls"] == "ticket" and f.get("ticket") in ("", "DRY", "SN-ERROR", None)]
    adopted = partial = 0
    if args.apply:
        with ledger.open("a", encoding="utf-8") as lf:
            for i, f in enumerate(to_ticket, 1):
                cid = f["correlation_id"]
                try:
                    existing = sn_find_chain(cid)
                    if existing:
                        f["ticket"] = existing
                        adopted += 1
                    else:
                        f["ticket"] = sn_create_ticket(f, cycle_id, cid)
                    entry = {"correlation_id": cid, "app": f["app"], "key": f["key"],
                             "ticket": f["ticket"], "adopted": bool(existing)}
                except PartialTicket as e:
                    partial += 1
                    f["ticket"] = "SN-PARTIAL"
                    f["partial_ticket"] = e.created
                    f["reason"] += " [PARTIAL TICKET — see partial_ticket in state.json]"
                    entry = {"correlation_id": cid, "app": f["app"], "key": f["key"],
                             "ticket": "SN-PARTIAL", "partial": e.created, "error": str(e)}
                    log.error(f"  PARTIAL {f['upn'] or f['alias']}: {e}")
                except biterm_http.HttpError as e:
                    f["ticket"] = "SN-ERROR"
                    entry = {"correlation_id": cid, "app": f["app"], "key": f["key"],
                             "ticket": "SN-ERROR", "error": str(e)}
                    log.error(f"  SN error for {f['upn'] or f['alias']}: {e}")
                # Append + fsync per chain: the ledger is the durable record, not state.json.
                lf.write(json.dumps(entry) + "\n")
                lf.flush()
                os.fsync(lf.fileno())
                log.info(f"  tickets {i}/{len(to_ticket)}: {f['ticket']} "
                         f"{f['upn'] or f['alias']}")
                if i % 25 == 0:
                    save_state()
    else:
        for f in to_ticket:
            f["ticket"] = "DRY"

    verified = reopened = swept = 0
    if args.apply:  # evidence write-back + stage sweep are SN writes; same gate
        verified, reopened = closure_writeback(findings, closures, roster_counts, cycle_id)
        swept = sweep_flow_stage_tasks()

    total = write_report(out_dir, rows_by_app, findings, closures, anomalies, cycle_id, today,
                         okta_assigns, unresolved)
    write_digests(out_dir, findings, closures, warnings, anomalies, total, cycle_id)
    save_state(total, {"adopted_tickets": adopted, "partial_tickets": partial,
                       "closures_verified": verified, "tasks_reopened": reopened,
                       "stage_tasks_swept": swept})

    outputs = [out_dir / n for n in ("report.xlsx", "state.json", "digest_admin.md",
                                     "digest_adjudication.md", "digest_ownership.md")]
    if ledger.exists():
        outputs.append(ledger)
    runlog.evidence_manifest(out_dir, inputs, outputs,
                             extra={"cycle": cycle_id, "tickets_live": args.apply,
                                    "environment": biterm_config.describe()})

    log.info(f"{cycle_id} DONE: {total['rows']} rows | {total['tickets']} ticket rows"
             f"{' (LIVE)' if args.apply else ' (DRY)'} | {adopted} adopted | {partial} partial | "
             f"{len(closures)} closures | {total['unknown']} unknowns | "
             f"{total['orphans']} orphans | {len(own_findings)} owner flags | outputs in {out_dir}")
    if partial:
        log.error(f"{partial} ticket chain(s) are PARTIAL — resolve them in ServiceNow before "
                  f"the next cycle; they are listed in state.json and tickets.jsonl")
        return 2
    return 0


if __name__ == "__main__":
    # Entrypoints translate typed library errors into a clean exit. Library code
    # never calls sys.exit itself — the caller decides what is fatal.
    try:
        sys.exit(main())
    except (biterm_config.ConfigError, biterm_creds.CredentialError,
            biterm_http.HttpError, okta_oauth.OAuthError,
            ExceptionRegisterError) as e:
        sys.exit(f"ABORTED: {e}")
