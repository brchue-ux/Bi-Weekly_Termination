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
and three notification digests (admin / adjudication / ownership) — the exact
would-send payloads; delivery channels are deferred by user decision.

Usage: biweekly_recon.py [--create-tickets] [--rosters DIR | --feeds DIR [--feed-date YYYYMMDD]]
       [--today YYYY-MM-DD]
       (--create-tickets absent = DRY: everything runs except ServiceNow writes)
"""
import datetime as dt
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from okta_client import paged  # OAuth service app (least privilege), NOT seed_tenant's SSWS
from seed_tenant import APP_LABEL_PREFIX, STARS_TABS, NO_UPN
from xlsx_min import load_workbook_rows
from xlsx_write import write_xlsx

PROJ = Path(__file__).parent
CYCLES = PROJ / "cycles"
CYCLES_FEED = PROJ / "cycles_feed"   # unjoined-drop lineage, kept apart from STARS-era state
SN_INSTANCE = "https://dev336362.service-now.com"
SN_CREDS = Path.home() / ".secrets" / "Service Now.txt"
SN_CATALOG_ITEM = "b02e8afc839a8310d89511b6feaad3c8"  # "Terminated User Access Removal"
SN_GROUP = "Access Management"     # fulfillment assignment group (seeded by sn_seed_users.py)
SN_ASSIGNEE = "brandon.chue"       # fulfiller all tasks are assigned to

LEGIT = {"Active", "Paid Leave", "Unpaid Leave"}
TERM = {"Terminated", "Retired"}
EXPIRY_WARN_DAYS = 30
ESCALATE_AGE = 2
ROSTER_SANITY_RATIO = 0.5  # export under half its previous size = anomaly, closures frozen


# ---------------------------------------------------------------- input loading

def load_rosters(rosters_dir):
    """Return (populations, hr_by_upn).

    populations[app] = [row dict: key/alias/upn/empid/hr/src] for the 10 STARS tabs;
    hr_by_upn is worst-wins across tabs, used for exception-owner lookups."""
    stars = load_workbook_rows(rosters_dir / "FAKE USERS - STARS Report.xlsx")
    populations, hr_by_upn = {}, {}
    for tab in STARS_TABS:
        rows = stars[tab]
        cols = {v: k for k, v in rows[1].items()}
        alias_c = cols[[c for c in cols if c.endswith(("_NetworkAlias", "_USERNAME"))][0]]
        pop = []
        for r in rows[2:]:
            if not any(str(v).strip() for v in r.values()):
                continue
            upn = (r.get(cols["TH_UPN"]) or "").strip().lower()
            if upn == NO_UPN.lower() or " " in upn or "@" not in upn:
                upn = ""
            alias = (r.get(alias_c) or "").strip()
            hr = (r.get(cols["TH_EmployeeStatus"]) or "").strip()
            pop.append({"key": upn or f"alias:{alias}", "alias": alias, "upn": upn,
                        "empid": (r.get(cols["TH_EmployeeID"]) or "").strip(),
                        "hr": hr, "src": {str(k): str(v) for k, v in r.items()}})
            if upn and (hr in TERM or upn not in hr_by_upn):
                hr_by_upn[upn] = hr
        populations[tab] = pop
    return populations, hr_by_upn


def load_exceptions(rosters_dir):
    """exceptions[app][upn] = {owner, expiry, type} (STARS apps only by construction)."""
    book = load_workbook_rows(rosters_dir / "FAKE USERS - Exception List.xlsx")
    out = {}
    for app, rows in book.items():
        out[app] = {r.get(1, "").strip().lower(): {"owner": r.get(6, "").strip().lower(),
                                                   "expiry": r.get(7, "").strip(),
                                                   "type": r.get(4, "").strip()}
                    for r in rows[1:] if r.get(1, "").strip()}
    return out


def okta_state():
    """(login->status, app label->set of assigned logins) — live pulls."""
    users = {}
    for u in paged("/api/v1/users?limit=200"):
        users[u["profile"]["login"].lower()] = (u["status"], u["id"])
    for u in paged("/api/v1/users?limit=200&filter=status%20eq%20%22DEPROVISIONED%22"):
        users[u["profile"]["login"].lower()] = (u["status"], u["id"])
    id_to_login = {uid: login for login, (_, uid) in users.items()}
    assigns = {}
    for a in paged("/api/v1/apps?limit=200"):
        if a["label"].startswith(APP_LABEL_PREFIX):
            assigns[a["label"]] = {id_to_login.get(au["id"], "?")
                                   for au in paged(f"/api/v1/apps/{a['id']}/users?limit=200")}
    return {l: s for l, (s, _) in users.items()}, assigns


# ---------------------------------------------------------------- classification

def classify(populations, exceptions, okta_users, today):
    """Bucket every roster row. Returns (rows_by_app, findings).

    Finding classes: ticket (auto-actioned), exception_expired, owner_terminated,
    unknown (loud). Non-finding buckets: pass, exception_ok. Each row also carries
    its live Okta status for the 3-way callout."""
    rows_by_app, findings = {}, []

    def okta_of(upn):
        return okta_users.get(upn, "NONE") if upn else "n/a"

    def finding(app, row, cls, reason):
        findings.append({"app": app, "key": row["key"], "cls": cls, "reason": reason,
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
                if e["expiry"] < today:
                    bucket, reason = "exception_expired", f"exception lapsed {e['expiry']}"
                    finding(app, row, bucket, reason)
                else:  # owner-terminated is judged from the register in ownership_review()
                    bucket, reason = "exception_ok", f"{e['type']}, owner {e['owner'] or '(none)'}"
            # 3. legitimate / loud unknown
            elif hr in LEGIT:
                bucket, reason = "pass", hr
            elif hr == NO_UPN or not upn:
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
    warn_before = (dt.date.fromisoformat(today) + dt.timedelta(days=EXPIRY_WARN_DAYS)).isoformat()
    for app, entries in exceptions.items():
        for upn, e in entries.items():
            if hr_by_upn.get(upn) in TERM:
                continue  # account holder terminated -> already a ticket; owner moot
            if hr_by_upn.get(e["owner"]) in TERM:
                findings.append({"app": app, "key": upn, "cls": "owner_terminated",
                                 "reason": f"owner {e['owner']} is {hr_by_upn[e['owner']]} — reassign",
                                 "upn": upn, "alias": "", "empid": "", "hr": hr_by_upn.get(upn, ""),
                                 "okta": "", "snapshot": e})
            elif today <= e["expiry"] <= warn_before:
                warnings.append(f"{app}: {upn} exception expires {e['expiry']} (owner {e['owner']})")
    return findings, warnings


# ---------------------------------------------------------------- closure

def closure_pass(prev_state, findings, roster_counts):
    """Carry ages/tickets forward; close what verifiably disappeared."""
    open_now = {(f["app"], f["key"], f["cls"]): f for f in findings}
    closures, anomalies = [], set()
    if prev_state:
        for app, prev_n in prev_state["roster_counts"].items():
            cur_n = roster_counts.get(app, 0)
            if cur_n < prev_n * ROSTER_SANITY_RATIO:
                anomalies.add(app)
        for f in prev_state["findings"]:
            k = (f["app"], f["key"], f["cls"])
            if k in open_now:
                open_now[k]["age"] = f.get("age", 1) + 1
                open_now[k]["ticket"] = f.get("ticket", "")
                open_now[k]["first_cycle"] = f.get("first_cycle", prev_state["cycle"])
            elif f["app"] in anomalies:
                open_now[k] = {**f, "reason": f["reason"] + " [UNVERIFIABLE: export anomaly]"}
            else:
                closures.append({**f, "closed_in": "current"})
    for f in findings:
        f.setdefault("age", 1)
        f.setdefault("ticket", "")
    return list(open_now.values()), closures, anomalies


# ---------------------------------------------------------------- servicenow

def sn_call(method, path, body=None):
    text = SN_CREDS.read_text()
    pw = next(l.split("=", 1)[1] for l in text.splitlines() if l.startswith("password="))
    user = next(l.strip() for l in text.splitlines() if l.strip() and "=" not in l)
    import base64
    req = urllib.request.Request(SN_INSTANCE + path, method=method,
                                 data=json.dumps(body).encode() if body else None)
    req.add_header("Authorization", "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode())
    req.add_header("Accept", "application/json")
    if body:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


_sn_ids = {}


def sn_id(table, query):
    """Resolve-and-cache a sys_id by query; '' when the record doesn't exist."""
    if (table, query) not in _sn_ids:
        r = sn_call("GET", f"/api/now/table/{table}?sysparm_query={urllib.parse.quote(query, safe='=^')}"
                           "&sysparm_fields=sys_id&sysparm_limit=1")["result"]
        _sn_ids[(table, query)] = r[0]["sys_id"] if r else ""
    return _sn_ids[(table, query)]


