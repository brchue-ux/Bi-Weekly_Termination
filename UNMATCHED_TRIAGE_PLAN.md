# Unmatched-Identifier Triage — Plan/Scope (no code yet)

## Problem
Every biweekly cycle, `resolve_user()` fails to match some roster identifiers
to an active Okta user. Today ALL of them dump into one flat "Unmatched"
list that a human re-reads in full, every cycle, even though most entries
are the same recurring, already-understood cases (a service account that's
never in Okta, a vendor alias, a user who already left). The goal is to
shrink the list a human actually has to look at, without weakening the
control.

## Non-goals (explicitly out of scope for this piece)
- Does NOT touch `--apply` / the assign-unassign write path at all. Unmatched
  identifiers are already excluded from `resolved`, so they're already
  excluded from the diff — that behavior is unchanged.
- Does NOT auto-suppress anything without either (a) provable Okta ground
  truth (deprovisioned status) or (b) a human-authored exception entry.
  Nothing is inferred/ML-guessed into silence.
- Does NOT replace the human review step — it narrows what reaches it.
- Not building the OIG campaign or ServiceNow remediation loop here — separate
  pieces, tracked separately.

## Design: three-bucket triage, run only on the existing unmatched list

Input: the `unmatched` list `okta_bookmark_sync.py`/`run_all.py` already
compute today. Output: same list, partitioned + annotated, printed instead
of (or alongside) the current flat dump.

### Bucket 1 — Auto-cleared: provably already gone
Query Okta for the identifier again, this time including
`DEPROVISIONED`/`SUSPENDED` users (current `resolve_user` implicitly only
matches active-status lookups via `GET /users/{id}`, which 404s for a login
that no longer resolves the normal way — needs confirming exactly which
statuses that endpoint does/doesn't return before finalizing the query
strategy). If it matches a deprovisioned user: auto-clear, no human review,
logged with reason `"already deprovisioned in Okta"` + the user id matched.

### Bucket 2 — Known exception: a human already decided this once
A new file, `known_exceptions.json`, hand-edited only (never
machine-written):
```json
{
  "vendor-noreply@thirdparty.com": {
    "reason": "Vendor distribution alias, never an Okta user",
    "added_by": "bchue",
    "added_date": "2026-07-13",
    "expires": "2026-10-13"
  }
}
```
Matching entries are suppressed from the review list, logged with the stored
reason. Expired entries (past `expires`) fall through to Bucket 3 instead of
silently continuing to suppress — forces periodic re-confirmation rather than
a permanent exemption.

### Bucket 3 — Needs human review (today's behavior, narrowed + assisted)
Everything not caught by Bucket 1 or 2. Each entry gets a fuzzy-match hint
(stdlib `difflib`) against current active users, e.g.:
```
NEEDS REVIEW: jon.smith@co.com  (closest active match: john.smith@co.com, 92% similar)
NEEDS REVIEW: newcontractor@co.com  (no close match)
```
This is a hint only — the human still decides; nothing here auto-corrects
or auto-adds an exception.

## Where this lives
New standalone module, `unmatched_triage.py`, imported by both
`okta_bookmark_sync.py` (single-app CLI) and `run_all.py` (multi-app
runner) — same pattern as the existing `okta_oauth.py` split. Pure function
in, pure classification out; no network calls beyond the one extra
deprovisioned-status lookup per still-unmatched identifier.

## Data/API questions to confirm before writing code
1. Exact Okta query for "does this identifier match a deprovisioned/suspended
   user" — `GET /users?search=...&status=...` vs per-identifier lookup;
   confirm against the sandbox which is cheaper at ~10-30 identifiers/app.
2. Confirm `known_exceptions.json` location/scope: one shared file across all
   18 apps, or per-app files? (Leaning shared — most exceptions like vendor
   aliases apply across apps, and one file is easier to keep an eye on.)
3. Confirm fuzzy-match threshold (e.g. 85%?) — too loose produces noisy false
   "possible typo" hints, too tight misses real ones. Needs a tuning pass
   against a real roster, not guessed upfront.
4. Where does the review-side edit `known_exceptions.json`? Same repo as the
   scripts, committed to git (audit trail via git history) is the default
   assumption — confirm that's acceptable vs. wanting something more
   formal/reviewed (e.g. a PR).

## Testing/verification plan (once built)
- Unit tests for each bucket's classification logic against synthetic
  identifiers (no network).
- One live dry-run against the sandbox's existing test roster, deliberately
  including a deprovisioned test user + a known-exception entry + a
  deliberate typo, confirming each lands in the correct bucket.
- No changes to the `--apply` path to verify — it's untouched.

## Roster source format — currently unresolved, blocks step-1 automation (not this triage piece)
Today's real source is **one Excel workbook, multiple tabs (one per app)** —
not the separate per-app files `config.json`'s `roster_dir` + `parse_export`
currently assume. `okta_bookmark_sync.py`'s `parse_xlsx` also only ever reads
`xl/worksheets/sheet1.xml`, so even today it would silently only pick up
whichever tab happens to be first if fed the multi-tab workbook directly
instead of pre-split files.

Bigger unknown: this workbook is presumably a SailPoint export, and SailPoint
is being retired org-wide (see the core project constraint). **What
generates the periodic per-app access list once SailPoint is gone is not
yet decided** — could stay a similar multi-tab export from whatever
replaces it, could partly become Okta OIG's own reporting, could be
per-app-owner manual exports. This is a real blocker for automating step 1
(roster refresh) and shouldn't be guessed at — needs resolving with
whoever owns the SailPoint retirement plan before that automation is
designed. Until then, the interim manual per-app-file workflow stays as-is,
and the unmatched-triage piece in this doc is unaffected either way (it
operates after parsing, regardless of source format).

**Considered and explicitly deferred: could Okta OIG itself become the
roster source, replacing the external export entirely?** Only in one
specific scenario — if the 18 apps get real Okta integrations (SCIM/API),
OIG can read live entitlements directly from the app, and steps 1+4
(external roster refresh + mirror sync) disappear entirely. Until then,
Bookmark apps are pure mirrors with zero native visibility into the real
app, so OIG has nothing of its own to report — it only ever echoes back
whatever was last synced in.

Confirmed with the user (2026-07-13): whether any of these 18 (core
business) apps get real integration is undecided, effort unknown, "might
be a while," and not something they have any control over or role in
deciding. **Decision: design against the current known state (Bookmark
mirror + external roster) and do not build around this possible future.**
If/when real integration happens for a given app, it independently
obsoletes that app's need for steps 1+4 — worth revisiting per-app at that
point, not worth speculatively designing for now.

## Rollout
Additive and reversible: if the triage output ever looks wrong, reverting to
the flat unmatched list is a one-line change (stop calling the triage
module), since the underlying `unmatched` list computation isn't modified.
