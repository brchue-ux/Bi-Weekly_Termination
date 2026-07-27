# termination_revamp_v1

This file holds only what's active or standing right now. Every dated "built + verified"
session narrative, every hard-won API gotcha's full story, and the complete history lives in
`CHANGELOG.md` — read it when you need detail on a past decision or script, not by default.

**Session-update convention:** when a session ends, append a dated entry to `CHANGELOG.md`
describing what was done. Only edit THIS file if a standing fact changed — the resume state,
a credential/environment detail, an architecture decision, or an open question. Don't let
"built + verified 2026-0X-XX" narrative accumulate back into this file.

## OIG entitlement rollout — LOADED + VERIFIED (2026-07-26); halted-load defect resolved

**All 10 apps: SAML + EM enabled + per-app `Role` entitlement (each app's own roles, all
`multiValue=False`) + grants loaded per HIGHEST-PRIVILEGE-WINS.** Independently verified:
`scripts/oig_verify_all.py` → **VERDICT PASS (10 apps, 0 failures)**; its `--selftest` fails on all
10 (checker proven falsifiable). Full story in CHANGELOG.md (2026-07-26 entry).

**The multi-account / conflicting-role fix (was the halted defect):** a person can hold several
accounts with different roles in one app; the old first-row-wins loader could grant a lower role
and hide an Administrator — the exact SOX masking the control exists to catch. **Resolved via
Option B+: aggregate every role a person holds, grant the single highest** (Administrator >
Power User > Standard User > Read Only > Service Account). Per-account detail stays in the
reconciliation (two-control split). 136 conflicted principals; 37 needed correction.

**Grant/entitlement mutability (probed live — governs any future change):** grant value can't be
PATCHed/PUT (400); grants can't be individually DELETEd (400); entitlement `multiValue` can't be
flipped in place (400); deleting an entitlement leaves *bare* grants (assignment intact,
`entitlements: []`), doesn't cascade; `DELETE /apps/{id}/users/{uid}` doesn't remove the grant
either. **The only lever is POSTing a value, which REPLACES the principal's current value.** So
corrections are overwrite-in-place; there is no clean grant deletion.

**Campaigns:** `scripts/oig_run_campaigns.py` (entitlement flags + AM-team reviewers Zyler/Phil).
- LIVE + ACTIVE: `BiTerm — Access Certification (LIVE): NA Saturn ComSat` `ici11c29d1yN6cZo9697`
  (20 items = 20 grants, all carry `entitlementValue`).
- DORMANT/SCHEDULED (never launched, +365d): `BiTerm — Access Certification (PREPARED):
  CloudForce HQ` `ici11c297d4rUoS5P697`. IDs also in `oig_run_campaigns.json`.
- `scripts/oig_build_campaigns.py` (build-but-never-launch the 3 archetypes) still exists, NEVER RUN.

**Pagination gotcha (baked into all OIG scripts):** Okta returns TWO `Link` headers;
`headers.get("Link")` grabs `rel="self"` and the pager silently truncates at 200 users. Use
`headers.get_all("Link")`.

**Population truth (correct paging):** 2,048 Okta users; grants across 10 apps; 431 orphans.