def sn_create_ticket(f, cycle_id):
    """Order the dedicated catalog item (variables carry the evidence), stamp the RITM,
    set requested_for to the terminated user's own SN record, and adopt the flow's
    fulfillment task — retitled and assigned to the Access Management fulfiller."""
    order = sn_call("POST", f"/api/sn_sc/servicecatalog/items/{SN_CATALOG_ITEM}/order_now", {
        "sysparm_quantity": "1",
        "variables": {"application": f["app"], "account_alias": f["alias"], "upn": f["upn"],
                      "employee_id": f["empid"], "hr_status": f["hr"], "okta_status": f["okta"],
                      "reason": f["reason"], "cycle_id": cycle_id}})["result"]
    requested_for = sn_id("sys_user", f"email={f['upn']}") if f["upn"] else ""  # email keeps full login (user_name truncates at 40)
    if requested_for:
        sn_call("PATCH", f"/api/now/table/sc_request/{order['request_id']}",
                {"requested_for": requested_for})
    ritm = sn_call("GET", f"/api/now/table/sc_req_item?sysparm_query=request={order['request_id']}"
                          "&sysparm_fields=sys_id,number")["result"][0]
    sn_call("PATCH", f"/api/now/table/sc_req_item/{ritm['sys_id']}", {
        "short_description": f"Remove access: {f['upn'] or f['alias']} from {f['app']}",
        "description": (f"Biweekly termination review {cycle_id}\n"
                        f"App: {f['app']}\nAccount: {f['alias'] or f['upn']}\nUPN: {f['upn']}\n"
                        f"EmployeeID: {f['empid']}\nHR status: {f['hr']}\nOkta status: {f['okta']}\n"
                        f"Reason: {f['reason']}\nRemediation is manual; closure verified next cycle."),
    })
    # adopt the flow-generated fulfillment task (closing it drives the RITM/REQ
    # lifecycle); poll briefly because the flow creates it async
    for _ in range(10):
        tasks = sn_call("GET", f"/api/now/table/sc_task?sysparm_query=request_item={ritm['sys_id']}"
                               "&sysparm_fields=sys_id,number")["result"]
        if tasks:
            break
        time.sleep(1)
    fill = {"assignment_group": sn_id("sys_user_group", f"name={SN_GROUP}"),
            "assigned_to": sn_id("sys_user", f"user_name={SN_ASSIGNEE}"),
            "short_description": f"Remove access: {f['upn'] or f['alias']} from {f['app']}",
            "description": f"Manually remove this access in {f['app']}, then close this task. "
                           f"Closure is verified by the next review cycle."}
    if tasks:
        task_num = tasks[0]["number"]
        sn_call("PATCH", f"/api/now/table/sc_task/{tasks[0]['sys_id']}", fill)
    else:  # flow produced nothing in time; create the task so work is never lost
        task_num = sn_call("POST", "/api/now/table/sc_task",
                           {**fill, "request_item": ritm["sys_id"]})["result"]["number"]
    return f"{order['request_number']}/{ritm['number']}/{task_num}"


