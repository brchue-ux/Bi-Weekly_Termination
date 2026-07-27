#!/usr/bin/env python3
"""Build docs/BiTerm_End_to_End_Build_Lab.docx — the Word edition of the end-to-end build lab.

Content parity with docs/BITERM_END_TO_END_BUILD_LAB.md; the Word edition exists because the
Markdown one is hard to read as a document (its mermaid sources are unrendered text). Every
mermaid diagram is re-expressed here as a native Word box-and-arrow figure, so the file is
self-contained with no image dependencies and no rendering toolchain.

Usage: python3 scripts/build_biterm_lab_docx.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from docx_write import (Docx, NAVY, STEEL, TEAL, AMBER, CRIMSON, INK, MUTED, BAND, BAND2)

OUT = Path(__file__).parent.parent / "docs" / "BiTerm_End_to_End_Build_Lab.docx"

# Panel fills, one light tint per accent.
F_NAVY, F_TEAL, F_AMBER, F_CRIM, F_GREY = "ECF0F7", "EAF3EF", "FBF2E0", "F8ECEC", BAND

d = Docx(title="BiTerm — End-to-End Build Lab",
         creator="Access Management",
         subject="Biweekly termination review: what was built, why, and why it is safe")


def built(lines):
    d.callout("BUILT", lines, accent=NAVY, fill=F_NAVY, bullets=True)


def why(lines):
    d.callout("WHY THIS WAY", lines, accent=STEEL, fill=F_GREY, bullets=len(lines) > 1)


def benefit(lines):
    d.callout("BENEFIT", lines, accent=TEAL, fill=F_TEAL, bullets=len(lines) > 1)


def safe(lines):
    d.callout("ENTERPRISE-SAFE BECAUSE", lines, accent=NAVY, fill=F_NAVY, bullets=True)


def caution(label, lines):
    d.callout(label, lines, accent=AMBER, fill=F_AMBER, bullets=len(lines) > 1)


def danger(label, lines):
    d.callout(label, lines, accent=CRIMSON, fill=F_CRIM, bullets=len(lines) > 1)


def box(title, *body, accent=NAVY, fill=BAND):
    return ([title] + list(body), fill, accent)


# ─────────────────────────────────────────────── title page
d.title_block(
    "BiTerm — End-to-End Build Lab",
    "Biweekly termination review, rebuilt on Okta Identity Governance and ServiceNow: "
    "what was built, in what order, why each choice was made, and why each piece is safe to "
    "run at enterprise scale.",
    kicker="Okta Identity Governance · ServiceNow · SOX detective control")

d.p("This document is the **build record**. It walks the whole system module by module — the data, "
    "the apps, the users, the Detective Control service identity, the reconciliation pipeline, "
    "entitlements and grants, certification campaigns, and the revoke-to-ticket closure loop — and "
    "for every component states the decision behind it, the benefit it buys, and the security "
    "argument that makes it defensible in an audit.")

d.h3("How this fits with the other documents")
d.table(
    ["Document", "Answers"],
    [["TERM_FLOW_EXPLAINER.md", "*Why* the biweekly review exists at all"],
     ["OIG_TERMINATION_LAB.md", "*How you do it by hand*, one click at a time, for one app"],
     ["**This document**", "**What was actually built, why, and why it is safe**"],
     ["OIG_WORKFLOWS_BUILD_GUIDE.md", "The Console-only Workflows build (no API can create a flow)"],
     ["OIG_FEASIBILITY_BRIEF.md", "The “is this real?” briefing for people who weren’t in the room"]],
    widths=[34, 66])

d.h3("How to read each module")
d.fig_grid(4, [[
    box("BUILT", "what exists today", accent=NAVY, fill=F_NAVY),
    box("WHY THIS WAY", "the decision, and", "the alternative rejected", accent=STEEL, fill=F_GREY),
    box("BENEFIT", "what it buys", "the program", accent=TEAL, fill=F_TEAL),
    box("SAFE BECAUSE", "the security", "argument", accent=NAVY, fill=F_NAVY),
]])

d.h3("Status marker used throughout")
d.table(
    ["Marker", "Meaning"],
    [["PROVEN", "Verified by an independent gate that ends in a single VERDICT: PASS line"],
     ["IN FLIGHT", "Deliberately halted mid-build; the reason is stated in the module"],
     ["DESIGNED", "Built and tested as a reference implementation, not executed in the tenant"]],
    widths=[18, 82])

d.p("**Every claim of state in this document traces to a verification run, not to the logs of the "
    "script that made the change.** That rule is itself part of the control and is set out in "
    "Module 9.", color=MUTED, size=19)

# ─────────────────────────────────────────────── contents
d.h1("Contents", page_break=True)
d.table(
    ["#", "Module", "State"],
    [["0", "The whole system on one page", "—"],
     ["1", "The data, and making it safe to use", "PROVEN"],
     ["2", "Creating the apps", "PROVEN"],
     ["3", "Creating the users", "PROVEN"],
     ["4", "The Detective Control service", "PROVEN"],
     ["5", "The detective control itself", "PROVEN"],
     ["6", "Entitlements and grants", "PROVEN — stop raised and cleared"],
     ["7", "Certification campaigns", "PROVEN — one LIVE, one dormant"],
     ["8", "Revoke → ServiceNow → verified closure", "PROVEN (scripted) · DESIGNED (Workflows)"],
     ["9", "The verification discipline governing all of it", "—"],
     ["A", "State of the build", "—"],
     ["B", "Reference IDs", "—"],
     ["C", "The open question worth more than the backlog", "—"]],
    widths=[7, 60, 33])

# ─────────────────────────────────────────────── module 0
d.h1("Module 0 · The whole system on one page", page_break=True)

d.h2("The control loop")
d.fig_stack([
    box("① SOURCES OF TRUTH",
        "TalentHub HR export (employment status)  ·  10 per-app user exports (dated drop)  ·  exception list",
        accent=STEEL, fill=F_GREY),
    box("② DETECTIVE CONTROL SERVICE",
        "Read-only OAuth service app  →  biweekly_recon.py",
        "Three-way join: app roster ↔ HR ↔ Okta, then classify",
        accent=NAVY, fill=F_NAVY),
    box("③ FINDINGS + EVIDENCE",
        "report.xlsx  ·  immutable state.json (with the source rows)  ·  three digests",
        accent=NAVY, fill=F_NAVY),
    box("④ SERVICENOW",
        "One REQ → RITM → SCTASK chain per person, full evidence in the request variables",
        accent=NAVY, fill=F_NAVY),
    box("⑤ HUMAN REMOVES ACCESS IN THE APP, CLOSES THE TASK",
        "This is the only step that removes anything. Nothing in this system does it automatically.",
        accent=STEEL, fill=F_GREY),
    box("⑥ NEXT CYCLE VERIFIES THE CLAIM",
        "Access really gone → BEFORE/AFTER evidence note.  Still present → “REMOVAL NOT VERIFIED”, task REOPENED.",
        accent=TEAL, fill=F_TEAL),
])
d.fig_caption("The arrow that matters is ⑥ back onto ④. Everything above it is detection; that step "
              "is what makes the control trustworthy.")

d.h2("The governance lane, running alongside")
d.fig_row([
    box("SAML app", "EM enabled", accent=NAVY, fill=F_NAVY),
    box("Entitlement", "“Role”", accent=NAVY, fill=F_NAVY),
    box("Grant", "person → value", accent=NAVY, fill=F_NAVY),
    box("Campaign", "reviewer certifies", accent=NAVY, fill=F_NAVY),
    box("Decision", "Revoke → ticket", accent=TEAL, fill=F_TEAL),
])

d.h2("Two facts that shaped every decision below")
danger("FACT 1 — THE APP TYPE DECIDES WHETHER IT CAN BE GOVERNED AT ALL", [
    "Entitlement Management cannot be switched on for Bookmark apps — the option does not exist "
    "for that type, and `PUT /api/v1/apps/{id}` accepts the field, returns **200, and silently "
    "ignores it**. Governing an app is a **rebuild as SAML, not a toggle**. Confirmed both in the "
    "Console and by API."])
danger("FACT 2 — A REVOKE DECISION AND A CLOSED TICKET ARE BOTH CLAIMS, NOT REMOVALS", [
    "Campaign remediation is set to `NO_ACTION` on every outcome, by design. Nothing here removes "
    "access. The control detects, evidences, tickets — and then verifies against the next export, "
    "reopening the ticket if the access is still there."])

# ─────────────────────────────────────────────── module 1
d.h1("Module 1 · The data, and making it safe to use", page_break=True)
d.p("**State: PROVEN.**", color=TEAL)

built([
    "`xlsx_min.py` — a zip + sheet-XML reader (no `openpyxl` in this environment), correct on the "
    "header-offset and cell-placement quirks of the real workbooks.",
    "`reidentity.py` — full re-identification of everyone in scope against a synthetic "
    "international name pool: 5,157 anchors, per-cell mapping, deterministic seed. Off-scheme "
    "stems keep their own shape; naming schemes, case, padding and even the source data's "
    "*defects* are preserved, because the defects are what the pipeline must survive.",
    "`rename_tenant.py` — in-place rename of all 2,027 live users, preserving ids and assignments.",
    "`verify_reidentity_tenant.py` — the gate: scans **every** live seeded user against the token "
    "union of **all** source files.",
])
why(["The cheap alternative — rotating the first-name column by ten rows — was **rejected**: a "
     "permutation of real values is still real values. Surnames never move, login stems survive "
     "verbatim, and anyone holding the original sheet can undo it. A synthetic pool that preserves "
     "*shape* gives realistic test data that is nobody."])
benefit(["The tenant carries a full-scale, realistically messy population — duplicate seats, "
         "off-scheme admin accounts, malformed status cells, future-dated terminations — so the "
         "pipeline is tested against real failure modes with zero real identities present."])
safe([
    "The gate enforces the guarantee that actually matters: **zero original first-plus-last "
    "pairings** among seeded users, since the pairing is the re-identifiable unit. Live result: "
    "**0 pairings → VERDICT: PASS**.",
    "Lone token coincidences across a 7,779-person source are reported as a note, not a failure. "
    "A gate that always fails is a liability nobody reads.",
    "A real incident drove this design: two family names from the *source* file were spotted live "
    "in Okta, deleted within minutes and confirmed 404. The gate now scans against every source "
    "file's token union, not just the sheets in scope.",
])

# ─────────────────────────────────────────────── module 2
d.h1("Module 2 · Creating the apps", page_break=True)
d.p("**State: PROVEN.**", color=TEAL)

d.fig_grid(2, [[
    box("BOOKMARK APP — “BiTerm · <tab>”",
        "Read by okta_state() for orphan detection",
        "Cannot hold entitlements, ever",
        accent=STEEL, fill=F_GREY),
    box("SAML APP — “BiTerm OIG · <tab>”",
        "Entitlement Management ENABLED",
        "Holds Role entitlement + grants",
        accent=TEAL, fill=F_TEAL),
]])
d.fig_caption("One roster tab, two Okta apps, deliberately disjoint label prefixes.")

built([
    "`seed_tenant.py` — 11 Bookmark apps (`BiTerm - <tab>`), idempotent, 429-backoff, resumable, "
    "writing `seed_manifest.json` with every id and every identity's fate.",
    "`oig_saml_rollout.py --apply` — the 9 remaining apps re-created as custom SAML "
    "(`BiTerm OIG - <tab>`); the ComSat pilot already existed → **10 governable apps**, all "
    "re-queried live as SAML and ACTIVE. Manifest: `oig_apps.json` (tab → app id → roles → EM status).",
    "The **user** then enabled Entitlement Management on all 10 in the Console — UI-only, no API "
    "exists — and `em=ENABLED` was verified on all 10 directly rather than taken on trust.",
])
why(["**Two prefixes, on purpose.** The bookmark apps were not deleted when the SAML ones arrived. "
     "The reconciliation reads apps by the `BiTerm - ` prefix; the governance tooling reads "
     "`BiTerm OIG - `. The two populations are deliberately disjoint, so bringing an app under "
     "governance cannot silently change what the detective control sees. Retiring the bookmarks is "
     "a separate, costed decision — not a free side effect."])
benefit(["A disconnected app — no SCIM, no connector, just a CSV drop — becomes governable today, "
         "with the same entitlement model, campaigns, reviewers, decision history and evidence "
         "trail it will have after SCIM onboarding. SCIM later swaps the data source underneath; "
         "it does not remodel anything."])
safe([
    "`oig_saml_rollout.py` is idempotent — an existing label is reused, never duplicated.",
    "It deliberately does **not** enable Entitlement Management. That stays a human action in the "
    "Console, per app, with the manifest as the checklist: automation creates the container, a "
    "person decides what comes under governance.",
    "**Stated plainly for the auditor:** in the CSV-fed model Okta's picture of who holds what is "
    "an *assertion* — it cannot be wrong in a way Okta can detect. Under SCIM it becomes an "
    "*observation*, where drift surfaces on its own. That is exactly why the external "
    "reconciliation is load-bearing today, and why it shrinks rather than disappears later.",
])

# ─────────────────────────────────────────────── module 3
d.h1("Module 3 · Creating the users", page_break=True)
d.p("**State: PROVEN.**", color=TEAL)

built(["`seed_tenant.py` created 7,505 users (later reduced to ~2,035 in BiTerm scope when the "
       "obsolete SFDC/DocuSign population was removed), login = UPN on the fake domain "
       "`bitermtest.com`, plus ~8,622 app assignments."])

d.h3("The population is a deliberate test surface")
d.table(
    ["Planted condition", "Count / share", "What it exercises"],
    [["Roster rows with no UPN", "409", "App-side **orphans** — no Okta account to join to"],
     ["Terminated/Retired, still ACTIVE in Okta", "~40% of terms",
      "The un-deprovisioned failure mode the control exists to catch"],
     ["Terminated/Retired, SUSPENDED", "~30%", "Unpaid-leave vs. terminated ambiguity"],
     ["Terminated/Retired, never created", "~30%", "The “no Okta account at all” branch"],
     ["Pre-existing demo-org users", "exactly 18", "Blast-radius check — must remain untouched"]],
    widths=[36, 18, 46])
d.p("All of it deterministic on `sha256(login) % 10`, so the same conditions land in the same "
    "places on every rebuild.", color=MUTED, size=19)

why(["A clean population proves nothing. Requirement #2 of this control is to report accounts with "
     "no related Okta account — *enabled, disabled, or nonexistent*. All three branches had to "
     "exist in the data before the classifier could be trusted to tell them apart."])
benefit(["Every branch of the classifier has a live example, so a regression shows up as a changed "
         "count in the next verification run rather than as a silent miss in production."])
safe([
    "Seeding is idempotent and manifest-recorded — every created id and every identity's fate is "
    "written down, so the blast radius of any change is knowable.",
    "`verify_seed.py` recomputes expected state **from the source files** and reconciles it against "
    "a live API pull. Live: **VERDICT: PASS on all 5 checks** — 7,505 users, 8 absent-as-designed, "
    "exact statuses, 0 name mismatches, all 11 apps' assignment sets exact at 8,622, and the 18 "
    "pre-existing users untouched.",
    "One account, `bchue@wm.com`, is a permanent off-limits allowlist entry in every script and "
    "every gate. **Named exclusions beat “be careful”.**",
])

# ─────────────────────────────────────────────── module 4
d.h1("Module 4 · The Detective Control service", page_break=True)
d.p("**State: PROVEN.** This is the identity the control runs as, and the module with the "
    "strongest security argument in the build.", color=TEAL)

d.fig_stack([
    box("ONE-TIME PRIVILEGED SETUP",
        "Personal admin SSWS token → oauth_bootstrap.py (idempotent) — mirrors production, where tenant IAM runs it once",
        accent=STEEL, fill=F_GREY),
    box("SERVICE APP · “BiTerm Detective Control - Service”",
        "private_key_jwt — no client secret exists to leak. Key held at ~/.secrets, 0600.",
        accent=NAVY, fill=F_NAVY),
])
d.fig_grid(2, [[
    box("LAYER 1 — GRANTED SCOPES",
        "okta.users.read", "okta.apps.read",
        "okta.governance.accessCertifications.read", accent=NAVY, fill=F_NAVY),
    box("LAYER 2 — ADMIN ROLES ON THE CLIENT",
        "READ_ONLY_ADMIN",
        "ACCESS_CERTIFICATIONS_ADMIN", accent=NAVY, fill=F_NAVY),
]])
d.fig_stack([
    box("EFFECTIVE PERMISSION = LAYER 1 ∩ LAYER 2",
        "Scopes alone yield E0000006. Both layers are load-bearing.",
        accent=AMBER, fill=F_AMBER),
    box("USED BY", "okta_client.py → biweekly_recon.py · campaign_report.py",
        accent=TEAL, fill=F_TEAL),
])

built([
    "OAuth service app using **private_key_jwt** — asymmetric key, no shared secret.",
    "`oauth_bootstrap.py` — the one-time privileged setup, idempotent, and the *only* place the "
    "personal admin token is used.",
    "`okta_client.py` — token cached with a 5-minute refresh margin, same call signatures as the "
    "seeder's client, so switching auth was a drop-in.",
    "`verify_oauth.py` — the independent gate. **VERDICT: PASS**, including the negative cases: a "
    "write POST returns **403**, and a token request for an ungranted scope is refused with "
    "`consent_required`.",
])
why(["The pipeline originally ran on an SSWS admin token. That was rejected on the user's own "
     "mandate: an SSWS token is **person-bound, unscoped, and dies with the person's account** — it "
     "cannot pass a SOX access review of the control itself. A service app with asymmetric-key "
     "auth and named scopes can.",
     "**Carry this into any production access request:** on this org, effective permission = "
     "granted scopes **∩** admin roles assigned to the client principal. A request that asks for "
     "only one of the two will look approved and still fail at runtime."])
benefit([
    "The control's identity is auditable on its own terms — “what can this control read?” is "
    "answerable without reference to any employee.",
    "Staff turnover does not break the control, and revoking a person's admin rights does not "
    "silently disable the biweekly run.",
    "Verified two independent ways: an **equivalence proof** (a full tenant pull through both the "
    "SSWS and the OAuth client, diffed — 7,523 users, 10 apps, **0 mismatches, VERDICT: PASS**) and "
    "a full `campaign_report.py` run under OAuth.",
])
safe([
    "**Read-only by construction.** The service principal cannot write, and that is proven by a 403 "
    "in the gate rather than asserted by policy. A detective control that cannot mutate the systems "
    "it inspects cannot destroy the evidence it exists to produce.",
    "**Two-layer least privilege**, both layers minimal and named.",
    "**Privilege separation by design:** seeding and campaign *management* deliberately stay on the "
    "admin token because they are privileged scaffolding, not the control. Campaign creation needs "
    "`accessCertifications.MANAGE`, which the service app does not have, on purpose.",
    "Key material lives in `~/.secrets/` at 0600 — never in a repo, never pasted into a Workflows card.",
])

# ─────────────────────────────────────────────── module 5
d.h1("Module 5 · The detective control itself", page_break=True)
d.p("**State: PROVEN.** `biweekly_recon.py` — the pipeline that replaced the manual VLOOKUP process.",
    color=TEAL)

d.fig_stack([
    box("THREE-WAY JOIN",
        "app roster row  ↔  TalentHub HR  ↔  Okta (read via the service app)",
        accent=NAVY, fill=F_NAVY),
])
d.fig_grid(3, [[
    box("ACTIVE / PAID LEAVE / UNPAID LEAVE",
        "Access legitimate", "no action", accent=TEAL, fill=F_TEAL),
    box("RETIRED / TERMINATED",
        "FINDING", "→ ServiceNow chain", accent=CRIMSON, fill=F_CRIM),
    box("CANNOT DETERMINE",
        "LOUD UNKNOWN", "→ adjudication digest", accent=AMBER, fill=F_AMBER),
]])
d.fig_stack([
    box("NO OKTA ACCOUNT AT ALL  →  ORPHAN",
        "431 rows — reported every cycle, never silently dropped, not auto-ticketed",
        accent=STEEL, fill=F_GREY),
])

built(["Per cycle, under `cycles/cycle_<timestamp>/`: an evidence workbook (`report.xlsx`, findings "
       "sorted first), an immutable `state.json` including the **source rows** behind every "
       "finding, and three digests — admin, adjudication, ownership.",
       "`feed_ingest.py` adapts raw unjoined drops into the exact same shapes, so the front half "
       "can later be replaced by a Workflows flow without touching the verified classifier."])

d.h3("Three design rules, each bought with a real defect")
d.numbered([
    "**An exception never suppresses a positive termination hit.** A process simulation found one "
    "identity that was *both* Terminated *and* exception-listed; with exception-matching running "
    "first, a terminated privileged account was silently suppressed **every cycle**. The HR check "
    "now runs on everyone, and exceptions only excuse accounts that cannot be HR-verified.",
    "**The unknown branch is loud.** The census is 30 clearly-terminated against **478 ambiguous** "
    "rows — 16:1. Adjudicating “can't tell” *is* the control's real cost; defaulting ambiguity to "
    "“fine” would have hidden the entire actual workload.",
    "**A missing export is an error, never an empty app.** An empty app reads as “every account "
    "vanished” and would hand closure verification a 100% false-closure rate.",
])

benefit(["Coverage went from **12 of 19 apps to any app with an export**.",
         "The three-way join added orphan detection that VLOOKUP never provided.",
         "478 ambiguous rows that used to pass silently are now surfaced.",
         "Reconciled live run (feed mode, all 10 apps): 4,404 app rows + 2,035 HR rows → 3,996 "
         "joined / 201 no-HR-match / 207 unjoinable → **57 REQ/RITM/SCTASK chains created, 0 "
         "errors**, 454 unknowns, 431 orphans — then independently re-queried out of ServiceNow, "
         "57/57 REQs and RITMs resolving live."])
safe([
    "**The pipeline never removes access.** It detects, evidences, tracks and confirms closure on "
    "the next cycle. The control documentation is forbidden from claiming otherwise.",
    "**Dry-run by default** — ticket creation requires an explicit `--create-tickets`.",
    "**Rehearsals cannot pollute the record.** A real defect was found and fixed here: a dry run "
    "wrote `state.json` into the live lineage, so the next real cycle read it as a prior cycle of "
    "record and aged every finding to “escalated”. The baseline is now the most recent state with "
    "`tickets_live` true — *a rehearsal is never a cycle of record*.",
    "**Counting discipline.** The report once said “75 tickets” while ServiceNow held 57, because "
    "flagged *rows* were counted under a *tickets* heading (one person's three seats = one finding "
    "= one ticket). Rows, chains and people are now three separate labelled numbers. In a SOX "
    "artifact, an unexplained number is a finding.",
])

# ─────────────────────────────────────────────── module 6
d.h1("Module 6 · Entitlements and grants", page_break=True)
d.p("**State: PROVEN — the correctness stop was raised, probed, and cleared.** This is where “who "
    "has the app” becomes **what they can do inside it**.", color=TEAL)

d.fig_row([
    box("SAML app", "EM enabled", accent=NAVY, fill=F_NAVY),
    box("Entitlement", "“Role”", accent=NAVY, fill=F_NAVY),
    box("Values", "Standard User · Read Only",
        "Power User · Administrator", "Service Account", accent=NAVY, fill=F_NAVY),
    box("Grant", "principal → value", accent=NAVY, fill=F_NAVY),
    box("Reviewer sees", "“Basim Uchida —", "Role: Power User”", accent=TEAL, fill=F_TEAL),
])

built([
    "`oig_pilot_load.py` + `verify_oig_pilot.py` — proven end-to-end on **NA Saturn ComSat**: "
    "entitlement `Role` with 5 values mirroring the drop's `app_role`; coverage on the 32-row drop "
    "reconciled exactly as **20 granted + 12 orphans = 32**. The gate returned **VERDICT: PASS on "
    "14 checks** — and was **proven able to fail** (a role was flipped in the drop, the mismatch "
    "was caught, the drop regenerated, PASS restored).",
    "A per-app `Role` entitlement now exists on **all 10** apps, each carrying that app's own roles.",
    "The working grant shape (`POST /governance/api/v1/grants`): `grantType:\"CUSTOM\"`, "
    "`target{externalId:appId,type:APPLICATION}`, "
    "`targetPrincipal{externalId:userId,type:OKTA_USER}`, `action:\"ALLOW\"`, "
    "`entitlements:[{id, values:[{id}]}]`.",
])
why(["Entitlements do **not** require SCIM — plain SAML apps hold real entitlements. That single "
     "finding is what makes governing 10 disconnected apps possible at all. The grant carries the "
     "**value**; writing app-user profile attributes is *not* the mechanism, as those attributes "
     "come back null."])

danger("THE CORRECTNESS STOP, AND HOW IT WAS CLEARED", [
    "**The defect.** The drops carry heavy duplicate emails, and **136 emails hold conflicting "
    "roles across their rows** — one identity that is both *Power User* and *Administrator* in the "
    "same app. The loader used a single-value entitlement with **first-row-wins**, so it could "
    "grant “Power User” and drop “Administrator” — **hiding an administrator behind a lower "
    "privilege, inside the very control whose purpose is to surface privilege.** It was killed "
    "mid-run rather than finished.",
    "**The constraint, probed live on one app before committing to ten:** `PATCH`/`PUT` a grant "
    "value → 400; `DELETE /grants/{id}` → 400; flipping `multiValue` → 400; deleting the "
    "entitlement returns 204 but leaves *bare* grants rather than cascading. **The only lever is "
    "POSTing a value for an existing principal, which REPLACES the current one.**",
    "**The decision — Option B+, highest privilege wins** (Administrator > Power User > Standard "
    "User > Read Only > Service Account): a clean, deletion-free correction, with per-account "
    "detail already living in the reconciliation under the two-control split.",
    "**The clean reload.** The loader now aggregates every distinct role a person holds per app and "
    "grants the single highest, re-POSTing only when the current value is wrong. Applied run: "
    "**err=0, granted=1,069, corrected=37** conflicted principals whose first-row value was not the "
    "highest, unchanged=2,024, conflicted=136, orphans=431.",
    "**The verdict.** `oig_verify_all.py` checks the highest-privilege contract per principal, with "
    "its coverage math fixed and un-deletable bare grants reported as WARN. **`--selftest` injects a "
    "bogus role and FAILS on all 10 — the checker is proven falsifiable — and the real run returns "
    "`VERDICT: PASS (10 apps, 0 failures)`.**",
])

benefit(["Certification moves from a binary — “does this person still need this app?” — to the "
         "question an auditor actually asks: “should this person still be an *Administrator* of "
         "this app?”"])
safe([
    "The verification gate has veto power over “done”, and it used it. **Stopping a load because it "
    "could mask privilege is the control working**, not the project slipping — and the fix was a "
    "probed API contract plus a rework, not a retry.",
    "**The privilege ceiling is a contract, not a hope:** every principal holds the highest role any "
    "of their accounts carries, and the verifier asserts exactly that, per principal.",
    "**The checker is proven falsifiable** — `--selftest` must fail, and does, on all 10 apps. A "
    "gate that has never been shown to fail is not evidence.",
    "The loader refuses to run unless `emOptInStatus == ENABLED`, and re-POSTs only when the current "
    "value is wrong, so a re-run is safe and near-silent.",
    "**Known and disclosed:** grants cannot be deleted through the API at all, so a decommissioned "
    "app's grants persist as bare grants. That is a platform limit, reported as WARN — not something "
    "the tooling can silently clean up.",
])

# ─────────────────────────────────────────────── module 7
d.h1("Module 7 · Certification campaigns", page_break=True)
d.p("**State: PROVEN — one campaign LIVE, one deliberately dormant.**", color=TEAL)

d.fig_grid(3, [[
    box("① PER-APP ENTITLEMENT CERT",
        "one per governed app",
        "“is this role right?”", accent=NAVY, fill=F_NAVY),
    box("② QUARTERLY UAR",
        "all 10 apps, one campaign",
        "routine attestation", accent=NAVY, fill=F_NAVY),
    box("③ FLAGGED POPULATION",
        "USER campaign scoped to the",
        "cycle's confirmed terminations", accent=TEAL, fill=F_TEAL),
]])
d.fig_caption("Archetype ③ is the join between the two controls: a machine-detected finding gets a "
              "named human decision.")

built([
    "**Proven live on the pilot:** campaign `ici119gnldxDWagCy697` ACTIVE with **20 items, each "
    "carrying `entitlementValue{name, entitlement{name:\"Role\"}}`** — the reviewer certifies "
    "“Basim Uchida — Role: Standard User”, not “has the app”.",
    "**Proven live in the bookmark era**, on re-identified users: Targeted Resource (ComSat, 20 "
    "items = the exact assignment count); Quarterly UAR: Saturn Regional (392 = 129+133+130, which "
    "catches a flagged Saturn West assignment inside a *routine* UAR); Flagged Population (27 "
    "items, **27/27 cross-referenced back to recon findings**, with the 4 identities holding no "
    "Okta account correctly uncertifiable).",
    "**`oig_run_campaigns.py`, run against the fully loaded tenant — LIVE:** “BiTerm — Access "
    "Certification (LIVE): NA Saturn ComSat” (`ici11c29d1yN6cZo9697`), launched and **ACTIVE**, with "
    "**20 review items = 20 grants and 0 lacking `entitlementValue`** (14 Standard User, 4 Read "
    "Only, 1 Power User, 1 Administrator). The landmine below was avoided, and that is verified by "
    "inspection rather than assumed.",
    "**DORMANT:** “BiTerm — Access Certification (PREPARED): CloudForce HQ” "
    "(`ici11c297d4rUoS5P697`) — created only, left SCHEDULED with a +365-day start, **never "
    "launched**: the demonstration that preparing a campaign and starting one are separate acts.",
    "The three-archetype all-apps builder still creates but never launches, and is **still never "
    "run**; it reads each campaign back and refuses to finish if any came up ACTIVE.",
    "`campaign_report.py` — a live-pull results workbook: decisions, per-app coverage, recon "
    "cross-reference.",
])
why(["**The reconciliation is not a campaign.** Running a 19-app attestation every two weeks is "
     "toil, and it asks a human the wrong question. The biweekly review is a *detective* control "
     "against HR truth; campaigns are *human attestation* and stay quarterly. Two controls, "
     "neither replacing the other."])

danger("THE LANDMINE — silent, and it cost one wrong campaign", [
    "Entitlement-level review requires **BOTH** `resourceSettings.includeEntitlements: true` "
    "**AND** each `targetResources[].includeAllEntitlementsAndBundles: true`.",
    "With neither, the campaign is created **happily, with no error**, and produces "
    "app-assignment-level items — detectable only by opening an item and looking. With only the "
    "latter, you get a clean 400.",
])

benefit(["Reviewers see role-level facts; the quarterly UAR and the biweekly detective control "
         "share one evidence model; and results come out as a workbook that cross-references "
         "findings rather than as a screenshot of a queue."])
safe([
    "**Remediation is `NO_ACTION` on every outcome** — approved, revoked, and no-response. Okta "
    "never auto-removes anything; removal is a tracked, verified ServiceNow action.",
    "**Build ≠ execute.** Campaigns notify real people and create real work, so the builder is "
    "dry-run by default, dormant when applied, and launching is a deliberate human act.",
    "**Reviewers are the named Access Management team** (Zyler, Phil). Their manager reviews "
    "nothing, which is the correct segregation, and the project owner's own account is never used "
    "as a reviewer.",
    "`campaign_report.py` prints a permanent note inside the workbook: **REVOKED is a certification "
    "decision, not proof of in-app removal.** The artifact cannot be read as more than it is.",
])

# ─────────────────────────────────────────────── module 8
d.h1("Module 8 · Revoke → ServiceNow → verified closure", page_break=True)
d.p("**State: PROVEN as scripted · DESIGNED for Workflows (Console-only).**", color=TEAL)

d.fig_stack([
    box("CONFIRMED TERMINATION FINDING  /  REVOKE DECISION", accent=STEEL, fill=F_GREY),
    box("OKTA WORKFLOWS",
        "POST /api/sn_sc/servicecatalog/items/{id}/order_now — then record the REQ number against the finding",
        accent=NAVY, fill=F_NAVY),
    box("SERVICENOW BUILDS THE CHAIN",
        "REQ → RITM → SCTASK, with application · account alias · UPN · employee ID · HR status · Okta status · reason · cycle id",
        accent=NAVY, fill=F_NAVY),
    box("ACCESS MANAGEMENT FULFILLER REMOVES ACCESS, CLOSES THE TASK", accent=STEEL, fill=F_GREY),
    box("NEXT CYCLE'S EXPORT SETTLES IT", accent=AMBER, fill=F_AMBER),
])
d.fig_grid(2, [[
    box("ACCESS GONE",
        "BEFORE/AFTER work note — VERIFIED",
        "finding closed", accent=TEAL, fill=F_TEAL),
    box("ACCESS STILL PRESENT",
        "“REMOVAL NOT VERIFIED” note",
        "task REOPENED (state 2), finding ages + escalates", accent=CRIMSON, fill=F_CRIM),
]])

built([
    "**ServiceNow org model:** 2,035 `sys_user` records matching the Okta identities, ~10% managers "
    "per app with every non-manager linked to one, an **Access Management** group, named "
    "fulfillers, and a dashboard (“Access Management — Termination Review”) showing task state and "
    "open counts.",
    "**Ticketing, live and verified:** every finding becomes a REQ → RITM → SCTASK chain with the "
    "terminated person as `requested_for` and full evidence in the RITM variables. Live run: **57 "
    "chains, 0 errors**, independently re-queried out of ServiceNow.",
    "**`closure_writeback()`** — the two-phase closure evidence shown in the figure above.",
    "**The Workflows build** (`OIG_WORKFLOWS_BUILD_GUIDE.md` plus Labs 3–4 of the click-by-click "
    "lab): folder, connection, scheduled trigger, For-Each, role-name→value-id lookup table, "
    "order-now card, response capture, duplicate guard, and an On-Error branch. **Console-only — "
    "no public API builds a Workflows flow** (`/api/v1/workflows`, `/api/v1/flows`, "
    "`/automations/*` all return 405/404).",
])
why(["**Order through the Service Catalog, not a direct table write.** Writing straight into "
     "`sc_request` / `sc_req_item` / `sc_task` was blocked by ServiceNow's own access rules, even "
     "for an account that could read those same tables fine. `order_now` builds the whole chain in "
     "one action and respects the platform's own model.",
     "**Poll on a schedule rather than assume a webhook.** An instance-side outbound REST message "
     "is a valid alternative, but only the scheduled check has been proven here — so the guide says "
     "so, rather than promising a capability nobody tested."])
benefit(["The loop closes with evidence instead of assertion, and it runs unattended: the schedule "
         "card is the entire answer to “how does this kick off with nobody clicking anything” — no "
         "server, no cron, no person remembering it's Monday."])
safe([
    "**The false-closure test is the acceptance criterion.** A ticket was deliberately closed while "
    "the access remained. Result: **11 genuine removals got VERIFIED notes; the 2 planted false "
    "closures were caught, noted “REMOVAL NOT VERIFIED”, and their tasks auto-reopened** — journal "
    "entries confirmed at the database level. *If a flow can be fooled by a closed ticket, it is "
    "not ready to run for real.*",
    "**A broken export can never look like a mass removal.** A truncated export fell below the 50% "
    "sanity ratio and its findings were tagged `[UNVERIFIABLE: export anomaly]` — explicitly not "
    "closed.",
    "**Attestation by screenshot was rejected outright** as proof of record. The evidence is a "
    "before/after work note on the ticket, generated from data.",
    "**The Workflows folder is the unit of permission.** Folder Access Control is restricted "
    "*before* anything is built inside it: once the classifier lives in a flow, **the flow is the "
    "SOX control** and needs the same change-management discipline as production code.",
    "**No token is ever pasted into a card** — the Okta API Connector authenticates as the tenant.",
    "**A silent failure here is worse than doing it by hand**, because the finding exists with no "
    "ticket and nobody knows. Hence the mandatory On-Error notification branch.",
    "Service-account hygiene was practised on the ServiceNow side too: elevated roles were granted "
    "for a specific change and revoked afterwards — with the lesson recorded that you revoke "
    "`admin` *last*, or you strand the roles that depended on it.",
])

# ─────────────────────────────────────────────── module 9
d.h1("Module 9 · The verification discipline that governs all of it", page_break=True)

d.callout("THE STANDING RULE", [
    "**No claim of “seeded / loaded / fixed / complete / good” about tenant state may rest on the "
    "writing script's own logs.** The only acceptable evidence is a fresh run of the matching "
    "`verify_*.py`, which recomputes expected state from the source files, reconciles it against a "
    "live API pull, and ends in a single `VERDICT: PASS|FAIL` line."],
    accent=NAVY, fill=F_NAVY)

d.table(
    ["Gate", "Covers", "Live result"],
    [["`verify_seed.py`", "Users, statuses, app assignments, blast radius", "**PASS** — 5 checks"],
     ["`verify_oauth.py`", "Service app auth, scopes, **and the negative cases**",
      "**PASS** — write 403s, ungranted scope refused"],
     ["`verify_reidentity_tenant.py`", "Zero original name pairings live", "**PASS** — 0 pairings"],
     ["`verify_oig_pilot.py`", "Entitlements, grants and campaign shape on ComSat",
      "**PASS** — 14 checks, *proven able to fail*"],
     ["`verify_mock_drops.py`", "The synthetic drop files feeding the flow design", "PASS"],
     ["`smoke_test.py`", "All 5 subsystems in one run — OAuth, Okta, recon, campaigns, ServiceNow",
      "**PASS**"],
     ["`oig_verify_all.py`", "All 10 apps' grants — highest-privilege contract per principal",
      "**PASS — 10 apps, 0 failures**; `--selftest` fails on all 10"]],
    widths=[24, 46, 30])

safe(["Everything else in this document is machinery; the discipline is what makes the machinery's "
      "output admissible. **A gate that has never been shown to fail is not evidence, and a claim "
      "sourced from the thing that made the change is not verification.** This rule is what caught "
      "the privilege-masking bug in Module 6 *before* it reached a campaign."])

# ─────────────────────────────────────────────── appendices
d.h1("Appendix A · State of the build", page_break=True)
d.table(
    ["Component", "State"],
    [["De-identified data + tenant re-identity", "COMPLETE — gated"],
     ["10 bookmark apps, ~2,035 users, assignments", "COMPLETE — gated"],
     ["10 SAML apps, Entitlement Management enabled on all 10", "COMPLETE — verified live"],
     ["OAuth Detective Control service app", "COMPLETE — gated, including negative cases"],
     ["Biweekly reconciliation, three-way join, digests, evidence workbook",
      "COMPLETE — run live end-to-end"],
     ["ServiceNow org, ticket chains, closure write-back, dashboard",
      "COMPLETE — false-closure test passed"],
     ["Per-app `Role` entitlements ×10", "CREATED"],
     ["Grants",
      "**COMPLETE — highest-privilege-wins contract; 1,069 granted, 37 conflicted principals "
      "corrected, err=0; VERDICT: PASS (10 apps, 0 failures)**"],
     ["Campaigns", "One LIVE (ComSat, 20 entitlement-level items) + one dormant SCHEDULED "
      "(CloudForce HQ, never launched); three-archetype builder still never run"],
     ["Workflows flows",
      "Console-only; documented build guide + labs, with the tested scripts as the reference "
      "implementation"]],
    widths=[46, 54])

d.h1("Appendix B · Reference IDs")
d.table(
    ["Thing", "Value"],
    [["Okta tenant", "`demo-beige-haddock-4684.okta.com` (ORGID `00o159zwmhz6L5eo4698`)"],
     ["Detective Control service app", "`0oa15jbaw6sllCbVB698`"],
     ["Pilot app — BiTerm OIG - NA Saturn ComSat", "`0oa15k4h5x3yZneqN698`"],
     ["Pilot entitlement `Role`", "`esp119gd9dqVhRIdA697`"],
     ["Pilot entitlement campaign", "`ici119gnldxDWagCy697`"],
     ["LIVE campaign — ComSat access certification", "`ici11c29d1yN6cZo9697`"],
     ["DORMANT campaign — CloudForce HQ (never launched)", "`ici11c297d4rUoS5P697`"],
     ["ServiceNow instance", "`dev336362.service-now.com`"],
     ["App manifest (tab → id → roles → EM status)", "`oig_apps.json`"]],
    widths=[38, 62])

d.h1("Appendix C · The open question worth more than the rest of the backlog")
d.p("The campaign `resourceSettings` payload carries a flag named **`includeAllAppServiceAccounts`**. "
    "Its existence implies Okta has a first-class concept of **app accounts not tied to an Okta "
    "user** — which is precisely the **431-orphan** bucket: the largest population in this control, "
    "and the entire reason the external reconciliation exists.")
caution("STATUS — A LEAD, NOT A CAPABILITY", [
    "The flag was **observed in an API response. Nothing more.** It has never been set, never "
    "tested, and no app on this tenant can currently import.",
    "If it works as its name suggests, SCIM onboarding would not merely add *enforcement* — it "
    "could make orphans **governable and certifiable for the first time**.",
    "It must not appear as fact in any management collateral until an app with a real connector "
    "proves it. **Overstating what a control can see is the most expensive mistake available in "
    "this domain.**",
])

d.tail()
d.save(OUT)
print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")
