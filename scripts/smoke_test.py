"""
End-to-end smoke test (2026-07-23): one consolidated verdict that the whole
termination-review system still hangs together after the re-identity, SFDC
removal, SN re-sync, campaign relaunch, and AM-team build. Read-only except for
one fresh DRY recon cycle (no tickets, no writes) to exercise the live pipeline.

Subsystems, each a live check:
  A. OAUTH        — service app mints a token via private_key_jwt; the 3 read
                    scopes return 200; a write is denied (least-privilege intact).
  B. OKTA STATE   — SFDC app gone; ~2027 seeded users; 0 original name-pairings;
                    AM team present + ACTIVE.
  C. RECON        — a fresh DRY cycle runs clean and its findings carry the NEW
                    identities (a sampled ticket UPN resolves live in Okta).
  D. CAMPAIGNS    — 3 ACTIVE BiTerm campaigns; flagged-pop reviewers are the AM
                    fulfillers (Zyler/Phil); results are pullable.
  E. SERVICENOW   — PDI awake; new identities present + old absent; AM team roles
                    (itil/pa_viewer) and ticket load correct, Bogan (mgr) at 0.

Ends in a single line: VERDICT: PASS | FAIL.
"""

import glob
import json
import subprocess
import sys
import urllib.parse
from collections import Counter
from pathlib import Path

failures = []


def check(name, ok, detail):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def section(title):
    print(f"\n{title}")


