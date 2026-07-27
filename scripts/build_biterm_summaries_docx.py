#!/usr/bin/env python3
"""Build the two short editions of the build lab:

  docs/BiTerm_Build_Lab_6pager.docx  — page 1 is the contents, pages 2-6 the substance
  docs/BiTerm_Build_Lab_1pager.docx  — the single-page executive version

Both are derived from docs/BITERM_END_TO_END_BUILD_LAB.md / the 19-page Word edition; nothing
here is new fact. Page counts are held by construction (one hard break per page) and checked
with scripts/docx_estimate.py, which runs ~5% long against the known 19-page reference — so an
estimate at or under the target is the acceptance condition.

Usage: python3 scripts/build_biterm_summaries_docx.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from docx_write import Docx, NAVY, STEEL, TEAL, AMBER, CRIMSON, MUTED, BAND

DOCS = Path(__file__).parent.parent / "docs"
F_NAVY, F_TEAL, F_AMBER, F_CRIM, F_GREY = "ECF0F7", "EAF3EF", "FBF2E0", "F8ECEC", BAND


def box(title, *body, accent=NAVY, fill=BAND):
    return ([title] + list(body), fill, accent)


# ══════════════════════════════════════════════════════════ 6-PAGER
d = Docx(title="BiTerm — Build Lab (6-page edition)", creator="Access Management",
         subject="Biweekly termination review on Okta Identity Governance + ServiceNow")

# ── page 1 · contents
d.title_block(
    "BiTerm — End-to-End Build Lab",
    "Biweekly termination review, rebuilt on Okta Identity Governance and ServiceNow. "
    "Six-page edition; the full build record runs to 19 pages.",
    kicker="Okta Identity Governance · ServiceNow · SOX detective control")

d.table(
    ["Page", "Section", "What it answers", "State"],
    [["2", "The system on one page", "How the control loop actually runs", "—"],
     ["3", "Foundations — data, apps, users",
      "What was created in the tenant, and why in that shape", "PROVEN"],
     ["4", "The Detective Control service + the pipeline",
      "What the control runs as, and what it does each cycle", "PROVEN"],
     ["5", "Entitlements, grants, campaigns",
      "How role-level certification was built, and how the privilege-masking defect was cleared",
      "PROVEN"],
     ["6", "Closure loop + verification discipline",
      "How a Revoke becomes a ticket, and how a claim becomes proof",
      "PROVEN · DESIGNED"]],
    widths=[8, 27, 45, 20])

d.h3("Reading key")
d.fig_grid(2, [[
    box("PROVEN", "verified by an independent gate ending in VERDICT: PASS",
        accent=TEAL, fill=F_TEAL),
    box("DESIGNED", "built + tested as a reference, not executed in the tenant",
        accent=STEEL, fill=F_GREY),
]])

d.h3("The two facts that shaped every decision")
d.callout("1 · THE APP TYPE DECIDES WHETHER IT CAN BE GOVERNED AT ALL", [
    "Entitlement Management cannot be switched on for Bookmark apps. `PUT /api/v1/apps/{id}` "
    "accepts the field, returns **200, and silently ignores it**. Governing an app is a "
    "**rebuild as SAML, not a toggle**."], accent=CRIMSON, fill=F_CRIM)
d.callout("2 · A REVOKE DECISION AND A CLOSED TICKET ARE BOTH CLAIMS, NOT REMOVALS", [
    "Campaign remediation is `NO_ACTION` on every outcome, by design. Nothing here removes access: "
    "the control detects, evidences and tickets, then **verifies against the next export** and "
    "reopens the ticket if the access is still there."], accent=CRIMSON, fill=F_CRIM)

d.p("Companion documents: `OIG_TERMINATION_LAB.md` (click-by-click, one app) · "
    "`OIG_WORKFLOWS_BUILD_GUIDE.md` (Console-only flow build) · `OIG_FEASIBILITY_BRIEF.md` "
    "(“is this real?”) · `BiTerm_End_to_End_Build_Lab.docx` (the full 19-page record).",
    color=MUTED, size=18)

# ── page 2 · the system
d.h1("The system on one page", page_break=True)
d.fig_stack([
    box("① SOURCES OF TRUTH",
        "TalentHub HR export · 10 per-app user exports (dated drop) · exception list",
        accent=STEEL, fill=F_GREY),
    box("② DETECTIVE CONTROL SERVICE",
        "Read-only OAuth service app → biweekly_recon.py · three-way join: app roster ↔ HR ↔ Okta",
        accent=NAVY, fill=F_NAVY),
    box("③ FINDINGS + EVIDENCE",
        "report.xlsx · immutable state.json with the source rows · three digests",
        accent=NAVY, fill=F_NAVY),
    box("④ SERVICENOW",
        "One REQ → RITM → SCTASK chain per person, evidence in the request variables",
        accent=NAVY, fill=F_NAVY),
    box("⑤ A HUMAN REMOVES ACCESS AND CLOSES THE TASK",
        "The only step that removes anything. Nothing in this system does it automatically.",
        accent=STEEL, fill=F_GREY),
    box("⑥ THE NEXT CYCLE VERIFIES THE CLAIM",
        "Gone → BEFORE/AFTER evidence note.   Still there → “REMOVAL NOT VERIFIED”, task REOPENED.",
        accent=TEAL, fill=F_TEAL),
])
d.fig_caption("The arrow that matters is ⑥ back onto ④. Everything above it is detection; that "
              "step is what makes the control trustworthy.")

d.h2("Running alongside it — the governance lane")
d.fig_row([
    box("SAML app", "EM enabled", accent=NAVY, fill=F_NAVY),
    box("Entitlement", "“Role”", accent=NAVY, fill=F_NAVY),
    box("Grant", "person → value", accent=NAVY, fill=F_NAVY),
    box("Campaign", "reviewer certifies", accent=NAVY, fill=F_NAVY),
    box("Decision", "Revoke → ticket", accent=TEAL, fill=F_TEAL),
])
d.p("**Two controls, neither replacing the other.** The biweekly review is a *detective* control "
    "against HR truth and runs every two weeks; campaigns are *human attestation* and stay "
    "quarterly. Running a 19-app attestation fortnightly would be toil, and it asks a person the "
    "wrong question.")

# ── page 3 · foundations
d.h1("Foundations — data, apps, users", page_break=True)
d.p("**State: PROVEN.**", color=TEAL)

d.h2("The data, and making it safe to use")
d.p("Every person in scope was re-identified against a synthetic name pool (5,157 anchors, "
    "deterministic seed), with naming schemes, case, padding and the source data's **defects** "
    "preserved — the defects are what the pipeline has to survive. Rotating the first-name column "
    "was rejected: a permutation of real values is still real values, since surnames never move "
    "and login stems survive verbatim.")
d.callout("SAFE BECAUSE", [
    "The gate enforces the guarantee that matters — **zero original first-plus-last pairings** "
    "among seeded users, the pairing being the re-identifiable unit. Live: **0 pairings → PASS**.",
    "Lone token coincidences over a 7,779-person source are a note, not a failure. A gate that "
    "always fails is a liability nobody reads.",
    "Driven by a real incident: two family names from the *source* file were found live in Okta, "
    "deleted within minutes, confirmed 404.",
], accent=NAVY, fill=F_NAVY, bullets=True)

d.h2("The apps — one roster tab, two Okta apps")
d.fig_grid(2, [[
    box("BOOKMARK — “BiTerm · <tab>”", "read by okta_state() for orphan detection",
        "cannot ever hold entitlements", accent=STEEL, fill=F_GREY),
    box("SAML — “BiTerm OIG · <tab>”", "Entitlement Management ENABLED",
        "holds the Role entitlement + grants", accent=TEAL, fill=F_TEAL),
]])
d.p("The prefixes are **deliberately disjoint**, so governing an app cannot silently change what "
    "the detective control sees. 10 governable SAML apps exist, all re-queried live as ACTIVE; the "
    "rollout script is idempotent and deliberately does **not** enable Entitlement Management — "
    "that stays a human action in the Console, per app.")

d.h2("The users — a deliberate test surface")
d.table(
    ["Planted condition", "Count", "What it exercises"],
    [["Roster rows with no UPN", "409", "App-side **orphans** — no Okta account to join to"],
     ["Terminated, still ACTIVE in Okta", "~40% of terms", "The un-deprovisioned failure mode"],
     ["Terminated, SUSPENDED", "~30%", "Unpaid-leave vs. terminated ambiguity"],
     ["Terminated, never created", "~30%", "The “no Okta account at all” branch"],
     ["Pre-existing demo-org users", "18", "Blast-radius check — must stay untouched"]],
    widths=[34, 16, 50])
d.p("A clean population proves nothing — all three branches had to exist before the classifier "
    "could be trusted to tell them apart. `verify_seed.py` recomputes expected state from the "
    "source files and reconciles it against a live pull: **PASS on all 5 checks**.")

# ── page 4 · service + pipeline
d.h1("The Detective Control service, and the pipeline it runs", page_break=True)
d.p("**State: PROVEN.**", color=TEAL)

d.h2("What the control runs as")
d.fig_grid(2, [[
    box("LAYER 1 — GRANTED SCOPES", "okta.users.read · okta.apps.read",
        "okta.governance.accessCertifications.read", accent=NAVY, fill=F_NAVY),
    box("LAYER 2 — ADMIN ROLES ON THE CLIENT", "READ_ONLY_ADMIN",
        "ACCESS_CERTIFICATIONS_ADMIN", accent=NAVY, fill=F_NAVY),
]])
d.fig_stack([
    box("EFFECTIVE PERMISSION = LAYER 1 ∩ LAYER 2",
        "Scopes alone yield E0000006. Both layers are load-bearing — a prod access request must ask for both.",
        accent=AMBER, fill=F_AMBER),
])
d.p("An OAuth service app using **private_key_jwt** — no client secret exists to leak. The "
    "pipeline previously ran on a personal admin SSWS token; that was rejected because it is "
    "**person-bound, unscoped, and dies with the person's account**, so it cannot pass a SOX "
    "access review of the control itself.")
d.callout("SAFE BECAUSE", [
    "**Read-only by construction**, proven by a 403 in the gate rather than asserted by policy. A "
    "detective control that cannot mutate what it inspects cannot destroy its own evidence.",
    "**Privilege separation:** seeding and campaign *management* stay on the admin token — they "
    "are privileged scaffolding, not the control.",
    "Verified two ways: negative cases in `verify_oauth.py` (write 403s, ungranted scope refused), "
    "and an equivalence proof — a full tenant pull through both clients, **0 mismatches**.",
], accent=NAVY, fill=F_NAVY, bullets=True)

d.h2("What it does each cycle")
d.fig_grid(3, [[
    box("ACTIVE / PAID / UNPAID LEAVE", "access legitimate", accent=TEAL, fill=F_TEAL),
    box("RETIRED / TERMINATED", "FINDING → ServiceNow", accent=CRIMSON, fill=F_CRIM),
    box("CANNOT DETERMINE", "LOUD UNKNOWN → adjudication", accent=AMBER, fill=F_AMBER),
]])
d.p("**Three rules, each bought with a real defect.** (1) An exception never suppresses a positive "
    "termination hit — one identity was both Terminated *and* exception-listed, and "
    "exception-matching first suppressed a terminated privileged account **every cycle**. (2) The "
    "unknown branch is loud: 30 clearly-terminated against **478 ambiguous** rows, 16:1 — "
    "adjudicating “can't tell” *is* the control's real cost. (3) A missing export is an error, "
    "never an empty app, which would read as “every account vanished” and hand closure a 100% "
    "false-closure rate.")
d.p("**Live run, all 10 apps:** 4,404 app rows + 2,035 HR rows → 3,996 joined / 201 no-HR-match / "
    "207 unjoinable → **57 REQ/RITM/SCTASK chains, 0 errors**, 454 unknowns, 431 orphans — then "
    "independently re-queried out of ServiceNow, 57/57 resolving live. Coverage went from **12 of "
    "19 apps to any app with an export**.")

# ── page 5 · entitlements, grants, campaigns
d.h1("Entitlements, grants, campaigns", page_break=True)
d.p("**State: PROVEN — grants loaded and gated; one campaign LIVE, one dormant.**", color=TEAL)

d.h2("Role-level certification")
d.fig_row([
    box("Entitlement", "“Role”", accent=NAVY, fill=F_NAVY),
    box("Values", "Standard · Read Only · Power",
        "Administrator · Service Account", accent=NAVY, fill=F_NAVY),
    box("Grant", "principal → value", accent=NAVY, fill=F_NAVY),
    box("Reviewer sees", "“— Role: Power User”", accent=TEAL, fill=F_TEAL),
])
d.p("Entitlements do **not** require SCIM — plain SAML apps hold them, which is what makes "
    "governing 10 disconnected apps possible at all. Certification becomes the question an auditor "
    "actually asks: **“should this person still be an Administrator?”**")

d.callout("THE PRIVILEGE-MASKING DEFECT, AND HOW IT WAS CLEARED", [
    "**The defect:** **136 identities hold conflicting roles** across duplicate accounts, and a "
    "first-row-wins loader can grant *Power User* while dropping **Administrator** — the exact "
    "masking this control exists to catch. The load was killed mid-run rather than finished.",
    "**The constraint (probed live first):** grants cannot be PATCHed or DELETEd (both 400) and "
    "`multiValue` cannot be flipped — **the only lever is re-POSTing a value, which replaces it.**",
    "**The fix — highest privilege wins.** Reload: **err=0, 1,069 granted, 37 conflicted principals "
    "corrected**; verifier **PASS, 0 failures**, `--selftest` fails on all 10 — proven falsifiable. "
    "**Stopping was the control working, not the project slipping.**",
], accent=CRIMSON, fill=F_CRIM, bullets=True)

d.h2("Campaigns")
d.table(
    ["Campaign", "State", "Proof"],
    [["Access Certification: ComSat", "**LIVE / ACTIVE**",
      "20 items = 20 grants, **0 lacking the entitlement value**"],
     ["Access Certification: CloudForce HQ", "SCHEDULED, never launched",
      "Dormant at +365d — preparing and starting are separate acts"],
     ["Quarterly UAR · Flagged Population", "Builder never run",
      "Earlier proof: 392-item UAR; 27/27 flagged items tie to recon findings"]],
    widths=[26, 22, 52])
d.callout("SAFE BECAUSE", [
    "Remediation is `NO_ACTION` on every outcome; **build ≠ execute**. Reviewers are the named "
    "Access Management team; their manager reviews nothing.",
    "**The landmine:** entitlement-level review needs BOTH `includeEntitlements` AND "
    "`includeAllEntitlementsAndBundles`. With neither, the campaign is created **happily, with no "
    "error**, at app-assignment level. It cost one wrong campaign.",
], accent=NAVY, fill=F_NAVY, bullets=True)

# ── page 6 · closure + discipline
d.h1("Closure loop, and the discipline that makes it evidence", page_break=True)
d.p("**PROVEN as scripted · DESIGNED for Workflows (Console-only — no API builds a flow).**",
    color=TEAL)

d.fig_stack([
    box("CONFIRMED TERMINATION  /  REVOKE DECISION", accent=STEEL, fill=F_GREY),
    box("ORDER FROM THE SERVICE CATALOG",
        "order_now — direct writes to sc_request/sc_task are blocked by ServiceNow's own rules",
        accent=NAVY, fill=F_NAVY),
    box("REQ → RITM → SCTASK, EVIDENCE IN THE VARIABLES",
        "app · account alias · UPN · employee ID · HR + Okta status · reason · cycle id",
        accent=NAVY, fill=F_NAVY),
    box("FULFILLER REMOVES ACCESS, CLOSES THE TASK", accent=STEEL, fill=F_GREY),
])
d.fig_grid(2, [[
    box("NEXT EXPORT: ACCESS GONE", "BEFORE/AFTER work note — VERIFIED", accent=TEAL, fill=F_TEAL),
    box("NEXT EXPORT: STILL PRESENT", "“REMOVAL NOT VERIFIED” + task REOPENED",
        accent=CRIMSON, fill=F_CRIM),
]])
d.callout("THE ACCEPTANCE CRITERION — the false-closure test", [
    "A ticket was deliberately closed **without** removing the access. Result: **11 genuine "
    "removals got VERIFIED notes; the 2 planted false closures were caught, noted “REMOVAL NOT "
    "VERIFIED”, and their tasks auto-reopened** — journals confirmed at the database level.",
    "*If a flow can be fooled by a closed ticket, it is not ready to run for real.* A truncated "
    "export is tagged `[UNVERIFIABLE: export anomaly]` — **a broken export must never look like a "
    "mass removal.**",
], accent=CRIMSON, fill=F_CRIM, bullets=True)

d.h2("The verification discipline")
d.p("**No claim about tenant state may rest on the writing script's own logs.** The only "
    "acceptable evidence is a fresh `verify_*.py` run that recomputes expected state from the "
    "source files, reconciles it against a live API pull, and ends in one `VERDICT` line.")
d.table(
    ["Gate", "Live result"],
    [["`verify_seed.py` — users, statuses, assignments, blast radius", "**PASS** — 5 checks"],
     ["`verify_oauth.py` — scopes **and the negative cases**", "**PASS** — write 403s"],
     ["`verify_reidentity_tenant.py` — zero original name pairings", "**PASS** — 0 pairings"],
     ["`oig_verify_all.py` — highest-privilege contract, all 10 apps",
      "**PASS — 0 failures**; `--selftest` fails on all 10"]],
    widths=[58, 42])
d.p("**A gate that has never been shown to fail is not evidence.** That rule is what caught the "
    "privilege-masking defect before it reached a campaign.", color=MUTED, size=19)

d.tail()
d.save(DOCS / "BiTerm_Build_Lab_6pager.docx")
print("wrote 6-pager")


# ══════════════════════════════════════════════════════════ 1-PAGER
o = Docx(title="BiTerm — Termination Review, One Page", creator="Access Management",
         subject="Executive summary of the biweekly termination review build")

o.title_block(
    "BiTerm — Biweekly Termination Review",
    "Detective control on Okta Identity Governance + ServiceNow.",
    kicker="Executive summary · 2026-07-26")

o.fig_stack([
    box("HR + 10 APP EXPORTS → THREE-WAY JOIN (roster ↔ HR ↔ Okta) → FINDINGS → "
        "SERVICENOW REQ/RITM/SCTASK → A HUMAN REMOVES ACCESS", accent=NAVY, fill=F_NAVY),
    box("THE NEXT CYCLE VERIFIES THE CLAIM — gone → evidence note · still there → REOPENED",
        accent=TEAL, fill=F_TEAL),
])

o.h2("What it does that the spreadsheet process could not")
o.table(
    ["", "Before", "Now"],
    [["Coverage", "12 of 19 apps", "Any app with an export"],
     ["Orphan accounts", "Invisible", "**431** surfaced every cycle"],
     ["Ambiguous rows", "Passed silently", "**478** raised for adjudication"],
     ["Ticketing", "Manual, per person", "**57 chains, 0 errors** last run"],
     ["Certification", "“Has the app”", "“Holds **Administrator**”"]],
    widths=[18, 30, 52], size=18)

o.callout("THE TWO THINGS TO UNDERSTAND", [
    "**Nothing here removes access.** A Revoke and a closed ticket are both *claims*; the control "
    "settles them against the next export — proven by a false-closure test: 2 planted false "
    "closures caught and reopened, 11 genuine ones evidenced.",
    "**It runs as a read-only service identity** — write access proven absent by a 403 in its gate.",
], accent=NAVY, fill=F_NAVY, bullets=True)

o.table(
    ["Where the build stands", "State"],
    [["Tenant data, 10 governable SAML apps, ~2,035 users", "COMPLETE — gated"],
     ["Reconciliation, ServiceNow chains, closure verification", "COMPLETE — live"],
     ["Role entitlements + grants, all 10 apps", "**COMPLETE — PASS, 0 failures**"],
     ["Certification campaigns", "One **LIVE**, one dormant"]],
    widths=[62, 38], size=18)

o.callout("THE DEFECT THIS BUILD CAUGHT IN ITSELF", [
    "The grant load was **killed mid-run on purpose**: 136 identities hold conflicting roles across "
    "duplicate accounts, and a first-row-wins loader can hide an **Administrator** behind a *Power "
    "User* — the exact masking this control exists to catch. Fixed by granting each person their "
    "**highest** role: 1,069 granted, 37 corrected, **VERDICT PASS**. Stopping was the control "
    "working."],
    accent=AMBER, fill=F_AMBER)

o.tail()
o.save(DOCS / "BiTerm_Build_Lab_1pager.docx")
print("wrote 1-pager")
