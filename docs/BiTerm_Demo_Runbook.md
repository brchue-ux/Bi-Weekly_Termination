# BiTerm — Biweekly Termination Review: Demo Runbook

A step-by-step script for running the termination-review process live, as the people who
actually operate it. Everything here runs against the demo tenant + dev ServiceNow PDI; no
real data. Passwords for the three demo staff are in `~/.secrets/am_team_demo_logins.txt`
(Brandon's are in `~/.secrets/brandon chue snow creds.txt`).

## What this demo proves

The control is a **detective control**: every two weeks it independently re-derives who has app
access, checks each person against HR employment status, and evidences anyone who shouldn't be
there. It never removes access itself — humans do that — and it confirms removal on the *next*
cycle. That closure loop is what makes it auditable, and the demo walks the whole loop.

## The cast (who logs in, and as what)

| Person | Role | Logs into | Does |
|---|---|---|---|
| **Bogan Wone** | Access Management manager | ServiceNow + Okta | Oversees the queue + dashboard; reports roll up to him; approves; assigns no work to himself |
| **Zyler Bawado** | Fulfiller / reviewer | ServiceNow + Okta | Works removal tasks; certifies flagged users |
| **Phil Manawan** | Fulfiller / reviewer | ServiceNow + Okta | Works removal tasks; certifies flagged users |
| **Brandon Chue** | Fulfiller (demo) | ServiceNow | Works removal tasks |

Systems: ServiceNow `https://dev336362.service-now.com` · Okta `https://demo-beige-haddock-4684.okta.com`

## Pre-flight (2 minutes)

1. **Wake the PDI if it's been idle.** If `dev336362` shows a "hibernating" page, sign in at
   `developer.servicenow.com` → Wake (3–5 min). It preserves everything; only the 10-day reclaim
   wipes data, so sign in weekly between demos.
2. **Health check.** `python3 smoke_test.py` → expect `VERDICT: PASS`. This confirms all five
   subsystems (OAuth, Okta, recon, campaigns, ServiceNow) are live and consistent before you
   present. If anything is red, fix it before the audience arrives.

## Act 1 — The detective control runs (recon cycle)

Run the biweekly reconciliation. Dry first (nothing written), then explain what it found:

```
python3 biweekly_recon.py --rosters "App User Lists"
```

Point out the one-line summary: **~4,404 account rows → 30 confirmed terminations with access →
475 loud unknowns → 431 orphans.** The story: the 30 clear terminations are mechanical; the real
cost is the 475 "I can't tell" rows (a person's name sitting in the status column, not-found-in-HR
service accounts) that the classifier surfaces *loudly* instead of passing silently. Open the
cycle's `report.xlsx` (tab per app, findings sorted to the top).

To actually create the ServiceNow tickets for the confirmed terminations, add `--create-tickets`
(this is the only step that writes to ServiceNow):

```
python3 biweekly_recon.py --rosters "App User Lists" --create-tickets
```

## Act 2 — Fulfiller works a ticket (ServiceNow)

1. Log in as **zyler.bawado**. Open **Service Desk → My Work** (or filter `sc_task` by
   Assigned to = me). He has ~5 open removal tasks, e.g. *"Remove access:
   bailey.smith@bitermtest.com from NA Orion."*
2. Open a task. Show the variables carrying the evidence: application, account alias, UPN,
   employee id, **HR status = Terminated**, Okta status, reason, cycle id. This is the fulfiller's
   proof of *why* the removal is authorized.
3. In the real world he'd now remove the account in the target app. For the demo, **close the
   task** (Close complete). Repeat as **phil.manawan** on one of his tasks to show two fulfillers
   working in parallel.
4. Note the separation: tickets are assigned to Zyler/Phil/Brandon; **Bogan has none** — a manager
   oversees, he doesn't fulfill.

## Act 3 — Manager oversight (ServiceNow + Okta)

1. Log in as **bogan.wone**. The three fulfillers report to him (My Team). He can see the whole
   Access Management queue.
2. Open the dashboard: **`/$pa_dashboard.do?sysparm_dashboard=908a5ab0839e8310d89511b6feaad3f6`**
   ("Access Management — Termination Review") — the bar of tasks by state and the open-count
   single score. This is the manager's at-a-glance health view; trend it toward zero over cycles.

## Act 4 — Governance certification (Okta campaigns)

The biweekly review asks *"is this person still employed?"* Certification campaigns ask the
different question *"should this person have this access?"* — that's why they're separate. Three
are live and ACTIVE:

- **Flagged Population Review (biweekly feed)** — the cycle's flagged users, split between the two
  reviewers (Zyler 19 items, Phil 20).
- **Quarterly UAR: Saturn Regional** — full-app attestation for Saturn East/Central/West.
- **Targeted Resource Review: NA Saturn ComSat** — one high-scrutiny app.

Demo: log in as **zyler.bawado** to the Okta **End-User Dashboard → Access Certifications**, open
his assigned reviews, and make a decision (Approve / Revoke) on a couple with a justification.
Show that Read-Only Admin also lets him *see* the whole governance picture. Honest caveat to state
out loud: on these disconnected apps a **Revoke is a certification decision, not proof of in-app
removal** — that proof still comes from the reconciliation's next-cycle export check.

## Act 5 — Closure verification (the auditable part)

This is the payoff. Run the *next* cycle after the fulfillers closed their tasks:

```
python3 biweekly_recon.py --rosters "App User Lists" --create-tickets
```

- For accounts actually removed from the fresh export → the pipeline writes a **BEFORE/AFTER
  VERIFIED** work note on the RITM and closes the loop.
- For a task closed while the account is **still in the export** → **REMOVAL NOT VERIFIED**: the
  pipeline auto-reopens the task and tags the finding. Demonstrate this deliberately by closing a
  task *without* editing the roster — the next cycle catches the false claim. This "you can't just
  say you did it" behavior is the single most convincing thing to show an auditor.

## Talking points (why it holds up)

- HR employment status is the only source of truth; Okta status is evidence, not authority.
- The classifier has three branches with a **loud unknown** — it never defaults to "fine."
- Closure requires **verified disappearance in a fresh export** — closed tickets and screenshots
  are not proof; a short/missing export freezes closure rather than auto-closing it.
- The pipeline runs as a **least-privilege OAuth service identity** (read-only scopes), not a
  personal admin token — every action is attributable and scoped.
- End state: as apps get SCIM/lifecycle onboarding, removal becomes preventive and the review's
  findings for that app trend to zero — the review becomes the *evidence the automation works*.

## After the demo

- Re-running Act 5 against a doctored roster resets the "false claim" story for next time.
- Sign into ServiceNow (or just run `smoke_test.py`) at least weekly so the PDI is never reclaimed.
- `python3 campaign_report.py` produces a results workbook (`reports/campaign_results_*.xlsx`) if
  you want a leave-behind of the certification outcomes.
