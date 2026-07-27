# termination_revamp_v1

## 2026-07-26 (late, after the hardening pass) — code-volume challenge; adoption risk raised

User pushed back on the hardening pass: *"You created 42 scripts. Seems kind of overkill. How
much code did you create? How much does this decrease the likelihood that the team would want to
use it because of how bloated it is for a bi-weekly term process that never had code to begin
with?"* Measured rather than estimated; numbers now in CLAUDE.md so nobody re-guesses them.

### Premise corrected

I did NOT create 42 scripts — 37 `.py` files were already tracked at HEAD. I created **11 new
files** (6 shared modules, 5 test files) and rewrote 11 existing ones.

| | Lines |
|---|---|
| New shared modules (`biterm_*`, `oig_common`) | 1,133 |
| New tests | 880 (never ships) |
| Net change across 11 rewritten files | +1,025 (1,921 added / 896 deleted) |
| **Total contribution** | **~3,038** |

Repo-wide: 5,960 → 10,871 lines of Python. But that total is the wrong unit:

- **Biweekly control: 11 files / 2,580 lines** (~2,090 excluding comments+blanks).
  `biweekly_recon.py` went 392 → **712 lines of code** (+320 real logic, +108 doc/comment).
- **Scaffolding: 35 files / 6,353 lines** — pre-existing OIG/demo/seeding, nobody runs it on a
  cycle, SCIM retires it.
- An analyst runs ONE script. A maintainer reads 11 files.

### The inconsistency I owned

CLAUDE.md says, in the user's own framing from the leadership conversation, that the
reconciliation is *"analyst-owned (Power Query/Power BI or a small script), not a dev project."*
The 2026-07-26 review was explicitly written to a "senior backend engineer on Okta's OIG team"
bar — a product-engineering standard applied to something this project's own architecture says
should not be a dev project. I applied that bar without flagging the mismatch. **Standing rule
added to CLAUDE.md: the bar for this reconciliation is analyst-ownable, not
product-engineering-grade.**

Also conceded: parts of what I added are enterprise plumbing a 10-person IAM team may never need
— `biterm_runlog`'s JSONL change log, `biterm_config`'s env-var coercion, the three-valued
verdict. Defensible for a SOX control; hard to justify to a team whose current process is a
vlookup. And 53 files reads as bloat on sight regardless of how the lines split — adoption is
judged on surface area, which the review pass did not weigh at all.

### Where the pushback does not land

The blocker fixes are small: fail-closed resolution ~15 lines, exact-hostname confirmation 1
line, date parsing ~40. Roughly 100 lines closed a path where a rate limit deprovisions real
people and a path where lapsed exceptions silently pass. The 880 test lines do not ship and are
what let the control's rules be asserted without running a live cycle — the opposite of a burden
for a team inheriting code it did not write.

### Open decision (NOT actioned — user's call)

Recorded in CLAUDE.md Open questions:
1. **RECOMMENDED — split `control/` (11 files) from `scaffolding/` (35 files).** Costs nothing,
   touches no logic, directly addresses what the team sees when they open the repo.
2. Collapse `biterm_runlog`/`biterm_config` into the control — saves ~250 lines and 2 files,
   costs the audit artifact.
3. Question the premise: if the appetite is "the vlookup, but less manual", Power Query + a
   ~200-line classifier is a legitimate answer and this pass is over-engineered relative to it.
   An architecture conversation that should have happened BEFORE the review, not after.

Nothing was refactored in response — the decision is the user's, and options (2) and (3) trade
away capability that was deliberately built.


## 2026-07-26 (late) — Senior-review hardening pass: 8 blockers closed, shared layer, test suite

Acted on every finding in `docs/CODE_REVIEW_2026-07-26.md`. Verified offline: 76 unit tests
green + all 7 control-rule mutations caught (`python3 tests/run_tests.py`), all 42 scripts
compile and import, and `load_rosters`/`load_exceptions`/`classify` reproduce the last recorded
cycle exactly against the real workbooks (4,404 rows, 30 ticket findings, 475 unknowns —
identical to cycle_20260723_163715; only `exception_expired` moves, 20→22, because the run date
moved 07-23→07-26 and two more exceptions lapsed).

### INCIDENT — 29 unintended ServiceNow ticket chains (self-inflicted, during this pass)

A diagnostic command `python3 biweekly_recon.py --create-ticket` was prefix-matched by argparse
to the compat alias `--create-tickets` (apply=True), and the newly written `confirm_apply()`
read `if args.yes or not sys.stdin.isatty(): return` — treating "no terminal" as "no
confirmation needed". It ran a full live cycle and created **29 chains (REQ0010143–REQ0010171 /
RITM0010132– / SCTASK0010190–)** against PDI dev336362 before a 2-minute timeout killed it
mid-loop. They duplicate the 30 chains from cycle_20260723_002630.
`cycles/cycle_20260726_221941/` holds the complete record: `tickets.jsonl` (29 chains, fsynced
per chain), `state.json` (`tickets_live: true`), and `logs/20260727T021939Z-3160483.changes.jsonl`
(116 mutating calls with full request/response). **That cycle must not be used as a closure
baseline** until it is voided or the duplicates are cancelled.

Fixes shipped as a result: writes fail CLOSED with no terminal (abort unless `--yes` is
explicit) on all three write paths; `allow_abbrev=False` on every parser. Regression tests:
`tests/test_safety_guards.py::NonInteractiveLiveRunIsRefused`. The reason the damage is fully
known is the crash-durability machinery built in this same pass — per-chain fsynced ledger,
atomic state, change log — which captured every write.

### Blockers closed

1. **Fail-open resolution → fail-closed.** `okta_bookmark_sync.resolve_user` returned `None` for
   429/500/503 exactly as for 404; `None` → not in `resolved` → into `to_remove` → DELETE under
   `--apply`. A rate limit deprovisioned people. Now FOUND/NOT_FOUND/UNKNOWN, and one UNKNOWN
   aborts before any write.
2. **Blast-radius guard.** `run_all.guard_removals` refuses a removal set above
   max(10, 10% of current) unless `--expect-removals N` matches exactly; identities are printed
   first. An empty parse raises instead of meaning "remove everyone".
3. **The `sheet1.xml` parser is gone** — `okta_bookmark_sync` uses `xlsx_min`, which resolves
   sheets through `xl/_rels/workbook.xml.rels`. App assignments now paginate (was one page of
   500; the comment admitted it).
4. **Confirmation is exact.** `if typed not in org` accepted the single character "o" and the
   empty string. Now `typed != hostname`, shown after the computed add/remove counts.
5. **Ticket idempotency.** Deterministic `correlation_id = sha256(app|key|cls|first_cycle)` is
   stamped on every RITM/SCTASK and queried in ServiceNow BEFORE ordering, so a re-run adopts
   rather than duplicates — the answer survives losing state.json entirely. `tickets.jsonl` is
   appended+fsynced per chain; `state.json` is written atomically before ticketing, every 25
   chains, and at the end. A partial chain raises `PartialTicket` carrying what exists (exit
   code 2) instead of collapsing to "SN-ERROR" and being silently re-ordered next cycle.
6. **Expiries are real dates.** `domain.parse_date` handles ISO, unambiguous slash formats and
   Excel serials, and REJECTS ambiguous D/M vs M/D rather than guessing. A malformed exception
   register is fatal. Root cause was `xlsx_min` ignoring number formats: a date cell arrived as
   "46023" and `"46023" < "2026-07-26"` is False, so lapsed exceptions silently passed.
7. **Exception register read by HEADER NAME**, not `r.get(6)/r.get(7)/r.get(4)`.
8. **Cycle evidence is tracked.** `.gitignore` no longer ignores the ledger; each cycle writes
   `SHA256SUMS` + `evidence_manifest.json` over its inputs and outputs. `feed_ingest` selects the
   exception register by cycle stamp (was `sorted(glob)[-1]`, so re-running an old cycle silently
   used today's register).
9. **Stable finding identity.** Findings key on the app-side account id, then employee id, then
   UPN. Under the old `upn or alias:` key, an app owner backfilling an email — the goal of the
   orphan-reduction workstream — read as a removal: a false "REMOVAL VERIFIED" work note plus a
   duplicate finding. Prior state is still matched via `legacy_identity_key`, so the first cycle
   after the change emits no wall of false closures.

### Structural

- **One HTTP client** (`biterm_http`): timeouts everywhere (exactly one `timeout=` existed in the
  whole repo before), retry on 429/5xx + network errors with Retry-After/X-Rate-Limit-Reset then
  jittered backoff, typed `OktaApiError`/`ServiceNowApiError`/`TransientError`/`AuthError`, and no
  `SystemExit` from library code (that is what killed cycles after tickets existed but before
  state was written). A paginated read that cannot finish RAISES — it used to `break` and return
  a truncated map, which in the loader made already-correct principals look empty and triggered a
  mass re-POST.
- `biterm_config` / `biterm_domain` / `biterm_creds` / `biterm_runlog` / `oig_common` added;
  `seed_tenant` no longer supplies the control's domain vocabulary.
- `oig_common` holds the OIG derivation ONCE. The verifier's independence is now stated honestly:
  it re-reads the tenant and re-derives from the drops, but it does not re-implement the logic —
  copy-pasting it created two places to fix a bug and the illusion of a second opinion.
- **The OIG dry run is a real plan** — current grants were only fetched under `--apply`, so a dry
  run compared against `{}` and reported every principal as "granted".
- A 401/5xx on the app read is no longer rendered as "emOptInStatus=None (enable EM in Console)";
  an app silently dropping out of a compliance load is a coverage gap.
- Unrankable roles raise instead of sorting as -1 (silently lowest privilege — the exact masking
  highest-privilege-wins exists to prevent). Duplicate emails raise: last-wins left one principal
  uncertified.
- **Verifier: PASS / FAIL / INCONCLUSIVE**, and `--selftest` corrupts each of the six checks in
  turn asserting PER APP (it corrupted one check and summed failures run-wide, so one app
  emitting ten failures while nine emitted none still "passed").
- **Campaign entitlement check is structural.** Was `"entitlement" in json.dumps(item).lower()`
  over ≤20 sampled items — which an app-level item satisfies via `"entitlements": []`. Now every
  item must carry a non-empty entitlement value AND the item count must equal the number of
  principals holding a grant.
- `logging` throughout with a run id; `logs/<run>.changes.jsonl` records every mutating call
  (ts, run_id, actor, method, url, status, request, response) — the mutating scripts used to
  leave no artifact at all.
- `expires_in` is read from the token endpoint (was hardcoded 3600); long runs refresh instead of
  401-ing mid-mutation. PyJWT imported lazily and declared in `requirements.txt`.
- `argparse` everywhere with `allow_abbrev=False`; `--apply` is the standard write gate
  (`--create-tickets` kept as a hidden alias). A typo'd flag is now an error — it used to silently
  produce a DRY run, so an operator believed tickets were filed when none were.
- Misc: sheet-name collisions de-duplicated; stage-task sweep paginates; unresolvable Okta
  assignees keep their ids instead of collapsing to "?"; the Okta assignment leg is now reported
  ("assigned in Okta, absent from this export") instead of computed and discarded; ServiceNow
  tickets route to the assignment GROUP by default rather than a named individual.


## 2026-07-26 — advisory: build-vs-native-vs-SCIM framing (for the leadership conversation)

User asked whether any of this needs the dev team ("currently we just use a vlookup"), how much
dev work it really is, and whether the same files extend once an app becomes SCIM-connected with
real remediation. Answer captured as standing architecture in CLAUDE.md; summary:
- **Not a dev-team backend build.** Campaign creation/launch = native Admin Console (scripts only
  add repeatability). Reconciliation = analyst-owned (Power Query/Power BI or a small script),
  the upgraded vlookup. The only "code" is the entitlement *loader*, needed only because the apps
  are disconnected/CSV-fed.
- **The loader is disposable scaffolding SCIM retires** app-by-app (a SCIM connector imports
  accounts+entitlements natively → loader deleted per app, not extended). Automation footprint
  SHRINKS over time, not grows.
- **Same files carry to SCIM:** campaign runner reused ~verbatim — the only change is
  `remediationSettings` NO_ACTION → actual deprovision, enforced by the connector, not new code.
  Reconciliation unchanged by design (detective control keeps running, trends to zero). Verifier
  reusable as a drift check. Loader is the one piece that goes away.
- No-code alternative to the loader = Okta Workflows (low-code cards, admin-owned) — the same path
  the user already rejected as 10× slower, so the small script stays the pragmatic interim.
- Offered a one-page leadership summary; not yet built.

## 2026-07-26 — OIG halted load RESOLVED; all 10 apps loaded + verified; live + dormant campaigns

The 2026-07-24 correctness stop (below) is cleared. Design decision + rework + clean reload +
independent verify + campaigns, all this session, on the disposable demo tenant.

**Grant/entitlement mutability — probed live on NA Saturn Corp (decides everything):**
- `PATCH`/`PUT` a grant's value → 400. Grants are immutable in place.
- `DELETE /grants/{id}` → 400. Grants cannot be individually removed.
- Flipping an entitlement's `multiValue` false→true → 400. Cannot change in place.
- `DELETE` an entitlement → 204, but its grants do NOT cascade — they persist as *bare* grants
  (app assignment intact, `entitlements: []`). `DELETE /apps/{id}/users/{uid}` → 204 but the
  governance grant still persists. So there is no clean way to remove a grant via API.
- **POSTing a grant for an existing principal with a different value REPLACES the value** (proven:
  Standard User → then Administrator → grant shows only Administrator). This is the only lever.

**Design decision: Option B+ — highest-privilege wins (single value).** Chosen because the replace
semantics make it a clean, deletion-free correction, multiValue can't be flipped in place (and its
multi-value capacity couldn't even be confirmed — a POST 502'd), and per-account detail already
lives in the reconciliation per the two-control split. Priority: Administrator > Power User >
Standard User > Read Only > Service Account. Privilege can never be hidden behind a lower role.

