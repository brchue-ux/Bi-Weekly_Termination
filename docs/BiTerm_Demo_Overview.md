# Biweekly Termination Access Review — Reference Implementation

**Audience:** management / stakeholders reviewing the demo
**Status:** working reference implementation on fully synthetic data (no production systems or real employee data were used)

---

## Executive summary

Every two weeks, we must verify that everyone holding access in ~19 applications is still a legitimate employee per the HR system. Today that control is a manual assembly line: per-app spreadsheet exports, VLOOKUPs against an exception list, a verification script, and hand-created ServiceNow tickets — repeated app by app, cycle after cycle. It is slow, it covers only 12 of 19 applications, and its hardest cases pass through silently.

This reference implementation automates every step of that control **except the one that must stay manual** (actually removing access in a disconnected application), and adds something the manual process never had: **proof**. Ticket closure alone is never accepted as evidence — the system independently verifies each removal against the next cycle's fresh application export, writes the before/after evidence onto the ticket, and automatically reopens any ticket that was closed without the access actually disappearing.

| Demo result (synthetic data, 10 applications, 4,404 access records) | |
|---|---|
| Certified per cycle, automatically | 4,404 records across 10 apps |
| Confirmed-termination tickets auto-created in ServiceNow (REQ→RITM→Task, fully populated) | 30 |
| Removals independently verified and evidenced on the ticket | 11 |
| Tickets closed *without* real removal — caught and auto-reopened | 2 |
| Ambiguous records surfaced for human adjudication (previously silent) | 475 |
| Governance campaigns run against the same data (UAR / targeted / flagged-population) | 3 |

---

## The problem with the manual process

- **Toil.** Each cycle: pull an export per app, VLOOKUP against the exception list, run a verify script on the residue, then manually create a ServiceNow REQ → RITM → Task per person, work it, and close it.
- **Coverage gaps.** The consolidated report covers 12 of 19 applications; the rest need separate handling.
- **Silent failure of the hard cases.** The real data contains a 16:1 ratio of *ambiguous* records ("not found in HR", malformed status values) to clear terminations. A VLOOKUP-based process has no lane for "can't tell" — those records sail through untouched, every cycle.
- **No proof of closure.** When a ticket is closed, nothing verifies the access actually went away. The control runs on the honor system exactly where it matters most.

## Design principles (the "why")

1. **HR employment status is the only authority on legitimacy.** Okta account state is enrichment, not truth — people on unpaid leave are often suspended in Okta yet legitimately retain access, and a terminated employee whose account was never cleaned up looks "fine" in Okta. Keying on Okta breaks both directions.
2. **This is a detective control; removal stays manual.** The pipeline detects, evidences, tracks, and verifies. It never touches access itself — so the control documentation never overstates what the automation does. (Automated deprovisioning is a separate, preventive control that arrives per-application with connector onboarding.)
3. **Never default-to-fine.** Classification has three branches — legitimate, confirmed-terminated, and a *loud* "cannot tell." Anything requiring judgment goes to a human queue; nothing ambiguous is silently passed. The rule for automation: **certainty + non-privileged = automatic; everything else = human.**
4. **An exception never suppresses a termination.** The HR check runs on every record first. Standing exceptions (admin/service accounts) are honored only for people HR confirms are active — and each exception now carries an **owner and an expiry**, so a lapsed exception or a terminated owner raises its own flag (including "this service account needs a new owner").
5. **Closure is proven by independent re-observation, never by attestation.** A closed ticket is a *claim*. The *evidence* is the next cycle's fresh export — produced by the application, independent of the person who did the work — no longer containing the account. A missing or suspiciously short export never auto-closes anything: absence of evidence is not evidence of removal.

## What was built

| Component | What it does |
|---|---|
| **Reconciliation pipeline** | One command per cycle: parses every app export, joins HR status and live Okta state (3-way join), classifies every record, produces an audit workbook (tab per app, findings first), immutable per-cycle evidence snapshots, and three notification digests (admin / adjudication queue / ownership events). |
| **ServiceNow integration** | For *confirmed* terminations only: orders a dedicated catalog item, generating the full REQ → RITM → Task chain — requested-for set to the terminated person, eight evidence fields on the ticket, task assigned to the Access Management group. Duplicate-safe: a finding is ticketed once; later cycles age and escalate it, never re-ticket. |
| **Closure verification with write-back** | Next cycle, each removal is checked against the fresh export. Verified → a BEFORE/AFTER work note lands on the ticket (self-contained audit evidence). Not verified → "REMOVAL NOT VERIFIED" note and the task is **automatically reopened**. |
| **Operations dashboard** | Live ServiceNow dashboard over the Access Management queue: open-work counter + tasks by state. |
| **Governance campaigns** | Three certification patterns run against the same data via Okta Identity Governance: a quarterly-style UAR (392 items across three apps), a targeted single-app resource review, and a **user-scoped campaign fed directly by the reconciliation's flagged population** — the pipeline's findings become a certification scope with no re-modeling. |
| **Verification gates** | Nothing is declared done from its own logs. Separate verification scripts recompute expected state from source data and reconcile against live systems (e.g., the environment build was accepted only on an independent 5-check PASS verdict). |

## How a cycle runs

1. Fresh application exports land (in production: pulled or delivered per app).
2. Pipeline classifies all records: auto-clear (HR-active, or valid exception with living owner) / **auto-actioned** (exact-match terminated → ServiceNow ticket created automatically) / **human review** (all ambiguity, expired exceptions, orphaned or owner-less accounts, anything privileged).
3. Fulfillers work the tickets manually in the real applications and close them.
4. Next cycle: every prior finding is re-checked. Gone from a sane export → closed, with evidence written to the ticket. Still present → aged, escalated, and if its ticket was closed, reopened.
5. The cycle workbook, snapshots, and digests are the audit trail; the tickets carry their own before/after evidence.

## What the demo proves

- The full loop runs end-to-end on realistic data volumes with exact reconciliation of every number (e.g., campaign item counts match assignment counts to the record).
- **The false-claim catch:** two tickets were deliberately closed without the underlying access being removed. The next cycle detected both, wrote "REMOVAL NOT VERIFIED" on the records, and reopened the tasks. This is the difference between a control and an honor system.
- **The ownership model:** a terminated employee who was also the registered owner of other people's service-account exceptions triggered both their own removal ticket *and* reassignment flags on the accounts they owned.
- **Reconciliation ↔ certification integration:** the flagged population from the biweekly review became a live certification campaign scope; a routine regional UAR independently surfaced the same problem account the reconciliation had flagged.

## Path to production

- **Data feeds:** replace file drops with scheduled exports; the parser already tolerates real-world defects observed in production-shaped data (header offsets, sentinel values, malformed statuses).
- **Coverage:** any application that can produce a user export is in scope — closing the 12-of-19 gap without waiting for integrations.
- **Per-app connector (SCIM) onboarding** shortens verification latency from "next cycle" to minutes and enables the *preventive* control (deprovision at termination). The review then keeps running as the detective layer and should trend toward zero findings — which is itself the evidence the preventive control works.
- **What deliberately stays manual:** removal execution in disconnected apps, and every judgment call in the human-review queue.

## Guardrails and honest limits

- The pipeline **never removes access**, and no document should claim it does.
- A certification "REVOKED" decision is a decision, not proof of removal — proof remains the next independent export (until an app is connector-onboarded).
- Everything above ran in an isolated sandbox on **synthetic, de-identified data**; production rollout requires the real data feeds, the production ServiceNow catalog, and access approvals — none of which were touched here.
