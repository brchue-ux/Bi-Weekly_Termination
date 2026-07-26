# termination_revamp_v1

This file holds only what's active or standing right now. Every dated "built + verified"
session narrative, every hard-won API gotcha's full story, and the complete history lives in
`CHANGELOG.md` — read it when you need detail on a past decision or script, not by default.

**Session-update convention:** when a session ends, append a dated entry to `CHANGELOG.md`
describing what was done. Only edit THIS file if a standing fact changed — the resume state,
a credential/environment detail, an architecture decision, or an open question. Don't let
"built + verified 2026-0X-XX" narrative accumulate back into this file.

## ⚠️ RESUME HERE — OIG rollout IN PROGRESS, halted mid-load 2026-07-24 (correctness stop)

**All 10 apps converted to SAML + EM enabled + entitlements created. Grant load was DELIBERATELY
KILLED partway to fix a real defect. Do NOT just "finish the load" — the loader is wrong for
multi-account users. Read this whole block before touching the tenant.**

**Done + verified this session:**
- `oig_saml_rollout.py` (ran `--apply`): created the 9 remaining apps as custom SAML, label
  `BiTerm OIG - <tab>` (never `BiTerm - `, which the recon filter keys on). ComSat pre-existed
  → 10 total. Re-queried live: all 10 SAML + ACTIVE. Manifest in `oig_apps.json`
  (tab→app_id→app_name→drop→roles→emOptInStatus) — source of truth for the OIG tooling.
- User enabled Entitlement Management on all 10 in the Console (UI-only, as always). Verified
  em=ENABLED on all 10 directly, not on trust.