def main():
    # ---- A. OAUTH (reuse the dedicated gate; it already proves token+reads+deny)
    section("A. OAUTH service app")
    r = subprocess.run([sys.executable, "verify_oauth.py"], capture_output=True, text=True)
    check("least-privilege auth", "VERDICT: PASS" in r.stdout,
          "verify_oauth.py " + ("PASS" if "VERDICT: PASS" in r.stdout else "FAIL:\n" + r.stdout[-400:]))

    from okta_client import paged, api
    from reidentity import BACKUP, STARS, EXCEPT, split_local, valid_email
    from xlsx_min import load_workbook_rows
    from verify_reidentity_tenant import original_identities, SFDC_APP_ID

    # ---- B. OKTA STATE
    section("B. Okta state")
    gone = api("GET", f"/api/v1/apps/{SFDC_APP_ID}", ok404=True)[0] is None
    check("SFDC app removed", gone, "404" if gone else "STILL PRESENT")

    manifest = json.load(open("seed_manifest.json"))
    seeded_ids = {u["id"] for u in manifest["users"].values() if isinstance(u, dict) and u.get("id")}
    live = list(paged("/api/v1/users?limit=200"))
    live += list(paged("/api/v1/users?limit=200&filter=status%20eq%20%22DEPROVISIONED%22"))
    seeded_live = [u for u in live if u["id"] in seeded_ids]
    check("seeded users present", 1900 <= len(seeded_live) <= 2100, f"{len(seeded_live)} live seeded")

    orig_pairs, _, _ = original_identities(load_workbook_rows(BACKUP / STARS),
                                           load_workbook_rows(BACKUP / EXCEPT))
    pair_leaks = [u["profile"]["login"] for u in seeded_live
                  if ((u["profile"].get("firstName") or "").lower(),
                      (u["profile"].get("lastName") or "").lower()) in orig_pairs]
    check("no re-identifiable person", not pair_leaks, f"{len(pair_leaks)} original pairings live")

    am = json.load(open("am_team_okta.json"))["users"]
    am_ok = all(api("GET", f"/api/v1/users/{uid}")[0].get("status") == "ACTIVE" for uid in am.values())
    check("AM team ACTIVE", am_ok and len(am) == 3, f"{len(am)} users")

    # ---- C. RECON (fresh dry cycle)
    section("C. recon pipeline (fresh DRY cycle)")
    r = subprocess.run([sys.executable, "biweekly_recon.py", "--rosters", "App User Lists"],
                       capture_output=True, text=True)
    tail = r.stdout.strip().splitlines()[-1] if r.stdout.strip() else r.stderr[-300:]
    ran = "DONE:" in (r.stdout + r.stderr)
    check("cycle runs clean", ran, tail)
    if ran:
        cyc = sorted(glob.glob("cycles/cycle_*"))[-1]
        findings = json.load(open(f"{cyc}/state.json"))["findings"]
        tickets = [f for f in findings if f["cls"] == "ticket" and f.get("upn")]
        check("tickets produced", len(tickets) >= 20, f"{len(tickets)} confirmed-term tickets")
        # sampled ticket identity must resolve live (new identity, not stale)
        sample = tickets[0]["upn"]
        found = api("GET", f"/api/v1/users?q={urllib.parse.quote(sample)}&limit=2")[0]
        resolves = any(u["profile"]["login"] == sample for u in found)
        check("finding identity is live", resolves, f"{sample} {'resolves' if resolves else 'MISSING'}")

    # ---- D. CAMPAIGNS
    section("D. campaigns")
    camps = []
    url = "/governance/api/v1/campaigns?limit=50"
    while url:
        body, _ = api("GET", url)
        camps += body.get("data", [])
        nxt = (body.get("_links", {}).get("next") or {}).get("href")
        url = nxt.replace("https://demo-beige-haddock-4684.okta.com", "") if nxt else None
    active = [c for c in camps if c.get("name", "").startswith("BiTerm") and c.get("status") == "ACTIVE"]
    check("3 active campaigns", len(active) == 3, f"{len(active)} ACTIVE: "
          + ", ".join(c["name"].replace("BiTerm — ", "") for c in active))
    flagged = next((c for c in active if "Flagged" in c["name"]), None)
    if flagged:
        q = urllib.parse.quote(f'campaignId eq "{flagged["id"]}"')
        body, _ = api("GET", f"/governance/api/v1/reviews?filter={q}&limit=200")
        reviewers = {it["reviewerProfile"]["email"] for it in body.get("data", [])}
        am_reviewers = {e for e in reviewers if e.startswith(("zyler", "phil"))}
        check("AM team reviews flagged pop", len(am_reviewers) == 2, f"reviewers={sorted(reviewers)}")

    # ---- E. SERVICENOW
    section("E. ServiceNow")
    from biweekly_recon import sn_call, sn_id
    try:
        awake = "result" in sn_call("GET", "/api/now/table/sys_user?sysparm_limit=1&sysparm_fields=sys_id")
    except Exception as e:
        awake = False
        print("    (SN call failed — PDI may be hibernating:", str(e)[:100], ")")
    check("PDI awake", awake, "reachable" if awake else "unreachable")
    if awake:
        def present(email):
            q = urllib.parse.quote(f"email={email}", safe="=^")
            return bool(sn_call("GET", f"/api/now/table/sys_user?sysparm_query={q}"
                                       "&sysparm_fields=sys_id")["result"])
        check("new identities in SN", present("eamon.chatterjee@bitermtest.com"), "sample new present")
        check("old identities purged", not present("melissa.chue@bitermtest.com"), "sample old absent")
        group = sn_id("sys_user_group", "name=Access Management")
        counts = {}
        for name in ("bogan.wone", "zyler.bawado", "phil.manawan"):
            q = urllib.parse.quote(f"email={name}@bitermtest.com", safe="=^")
            uid = sn_call("GET", f"/api/now/table/sys_user?sysparm_query={q}&sysparm_fields=sys_id")["result"][0]["sys_id"]
            rq = urllib.parse.quote(f"user={uid}", safe="=^")
            roles = {x.get("role.name") for x in sn_call("GET",
                     f"/api/now/table/sys_user_has_role?sysparm_query={rq}&sysparm_fields=role.name&sysparm_limit=100")["result"]}
            tq = urllib.parse.quote(f"assignment_group={group}^assigned_to={uid}^active=true", safe="=^")
            tix = int(sn_call("GET", f"/api/now/stats/sc_task?sysparm_query={tq}&sysparm_count=true")["result"]["stats"]["count"])
            counts[name] = (roles.issuperset({"itil", "pa_viewer"}), tix)
        check("fulfillers can work + see dashboard",
              all(counts[n][0] for n in counts), {n: counts[n][0] for n in counts})
        check("Bogan (manager) holds no tickets", counts["bogan.wone"][1] == 0,
              f"bogan={counts['bogan.wone'][1]}, zyler={counts['zyler.bawado'][1]}, phil={counts['phil.manawan'][1]}")

    print(f"\nVERDICT: {'PASS' if not failures else 'FAIL (' + ', '.join(failures) + ')'}")
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
