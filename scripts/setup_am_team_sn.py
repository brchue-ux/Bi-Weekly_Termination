"""
Access Management team in ServiceNow (2026-07-23, user-authorized): create the
three AM staff, wire the reporting line (incl. the demo fulfiller Brandon Chue),
put everyone in the Access Management group, and give the fulfillers tickets.

  Bogan Wone   (manager) -> no tickets
  Zyler Bawado -> reports to Bogan, gets tickets
  Phil Manawan -> reports to Bogan, gets tickets
  Brandon Chue -> reports to Bogan, gets tickets   (demo fulfiller brandon.chue@bitermtest.com,
                                                     NOT the real bchue@wm.com)

Tickets, BOTH ways (user choice):
  - REASSIGN a slice of the current cycle's open removal SCTASKs across the three.
  - CREATE new demo removal tickets (order the catalog item) split across the three.
Bogan, the manager, is assigned none.

Idempotent by user_name/email + group membership. Needs the integration user's
write access (admin re-granted 2026-07-23).
"""

import sys
import time
import urllib.parse
from pathlib import Path

from biweekly_recon import (sn_call, sn_id, SN_CATALOG_ITEM, SN_GROUP)

DOMAIN = "bitermtest.com"
CREDS_FILE = Path.home() / ".secrets" / "am_team_demo_logins.txt"


def load_creds():
    creds = {}
    for line in CREDS_FILE.read_text().splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
    return creds


def grant_role(uid, role_name):
    role_id = sn_id("sys_user_role", f"name={role_name}")
    q = urllib.parse.quote(f"user={uid}^role={role_id}", safe="=^")
    if not sn_call("GET", f"/api/now/table/sys_user_has_role?sysparm_query={q}"
                          "&sysparm_fields=sys_id")["result"]:
        sn_call("POST", "/api/now/table/sys_user_has_role", {"user": uid, "role": role_id})
        return True
    return False
BOGAN = {"user_name": "bogan.wone", "first_name": "Bogan", "last_name": "Wone"}
ZYLER = {"user_name": "zyler.bawado", "first_name": "Zyler", "last_name": "Bawado"}
PHIL = {"user_name": "phil.manawan", "first_name": "Phil", "last_name": "Manawan"}
BRANDON_EMAIL = "brandon.chue@bitermtest.com"
NEW_TEAM = [BOGAN, ZYLER, PHIL]
TICKETS_PER_FULFILLER = 3


def ensure_user(rec):
    email = f"{rec['user_name']}@{DOMAIN}"
    q = urllib.parse.quote(f"email={email}", safe="=^")
    found = sn_call("GET", f"/api/now/table/sys_user?sysparm_query={q}&sysparm_fields=sys_id")["result"]
    if found:
        print(f"user exists: {email} ({found[0]['sys_id']})")
        return found[0]["sys_id"]
    made = sn_call("POST", "/api/now/table/sys_user",
                   {**rec, "email": email, "active": "true"})["result"]
    print(f"created: {email} ({made['sys_id']})")
    return made["sys_id"]


def user_by_email(email):
    q = urllib.parse.quote(f"email={email}", safe="=^")
    r = sn_call("GET", f"/api/now/table/sys_user?sysparm_query={q}&sysparm_fields=sys_id")["result"]
    return r[0]["sys_id"] if r else ""