def closure_writeback(findings, closures, roster_counts, cycle_id):
    """Two-phase closure evidence, written onto the tickets themselves.

    Verified closures get a BEFORE/AFTER work note on their RITM (the ticket becomes
    self-contained audit evidence). Ticket findings whose task was closed but whose
    account is STILL in the roster are false claims: note both records, reopen the
    task, and tag the finding so the report/digest carry the reopen."""
    def parts(t):
        p = t.split("/")
        return (p[1], p[2]) if len(p) == 3 else ("", "")

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
            print(f"  VERIFIED  {ritm_num}: {c['upn'] or c['alias']}", file=sys.stderr)

    for f in findings:
        if f["cls"] != "ticket" or f.get("age", 1) < 2:
            continue
        ritm_num, task_num = parts(f.get("ticket", ""))
        if not task_num:
            continue
        t = sn_call("GET", f"/api/now/table/sc_task?sysparm_query=number={task_num}"
                           "&sysparm_fields=sys_id,state")["result"]
        if t and t[0]["state"] in ("3", "4", "7"):  # closed, yet the account is still there
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
            print(f"  REOPENED  {task_num}: {f['upn'] or f['alias']}", file=sys.stderr)


def sweep_flow_stage_tasks():
    """The OOB item flow spawns a stage-2 'deploy' task (Field Services) whenever a
    removal task closes. Irrelevant to access removal: close each as skipped with a
    note — which also lets the flow finish and close its RITM/REQ properly."""
    strays = sn_call("GET", "/api/now/table/sc_task?sysparm_query="
                     f"request_item.cat_item={SN_CATALOG_ITEM}^active=true"
                     "^short_descriptionNOT%20LIKERemove%20access"
                     "&sysparm_fields=sys_id,number&sysparm_limit=200")["result"]
    for t in strays:
        sn_call("PATCH", f"/api/now/table/sc_task/{t['sys_id']}", {
            "state": "7", "work_notes": "Auto-skipped by reconciliation: flow stage not "
                                        "applicable to access-removal fulfillment."})
        print(f"  swept {t['number']}", file=sys.stderr)
    return len(strays)