**Next (not started): orphan reduction** — `docs/ORPHAN_REDUCTION_PLAN.md` maps real people to the
431 orphans. Measured slices: 145 service accounts, 62 name-match an Okta person, 157 privileged
(do first), 41 active-recent-login, 20 disabled. Highest lever = ask app owners to add
`employee_id` (+ `owner` for service accounts). First slice proposed: 62 name-matches,
privileged-first, cascade → propose → human worksheet → write-back alias → prove count drops.

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
- **Code footprint / what to tell leadership (2026-07-26):** this is NOT a dev-team backend
  build. Campaign creation, reviewer assignment, and launch are **native Admin Console** (the
  scripts only make it repeatable). The only "code" is the entitlement *loader* — needed solely
  because these apps are disconnected/CSV-fed — and it is **disposable scaffolding that SCIM
  retires app-by-app** (a SCIM connector imports accounts+entitlements natively, so the loader
  is deleted per app, not extended). The reconciliation (today's vlookup, upgraded) is
  analyst-owned (Power Query/Power BI or a small script), not a dev project. **The automation
  footprint SHRINKS as SCIM onboards, it does not grow.**
- **Campaign → real remediation is a config flip, not a rewrite:** the same campaign runner
  carries over to a SCIM-connected app; the one change is `remediationSettings` NO_ACTION →
  actual deprovision, which the connector (not new code) enforces. The verifier is reusable as a
  drift check against imported data.

## Verification gate (user-mandated 2026-07-22)

**No "seeded / fixed / complete / good" claim about tenant state may be made from a seeder or
fixer's own logs.** The only acceptable evidence is a fresh run of the relevant `verify_*.py`
script — it recomputes expected state from the source files and reconciles against live API
pulls, ending in a single `VERDICT: PASS|FAIL|INCONCLUSIVE` line. Quote that run's output when
claiming done. `INCONCLUSIVE` is never a pass: it means a check could not be evaluated (rate
limit, truncated read), which a verifier must be able to say rather than manufacture a verdict.

**For control LOGIC (not tenant state), the gate is `python3 tests/run_tests.py`** — green
suite plus every control-rule mutation caught.

**Write guard (learned the hard way 2026-07-26):** every script that writes fails CLOSED when
there is no terminal — non-interactive + no explicit `--yes` = abort, never "proceed
unconfirmed". Absence of a human is a reason to stop. `allow_abbrev=False` everywhere, so no
partial flag is ever guessed into a live run.

## Reference — credentials & environment

- Okta tenant: `demo-beige-haddock-4684.okta.com`, token `~/.secrets/claude_3rd_party.txt`
  (personal admin SSWS — pipeline itself uses OAuth service app, see below).
- OAuth service app "BiTerm Detective Control - Service" (`0oa15jbaw6sllCbVB698`),
  private_key_jwt, key `~/.secrets/term_revamp_oauth_demo_private.pem`. Effective permission =
  granted scopes ∩ admin roles on the client principal (two-layer least privilege — a prod
  access request must ask for both). `scripts/okta_client.py` backs `scripts/biweekly_recon.py` /
  `scripts/campaign_report.py`; `scripts/seed_tenant.py` deliberately stays SSWS (privileged scaffolding, not
  the control).
- ServiceNow: `dev336362.service-now.com`, integration user `biterm.termination` (itil + admin),
  creds `~/.secrets/Service Now.txt`. AM team login shared file:
  `~/.secrets/am_team_demo_logins.txt`.
- **PDI reclaim: 10 days of inactivity WIPES the instance (factory reset).** Only an interactive
  login resets the clock — API activity does NOT. Weekly keepalive: `scripts/pdi_keepalive.py` via
  systemd user timer (`pdi-keepalive.timer`, Mon 09:00); ANY failure pushes to ntfy.sh
  (`biterm-pdi-ea3c383b70d9`). Backstop: ServiceNow's own 10-day warning email.
- Config: `config.json` (git-ignored; template `config.example.json`) or `BITERM_*` env vars
  override the demo-tenant defaults in `scripts/biterm_config.py`. Nothing hardcodes the org.
- Dependencies: `requirements.txt` — PyJWT only, imported lazily, so tests and every
  read-only/`--help` path run on bare system python.
- Data: `App User Lists/` (real de-identified rosters). **System python3 has NO pip and NO
  openpyxl/lxml, and is PEP-668 externally managed** — project scripts run on system python and
  must keep using `scripts/xlsx_min.py` (zip+XML) / `scripts/docx_write.py` (raw OOXML). A separate
  venv `~/.venvs/docs/bin/python` (created 2026-07-26) holds python-docx + openpyxl + lxml, for
  *verifying* generated files and for one-off tooling — never as a runtime dependency of the
  pipeline.
- **Document page counts must be MEASURED, never estimated:**
  `soffice --headless --convert-to pdf --outdir <dir> <f>.docx && pdfinfo <dir>/<f>.pdf`.
  Visual check: `pdftoppm -png -r 100 <f>.pdf out` then view the PNG. LibreOffice + pandoc are
  installed (user, 2026-07-26). **Carlito is required for fidelity** — it's Calibri's metric twin,
  installed user-level in `~/.local/share/fonts` with a `~/.config/fontconfig/fonts.conf` rule
  mapping `Calibri Light`→Carlito; without it LibreOffice substitutes a wider face and reports
  ~15% more pages than Word (19-page doc read as 22). With it, LO matches Word exactly.
  `scripts/docx_estimate.py` is a fast smell test only — it was wrong by 3 pages; don't quote it. Users fully re-identified (no source names survive as pairings —
  `scripts/verify_reidentity_tenant.py` → PASS). **`bchue@wm.com` is the user's own account, PERMANENTLY
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