- Per-app `Role` entitlement created on ALL 10 (values = each app's OWN distinct roles). **These
  are all `multiValue=False` right now.**
- Pagination bug fixed + reusable: Okta returns TWO `Link` headers; `headers.get("Link")` grabs
  `rel="self"` and the pager never advances (silently truncates at 200 users). Use
  `headers.get_all("Link")`. All new OIG scripts do.
- Population truth (from correct paging): 2,048 Okta users; 3,953 resolvable assignments across
  the 9 new apps; 431 orphans. Reconciles across both scripts.

**Why the load was killed (the defect — this is the resume task):**
- Drops have heavy DUPLICATE emails (one person, multiple accounts in the same app): Orion 293
  dup rows, Stellar 539, HQ 9, etc. Grant count therefore = DISTINCT resolvable principals, not rows.
- Worse: **137 emails hold CONFLICTING roles across their rows** (e.g. `umar.hoshino` = Power User
  AND Administrator in NA Orion). The single-value `Role` + first-row-wins loader grants ONE role
  and skips the rest → **it can hide an Administrator behind a Power User.** For a SOX
  privilege-certification control that is exactly the masking the control exists to catch.
  Unacceptable; stopped rather than finish a wrong load.
- **Current tenant state (authoritative, measured post-kill): 2,072 single-value grants exist**
  (interrupted mid-Stellar: Stellar only 240 of ~1,367). Non-conflicted principals (~96%) are
  correct; the 137 conflicted ones may carry the wrong (non-highest) role.

**Unresolved DESIGN DECISION (make this first, next session):** how to represent a person with
multiple accounts / conflicting roles in one app:
- **Option A — multiValue=true `Role`**: grant ALL of a person's distinct roles; reviewer sees
  "Power User, Administrator". Most faithful (textbook IGA). Needs entitlement recreation + reload.
- **Option B+ — single value, HIGHEST-PRIVILEGE wins** (Administrator>Power User>Standard User>
  Read Only>Service Account): never hides privilege; per-account detail stays in the
  reconciliation (per-row). Simpler; fits the two-control split.
- **BLOCKER TO PROBE before choosing:** grant mutability. `DELETE /grants/{id}` → 400 (individual
  grants can't be deleted). So correcting the 137 in place may be impossible → likely must DELETE
  the entitlement (does it cascade its grants? UNTESTED) and reload, OR test PATCH/PUT of
  entitlement `multiValue` and whether a 2nd grant for the same principal+entitlement+different
  value is accepted. Probe these on ONE app before committing.

**Resume steps (in order):**
1. Probe the grant/entitlement mutability APIs above on one app; pick Option A or B+.
2. Rework `oig_load_all.py`: dedupe per principal, AGGREGATE roles per person (set), grant per
   the chosen option. It currently does first-row-wins — that is the bug.
3. Fix `oig_verify_all.py` (written, NEVER RUN — has a latent bug): its coverage check
   `len(expected_distinct_uids) + orphan_ROWS == len(rows)` is WRONG with duplicates, and its
   role check compares a single value, not a set. Rework to compare role SETS per principal.
4. Clean up the 2,072 partial single-value grants + entitlements per the chosen path, then reload
   cleanly and get `oig_verify_all.py` → VERDICT PASS on all 10 (prove it can fail first).
5. Build campaigns: `oig_build_campaigns.py` (written, NEVER RUN). Creates-but-NEVER-launches the
   3 archetypes (per-app entitlement cert ×10, Quarterly UAR over all 10, Flagged-Population user
   campaign from latest cycle), reviewers = AM team Zyler/Phil (NOT bchue@wm.com — off-limits),
   dormant SCHEDULED with a +365d start, both entitlement flags set, remediation NO_ACTION. Run
   `--apply` and confirm 0 ACTIVE. **User instruction: build campaigns + flow but EXECUTE NEITHER.**
6. Flow = Console-only (no API builds Workflows). `docs/OIG_WORKFLOWS_BUILD_GUIDE.md` updated with
   a "generalize to all 10 apps" section. Nothing to run; the scripts are the tested reference.

**Also delivered (not blocked): `docs/ORPHAN_REDUCTION_PLAN.md`** — data-grounded plan to map real
people to the 431 orphans. Measured slices: 145 service accounts, 62 already name-match an Okta
person, 157 privileged (do first), 41 active-recent-login, 20 disabled. Highest lever = ask app
owners to add `employee_id` (+ `owner` for service accounts) to every export.

## Real work deliverable (SOX-controlled)

Biweekly termination/access review across ~19 apps. Every two weeks, verify that everyone with
access in each app is still legitimate per the HR system (TalentHub). This is production
compliance work, not a learning sandbox. **Build authorized 2026-07-22** for seeding
`demo-beige-haddock-4684` with the roster apps/users; the reporting pipeline itself is
authorized beyond seeding (biweekly reconciliation pipeline is built + verified, see below).

## Architecture (settled 2026-07-21, confirmed + extended 2026-07-22)

- **Reconciliation → exception report, NOT an OIG campaign.** Campaigns are human attestation
  workflows; running one biweekly across 19 apps is toil and asks the wrong question. Campaigns
  stay for the separate quarterly UAR.
- **Source of truth is HR employment status, not Okta user status.** Rule: `Active / Paid Leave /
  Unpaid Leave` = access legitimate; `Retired / Terminated` = flag. (Unpaid-leave users are often
  suspended in Okta but legitimately keep app access.)
- **Remediation is always manual** in the real app. The pipeline detects/evidences/tracks and
  confirms closure on the *next* cycle. Control docs must never claim it removes access.
- **Classifier needs three branches with a LOUD unknown** — never default-to-fine.
- **Detective vs. preventive.** The biweekly review is a *detective* control; end-state is two
  separate controls: (a) preventive — HR termination → Okta lifecycle → deprovision at term time
  (SCIM/connector onboarding, per app); (b) detective — the review keeps running, trending toward
  zero findings, evidencing that (a) works. The pipeline itself never removes access.
- **Sequencing (resolved 2026-07-22):** now, Okta is a *data source* (orphan-detection leg)
  feeding an external pipeline. Later, per-app as SCIM/connector onboarding lands, Okta becomes
  the *remediation engine* and that app's manual-removal step drops out; the review runs unchanged.

## Verification gate (user-mandated 2026-07-22)

**No "seeded / fixed / complete / good" claim about tenant state may be made from a seeder or
fixer's own logs.** The only acceptable evidence is a fresh run of the relevant `verify_*.py`
script — it recomputes expected state from the source files and reconciles against live API
pulls, ending in a single `VERDICT: PASS|FAIL` line. Quote that run's output when claiming done.

## Reference — credentials & environment

- Okta tenant: `demo-beige-haddock-4684.okta.com`, token `~/.secrets/claude_3rd_party.txt`
  (personal admin SSWS — pipeline itself uses OAuth service app, see below).
- OAuth service app "BiTerm Detective Control - Service" (`0oa15jbaw6sllCbVB698`),
  private_key_jwt, key `~/.secrets/term_revamp_oauth_demo_private.pem`. Effective permission =
  granted scopes ∩ admin roles on the client principal (two-layer least privilege — a prod
  access request must ask for both). `okta_client.py` backs `biweekly_recon.py` /
  `campaign_report.py`; `seed_tenant.py` deliberately stays SSWS (privileged scaffolding, not
  the control).
- ServiceNow: `dev336362.service-now.com`, integration user `biterm.termination` (itil + admin),
  creds `~/.secrets/Service Now.txt`. AM team login shared file:
  `~/.secrets/am_team_demo_logins.txt`.
- **PDI reclaim: 10 days of inactivity WIPES the instance (factory reset).** Only an interactive
  login resets the clock — API activity does NOT. Weekly keepalive: `pdi_keepalive.py` via
  systemd user timer (`pdi-keepalive.timer`, Mon 09:00); ANY failure pushes to ntfy.sh
  (`biterm-pdi-ea3c383b70d9`). Backstop: ServiceNow's own 10-day warning email.
- Data: `App User Lists/` (real de-identified rosters; `openpyxl` NOT installed — `xlsx_min.py`
  parses xlsx as zip+XML). Users fully re-identified (no source names survive as pairings —
  `verify_reidentity_tenant.py` → PASS). **`bchue@wm.com` is the user's own account, PERMANENTLY
  OFF-LIMITS.**

## API gotchas (OIG entitlements) — needed for the resume work

- App TYPE decides governability: bookmark apps CANNOT be opted into Entitlement Management
  (UI-confirmed "unavailable") — governing them requires re-creating each as SAML/custom, not a
  toggle. `emOptInStatus` is UI-only; no API opt-in endpoint exists.
- This org returns 405 for EVERY unmatched path — 405 proves nothing about existence, only 400
  (validation) and 200 do.
- **Grant shape that works** (`POST /governance/api/v1/grants`): `grantType:"CUSTOM"`,
  `target{externalId:appId,type:APPLICATION}`, `targetPrincipal{externalId:userId,
  type:OKTA_USER}`, `action:"ALLOW"`, `entitlements:[{id:entId,values:[{id:valueId}]}]`.
  `DELETE /grants/{id}` → 400 (cannot delete individually — load-bearing for the resume decision above).
- **Campaign landmine:** entitlement-level review needs BOTH
  `resourceSettings.includeEntitlements:true` AND
  `targetResources[].includeAllEntitlementsAndBundles:true`, or it silently creates
  app-assignment-level items with no error.
- No public API builds Workflows flows — Console-only.
- **UNTESTED LEAD (highest-value open question):** campaign `resourceSettings` carries
  `includeAllAppServiceAccounts` — implies Okta has a first-class concept of app accounts not
  tied to an Okta user (= the 431-orphan bucket). Only testable once an app has a real
  SCIM/provisioning connector. Never repeat as capability — it's a lead, not proven.

Full API-fact list, campaign body schemas, and every proof run: `CHANGELOG.md`.

## Code state

- `okta_bookmark_sync.py` — **known bug: parse_xlsx reads only `sheet1.xml`**, silently empty for
  9 of 10 STARS tabs. Fix before trusting anything downstream (superseded by `xlsx_min.py` for
  new work).
- `run_all.py` — config-driven runner; real-tenant runs are executed by the user, not Claude
  (build/test in sandbox only, user runs against real data).
- `bulk_bookmark_rollout.py` — obsolete (dead sandbox), safe to ignore.
- `UNMATCHED_TRIAGE_PLAN.md` — triage design, plan only.

## Open questions

- Disposition of the 408 "Not found in TalentHub" rows (service accounts? standing exemptions?)
  — see `docs/ORPHAN_REDUCTION_PLAN.md` for the measured breakdown.
- What replaces the TalentHub status join once SailPoint is retired?
- Is ServiceNow in scope (19 vs 18 apps)? Why is the DocuSign-schema file named SFDC?
- Management 2-pager (`docs/BiTerm_Demo_Overview.md`) is DRAFT ONLY — user said "not quite what I
  meant" 2026-07-23; needs a review session on what was off before it's shown to anyone. Don't
  guess at a rewrite.

## Reference docs (read on demand)

`docs/TERM_FLOW_EXPLAINER.md` (before/during/after flow explainer), `docs/BiTerm_Demo_Overview.md`
(management doc, DRAFT), `docs/BiTerm_Demo_Deck.pptx` / `docs/BiTerm_Demo_Runbook.md` (demo
collateral), `docs/OAUTH_SETUP_WALKTHROUGH.md`, `docs/OIG_WORKFLOWS_BUILD_GUIDE.md`,
`docs/OIG_FEASIBILITY_BRIEF.md`, `docs/ORPHAN_REDUCTION_PLAN.md`, `docs/OIG_TERMINATION_LAB.md`.