# ---------------------------------------------------------------- outputs

BUCKET_ORDER = {"ticket": 0, "owner_terminated": 1, "exception_expired": 2, "unknown": 3,
                "exception_ok": 4, "pass": 5}
HEADER = ["Account (alias)", "UPN", "EmployeeID", "HR Status", "Okta", "Bucket", "Reason", "Ticket", "Age"]


def write_report(out_dir, rows_by_app, findings, closures, anomalies, cycle_id, today):
    tickets = {(f["app"], f["key"]): (f.get("ticket", ""), f.get("age", 1))
               for f in findings}
    # Flagged ROWS and ticket CHAINS are different units: duplicate seats for one person on
    # one app collapse to a single finding and a single ticket. Reporting only rows under a
    # "tickets" heading makes the workbook disagree with ServiceNow's actual chain count.
    chains_by_app = {}
    for f in findings:
        if f["cls"] == "ticket":
            chains_by_app[f["app"]] = chains_by_app.get(f["app"], 0) + 1
    summary = [["Biweekly Termination Review", cycle_id], ["Run date", today], [],
               ["App", "rows", "pass", "exception_ok", "ticket rows", "ticket chains",
                "expired_exc", "unknown", "no-Okta orphans", "closures"]]
    sheets = []
    total = dict.fromkeys(["rows", "tickets", "ticket_chains", "unknown", "orphans", "closures"], 0)
    owner_flags = [f for f in findings if f["cls"] == "owner_terminated"]
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
                         r["okta"], r["bucket"], r["reason"], t, age if t else ""])
        closed_here = sum(1 for c in closures if c["app"] == app)
        summary.append([app + (" ⚠ EXPORT ANOMALY" if app in anomalies else ""),
                        len(rows), counts.get("pass", 0), counts.get("exception_ok", 0),
                        counts.get("ticket", 0), chains_by_app.get(app, 0),
                        counts.get("exception_expired", 0),
                        counts.get("unknown", 0), orphans, closed_here])
        total["rows"] += len(rows); total["tickets"] += counts.get("ticket", 0)
        total["ticket_chains"] += chains_by_app.get(app, 0)
        total["unknown"] += counts.get("unknown", 0); total["orphans"] += orphans
        total["closures"] += closed_here
        sheets.append((app[:31], body))
    summary += [[], ["TOTAL rows", total["rows"], "ticket rows", total["tickets"],
                     "ticket chains (= ServiceNow REQs)", total["ticket_chains"], "unknown",
                     total["unknown"], "orphans", total["orphans"], "closures", total["closures"]],
                [], ["Ownership reassignment flags", len(owner_flags)],
                *[["", f"{f['app']}: {f['upn']} — {f['reason']}"] for f in owner_flags]]
    write_xlsx(out_dir / "report.xlsx", [("Summary", summary)] + sheets)
    return total


def write_digests(out_dir, findings, closures, warnings, anomalies, total, cycle_id):
    new = [f for f in findings if f.get("age", 1) == 1]
    aged = [f for f in findings if f.get("age", 1) >= ESCALATE_AGE]
    adm = [f"# Admin digest — cycle {cycle_id}", "",
           f"Population {total['rows']} rows | ticket chains open {total['ticket_chains']} "
           f"(from {total['tickets']} flagged rows) "
           f"({len([f for f in new if f['cls'] == 'ticket'])} new) | verified closures {total['closures']} "
           f"| unknowns {total['unknown']} | no-Okta orphans {total['orphans']}", ""]
    adm += [f"- NEW {f['app']}: {f['upn'] or f['alias']} — {f['reason']} [{f.get('ticket', 'DRY')}]"
            for f in new if f["cls"] == "ticket"]
    adm += [f"- CLOSED {c['app']}: {c['upn'] or c['alias']} — verified gone [{c.get('ticket', '')}]"
            for c in closures]
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
        (out_dir / name).write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- main