## Code state (hardened 2026-07-26 — see docs/CODE_REVIEW_2026-07-26.md)

**Shared layer — use these, do not hand-roll:** `biterm_config` (tenant/SN/http settings;
`config.json` + `BITERM_*` env override demo defaults), `biterm_domain` (HR status sets,
privilege order, date parsing, identity keys — pure, unit-tested), `biterm_creds` (0600-checked,
key-based secret parsing), `biterm_http` (**the** HTTP client: timeouts, uniform retry ladder,
typed errors, `get_all("Link")` paging), `biterm_runlog` (logging + `logs/<run>.changes.jsonl`
change record + SHA256 evidence manifests), `oig_common` (the single OIG derivation shared by
loader and verifier). 21 hand-rolled clients and 19 hardcoded org URLs are gone.

- `scripts/okta_bookmark_sync.py` — the `sheet1.xml` parser bug is **FIXED** (uses `xlsx_min`).
  Resolution is fail-closed (a 429/5xx can no longer be read as "user absent" and land someone
  in the removal set); assignments paginate.
- `scripts/run_all.py` — the only entrypoint that can REMOVE access. Blast-radius guard
  (`guard_removals`) + exact-hostname confirmation showing the computed change set.
  Real-tenant runs are executed by the user, not Claude.
- `scripts/biweekly_recon.py` — tickets are idempotent via a deterministic `correlation_id`
  queried in ServiceNow before ordering; `tickets.jsonl` is fsynced per chain and `state.json`
  written atomically, so a crash mid-loop no longer orphans chains. Findings key on a stable
  account identity (a backfilled UPN is no longer a false "verified closure"); prior-state
  matching falls back to the legacy key.
- `scripts/oig_verify_all.py` — three-valued verdict PASS/FAIL/**INCONCLUSIVE**; `--selftest`
  corrupts every check in turn and asserts per app.
- `tests/` — 76 unit tests, stdlib only: `python3 tests/run_tests.py`. It also runs a
  MUTATION pass that breaks each control rule and requires the suite to go red; a surviving
  mutation is reported by name. Green suite + all mutations caught = the rules are covered.
- `scripts/bulk_bookmark_rollout.py` — obsolete (dead sandbox), safe to ignore.
- `UNMATCHED_TRIAGE_PLAN.md` — triage design, plan only.

**Permanent control vs disposable scaffolding.** The loader/seeder/campaign scripts are
scaffolding SCIM retires per app. `biweekly_recon.py`, `feed_ingest.py`, `xlsx_min.py`, the
shared `biterm_*` layer and the cycle ledger are PERMANENT — they are the detective control
that keeps running after SCIM lands. Engineering standard (timeouts, tests, idempotency,
evidence) applies to the permanent half without exception; the shrinking-footprint story is
about the scaffolding, not a licence to hold the control to script standards.

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