**Loader reworked (`oig_load_all.py`):** aggregates every distinct role a person holds per app,
grants the single highest; only re-POSTs when the current value ≠ the winner (idempotent). Applied
run: err=0, granted=1069 (Corp repopulated + Stellar completed from its interrupted 240→1298),
**corrected=37** conflicted principals whose first-row value wasn't the highest (Orion 22, Stellar
12, HQ 2, Central 1), unchanged=2024, conflicted=136 total, orphan=431.

**Verifier reworked (`oig_verify_all.py`):** checks the highest-privilege contract per PRINCIPAL,
fixed the coverage math (was conflating principal-count with row-count → broke on every app with
duplicate rows), reports un-deletable bare grants as WARN not FAIL, and added `--selftest` that
injects a bogus role. `--selftest` → FAIL on all 10 (checker proven falsifiable). Real run →
**VERDICT: PASS (10 apps, 0 failures)**, no bare-grant warnings.

**Campaigns (`oig_run_campaigns.py`, NEW):** entitlement-level flags + AM-team reviewers (Zyler/
Phil; never bchue@wm.com).
- LIVE: `BiTerm — Access Certification (LIVE): NA Saturn ComSat` id `ici11c29d1yN6cZo9697` →
  launched, **ACTIVE**. 20 review items = 20 grants, **0 lacking `entitlementValue`** (roles seen:
  14 Standard User / 4 Read Only / 1 Power User / 1 Administrator). Landmine avoided.
- DORMANT: `BiTerm — Access Certification (PREPARED): CloudForce HQ` id `ici11c297d4rUoS5P697` →
  created only, **SCHEDULED** (start +365d), never launched. IDs in `oig_run_campaigns.json`.

---

## ⚠️ (RESOLVED — see above) RESUME HERE — OIG rollout IN PROGRESS, halted mid-load 2026-07-24 (correctness stop)

**All 10 apps converted to SAML + EM enabled + entitlements created. Grant load was DELIBERATELY
KILLED partway to fix a real defect. Do NOT just "finish the load" — the loader is wrong for
multi-account users. Read this whole block before touching the tenant.**

**Done + verified this session:**
- `oig_saml_rollout.py` (NEW, ran `--apply`): created the 9 remaining apps as custom SAML,
  label `BiTerm OIG - <tab>` (never `BiTerm - `, which the recon filter keys on). ComSat pre-existed
  → 10 total. Re-queried live: all 10 SAML + ACTIVE. Manifest written to `oig_apps.json`
  (tab→app_id→app_name→drop→roles→emOptInStatus) — this is the source of truth for the OIG tooling.
- **User enabled Entitlement Management on all 10 in the Console** (UI-only, as always). Verified
  em=ENABLED on all 10 directly, not on trust.
- Per-app `Role` entitlement created on ALL 10 (values = each app's OWN distinct roles — Corp has
  4, not 5; a shared taxonomy would be wrong). **These are all `multiValue=False` right now.**
- Pagination bug fixed + reusable: Okta returns TWO `Link` headers; `headers.get("Link")` grabs
  `rel="self"` and the pager never advances (silently truncates at 200 users → real users mislabeled
  orphans). Use `headers.get_all("Link")`. All new OIG scripts do.
- Population truth (from correct paging): 2,048 Okta users; 3,953 resolvable assignments across the
  9 new apps; 431 orphans. Reconciles across both scripts.

**Why the load was killed (the defect — this is the resume task):**
- Drops have heavy DUPLICATE emails (one person, multiple accounts in the same app): Orion 293 dup
  rows, Stellar 539, HQ 9, etc. Grant count therefore = DISTINCT resolvable principals, not rows.
- Worse: **137 emails hold CONFLICTING roles across their rows** (e.g. `umar.hoshino` = Power User
  AND Administrator in NA Orion; `ravi.ozturk` = Administrator AND Read Only in Saturn East). The
  single-value `Role` + first-row-wins loader grants ONE role and skips the rest → **it can hide an
  Administrator behind a Power User.** For a SOX privilege-certification control that is exactly the
  masking the control exists to catch. Unacceptable; stopped rather than finish a wrong load.
- **Current tenant state (authoritative, measured post-kill): 2,072 single-value grants exist**
  (interrupted mid-Stellar: Stellar only 240 of ~1,367). Non-conflicted principals (~96%) are
  correct; the 137 conflicted ones may carry the wrong (non-highest) role.

**Unresolved DESIGN DECISION (make this first, next session):** how to represent a person with
multiple accounts / conflicting roles in one app:
- **Option A — multiValue=true `Role`**: grant ALL of a person's distinct roles; reviewer sees
  "Power User, Administrator". Most faithful (textbook IGA). Needs entitlement recreation + reload.
- **Option B+ — single value, HIGHEST-PRIVILEGE wins** (Administrator>Power User>Standard User>
  Read Only>Service Account): never hides privilege; per-account detail stays in the reconciliation
  (which is per-row). Simpler; fits the two-control split.
