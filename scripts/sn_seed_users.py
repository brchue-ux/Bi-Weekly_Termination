#!/usr/bin/env python3
"""Seed ServiceNow (PDI) with the certified population as sys_user records.

- Every unique identity across the 10 STARS tabs (2,035) becomes a sys_user.
- Per app, ~10% of its users are managers — jittered per-app (+/- up to 2, keyed by a
  stable hash) so the ratio is "not quite equal" across apps, as specified.
- Every non-manager gets a manager from their first-seen app's manager pool
  (round-robin, so every manager ends up with direct reports).
- Creates assignment group "Access Management"; creates fulfiller "Brandon Chiu"
  (user_name brandon.chiu) with the itil role, member of that group.

Idempotent: existing user_names / group / role grants / memberships are skipped.
Run with no args; prints a summary. The reconciliation pipeline then resolves
requested_for / assigned_to / assignment_group against these records.
"""
import hashlib
import sys

from biweekly_recon import load_rosters, sn_call, PROJ

GROUP_NAME = "Access Management"
FULFILLER = {"user_name": "brandon.chue", "first_name": "Brandon", "last_name": "Chue",
             "email": "brandon.chue@bitermtest.com"}


def jitter(app):  # stable per-app wobble in [-2, 2]
    return int(hashlib.sha256(app.encode()).hexdigest(), 16) % 5 - 2


def build_org():
    """Return (users[upn] = {first,last,manager_upn,is_mgr}, per-app manager counts)."""
    populations, _ = load_rosters(PROJ / "App User Lists")
    users, app_of = {}, {}
    for app, pop in populations.items():
        for row in pop:
            if row["upn"] and row["upn"] not in users:
                parts = row["upn"].split("@")[0].split(".")
                users[row["upn"]] = {"first": parts[0].title(),
                                     "last": parts[-1].title() if len(parts) > 1 else "User",
                                     "manager": "", "is_mgr": False}
                app_of[row["upn"]] = app
    mgr_counts = {}
    managers_by_app = {}
    for app, pop in populations.items():
        members = sorted({r["upn"] for r in pop if r["upn"]})
        n = max(1, round(len(members) * 0.10) + jitter(app))
        # stable pick: hash-ranked so reruns choose the same managers
        ranked = sorted(members, key=lambda u: hashlib.sha256((app + u).encode()).hexdigest())
        mgrs = ranked[:n]
        managers_by_app[app] = mgrs
        mgr_counts[app] = n
        for u in mgrs:
            users[u]["is_mgr"] = True
    for upn, u in users.items():
        if not u["is_mgr"]:
            pool = managers_by_app[app_of[upn]]
            u["manager"] = pool[int(hashlib.sha256(upn.encode()).hexdigest(), 16) % len(pool)]
    return users, mgr_counts


def ensure_user(rec, existing):
    if rec["user_name"] in existing:
        return existing[rec["user_name"]]
    made = sn_call("POST", "/api/now/table/sys_user", rec)["result"]
    existing[rec["user_name"]] = made["sys_id"]
    return made["sys_id"]


def main():
    users, mgr_counts = build_org()
    print(f"plan: {len(users)} users; managers/app: {mgr_counts}", file=sys.stderr)

    existing, offset = {}, 0
    while True:  # instance may cap page size below the requested limit — stop only on empty
        page = sn_call("GET", f"/api/now/table/sys_user?sysparm_fields=user_name,email,sys_id"
                              f"&sysparm_limit=1000&sysparm_offset={offset}")["result"]
        if not page:
            break
        for r in page:
            # user_name truncates at 40 chars; email keeps the full login, so index both
            for k in (r.get("user_name"), r.get("email")):
                if k:
                    existing[k] = r["sys_id"]
        offset += len(page)
    print(f"existing sys_user records: {len(existing)}", file=sys.stderr)

    created = 0
    for i, (upn, u) in enumerate(sorted(users.items()), 1):
        if upn not in existing:
            ensure_user({"user_name": upn, "first_name": u["first"], "last_name": u["last"],
                         "email": upn, "title": "Manager" if u["is_mgr"] else "",
                         "active": "true"}, existing)
            created += 1
        if i % 250 == 0:
            print(f"  users {i}/{len(users)}", file=sys.stderr)

    linked = 0
    for upn, u in users.items():
        if u["manager"]:
            sn_call("PATCH", f"/api/now/table/sys_user/{existing[upn]}",
                    {"manager": existing[u["manager"]]})
            linked += 1
            if linked % 250 == 0:
                print(f"  manager links {linked}", file=sys.stderr)

    groups = sn_call("GET", "/api/now/table/sys_user_group?sysparm_query=name="
                            f"{GROUP_NAME.replace(' ', '%20')}&sysparm_fields=sys_id")["result"]
    group_id = groups[0]["sys_id"] if groups else \
        sn_call("POST", "/api/now/table/sys_user_group",
                {"name": GROUP_NAME, "description": "Biweekly termination review fulfillment"})["result"]["sys_id"]

    brandon = ensure_user({**FULFILLER, "active": "true"}, existing)
    itil = sn_call("GET", "/api/now/table/sys_user_role?sysparm_query=name=itil"
                          "&sysparm_fields=sys_id")["result"][0]["sys_id"]
    if not sn_call("GET", f"/api/now/table/sys_user_has_role?sysparm_query=user={brandon}"
                          f"^role={itil}&sysparm_fields=sys_id")["result"]:
        sn_call("POST", "/api/now/table/sys_user_has_role", {"user": brandon, "role": itil})
    if not sn_call("GET", f"/api/now/table/sys_user_grmember?sysparm_query=user={brandon}"
                          f"^group={group_id}&sysparm_fields=sys_id")["result"]:
        sn_call("POST", "/api/now/table/sys_user_grmember", {"user": brandon, "group": group_id})

    print(f"DONE: {created} users created, {linked} manager links, "
          f"group={group_id}, brandon.chiu={brandon}", file=sys.stderr)


if __name__ == "__main__":
    main()