def main():
    args = sys.argv[1:]
    create_tickets = "--create-tickets" in args
    rosters = Path(args[args.index("--rosters") + 1]) if "--rosters" in args else PROJ / "App User Lists"
    feeds = Path(args[args.index("--feeds") + 1]) if "--feeds" in args else None
    today = args[args.index("--today") + 1] if "--today" in args else dt.date.today().isoformat()

    if feeds:
        import feed_ingest
        stamp = args[args.index("--feed-date") + 1] if "--feed-date" in args else None
        populations, hr_by_upn, exceptions, meta = feed_ingest.load(feeds, stamp)
        if "--today" not in args:
            today = meta["cycle_date"]      # the cycle IS the drop date, not the wall clock
        # Feed cycles keep their own lineage: the STARS-era state under cycles/ keys findings
        # off a different join, so closure_pass would read every prior finding as remediated.
        lineage = CYCLES_FEED
    else:
        populations, hr_by_upn = load_rosters(rosters)
        exceptions = load_exceptions(rosters)
        meta = None
        lineage = CYCLES

    cycle_id = time.strftime("cycle_%Y%m%d_%H%M%S")
    out_dir = lineage / cycle_id
    out_dir.mkdir(parents=True)
    if meta:
        print(f"{cycle_id}: feed drop {meta['stamp']} — {meta['rows']} app rows across "
              f"{meta['apps']} apps, {meta['hr_rows']} HR rows | joined {meta['joined']}, "
              f"no HR match {meta['no_hr_match']}, unjoinable {meta['unjoinable']}",
              file=sys.stderr)
    print(f"{cycle_id}: {sum(len(p) for p in populations.values())} roster rows, "
          f"{len(populations)} apps; pulling Okta...", file=sys.stderr)
    okta_users, _okta_assigns = okta_state()

    rows_by_app, findings = classify(populations, exceptions, okta_users, today)
    own_findings, warnings = ownership_review(exceptions, hr_by_upn, today)
    findings += own_findings

    # The baseline is the last cycle OF RECORD, i.e. one that actually wrote tickets. A DRY
    # run is a rehearsal: letting it become the baseline ages every finding on the next real
    # cycle (age 2 => "escalated" and "0 new" on a first cycle) and would eventually let a
    # rehearsal's roster counts drive the closure sanity check.
    prev_state = None
    for p in sorted(lineage.glob("cycle_*/state.json"), reverse=True):
        candidate = json.loads(p.read_text())
        if candidate.get("tickets_live"):
            prev_state = candidate
            break
    roster_counts = {app: len(rows) for app, rows in populations.items()}
    findings, closures, anomalies = closure_pass(prev_state, findings, roster_counts)
    for f in findings:
        f.setdefault("first_cycle", cycle_id)  # birth cycle, for BEFORE/AFTER evidence

    to_ticket = [f for f in findings
                 if f["cls"] == "ticket" and f.get("ticket") in ("", "DRY", "SN-ERROR")]
    for i, f in enumerate(to_ticket, 1):
        if create_tickets:
            try:
                f["ticket"] = sn_create_ticket(f, cycle_id)
            except Exception as e:  # a SN outage must not kill the cycle evidence
                f["ticket"] = "SN-ERROR"
                print(f"  SN error for {f['upn']}: {e}", file=sys.stderr)
            print(f"  tickets {i}/{len(to_ticket)}: {f['ticket']} {f['upn'] or f['alias']}",
                  file=sys.stderr)
        else:
            f["ticket"] = "DRY"

    if create_tickets:  # evidence write-back + stage sweep are SN writes; same gate
        closure_writeback(findings, closures, roster_counts, cycle_id)
        sweep_flow_stage_tasks()

    total = write_report(out_dir, rows_by_app, findings, closures, anomalies, cycle_id, today)
    write_digests(out_dir, findings, closures, warnings, anomalies, total, cycle_id)
    (out_dir / "state.json").write_text(json.dumps({
        "cycle": cycle_id, "today": today, "tickets_live": create_tickets,
        "roster_counts": roster_counts, "findings": findings, "closures": closures,
        "anomalies": sorted(anomalies), "summary": total}, indent=1))

    print(f"{cycle_id} DONE: {total['rows']} rows | {total['tickets']} tickets"
          f"{' (LIVE)' if create_tickets else ' (DRY)'} | {len(closures)} closures | "
          f"{total['unknown']} unknowns | {total['orphans']} orphans | "
          f"{len(own_findings)} owner flags | outputs in {out_dir}", file=sys.stderr)


if __name__ == "__main__":
    main()