- **BLOCKER TO PROBE before choosing:** grant mutability. CLAUDE.md already records `DELETE /grants
  /{id}` → 400 (individual grants can't be deleted). So correcting the 137 in place may be
  impossible → likely must DELETE the entitlement (does it cascade its grants? UNTESTED) and reload,
  OR test PATCH/PUT of entitlement `multiValue` and whether a 2nd grant for the same principal+
  entitlement+different value is accepted. Probe these on ONE app before committing.

**Resume steps (in order):**
1. Probe the grant/entitlement mutability APIs above on one app; pick Option A or B+.
2. Rework `oig_load_all.py`: dedupe per principal, AGGREGATE roles per person (set), grant per the
   chosen option. It currently does first-row-wins — that is the bug.
3. Fix `oig_verify_all.py` (NEW, written, NEVER RUN — has a latent bug): its coverage check
   `len(expected_distinct_uids) + orphan_ROWS == len(rows)` is WRONG with duplicates, and its role
   check compares a single value, not a set. Rework to compare role SETS per principal and to
   account rows properly (a principal can own multiple rows).
4. Clean up the 2,072 partial single-value grants + entitlements per the chosen path, then reload
   cleanly and get `oig_verify_all.py` → VERDICT PASS on all 10 (prove it can fail first).
5. Build campaigns: `oig_build_campaigns.py` (NEW, written, NEVER RUN). Creates-but-NEVER-launches
   the 3 archetypes (per-app entitlement cert ×10, Quarterly UAR over all 10, Flagged-Population
   user campaign from latest cycle), reviewers = AM team Zyler/Phil (NOT bchue@wm.com — off-limits),
   dormant SCHEDULED with a +365d start, both entitlement flags set, remediation NO_ACTION. Run
   `--apply` and confirm 0 ACTIVE. **User instruction: build campaigns + flow but EXECUTE NEITHER.**
6. Flow = Console-only (no API builds Workflows). `docs/OIG_WORKFLOWS_BUILD_GUIDE.md` updated with a
   "generalize to all 10 apps" section (one flow + per-app table, role values per app). Nothing to
   run; the scripts are the tested reference the flow mirrors.

**Also delivered (not blocked): `docs/ORPHAN_REDUCTION_PLAN.md`** — data-grounded plan to map real
people to the 431 orphans. Measured slices: 145 service accounts (owner-registry, not identity
mapping), 62 already name-match an Okta person (quick remap wins), 157 privileged (do first), 41
active-recent-login, 20 disabled. Highest lever = ask app owners to add `employee_id` (+ `owner`
for service accounts) to every export — the only join key today is email, which is exactly what's
missing/aliased on the unresolvable accounts. Two horizons: attribute-and-remap now (CSV), SCIM
import later may surface orphans as first-class app accounts (the untested `includeAllApp
ServiceAccounts` lead). Full cascade + closure loop in the doc.

---

Real work deliverable (SOX-controlled): biweekly termination/access review across ~19 apps.
Every two weeks, verify that everyone with access in each app is still legitimate per the HR
system (TalentHub). This is production compliance work, not a learning sandbox.

**Build authorized 2026-07-22** ("build this setup for every single app with each user on the
app in the Okta tenant") — scope: seed `demo-beige-haddock-4684` with the roster apps/users.
The reporting pipeline itself is NOT yet authorized beyond this seeding step.

## Architecture (settled 2026-07-21, confirmed + extended 2026-07-22)

- **Reconciliation → exception report, NOT an OIG campaign.** Campaigns are human attestation
  workflows; running one biweekly across 19 apps is toil and asks the wrong question. Campaigns
  stay for the separate quarterly UAR.
- **Source of truth is HR employment status, not Okta user status.**
  Rule: `Active / Paid Leave / Unpaid Leave` = access legitimate; `Retired / Terminated` = flag.
  (Unpaid-leave users are often suspended in Okta but legitimately keep app access.)
- **Remediation is always manual** in the real app. The pipeline detects/evidences/tracks and
  confirms closure on the *next* cycle. Control docs must never claim it removes access.
- **Classifier needs three branches with a LOUD unknown** — never default-to-fine. Real data has
  70 rows with a person's *name* in the status column; a naive terminated-check passes them silently.
- **Detective vs. preventive (level-set 2026-07-22).** The biweekly review is a *detective*
  control; today it also doubles as the removal mechanism, which is why it hurts. End-state is
  two separate controls: (a) preventive — HR termination → Okta lifecycle → deprovision at term
  time (this is what SCIM/connector onboarding buys, per app); (b) detective — the review keeps
  running and trends toward zero findings, evidencing that (a) works. "100% automation" is not
  one project. The pipeline itself never removes access (see above); automated remediation lives
  in the lifecycle control with its own control testing — never blur the two in control docs.
- **FORK RESOLVED as sequencing (2026-07-22):** now, Okta is a *data source* (orphan-detection
  leg) feeding an external pipeline — small lift, unblocked. Later, per-app as SCIM/connector
  onboarding lands, Okta becomes the *remediation engine* and that app's manual-removal step
  drops out; the review runs unchanged throughout.

## Manual process baseline (user's rundown, 2026-07-22)

1. STARS workbook: tab per app (**12 of 19 apps only**), columns name / UPN / EmployeeID / HR status.
2. Pull a user-list export per app.
3. VLOOKUP app export vs. **exception list** → strips admin accounts (off-scheme naming, absent
   from HR), vendor accounts, maybe orphans. (Fake-data copy of the exception list incoming.)
4. Residual list → separate sheet → script flags who is disabled in HR but still has app access.
5. Manually create an ITSM task per person → manual removal → close task.
6. (Implicit) next cycle re-catches anything not actually removed.

**Exception list (fake, Claude-generated 2026-07-22):** `App User Lists/FAKE USERS - Exception
List.xlsx` — tab per app, keyed by literal identity (Name/UPN/EmployeeID/app alias/type/justification),
so `known_exceptions.json` can be a lookup table, not a rules engine. Populated with the 15 most
cross-tab-present people as the "IT team" (nobody spans all 10 tabs; Saturn Corp has 0 team members
→ empty tab), random 5–15 per app capped by roster presence, seed 20260722. Fidelity caveat: real
exceptions are off-scheme admin accounts absent from HR; these are normal on-roster people, so the
not-in-HR exception case isn't exercised (the 408 rows cover it).

**Process simulation ran 2026-07-22** (throwaway scripts, scratchpad): 4,404 accounts → 55
exception-matched, 3,845 pass, 29 tickets, 475 loud-unknown. **Found an ordering flaw: census has
30 terminated/retired but only 29 ticketed — `avery.gonzalez` (NA Saturn West) is both Terminated
and exception-listed, and the exception match runs first, silently suppressing a terminated
privileged account every cycle.** Design rule: HR status check runs on everyone; exceptions only
excuse accounts that can't be HR-verified — an exception never suppresses a positive termination
hit. **User confirmed 2026-07-22: the real work process does NOT have this flaw** — the verify
script runs on everyone; exception-listed people who terminate do get caught. The rule stands as
a design requirement for the pipeline.

Interim automation (no connectors needed): everything except the physical removal click —
parse STARS, exception matching as the `known_exceptions.json` bucket with naming-scheme rules,
one-pass 3-way join replacing steps 3–4 (plus orphan detection VLOOKUP never gave), auto-create
ITSM tickets via API, auto-verify closure by diffing cycles, auto-assemble the timestamped
evidence workbook. Bonus coverage: pipeline scope = any app with an export, closing the
12-of-19 gap; loud-unknown surfaces the 478 ambiguous rows VLOOKUP passes silently.

## Requirements re-stated by user 2026-07-22

1. Biweekly, 19 apps, verify app access vs HR active status. (Matches reconciliation design.)
2. Report must call out orphaned accounts / accounts with no related Okta account
   (enabled, disabled, or nonexistent) → makes it a **3-way join: app roster ↔ HR ↔ Okta**.
   The Okta leg is new; tenant `demo-beige-haddock-4684.okta.com` is live
   (token `~/.secrets/claude_3rd_party.txt`; `/api/v1/users` and governance APIs verified 200).
3. Risk tiers: low-risk auto-handled, high-risk flagged for manual review. Maps onto
   `UNMATCHED_TRIAGE_PLAN.md`'s three buckets (auto-clear / known_exceptions.json / human review).
4. ~~UNRESOLVED FORK~~ **RESOLVED 2026-07-22 as sequencing** (see Architecture). Original
   tension kept for the record: user wanted "all apps onboarded into Okta so reports run against
   HR + app," but Okta's user schema has zero custom attributes (no HR status to report
   against; the existing HR join comes via SailPoint, which is being retired), and Bookmark-app
   sync is a shadow record. Resolution: data-source now, remediation-engine per app as
   SCIM/connector onboarding lands — not a reporting engine.

## Data — `App User Lists/` (real, de-identified rosters; originals in `.originals/`)

2 of ~13 files landed. `openpyxl` is NOT installed — parse xlsx as zip + sheet XML, placing
cells by their `r` attribute; the `<sheet>` `r:id` is the officeDocument relationship NS.

- `FAKE USERS - STARS Report.xlsx` — 12 sheets, **10 apps (unit of certification = the TAB)**.
  HR status column `TH_EmployeeStatus`. Header row is offset (banner row above).
- `FAKE USERS - SFDC 3rd party user list.xlsx` — 1 sheet, 7,535 rows, DocuSign schema despite
  the name. **No HR status column**; possible join via the STARS `TalentHub - Invalid UPN` tab
  (`TH_BusinessEmail`/`TH_EmployeeID`) — untested.

Parser landmines: `"Not found in TalentHub"` sentinel appears in `TH_UPN` too;
`TH_TerminationDate = 1` means "none" (Excel serial); some Active users have future-dated
termination dates; header offsets differ per file.

Status census (10 STARS tabs, 4,404 rows): 30 clearly terminated/retired vs. **478 ambiguous**
(408 "Not found in TalentHub" + 70 name-in-status defects) — 16:1. Adjudicating "can't tell"
is the control's real cost.

De-identification is incomplete: real names survive (whitespace-after-`\` randomizer bug,
4 real emails in the Invalid UPN tab). Expect the same leaks in the 11 pending files.
STERICORP→MERRYCORP rename done on the STARS file only.

## Verification gate (user-mandated 2026-07-22)

**No "seeded / fixed / complete / good" claim about tenant state may be made from a seeder or
fixer's own logs.** The only acceptable evidence is a fresh run of `verify_seed.py` — it
recomputes expected state from the source xlsx files and reconciles against live API pulls,
ending in a single `VERDICT: PASS|FAIL` line. Quote that run's output when claiming done.

Known data defect fixed 2026-07-22: the SFDC file's FirstName/LastName columns are de-id
scrambled relative to UserEmail (5,449 of 7,513 identities) — canonical profile names derive
from the login local part ("_"→".", split "."; single-token logins get lastName "User").
`fix_profile_names.py` repairs the tenant; seed_tenant.py must never import SFDC name columns.

## Tenant seeding (build authorized + executed 2026-07-22)

**DONE + INDEPENDENTLY VERIFIED 2026-07-22: `verify_seed.py` full run → `VERDICT: PASS` (exit 0)
on all 5 checks — 7,505 users, 8 absent-as-designed, exact statuses, 0 name mismatches, all 11
apps' assignment sets exact (8,622), 18 pre-existing users untouched.**

`seed_tenant.py` + `xlsx_min.py` (verified zip+XML reader; supersedes the buggy parse in
`okta_bookmark_sync.py`). Seeds 11 Bookmark apps (`BiTerm - <tab>` ×10 + `SFDC 3rd Party
(DocuSign)`), 7,505 users (login = UPN/email, fake domain `bitermtest.com` — the user's
"biterm"), ~8,622 assignments. Idempotent (skips existing by login/label/assignment), 429
backoff, resumable; writes `seed_manifest.json` (ids + each identity's fate + HR status).

Deliberate test surface, all deterministic (seed = sha256(login) % 10):
- 409 no-UPN roster rows → no Okta user = app-side orphans.
- Terminated/Retired: ~40% ACTIVE (un-deprovisioned failure mode), ~30% SUSPENDED (9 users),
  ~30% never created (8 users) → covers enabled/disabled/nonexistent branches of req #2.
- 18 pre-existing demo-org users (incl. `bchue@wm.com`) untouched — never delete them.
- SFDC Closed seats (73% of that file) ARE created; seat status is app-side data, not Okta state.

## Biweekly reconciliation pipeline (BUILT + VERIFIED 2026-07-22)

`biweekly_recon.py` — the detective control, end-to-end. Scope: **10 STARS tabs only** (user
2026-07-22: the SFDC 3rd-party file is an obsolete legacy export; Salesforce is covered by the
randomized tabs — its 5,492 seeded Okta users remain, harmless, uncertified). Per cycle under
`cycles/cycle_<ts>/`: report.xlsx (Summary + tab/app, findings sorted first), state.json
(immutable snapshots incl. per-finding source rows), 3 digests (admin/adjudication/ownership —
channels deferred). Flags: `--create-tickets` (default DRY), `--rosters DIR`, `--today`.

Verified behaviors (each proven by test, not assertion):
- Cycle math reconciles to the hand-computed sim: 4,404 rows → 30 tickets / 475 unknowns /
  22 expired exceptions / 4 owner-terminated flags (2 planted + 2 from expired entries — an
  expired exception with a dead owner is legitimately both) / 431 no-Okta orphans.
- Duplicate roster rows collapse to one finding per (app, identity, class) — francis.scott's
  3 seats = 1 ticket. Closure test (doctored rosters): 3 remediated Saturn East rows → verified
  closures; ComSat export truncated below the 50% sanity ratio → findings carried
  `[UNVERIFIABLE: export anomaly]`, NOT closed; 517 carried findings aged to 2 + escalated.
- LIVE ServiceNow run independently verified: 30 REQ/RITM chains queried back out of the PDI
  (count + evidence content match state.json, 0 errors). Ticket bug fixed on the way: DRY/
  SN-ERROR markers count as never-created so a later live run creates them.

## ServiceNow org model + demo flow (2026-07-23)

`sn_seed_users.py`: 2,035 sys_user records (person names = UPN-derived, same canonical rule as
Okta — NOT the sheets' name columns: 835 rows have de-id-desynced names, 408 no-UPN rows get no
record on purpose; sheet spelling survives as the ticket's account_alias variable). ~10%±2
managers per app (hash-jittered), every non-manager linked to a manager. Group **Access
Management** (`20065e70835e8310d89511b6feaad36f`); fulfiller **brandon.chue** (itil,
`2c065e70835e8310d89511b6feaad377`). Landmine: sys_user.user_name truncates at 40 chars —
key lookups on email, which keeps the full login.

Cycle 2 LIVE + verified (REQ0010036–0010065): requested_for = the terminated person, RITM
variables filled, one SCTASK each (the FLOW's task, adopted+retitled — closing it drives the
RITM lifecycle) assigned Brandon Chue/Access Management. Cycle-1's 30 generic-item chains
closed-cancelled with a superseded note. Integration user now also holds pa_power_user/pa_admin
(and admin) — strip when done.

Dashboard BUILT 2026-07-23 (earlier "not REST-buildable" claim was wrong): classic
pa_dashboards creation is gated by property `com.snc.par.coreui.dashboard_create.enabled`
(default false; creating the property needs UI security_admin elevation, but UPDATING an
existing record worked via API). Dashboard "Access Management — Termination Review"
(`908a5ab0839e8310d89511b6feaad3f6`), owner brandon.chue, shared read to Access Management,
two report widgets (state bar + open-tasks list, reports owned by brandon.chue). Chain:
pa_dashboards → pa_tabs → sys_portal_page → sys_portal (dropzone) → sys_portal_preferences
(renderer com.glide.ui.portal.RenderReport, sys_id=<report>). URL:
`/$pa_dashboard.do?sysparm_dashboard=908a5ab0839e8310d89511b6feaad3f6`.
User finished the Next Experience version in the UI 2026-07-23 (this release's PA editor has no
"Report" element — existing reports can't be embedded; rebuilt natively as two Data
visualizations: bar of sc_task by state + Single-score open count, both filtered
assignment_group=Access Management). Both classic and PA views now exist on the same record.

## Closure write-back (built + demoed 2026-07-23)

`closure_writeback()` in biweekly_recon.py — two-phase closure evidence on the tickets
themselves (user's before/after requirement; fulfiller-screenshot attestation explicitly
REJECTED as proof of record): verified disappearance → BEFORE/AFTER work note on the RITM;
task closed but account still in the fresh export → REMOVAL NOT VERIFIED note on task+RITM,
task auto-REOPENED (state 2), finding reason tagged. Same gate as ticket creation
(--create-tickets). Demo cycle_20260723_011419: user closed 13 tasks at random; doctored
rosters removed 11 → all 11 RITMs got VERIFIED notes; riley.wright + avery.gonzalez left in
roster → both tasks reopened with NOT-VERIFIED notes. Journals confirmed via sys_journal_field.
Notes go to **work_notes** (user 2026-07-23; comments-era notes back-filled). Gotcha: the OOB
flow on the item is generic hardware fulfillment — closing a removal task spawns a stage-2
"deploy" task assigned Field Services; `sweep_flow_stage_tasks()` auto-skips those each run
(which also completes the flow → closes RITM/REQ). Real-world fix = purpose-built single-stage
flow (Flow Designer, UI-only). Catalog variables all carry help_text (the ⓘ field icons).
TASK0000001 [Procurement] is OOB PDI sample data — not ours, leave it.

## Governance campaigns + results reporting (2026-07-23)

Three live campaigns on the demo tenant (reviewer = bchue@wm.com so the queue is clickable),
created via `/governance/api/v1/campaigns` + `/launch`, provable-results-only per user
constraint (no remediation claims, no SCIM-dependent proofs, manager-routing skipped —
managerId unset in Okta and fake reviewers prove nothing):
- Targeted resource: "BiTerm — Targeted Resource Review: NA Saturn ComSat" (20 items = exact
  assignment count).
- Quarterly UAR: "BiTerm — Quarterly UAR: Saturn Regional" (392 = 129+133+130; catches
  avery.gonzalez's flagged Saturn West assignment inside a routine UAR).
- User-scoped: "BiTerm — Flagged Population Review (biweekly feed)" — principalScope userIds =
  the cycle's 15 open-ticket identities in Okta (4 absent fates uncertifiable, correctly);
  27 items, 27/27 cross-ref to recon findings.

API gotchas (hard-won): reviewerSettings.type is USER (not REVIEWER); campaignType RESOURCE
silently IGNORES principalScopeSettings.userIds (generated all 3,130 assignments) — user-scoped
needs campaignType USER, whose resourceSettings must be `{type: APPLICATION, targetTypes:
[APPLICATION]}` with NO targetResources; decision enum is UNREVIEWED (not PENDING);
principalProfile carries email not login; ended campaigns stay in listings (filter status).
One mis-scoped campaign was ended, undeletable (409) — ignore it.

`campaign_report.py` — live-pull results workbook (reports/campaign_results_*.xlsx): per-campaign
decisions, per-app coverage, recon cross-reference; carries the explicit note that REVOKED is a
certification decision, not proof of in-app removal.

## Demo collateral + role hygiene (2026-07-23)

`docs/TERM_FLOW_EXPLAINER.md` — raw saved explanation (2026-07-23) of the before/during/after-
Okta flow, control logic, review method, rules, and OIG improvement path; user will trim it
themselves — don't rewrite unasked.

`docs/BiTerm_Demo_Overview.md` — 2–3 page management doc (why + how, results table, path to
production, honest limits). **DRAFT ONLY — user 2026-07-23: "not quite what I meant"; needs a
review session before it's shown to anyone. Do not treat as approved; ask what they actually
wanted rather than guessing at a rewrite.** `docs/BiTerm_Demo_Deck.pptx` — 6-slide deck (problem / principles /
architecture flow / demo proof incl. false-claim catch / campaigns + roadmap), hand-rolled OOXML
via scratchpad `pptx_write.py`/`build_deck.py` (zip+XML validated; if visual tweaks needed the
builder regenerates). Both copied to `~/Shares/Backups/` for Windows access.

Role hygiene EXECUTED: `admin` revoked from biterm.termination (user-approved). Lesson: revoke
admin LAST — losing it first stranded `pa_admin`/`pa_power_user` (harmless, dashboard-scope;
optional UI removal) and left `com.snc.par.coreui.dashboard_create.enabled=true` (optional UI
flip back, needs security_admin elevation). itil day-job verified working post-revoke.

## OAuth service-app auth (enterprise pattern, PROVEN 2026-07-23)

User mandate: conform to corporate/SOX standards, not personal-setup convenience — SSWS
(personal admin token, unscoped, person-bound) is out for the pipeline; OAuth service app is in.

**PROVEN on demo tenant — `verify_oauth.py` → `VERDICT: PASS` (2026-07-23):** app
`0oa15jbaw6sllCbVB698` "BiTerm Detective Control - Service", private_key_jwt
(key `~/.secrets/term_revamp_oauth_demo_private.pem`, kid `biterm-2026-07`), scopes
okta.users.read / okta.apps.read / okta.governance.accessCertifications.read. Proof includes
the negative cases: write POST 403s, ungranted-scope token request refused (consent_required).

**Key discovery — TWO-LAYER least privilege on this org:** scope grants alone yield E0000006;
effective permission = granted scopes ∩ admin roles assigned to the client principal
(`/oauth2/v1/clients/{cid}/roles`). Roles assigned: READ_ONLY_ADMIN +
ACCESS_CERTIFICATIONS_ADMIN. Both layers are load-bearing — a prod access request must ask for
both. Payload gotcha: token_endpoint_auth_method lives under credentials.oauthClient, NOT
settings.oauthClient (400 not-well-formed otherwise).

`oauth_bootstrap.py` = the one-time privileged setup (idempotent; SSWS admin used ONLY here,
mirroring prod where tenant IAM runs it once). `verify_oauth.py` = independent gate.

**Pipeline SWITCHED to OAuth 2026-07-23:** `okta_client.py` (OAuth api/paged, token cached w/
5-min-margin refresh, same signatures as seed_tenant's) now backs `biweekly_recon.py` and
`campaign_report.py`. `seed_tenant.py` deliberately stays SSWS — seeding is privileged
scaffolding, not the control. Verified two ways: (1) equivalence proof — full tenant pull via
both clients diffed, 7,523 users / 10 apps, 0 mismatches, VERDICT: PASS; (2) campaign_report.py
full run under OAuth (all 3 campaigns + workbook). Teaching walkthrough of the whole setup:
`docs/OAUTH_SETUP_WALKTHROUGH.md`.

## Enterprise conformance + full re-identity (IN FLIGHT 2026-07-23, all user-confirmed)

User mandate: tenant must operate like a high-market-cap enterprise tenant, not a dev sandbox.
Four confirmed decisions: (1) Demo Platform Management (vendor app, 46 manage scopes +
SUPER_ADMIN — the Okta demo platform's own provisioning engine) = GOVERN IN PLACE (ownership
register + evidence + compensating controls; the SailPoint-connector analog — never strip a
vendor's scopes, govern them); (2) REMOVE SFDC (DocuSign) app + its 5,478 exclusive users
(overlap-guarded: 14 dual-assigned users survive; all ids manifest-verified); (3) FULL
RE-IDENTITY of BiTerm-scope names — no worksheet name may exist in Okta (Option B: worksheets
rewritten in lockstep); (4) SN person records reseeded to match.

State:
- **Worksheets REWRITTEN + VERIFIED (reidentity_verify.py → VERDICT: PASS, 6 checks):**
  reidentity.py, seed 20260723, 5,157 anchors, international name pool, per-cell mapping
  (desynced off-scheme stems like jphilpott1 keep their own identities + shape), schemes/case/
  padding/defects preserved, deterministic scrub pass for malformed junk cells. Originals +
  map in `App User Lists/.originals/pre_reidentity_20260723/` + reidentity_map_20260723.json.
  Re-run safe (reads originals from backup).
- **SFDC removal RUNNING** (remove_sfdc.py, background; deactivate→delete per user, then app).
- **PENDING:** rename_tenant.py (built — in-place profile rename preserving ids/assignments;
  run AFTER deletion completes to avoid rate-limit contention) → verify (zero forbidden tokens
  live, logins match new sheets) → verify_seed.py update (SFDC expectations out) → SN sys_user
  re-identity → end/relaunch 3 campaigns on new identities → vendor-app governance register
  (System Log evidence pull was 429-starved during deletion; rerun after).
- Cycle history in cycles/ predates re-identity (old names in local snapshots) — treat as
  prior-era; next recon run starts the new baseline.
- **RENAME EXECUTED + gate run 2026-07-23:** rename_tenant.py renamed all 2,027 live seeded
  users (new logins/emails/first/last); verify_reidentity_tenant.py → SFDC APP GONE ok, LOGINS
  RECONCILE ok, SCOPE SANITY ok (18 pre-existing, bchue@wm.com untouched). NO SOURCE NAME check
  FAILED: 2,304 hits — synthetic SURNAME tokens (e.g. Chatterjee) coincide with real SFDC
  surnames because the name pool was filtered vs STARS/exception tokens but NOT the 6,884 SFDC
  tokens. NOT original name-PAIRINGS (0 overlap old/new UPNs) — coincidental shared-vocabulary
  collisions. **User decision: "leave it, the current rename is enough"** — no re-map. Fix if
  ever revisited: union SFDC tokens into build_map's exclusion (pool survives: 138×152=20,976
  combos), then re-rename by okta id (recover anchor via reverse-lookup of current names against
  reidentity_map_OLD_collision.json backup). Stray backup file left in .originals/.
- **Post-rename recon baseline:** cycle_20260723_150739 (dry, OAuth) — 4,404 rows, 30 tickets,
  475 unknowns, 431 orphans; new identities throughout.
- **Campaigns RELAUNCHED 2026-07-23** (launch_campaigns.py, SSWS admin — mgmt needs
  accessCertifications.MANAGE which the least-priv service app lacks by design): ended the 2
  stale ACTIVE, created+launched 3 fresh ACTIVE on new identities — Targeted Resource: ComSat
  (ici118cvoixbFG04g697), Quarterly UAR: Saturn Regional (ici118cvola3sgOXU697), Flagged
  Population (ici118cvovgsMIX25697, 22/30 flagged users resolved — 8 absent/never-created
  correctly uncertifiable). Campaign body schema (mirror an existing one): scheduleSettings
  ONE_OFF + startDate FUTURE + durationInDays + timeZone; remediationSettings all NO_ACTION;
  RESOURCE uses resourceSettings.targetResources[{resourceId,resourceType:APPLICATION}]; end via
  POST /campaigns/{id}/end (202).
- **ServiceNow sync BLOCKED (privilege):** sn_seed_users.py re-run 403 Forbidden on write. The
  role-hygiene step earlier revoked `admin` from biterm.termination (now itil + inherited only);
  creating/deleting sys_user needs user_admin/admin. Reads work. sn_purge_old.py built (deletes
  old-name records email∈old_upns−new_upns, bitermtest only, keeps fulfiller) but also needs the
  role. AWAITING USER: re-grant user_admin/admin in SN UI (elevate-for-change, drop after) then
  run sn_seed_users.py + sn_purge_old.py, OR skip. Old-name sys_user records still present until
  then. New/old UPN sets fully disjoint (2,035 each, 0 overlap).
- **Scheme decision 2026-07-23:** user proposed first-name-column rotation (shift 10) as a
  simpler randomization, then delegated: "whatever is easier, but only if an enterprise would
  consider that sufficient scrubbing." Resolved: rotation REJECTED (permuted real values stay
  re-identifiable — surnames never move; stems survive verbatim); the verified synthetic-pool
  re-identity stands. Rationale also recorded in reidentity.py's build_map docstring.
- **2026-07-23 incident:** user spotted family names (Melissa Chue, Michaela Chue) live in
  Okta — they were in the SOURCE SFDC file (original work de-id missed them) and seeded on
  07-22; both deleted immediately + confirmed 404. **bchue@wm.com = the user's own account,
  PERMANENTLY OFF-LIMITS (explicit: "leave brandon chue alone").** Lesson: the final gate
  (verify_reidentity_tenant.py) scans EVERY live seeded user against the token union of ALL
  source files (STARS + exceptions + 6,884 SFDC tokens, incl. chue/melissa/michaela) — zero
  tolerance; pre-existing count must be exactly 18. No Okta-name claim before that PASS.

## Access Management team (built + verified 2026-07-23)

Demo staff who operate the termination flow for real, in both systems (user: "be able to just
do it"). `setup_am_team_okta.py` + `setup_am_team_sn.py`; shared per-person passwords in
`~/.secrets/am_team_demo_logins.txt` (0600; same pw in both systems; user will simplify).
Bogan Wone (manager, top of line) · Zyler Bawado · Phil Manawan → report to Bogan; demo
fulfiller brandon.chue also reports to Bogan. **bchue@wm.com never touched.**

- **Okta:** 3 ACTIVE users, Read-Only Administrator (governance/dashboards visibility),
  Zyler/Phil managerId→Bogan; the Flagged-Population campaign's review items reassigned to
  Zyler (19) + Phil (20) so they actually certify (reassign body REQUIRES a `note` field).
  am_team_okta.json records ids; verify_reidentity_tenant.py allowlists them (expected
  pre-existing = 18 + AM team).
- **ServiceNow:** 3 users, passwords + login enabled; itil (work/close SCTASKs) + pa_viewer
  (render the "Access Management — Termination Review" dashboard, already group-shared) for all
  three; Bogan = manager (itil to oversee, **0 tickets**). Access Management group membership for
  all. Tickets BOTH ways: 6 existing SCTASKs reassigned + 9 new catalog orders → tasks assigned
  round-robin to Zyler/Phil/Brandon. Final open-ticket counts: Zyler 5, Phil 5, Brandon 16,
  Bogan 0. Async gotcha: the catalog flow spawns the fulfillment SCTASK asynchronously — script
  now polls (6×2s) for it before assigning (first run missed them; fixed up manually +
  in-script).

## SN re-identity sync COMPLETE (2026-07-23)

Admin re-granted to biterm.termination (user did it in SN UI) → writes unblocked. sn_seed_users.py
re-seeded 2,035 new-identity sys_users; sn_purge_old.py deleted all 2,035 old-name records
(email ∈ old_upns−new_upns, bitermtest only, fulfiller kept). Verified: old names (jesse.anderson,
melissa.chue) absent, new names present, 2,037 @bitermtest.com total. DELETE returns empty 204 —
sn_call chokes on it, so sn_purge_old has its own sn_delete().

## Quality backbone (2026-07-23)

- **Re-identity gate refactored to the accepted standard** (verify_reidentity_tenant.py): the
  real guarantee is **0 original name-PAIRINGS** among seeded users (first+last together = the
  re-identifiable unit), computed from ALL sources incl. the SFDC file. Lone first/last token
  overlap with source vocabulary is REPORTED as a note, not failed (unavoidable with a finite
  pool over a 7,779-person source; a common name alone leaks nobody). Live result: **0 pairings
  / 28 first + 38 last token coincidences → VERDICT: PASS.** (Old zero-token-tolerance was wrong
  altitude — perpetual FAIL while the true guarantee held. A gate that always fails is a
  liability.)
- **End-to-end smoke test (smoke_test.py) → VERDICT: PASS** across all 5 subsystems in one run:
  A OAuth (verify_oauth PASS), B Okta state (SFDC 404, 2027 seeded, 0 pairings, AM team ACTIVE),
  C fresh DRY recon cycle runs clean + a sampled ticket UPN (lorenzo.bekele) resolves live
  (new identity), D 3 ACTIVE campaigns + AM fulfillers review the flagged pop, E SN awake +
  new-present/old-absent + AM roles (itil/pa_viewer) + Bogan(mgr)=0 tickets, Zyler/Phil=5 each.
  One-shot health check for the whole system; re-run after any future change. (Note: each run
  leaves a throwaway cycles/cycle_* dir — prune if they accumulate.)

## PDI keepalive (built 2026-07-23) — VERIFIED FACT FIRST

**API activity does NOT reset ServiceNow's 10-day reclaim clock** (verified vs ServiceNow docs +
community 2026-07-23). Only an INTERACTIVE login counts; background jobs, record changes, and
REST calls explicitly do not. So a "ping the instance weekly" script would be pure theater —
green every week while the PDI gets wiped on day 10. Do not build that.

`pdi_keepalive.py` — headless-Chromium **real browser login** to `dev336362.service-now.com/login.do`,
then verifies an authenticated session via `/api/now/ui/user/current_user`. Success = silent
(logged to `pdi_keepalive.log`); ANY failure = **ntfy.sh push** (topic `biterm-pdi-ea3c383b70d9`)
+ exit 1, because a keepalive that fails quietly is worse than none.

- Tooling: system Python 3.14 blocks user pip (`No module named pip`, ensurepip won't stick) →
  **venv at `.venv/`** (playwright 1.61.0 + chromium headless shell). Run via `.venv/bin/python`.
- Schedule: systemd **user** units `pdi-keepalive.{service,timer}` (matches the box's
  market-watch/dashcam-watch convention; crontab is unused). Weekly Mon 09:00, `Persistent=true`,
  `Linger=yes` so it runs while logged out. Next run confirmed by `systemctl --user list-timers`.
- Landmine fixed: `read_creds` originally raised `SystemExit` (a BaseException) which slipped past
  `except Exception` — missing creds would have exited SILENTLY, the exact failure it must alert
  on. Now RuntimeError.
- Failure path proven live: ran with creds absent → log + ntfy push + unit `Result=exit-code`.
- **WORKING 2026-07-23** (`user=brandon.chue` in `~/.secrets/sn_dev_portal_login.txt`, chmod 600):
  manual run and `systemctl --user start` both → `Result=success`, exit 0, success is silent.
  Creds parser accepts `user=`/`password=` OR two bare lines.
- **Credential landmine:** Developer Site creds (brchue@gmail.com) are NOT instance creds —
  ServiceNow's own login error says so ("different than the credentials used to sign in to the
  Developer Site"). Instance login needs a local account; `brandon.chue` + that password works.
- **Two bugs this test exposed (both would have made a WORKING login report failure):**
  (1) verifying via `/api/now/ui/user/current_user` — REST endpoints demand auth headers and
  reject a browser cookie session, so it returned "not authenticated" on a good login. Verify the
  UI session instead: left `login.do`, no "invalid" text, and `/navpage.do` doesn't bounce back.
  (2) `wait_for_load_state("networkidle")` NEVER fires — the post-login workspace `/now/sow` is a
  polling SPA, so networkidle timed out on SUCCESS. Wait for the URL to leave login.do instead.
  (Same SPA trap killed the developer.servicenow.com automation attempt — that portal never
  settles, renders empty to headless, and is SSO/bot-walled: not automatable, don't retry.)
- Residual uncertainty (stated, not hidden): `brandon.chue` is a local demo account, NOT the PDI
  owner's identity, and ServiceNow emphasizes signing in at developer.servicenow.com. So a green
  keepalive is not proof the owner's reclaim clock reset. Backstop = ServiceNow's 10-day warning
  email; if one arrives despite green runs, instance-login alone is insufficient → fall back to a
  manual weekly Developer Site click.

## Mock scheduled drops for the Workflows design (built + gated 2026-07-23)

`bi-weekly term and app list/` — future-state mock feeds for the **Okta Workflows** design
(user 2026-07-23: prod will be UI-only, no CLI; showcase max stack capability to the team).
`make_mock_drops.py` generates, `verify_mock_drops.py` gates → **VERDICT: PASS**.

- **Architecture point (load-bearing): the drops are UNJOINED.** Real STARS arrives with TH_*
  columns beside the app columns — i.e. someone did the app↔HR join before the file existed,
  and that pre-join IS the manual labour. Mocks split it: app export = app-native only
  (account/role/enabled/last-login), HR export = TalentHub standalone, Okta = third leg read
  natively (no file). The gate FAILS if an HR column leaks into an app export.
- Layout: one folder per app (= the unit a SCIM connector later replaces, so migration is
  visible in the filesystem) + `_HR_TalentHub/` + `_reference/` (exception list → Workflows
  Table) + `MANIFEST.json` naming every seeded case with example rows.
- Two cycles: 2026-07-23 (4,404 app rows / 2,035 HR) and 2026-08-06 (4,321 / 2,035).
- Modernised vs. today's file: ISO dates (not Excel serial, no `1` sentinel), mixed-case emails
  so the join must normalise, plus two columns that DO NOT EXIST today and are aspirational —
  `last_login_date` (enables orphan attribution + "used after termination") and `manager_upn`
  (enables manager-routed campaigns). Flag both as asks, not assets.
- Seeded cases all verified individually: 75 roster rows → **64 accounts → 30 people**
  terminated-with-access; 5 privileged; 4 login-after-term; 408 orphans; 207 unjoinable;
  30 malformed HR status; 20 expired exceptions; 4 owner-terminated; 8 with no Okta account.
  Cycle 2: 51 verified closures, **2 named false claims**, 11 aging (counted SEPARATELY — a
  survivor is only a false claim if a task was closed against it), 8 new terminations,
  ComSat 32→12 tripping the 50% sanity ratio.
- **Live-tenant cross-check in the gate: 22/30 flagged identities resolve in Okta, 8 never
  seeded** — independently reproduces the documented Flagged Population 22/30 via a different
  code path. Invariant is "resolves live OR deliberately unseeded", NOT "all resolve":
  absence is a designed test surface here.
- **DEMO LEAD (best finding):** source has 30 terminated people and exactly 30 rows marked
  Terminated, but **25 of those 30 also hold seats on other tabs whose row says "Active"** —
  today's per-tab workbook cannot see them. One authoritative HR feed flags all 64 accounts:
  **~34 extra live seats held by terminated people, surfaced by fixing where truth comes from,
  no new automation required.**
- Landmines hit + fixed (both were real, both caught by the gate not by inspection):
  (1) generating `last_login` from the row-level term date while detecting against the HR
  master fabricated 44 fake post-termination logins — both must read the HR master;
  (2) `okta_client.api` is `api(method, path)` and raises **SystemExit (a BaseException)**, so
  `except Exception` turns a dead credential into a silent SKIP — catch BaseException.
  Gate proven to fail (injected an HR column → FAIL) then pass after regeneration.
- Nothing here feeds `biweekly_recon.py` — that still reads the STARS workbook. Repointing it
  at this shape is a separate deliberate change with its own verification.

## SN person-profile fields (2026-07-23) + a settled schema decision

Turned on (added to `sys_ui_element` for BOTH the **Default view** `5134502bc611227c019dbdc4d7e32319`
and **Workspace** `4c08afb7736013001923054dfff6a7af` sections — neither field was on ANY of the 8
sys_user form sections): `employee_number`, `manager`, `country`. `title` was already on both.

Filled from roster data only (no invented values):
- `employee_number` ← TH_EmployeeID — `sn_backfill_empid.py`, **2,035 updated, independently
  re-queried: 2,035 populated of 2,040 total** (the 5 non-roster records are the AM team +
  brandon.chue); spot-checks match the roster exactly.
- **FINAL VERIFIED COUNTS (independent re-query, 2,040 person records):** employee_number 2,035 ·
  title 1,976 · country 1,965 · manager 1,735. Remaining blanks are the 65 seeded people with no
  row in the HR event log + the 5 staff accounts (AM team + brandon.chue) — explained, not missing.
- **LANDMINE — sys_user.country is a 3-char choice field and PDIs ship most countries
  `inactive=true`.** ServiceNow then SILENTLY drops the value (writing the raw code 'CA' doesn't
  stick either) — first pass left 222 blank while US/GB/BR/ES worked because those choices happened
  to be active. Fix: activate the choice rows for the countries the workforce is actually in
  (`sys_choice` where name=sys_user^element=country, set inactive=false), then re-run. 9 activated:
  AR AU BE CA CL IE MX PT RO. Storage is the ISO code; display_value renders the full name — so
  comparing stored 'US' against roster 'United States' looks like a mismatch but is correct.
- `title` ← TH_JobTitle and `country` ← TH_CountryName — `sn_backfill_profile.py`, from the
  **"TalentHub - Invalid UPN" tab, which despite its name is the HR EVENT LOG and DOES join to
  the seeded population** (8,080 rows join; 1,970 of 2,035 people covered). Multiple rows per
  person → takes the row with the LATEST TH_EventDate so titles are current, not stale. Replaces
  the seed's placeholder title ("Manager"/empty); manager status is properly expressed by the
  reporting hierarchy, not a title string.
- Deliberately left EMPTY (no data): `department`, `location`, `phone`. Inventing them would make
  the demo less credible, not more.
- Both backfills key on **email**, never user_name (truncates at 40 chars).

**DECISION — do NOT add u_hire_date / u_termination_date custom fields (user: "keep as is",
2026-07-23).** We HAVE the data (TH_HireDate/TH_TerminationDate) and sys_user has no OOB home for
it, but the enterprise answer is not to shortcut it: (a) HR employment-lifecycle data belongs in
HRSD's `sn_hr_core_profile` (or the HR system), not duplicated onto sys_user; (b) an unfed copy is
a second source of truth that drifts the moment HR changes — enterprises add fields *because a
feed populates them* (AD/LDAP import, Workday/SuccessFactors, SCIM), never hand-written;
(c) sys_user is broadly readable by itil users, so HR dates there is a data-classification
disclosure with no operational gain; (d) **the audit evidence already lives correctly on the
ticket** as immutable point-in-time variables (hr_status/employee_id/upn/okta_status/cycle_id) —
better evidence than a mutable profile field, and no way for the two to disagree.
The answer only flips if the goal becomes demonstrating the HR-INTEGRATION pattern, which would
require custom fields PLUS a scheduled import/transform map PLUS ACLs — a different, bigger build.
Rationale is architectural, not effort-based; don't relitigate as "it was too much work."

## Feed-mode cycle: BUILT + RUN LIVE end-to-end 2026-07-23

**Okta stack capability CONFIRMED on tenant** (`/api/v1/features`, 15 ENABLED): Workflows
(+ Governance for Workflows, Audit/Revert, Folder Access Control), **Import user entitlements
from CSV** (= disconnected apps become governable in OIG natively from the drop, no SCIM),
OPP Agent w/ SCIM 2.0, On-prem Connector for Generic Databases, Resource + User campaigns,
Access Requests, ML review recommendations. Workflows licensing question is ANSWERED — enabled.

`feed_ingest.py` — **adapter, deliberately not a second pipeline**: reads the unjoined drops and
returns biweekly_recon's exact `(populations, hr_by_upn, exceptions)` shapes, so the verified
classifier/risk-tiers/closure/SN layers are untouched. Models the front half of the Workflows
flow (read → normalise → join). Missing app export = RuntimeError, never an empty app (that
would read as "every account vanished" and hand closure 100% false closures).

`biweekly_recon.py` gains `--feeds DIR [--feed-date YYYYMMDD]`; cycle date = the DROP date, not
the wall clock. **Separate lineage `cycles_feed/`** — STARS-era state keys off a different join,
so sharing it would false-close everything.

**LIVE RUN cycle_20260723_204041 (drop 20260723, all 10 apps):** 4,404 app rows + 2,035 HR rows
→ joined 3,996 / no-HR-match 201 / unjoinable 207 → **57 REQ/RITM/SCTASK chains created, 0
SN errors**, 454 unknowns, 431 orphans, 20 expired exceptions, 4 owner-terminated flags.
Independently reconciled: sc_request 73→130 (+57), open AM tasks 26→74 (=26−9 swept+57),
**57/57 REQ + 57/57 RITM resolve live**, sampled RITM variables carry full evidence.
Ticketed = 30 distinct people (24 still ACTIVE in Okta, 15 SUSPENDED, 18 no Okta account).

**Two real defects found and fixed by this run (both would have shown in the demo):**
1. **DRY runs polluted the lineage.** The rehearsal wrote `state.json` into `cycles_feed/`, so
   the live cycle read it as a prior cycle of record → every finding aged to 2 ("escalated" on
   a first cycle) and the digest said "0 new". Fix: baseline = most recent state with
   `tickets_live` true; a rehearsal is never a cycle of record. Rehearsal dir deleted, the
   cycle of record regenerated from source (not hand-patched).
2. **Report said 75 tickets while ServiceNow had 57.** The summary counted flagged ROWS under a
   "tickets" heading; duplicate seats for one person on one app collapse to one finding = one
   ticket. Fix: separate `ticket rows` and `ticket chains (= ServiceNow REQs)` columns +
   `total["ticket_chains"]`; digest reports chains. 75 rows → 57 chains → 30 people.
- STARS mode regression-checked after the shared-code edits: still 30 tickets, unchanged.

## OKTA OIG ENTITLEMENTS — capability probed + Phase 1 PROVEN on one app (2026-07-23)

Consolidates three earlier passes. Read this section as the authority on OIG entitlements.

### Tenant capability (user enabled ALL features: 69 ENABLED, 5 off)
Relevant + confirmed live: Workflows (+ Governance for Workflows, Audit and Revert, Folder
Access Control), Import user entitlements from CSV, OPP Agent w/ SCIM 2.0, On-prem Connector for
Generic Databases, Resource + User campaigns, Access Requests, ML review recommendations,
Public API Support for Access Certification Campaign Decisions.

### The load-bearing constraint: app TYPE decides governability
- `settings.emOptInStatus` is the Entitlement Management switch: `ENABLED` on the demo
  platform's CRM/FinWorld, `NONE` everywhere else.
- **It is UI-only.** `PUT /api/v1/apps/{id}` with the field changed returns **200 and silently
  ignores it**. `PUT .../features/ENTITLEMENT_MANAGEMENT` → 404 unknown enum (`GOVERNANCE_ENGINE`
  too), while `USER_PROVISIONING` → 400 "Provisioning is not supported" (proves the enum is real
  and those names are not). No opt-in endpoint exists.
- **Bookmark apps CANNOT be opted in — user-confirmed in the Console ("unavailable").** All 10
  `BiTerm - *` apps are bookmarks. Governing them REQUIRES re-creating each as a SAML/custom app.
  A rebuild, not a toggle. This is settled; do not re-litigate.
- Entitlements do NOT require SCIM/provisioning: CRM/FinWorld are plain `SAML_2_0`,
  `features: []`, provisioning unsupported, yet hold real entitlements.
- OIN "Governance with SCIM 2.0" templates (`scim2_oig_basic_auth|_header_auth|_oauth_auth`)
  still come up `emOptInStatus=NONE` and need a REAL SCIM endpoint first — not a sandbox path.

### API facts (hard-won)
- **This org returns 405 for EVERY unmatched path**, so 405 proves nothing about existence. Only
  400 (validation) and 200 prove it. Real: `entitlements`, `grants`, `principal-entitlements`,
  `entitlement-bundles`, `campaigns`, `reviews`, `requests`. NOT proven: `resources`,
  `entitlement-imports`, `imports`.
- ORN: `orn:okta:idp:{ORGID}:apps:{app.name}:{app.id}` with ORGID `00o159zwmhz6L5eo4698`
  (NOT the subdomain). `/governance/api/v1/entitlements` REQUIRES a `filter` param.
- `POST /api/v1/apps` for custom SAML **requires `visibility`** or 400 "Missing visibility".
- Entitlement POST needs `dataType` (**lowercase `string`|`array`**), `externalValue`,
  `multiValue`, `name`, `parent{externalId,type}`, `values[]`. If the app is not opted in it
  fails 404 "Resource not found: null (SharedAppInstance)".
- **Grant shape that works** (`POST /governance/api/v1/grants`): `grantType:"CUSTOM"`,
  `target{externalId:appId,type:APPLICATION}`,
  `targetPrincipal{externalId:userId,type:OKTA_USER}`, `action:"ALLOW"`,
  `entitlements:[{id:entId,values:[{id:valueId}]}]`. Grants carry the VALUE — CRM's app-user
  profile lists entitlement externalValues as attributes but they are null and the custom schema
  is empty, so profile-writing is NOT the mechanism. `DELETE /grants/{id}` → 400.
- **CAMPAIGN LANDMINE (silent, cost me a wrong campaign):** entitlement-level review needs BOTH
  `resourceSettings.includeEntitlements:true` AND
  `targetResources[].includeAllEntitlementsAndBundles:true`. With neither, the campaign is
  created happily and yields **app-assignment-level items with NO error** — detectable only by
  inspecting an item. With only the latter → 400 "includes entitlements ... but
  includeEntitlements flag false".
- **No public API builds Workflows flows** (`/api/v1/workflows`, `/api/v1/flows`,
  `/automations/*` → 405/404). Flow construction is Console-only.

### Phase 1 result — PROVEN on NA Saturn ComSat
`verify_oig_pilot.py` → **VERDICT: PASS** (14 checks), proven able to FAIL (flipped a role in the
drop → mismatch caught → regenerated → PASS).
- App **"BiTerm OIG - NA Saturn ComSat"** `0oa15k4h5x3yZneqN698`, SAML_2_0, emOptInStatus ENABLED
  by the user. Deliberately NOT `BiTerm - ` prefixed so `okta_state()`'s filter cannot pick it up.
- Entitlement `Role` `esp119gd9dqVhRIdA697` + 5 values mirroring the drop's `app_role`.
- Campaign `ici119gnldxDWagCy697` ACTIVE, **20 items each carrying
  `entitlementValue{name, entitlement{name:"Role"}}`** — reviewer certifies "Basim Uchida —
  Role: Standard User", not "has the app".
- Coverage on the 32-row drop: **20 granted + 12 orphans = 32** (5 no email, 7 no Okta user).
- Tooling: `oig_pilot_load.py` (idempotent; refuses to run unless emOptInStatus==ENABLED) and
  `verify_oig_pilot.py` (independent; rebuilds expectations from drop + live tenant). IDs in
  `oig_pilot_{app,entitlement,campaign}.json`.

### Architectural boundary — scoped correctly (earlier "forever" claim was OVERSTATED)
Entitlement GRANTS attach to PRINCIPALS (Okta users), so in the **CSV-fed disconnected model the
431 app-side orphans cannot be represented** — that part is proven and holds today. But the
blanket claim "structurally unrepresentable in OIG, forever" was too strong; see the service
account lead below.

For now: OIG owns certification; the biweekly reconciliation owns HR-status truth, orphan
detection, ServiceNow ticketing and closure verification. Two controls, not one replacing the
other.

### UNTESTED LEAD — `includeAllAppServiceAccounts` (highest-value open question)
The campaign `resourceSettings` payload returned by the API carries:

```json
"includeAllAppServiceAccounts": false
```

alongside `includeEntitlements` and `includeAllEntitlementsAndBundles`. Its existence implies
Okta has a first-class concept of **app accounts NOT tied to an Okta user** — which is exactly
the 431-row orphan bucket, the single largest population in this control and the one the whole
external reconciliation exists to chase.

Reasoning (not proof): such accounts can only populate for apps Okta can IMPORT from, i.e.
provisioning/SCIM-enabled ones — a bookmark or CSV-fed SAML app gives Okta nothing to discover.
If that holds, SCIM onboarding does not merely add enforcement, it may make orphans **governable
and certifiable for the first time**.

**Status: I saw the flag, nothing more. Never set, never tested, no app on this tenant can
currently import.** Do not repeat this as capability — it is a lead. Test it on the first app
that gets a real connector: set the flag true on a campaign over a provisioning-enabled app and
see whether unmatched app accounts generate review items. That single test would materially
change how much of the orphan problem stays with the external pipeline permanently.

### Why the CSV work is not throwaway
The entitlement model, campaigns, reviewers, decision history and evidence trail are IDENTICAL
under CSV and under SCIM — SCIM only swaps the data source underneath them. CSV gets every app
*governed*; SCIM later gets them *enforced*, per app, without remodelling anything. Also the real
CSV-vs-SCIM delta is not just enforcement: CSV makes Okta's picture an **assertion** (cannot be
wrong in a way Okta can detect), SCIM makes it an **observation** (drift surfaces on its own),
which is precisely why the reconciliation is load-bearing in the CSV model and shrinks under SCIM.

## NEXT SESSION — agreed plan (user, 2026-07-23)

1. **Convert the remaining 9 apps to SAML** (same pattern as the ComSat pilot: `POST /api/v1/apps`
   with `visibility`, label `BiTerm OIG - <tab>`, NEVER the `BiTerm - ` prefix). Build a script;
   do not hand-roll 9 times.
2. **USER then enables Entitlement Management on each in the Console** (UI-only, unavoidable).
3. Define `Role` + 5 values per app, load grants from each app's drop, gate each one.
4. Generalise `oig_pilot_load.py` / `verify_oig_pilot.py` from single-app to all-apps.
5. Decide the fate of the 10 old bookmark apps (leave as-is for the recon, or retire) — the
   reconciliation reads them via `okta_state()`, so retiring them is NOT free.
5b. **Carry the `includeAllAppServiceAccounts` lead forward** (see the OIG section). Nothing on
   this tenant can import today, so it stays untestable until an app has a real connector — but
   it is the one open question that could move the 431 orphans inside Okta. Do not let it get
   lost, and do not state it as fact in any collateral until tested.
6. Workflows scheduled flow stays MANUAL (Console-only) — walkthrough in `docs/OIG_WORKFLOWS_BUILD_GUIDE.md`;
   colleague-facing "is this real" briefing in `docs/OIG_FEASIBILITY_BRIEF.md`.


## Remaining to complete
3. **DONE 2026-07-23:** `docs/BiTerm_Demo_Runbook.md` — 5-act live demo script (recon →
   fulfiller works a ticket → manager dashboard → Okta certification → closure/false-claim catch)
   with the real logins, ticket examples, dashboard URL, and talking points. 4. Management
   2-pager rewrite — DRAFT flagged "not what I meant", STILL needs user input on what was off
   (do not guess). 5. Optional weekly PDI keepalive vs the 10-day reclaim.

## Code state

- `okta_bookmark_sync.py` — **known bug: parse_xlsx reads only `sheet1.xml`**, silently empty
  for 9 of 10 STARS tabs. Fix before trusting anything downstream.
- `run_all.py` — config-driven runner; real-tenant runs are executed by the user, not Claude
  (build/test in sandbox only, user runs against real data).
- `okta_oauth.py` — OAuth private-key-JWT done; SSWS removed.
- `bulk_bookmark_rollout.py` — obsolete (dead `integrator-2343242` sandbox).
- `UNMATCHED_TRIAGE_PLAN.md` — triage design, plan only.

## Open questions

- Disposition of the 408 "Not found in TalentHub" rows (service accounts? standing exemptions?).
- ~~Which ITSM~~ Answered 2026-07-22: ServiceNow (REQ → RITM → task). ~~Explicitly out of scope~~
  **REVERSED later 2026-07-22: ServiceNow integration IS wanted** — pipeline auto-creates
  REQ/RITM/tasks, but ONLY for confirmed-without-doubt removals (exact identity match +
  Terminated/Retired). **PDI live + verified 2026-07-22: `https://dev336362.service-now.com`,
  integration user `biterm.termination` (itil role), creds `~/.secrets/Service Now.txt`
  (key=value lines; had CRLF endings once — keep stripped).** Modern-release gotchas: direct
  Table-API inserts into sc_request/sc_req_item/sc_task are ACL-blocked even with itil — create
  via Service Catalog `order_now` (POST /api/sn_sc/servicecatalog/items/{sys_id}/order_now),
  which generates the full REQ→RITM→SCTASK chain; then PATCH details onto the RITM.
  **Proper setup DONE 2026-07-23** (user granted `admin` to biterm.termination): dedicated
  catalog item **"Terminated User Access Removal"** (`b02e8afc839a8310d89511b6feaad3c8`, in
  Service Catalog → Application and Account Access) with 8 variables (application/account_alias/
  upn/employee_id/hr_status/okta_status/reason/cycle_id — table is `item_option_new`, NOT
  sc_item_option_new); pipeline orders it with variables, titles the RITM, and creates the
  SCTASK itself (assignment group Service Desk `d625dccec0a8016700a222a0f7900d06`). Chain proven:
  REQ0010034→RITM0010034→SCTASK0010036. The 30 cycle-1 tickets predate this and ride the generic
  item — history, not worth rewriting. PDI lifecycle (verified vs ServiceNow docs 2026-07-23): **hibernation** = temporary
  sleep, DATA PRESERVED, URL serves an HTML "hibernating" page instead of JSON — wake is
  self-service at developer.servicenow.com, 3–5 min (≤20 max), resets the clock; the idle
  threshold for hibernation is NOT officially published (earlier "~1 day" was community lore,
  not fact). **Reclamation** = the one that WIPES the instance (factory reset + reassigned) at
  **10 days** of inactivity, with an email warning — the real risk to all this seeding/tickets/
  campaigns/AM-team. Mitigation: any login or API hit at least weekly. HTML-instead-of-JSON =
  hibernating, not reclaimed.

## Requirements refined (user word-dump, late 2026-07-22)

- **Ownership of non-human accounts (NEW):** exception list gains `owner` (UPN) + `expiry`;
  classifier branch: exception valid but owner terminated/missing → flag for reassignment.
  Converts the 408 not-in-TalentHub rows into one-time owner adjudication. Okta-side mirror
  (custom `accountOwner` attribute on demo tenant) = later prototype, not load-bearing.
  Orphan *attribution* (last-login → propose owner → confirm/deny) is per-app, only where the
  export carries activity columns (STARS format doesn't; SFDC partially).
- **Risk tiers defined:** auto-clear = HR-legit pass, or unexpired exception w/ living owner
  (HR check always first). Auto-actioned = exact-match Terminated/Retired → auto-create SN
  ticket; human executes removal. High-risk floor = anything requiring judgment: loud unknowns,
  fuzzy joins, expired/owner-dead exceptions, ownerless orphans, privileged accounts flagged for
  any reason. One-liner: certainty + non-privileged = auto; everything else = human.
- **Closure evidence chain:** flag (w/ immutable per-cycle source-row snapshot) → RITM recorded
  → next-cycle verified disappearance; aging + escalation if still present. Guard: a missing or
  suspiciously short export never auto-closes its flags (absence of evidence ≠ removal).
- **Output workbook:** same tab-per-app shape as the real STARS-era report but cleaner; user
  granted formatting freedom ("it actually came out pretty jumbled").
- **Execution boundary clarified:** sandbox = Claude end-to-end; anything corporate (work Okta
  dev OR prod, real HR exports, real ServiceNow) = user-run, no corporate creds to Claude.
- **Notifications (NEW):** pluggable notifier seam, per-cycle digest per audience (not per-event
  spam). Event classes: ticket-created FYI, high-risk adjudication queue, ownership events
  (owner terminated → reassign; expiry approaching → renew-or-lapse), aging/escalation.
  Channels: DEFERRED (user 2026-07-22: skip Teams, no way to demo it). Build = digest files
  only (the exact would-send payloads per audience); a channel plugs into the seam later.
- **Campaign integration (NEW, "seamless"):** pipeline data must feed OIG campaigns on this
  tenant with no re-modeling — quarterly UAR (full-app), targeted resource campaigns (per app),
  user-scoped campaigns (e.g., a cycle's flagged population), and ownership registry as
  campaign reviewer routing. Learning goal: user practices campaign setup here pre-corporate.
- **Exception list v2 REGENERATED + branches TESTED 2026-07-22:** Owner + Expiry columns added
  (same membership/seed as v1; 2 owner-terminated cases planted deterministically — random draw
  produced zero, and an untested branch is an unproven branch). Sim results: 30 tickets
  (HR-first rule catches the terminated+exception-listed avery.gonzalez that v1's ordering
  absorbed), 22 expired-exception flags, 2 owner-terminated reassignment flags, 32 clean
  exception passes, 475 loud unknowns.
- **Follow-on (not this build):** run a quarterly-UAR-style certification campaign on a seeded
  app so the user learns resource/identity campaign setup pre-corporate; bridges to
  app_onboarding_pattern_v1. Keep deprovisioning design in mind — parts resurface there.
- What replaces the TalentHub status join once SailPoint is retired?
- Is ServiceNow in scope (19 vs 18 apps)? Why is the DocuSign-schema file named SFDC?

## End-to-end build lab written (2026-07-26)

`docs/BITERM_END_TO_END_BUILD_LAB.md` — visual, module-by-module record of the whole build
(data/re-identity → apps → users → Detective Control OAuth service → reconciliation →
entitlements+grants → campaigns → revoke→ServiceNow closure loop → verification discipline),
each module carrying BUILT / WHY THIS WAY / BENEFIT / ENTERPRISE-SAFE BECAUSE blocks and 7 mermaid
diagrams. Complements (does not duplicate) `OIG_TERMINATION_LAB.md`, which is the click-by-click
single-app how-to. State honestly tagged per module: grants shown as ⚠️ IN FLIGHT (2,072 partial,
privilege-masking stop), all-apps campaigns as 📐 built-not-executed, Workflows as Console-only
design + tested script reference. Doc only — no tenant or code changes.

### Word edition (2026-07-26)

Markdown edition judged hard to read as a document. Built `scripts/docx_write.py` — a
dependency-free WordprocessingML writer (no python-docx/lxml/pip/pandoc in this environment;
same hand-rolled-OOXML approach as xlsx_write.py) supporting headings, rich inline runs,
banded tables, shaded left-accent callout panels, box-and-arrow figures, title page and a
PAGE-field footer — plus `scripts/build_biterm_lab_docx.py` (content) →
`docs/BiTerm_End_to_End_Build_Lab.docx` (~26 KB, 506 paragraphs, 59 tables). Mermaid diagrams
re-expressed as native Word figures, so no image or rendering dependency.
Validation harness caught two real defects pre-delivery: (1) `<w:contextualSpacing/>` emitted
before `spacing`/`ind` — CT_PPr children are a validated SEQUENCE and Word rejects wrong order;
(2) `**`code`**` left literal backticks because the bold branch didn't recurse. Both fixed;
file now passes zip integrity, XML well-formedness on all 8 parts, 0 child-order violations,
0 table row/gridCol mismatches, 0 leftover markup. NOT visually confirmed — no Word/LibreOffice
on this box; user must eyeball it.

### Doc tooling installed (2026-07-26)

Word file copied to `~/Shares/Backups/` for Windows access (md5 matched after copy).
Tooling: system python3 has no pip and is PEP-668 externally managed, but `ensurepip` works, so
a venv was created at `~/.venvs/docs` with **python-docx 1.2.0, openpyxl 3.1.5, lxml 6.1.1**
(no sudo needed). Used immediately as an INDEPENDENT consumer to re-verify the generated file:
python-docx opened it cleanly — 59 tables, correct core title, Letter/0.75in margins, footer part
present, all 15 module/appendix headings in order. That is third-party confirmation the
hand-rolled OOXML is valid, replacing "my own validator says so".
Pipeline scripts still run on system python and keep xlsx_min.py / docx_write.py — the venv is a
verification + tooling environment, NOT a runtime dependency.
STILL MISSING (needs sudo, user must run): libreoffice-writer for headless render→PDF, which is
the only way to actually EYEBALL generated documents from here; pandoc optional.

### Short editions + doc facts refreshed (2026-07-26)

19-page Word edition judged too long. Built `scripts/docx_estimate.py` — an arithmetic paginator
(no renderer on this box): it flows document.xml through the same font metrics docx_write.py
emits and honours hard breaks. Calibrated against the known 19-page file, where it reports 20 —
~5% conservative, so "estimate ≤ target" is the acceptance condition.
`scripts/build_biterm_summaries_docx.py` → `docs/BiTerm_Build_Lab_6pager.docx` (page 1 contents,
pages 2-6 substance, one hard break per page) and `docs/BiTerm_Build_Lab_1pager.docx`. Estimates:
6 and 1. Three trim passes; facts kept, prose cut.
`docx_write.py` gained `tail()`: a trailing table needs a following paragraph, but the standard
~21pt spacer pushed the 1-pager onto a blank second page — tail() emits a 1pt paragraph instead.
Wired into all three builders.
**All three docs re-synced to the 2026-07-26 tenant state** (the grants section was written while
the load was still halted): Module 6 now PROVEN with the mutability probe table + highest-privilege
fix (1,069 granted / 37 corrected / PASS / --selftest falsifiable), Module 7 carries the LIVE
ComSat + dormant CloudForce HQ campaigns and their IDs, gate table and Appendix A/B updated.
Markdown edition updated to match. All three validated (zip, XML, 0 order violations, 0 table
mismatches, 0 leftover markup) and opened cleanly by python-docx. Copied to ~/Shares/Backups/.
Page counts remain ESTIMATES — no renderer here; install libreoffice-writer to confirm.

### Page counts MEASURED, docs visually verified (2026-07-26)

User installed libreoffice + pandoc, so the estimator was replaced with real measurement.
**First render read 2 / 9 / 22 pages** — the estimator had been wrong by up to 3 pages (it can't
model Word's breaks inside tables). Root cause of part of that gap: **no metric-compatible font** —
Calibri fell back to Noto Sans (wider). Fixed without sudo: `apt-get download
fonts-crosextra-carlito` + `dpkg-deb -x` into `~/.local/share/fonts`, plus a
`~/.config/fontconfig/fonts.conf` rule mapping `Calibri Light`→Carlito. After that LO reports
**1 / 6 / 19** — and 19 exactly matches the user's own Word page count for the full doc, which
validates the render pipeline as a Word proxy.
Both short docs therefore already met their targets; the earlier over-counts were rendering
artifacts, not layout defects.
**Two real visual defects found by actually looking at rasterized pages** (`pdftoppm` → view):
(1) `fig_row`'s `▶` connector rendered as an orange tofu box — now `→`; (2) the 6-pager's reading
key still carried an "IN FLIGHT" tile after the grant load was resolved — now a two-tile key.
Both fixed, rebuilt, re-rendered, re-validated (0 order violations, 0 table mismatches, 0 leftover
markup), and recopied to `~/Shares/Backups/`. `docx_estimate.py` docstring demoted to "smell test,
never quote it"; the measure-don't-estimate workflow is now in CLAUDE.md.