def main():
    creds = load_creds()
    ids = {rec["user_name"]: ensure_user(rec) for rec in NEW_TEAM}
    brandon = user_by_email(BRANDON_EMAIL)
    if not brandon:
        sys.exit(f"demo fulfiller {BRANDON_EMAIL} not found — run sn_seed_users.py first")
    ids["brandon.chue"] = brandon
    bogan = ids["bogan.wone"]

    # login passwords for the 3 new accounts (Brandon already has creds) + ensure login enabled
    for rec in NEW_TEAM:
        pw = creds[f"{rec['user_name']}@{DOMAIN}"]
        sn_call("PATCH", f"/api/now/table/sys_user/{ids[rec['user_name']]}",
                {"user_password": pw, "web_service_access_only": "false",
                 "locked_out": "false", "password_needs_reset": "false"})
        print(f"password set + login enabled: {rec['user_name']}")

    # roles: itil = work/close tickets + view the queue (Bogan as manager oversees);
    # pa_viewer = render the "Access Management — Termination Review" dashboard's PA widgets
    # (the dashboard is already shared read to the Access Management group they're now in).
    for name in ("bogan.wone", "zyler.bawado", "phil.manawan"):
        for role in ("itil", "pa_viewer"):
            added = grant_role(ids[name], role)
            print(f"  {role} {'granted' if added else 'already present'}: {name}")
    # Brandon already holds itil (verified); the fulfillers can now work + close SCTASKs

    # reporting line -> Bogan
    for name in ("zyler.bawado", "phil.manawan", "brandon.chue"):
        sn_call("PATCH", f"/api/now/table/sys_user/{ids[name]}", {"manager": bogan})
        print(f"reports-to: {name} -> bogan.wone")

    # Access Management group membership
    group_id = sn_id("sys_user_group", f"name={SN_GROUP}")
    for name, uid in ids.items():
        q = urllib.parse.quote(f"user={uid}^group={group_id}", safe="=^")
        if not sn_call("GET", f"/api/now/table/sys_user_grmember?sysparm_query={q}"
                              "&sysparm_fields=sys_id")["result"]:
            sn_call("POST", "/api/now/table/sys_user_grmember", {"user": uid, "group": group_id})
            print(f"group +: {name}")
        else:
            print(f"group already: {name}")

    fulfillers = {"zyler.bawado": ids["zyler.bawado"],
                  "phil.manawan": ids["phil.manawan"],
                  "brandon.chue": ids["brandon.chue"]}

    # --- REASSIGN existing open SCTASKs across the fulfillers
    q = urllib.parse.quote(f"assignment_group={group_id}^active=true", safe="=^")
    tasks = sn_call("GET", f"/api/now/table/sc_task?sysparm_query={q}"
                           "&sysparm_fields=sys_id,number&sysparm_limit=30")["result"]
    print(f"open SCTASKs in group: {len(tasks)}")
    names = list(fulfillers)
    reassigned = 0
    for i, t in enumerate(tasks[:len(names) * 2]):  # a couple each
        who = names[i % len(names)]
        sn_call("PATCH", f"/api/now/table/sc_task/{t['sys_id']}",
                {"assigned_to": fulfillers[who]})
        print(f"  reassigned {t['number']} -> {who}")
        reassigned += 1

    # --- CREATE new demo removal tickets, split across the fulfillers
    created = 0
    demo_terms = [
        {"application": "NA Apollo", "account_alias": "AMDEMO1", "upn": "amdemo.one@bitermtest.com"},
        {"application": "NA Stellar", "account_alias": "AMDEMO2", "upn": "amdemo.two@bitermtest.com"},
        {"application": "NA Orion", "account_alias": "AMDEMO3", "upn": "amdemo.three@bitermtest.com"},
    ]
    for i in range(TICKETS_PER_FULFILLER * len(names)):
        who = names[i % len(names)]
        base = demo_terms[i % len(demo_terms)]
        order = sn_call("POST", f"/api/sn_sc/servicecatalog/items/{SN_CATALOG_ITEM}/order_now", {
            "sysparm_quantity": "1",
            "variables": {**base, "employee_id": f"AMDEMO{i:03d}", "hr_status": "Terminated",
                          "okta_status": "ACTIVE", "reason": "AM team demo ticket",
                          "cycle_id": "am_team_demo"}})["result"]
        # the fulfillment SCTASK is spawned by an async flow — poll briefly for it
        req_item = order.get("request_item_id") or order.get("sysparm_id")
        tasks = []
        for _ in range(6):
            if req_item:
                tasks = sn_call("GET", "/api/now/table/sc_task?sysparm_query="
                                + urllib.parse.quote(f"request_item={req_item}", safe="=^")
                                + "&sysparm_fields=sys_id,number")["result"]
            if tasks:
                break
            time.sleep(2)
        for t in tasks:
            sn_call("PATCH", f"/api/now/table/sc_task/{t['sys_id']}",
                    {"assigned_to": fulfillers[who], "assignment_group": group_id})
        created += 1
        print(f"  created demo ticket #{i+1} -> {who} ({len(tasks)} task(s))")

    print(f"\nAM team (SN) done: 3 users, reporting line to Bogan, group set, "
          f"{reassigned} reassigned + {created} new tickets across "
          f"{', '.join(fulfillers)}; Bogan has none.")


if __name__ == "__main__":
    main()
