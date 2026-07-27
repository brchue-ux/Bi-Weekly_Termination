# Code review — biweekly termination control

> **STATUS: REMEDIATED 2026-07-26.** Every finding below has been actioned; see the
> `2026-07-26 (late)` entry in `CHANGELOG.md` for what shipped, and `CLAUDE.md` §Code state
> for the current architecture. Verification: 76 unit tests green plus all 7 control-rule
> mutations caught (`python3 tests/run_tests.py`); all 42 scripts compile and import; the
> real workbooks reproduce the last recorded cycle (4,404 rows / 30 ticket findings / 475
> unknowns). This document is kept as the rationale record — it explains WHY each change
> exists, which the code comments reference.
>
> One thing this review did not anticipate: the confirmation guard added for §1.4 was
> itself written fail-OPEN for non-interactive runs, and a diagnostic command created 29
> unintended ServiceNow ticket chains. That incident, its blast radius, and the fail-closed
> fix are recorded in the CHANGELOG entry.

**Reviewer frame:** senior backend engineer on an identity-governance product team (the
people who would write the OIG service itself). Lens applied: *this code makes and evidences
access decisions for a SOX control.* Every finding is judged by "what happens on a bad day"
— partial API failure, malformed export, crash mid-run, machine loss — not by whether it
works on the happy path.

**Scope:** all 42 scripts (~7.9k lines), `CLAUDE.md`, `.gitignore`, git manifest.
**Nothing was executed.** Every finding below is from reading source.

**Summary:** the *design* is stronger than the *engineering*. The control model
(detective-only, HR as authority, loud unknown, closure-verified-next-cycle, falsifiable
verifier) is genuinely good and better reasoned than most internal compliance tooling. The
implementation underneath it has the failure profile of scripts, not of a control: no
timeouts, no tests, silent error swallowing in mutating paths, non-idempotent ticket
creation, and evidence stored in one untracked local directory. The gap between the two is
the whole review.

---

## 0. What I would keep, unchanged

Stated first because a rewrite would likely destroy these:

1. **`classify()`'s three-branch model with a LOUD unknown** and HR evaluated *before*
   exceptions (`biweekly_recon.py:135-165`). The ordering is the control. Most homegrown
   versions let an exception suppress a termination hit.
2. **DRY runs excluded from the closure baseline** (`biweekly_recon.py:476-485`). A rehearsal
   silently becoming the baseline would age every finding and fake "0 new" on a first real
   cycle. Very few people catch this; the comment explains exactly why.
3. **Roster-shrink anomaly freeze** (`ROSTER_SANITY_RATIO`, `closure_pass`). "Absence of
   evidence != removal" is the correct default and is actually implemented, not just stated.
4. **Two-phase closure write-back with reopen** (`closure_writeback`). Detecting "task closed
   but the account is still in the export" and reopening is the difference between a control
   and a reporting job.
5. **The falsifiable verifier concept** (`oig_verify_all.py --selftest`). Proving the checker
   *can* fail is the right instinct. (Its execution has gaps — §2.4/§2.5.)
6. **Two-layer least privilege** on the OAuth service app (scopes ∩ admin role), and the
   deliberate refusal to let the control run under the SSWS seeding token
   (`okta_client.py:1-12`).
7. **Docstrings that record rationale and rejected alternatives** (`oig_load_all.py:11-32` on
   highest-privilege-wins, `feed_ingest.py:1-14` on adapter-not-second-pipeline). This is the
   most valuable artifact in the repo. Preserve it verbatim through any refactor.

---

## 1. Blockers — I would not let these into a production control

### 1.1 A transport failure removes a real person's access
`okta_bookmark_sync.py:116-120` + `run_all.py:84-108`

```python
def resolve_user(org, auth_header, identifier):
    status, user = okta_request(...)
    if status == 200:
        return user
    return None            # 429-exhausted, 500, 503 -> indistinguishable from "no such user"
```

`None` puts the identifier in `unmatched`, which keeps the user out of `resolved`, which puts
them in `to_remove = set(current) - set(resolved)` — and with `--apply` that is a `DELETE
/apps/{id}/users/{uid}`. **A rate limit or a 503 deprovisions someone.**

*Edit:* distinguish the three outcomes explicitly — `FOUND` / `NOT_FOUND` (404 only) /
`UNKNOWN` (anything else). Any `UNKNOWN` in the roster aborts the run before a single write.
Never let an error state flow into a removal set.

### 1.2 No blast-radius guard on the removal path
`run_all.py:92-108`

`to_remove` is applied with no ceiling. Combine with 1.1, or with the *known* parser bug in
1.3, and an empty/short parse means "unassign everyone from this app". There is no
`--max-removals`, no percentage guard, no "0 parsed rows = abort".

*Edit:* three cheap guards, all fail-closed:
- `if not identifiers: abort` (an empty roster is a parse failure, never an instruction).
- `if len(to_remove) > max(10, 0.10 * len(current)): abort` unless `--force-large-removal N`
  is passed with the exact expected count.
- Print the full removal list and require typed confirmation of the *count*.

### 1.3 The known-broken parser is still wired to the polished entrypoint
`okta_bookmark_sync.py:148` — `z.open("xl/worksheets/sheet1.xml")`

`CLAUDE.md:149` already flags this: it reads only `sheet1.xml`, silently empty for 9 of 10
STARS tabs. Two things make it worse than documented: (a) `sheet1.xml` is not guaranteed to
be the workbook's *first* sheet — the mapping lives in `xl/_rels/workbook.xml.rels`, which
`xlsx_min.py` handles correctly and this file ignores; (b) `run_all.py`, the best-engineered
entrypoint in the repo (argparse, key-permission check, confirmation prompt), drives exactly
this function. The safest-looking front door is bolted to the broken parser.

*Edit:* delete `parse_xlsx`/`_col_to_idx` from `okta_bookmark_sync.py` and import
`xlsx_min.load_workbook_rows`. If that's more than a one-line change, mark the module
`DEPRECATED — DO NOT RUN` at the top and have `run_all.py` refuse `--apply` until it's fixed.

### 1.4 The confirmation prompt does not confirm anything
`run_all.py:135-137`

```python
typed = input("Type the org hostname to confirm: ").strip()
if typed not in org:
    sys.exit(...)
```

Substring, not equality. Typing `o` passes. So does the empty string in some shells
(`"" in org` is `True`).

*Edit:* `if typed != urlparse(org).hostname: abort`. Also echo the target org, the app count,
and the computed add/remove counts *before* prompting, so the human confirms the blast
radius, not the URL.

### 1.5 Ticket creation is not idempotent, and state is written only at the end
`biweekly_recon.py:491-514`

Tickets are created in a loop; `state.json` — the only record that a ticket exists — is
written after the loop, after the report, at line 511. Crash, `SystemExit` from the Okta
client, `Ctrl-C`, or a laptop sleep after ticket 300 of 400 loses all 300 linkages. The next
cycle sees `f.get("ticket") in ("", "DRY", "SN-ERROR")` and **orders 300 duplicate catalog
items**. Nothing queries ServiceNow to ask "does a ticket for this finding already exist?"

*Edit:*
- Stamp a deterministic idempotency key on every RITM — `correlation_id =
  sha256(app|key|cls|first_cycle)` — and query `sc_req_item?sysparm_query=correlation_id=…`
  before `order_now`. That single query makes the whole path safely re-runnable.
- Persist `state.json` incrementally (append each ticket as it is created, or write after
  every N), and write it as `state.partial.json` → atomic rename on success so a torn file
  is never mistaken for a completed cycle.
- `SN-ERROR` should be a *distinct* terminal state that a human must clear, not a value that
  silently re-enters the ticketing loop next cycle.

### 1.6 Exception expiry is a string comparison over unvalidated input
`biweekly_recon.py:149` — `if e["expiry"] < today:`

Three ways this silently fails open (a lapsed exception passing as valid is a direct control
failure):
- `xlsx_min.py` returns **raw cell text**. An Excel *date-formatted* cell yields the serial
  number `"46234"`, and `"46234" < "2026-07-26"` is `False` → never expires.
- A US-format string `"12/31/2026"` compares `"1" < "2"` → `True` → flagged expired
  (noise, at least fails loud).
- A blank expiry → `"" < today` → `True`. Accidentally safe, for the wrong reason.

*Edit:* parse to `datetime.date` at load time. Anything unparseable is a **hard error on the
exception register**, not a row-level guess — a corrupt exception file must stop the cycle,
because every downstream verdict depends on it. Same treatment for `expiry` in
`feed_ingest.py:109`.

### 1.7 The exception register is addressed by column position
`biweekly_recon.py:93-98`

```python
{"owner": r.get(6, ""), "expiry": r.get(7, ""), "type": r.get(4, "")}
```

Magic indices, no header validation. Someone inserts a column in the Exception List — a
spreadsheet maintained by humans outside this repo — and `owner`/`expiry`/`type` silently
shift. There is no assertion that column 7 is even called "Expiry".

*Edit:* resolve columns by header name (as `load_rosters` already does for `TH_UPN`), and
fail hard with the actual header row printed if an expected header is missing. This is the
single highest-value 10-line change in the repo.

### 1.8 The control's memory is untracked, unbacked-up local state
`.gitignore` ignores `cycles/`, `cycles_feed/`, `reports/`

Ages, ticket numbers, first-seen cycles, and closure baselines live only in
`cycles/*/state.json` on one workstation. Lose the machine and: every open finding re-tickets
(§1.5), no closure can be proven, and the BEFORE/AFTER audit evidence is gone. "Regenerable
from the scripts" is stated in the ignore-file comment but is false — the outputs depend on
live tenant state at run time and on the prior cycle's state.

*Edit:* separate *evidence* from *scratch*. Evidence (`state.json`, `report.xlsx`, digests)
goes to durable, retained storage with a `SHA256SUMS` manifest per cycle and, ideally,
append-only/WORM semantics. At minimum: commit `state.json` (it is the control's ledger), or
sync the cycle directory somewhere backed up. Note `state.json` embeds full source rows
(`"snapshot": row["src"]`) — real HR/PII — so pick the destination with that in mind rather
than committing it blindly.

---

## 2. High — correctness and verification integrity

### 2.1 Nothing has a socket timeout
One `timeout=` in the entire codebase, in `pdi_keepalive.py`. `urllib.request.urlopen`
defaults to *no timeout*. A half-open connection hangs the biweekly control indefinitely,
mid-ticket-run, with no watchdog.

*Edit:* `urlopen(req, timeout=30)` everywhere, enforced by there being exactly one HTTP
client (§3.1).

### 2.2 A 401 is reported to the operator as "EM not enabled"
`oig_load_all.py:160-163`

```python
code, live = call(f"/api/v1/apps/{app['app_id']}")   # code discarded
em = live.get("settings", {}).get("emOptInStatus")
if em != "ENABLED":
    return {"skipped": f"emOptInStatus={em} (enable EM in Console)"}
```

`call()` returns `(code, {})` on error, so any 401/403/404/5xx becomes `em=None` and the app
is dropped from the load with a message that sends the operator to the Admin Console UI to
fix a problem that isn't there. **An app silently falling out of a compliance load is exactly
the coverage gap the control exists to prevent.** The same pattern (`code` assigned, never
checked) appears at `oig_verify_all.py:130` and `:136`.

*Edit:* `if code != 200: raise` — or at minimum a third outcome, `ERROR`, that is counted
separately from `SKIP` and makes the run exit non-zero. Never let "I couldn't ask" render as
"the answer is no".

### 2.3 Silent partial reads inside a mutating path
`oig_load_all.py:141-156` — `existing_values()`:

```python
code, body = call(url)
if code != 200:
    break            # partial `current` map, returned as if complete
```

A failure on page 3 of 5 makes principals on pages 4-5 look like they hold nothing, so the
loader re-POSTs grants for them and reports them as `granted`/`corrected`. The tenant
survives (POST replaces with the same value), but the *evidence is wrong* — the run report
claims corrections that never happened, and a real drift would be invisible in the noise.

*Edit:* raise on non-200; a paginated read that cannot complete must abort the app, not
return a truncated map. Same fix in `oig_verify_all.py:100-119`.

### 2.4 The verifier has no retry and cannot say "I don't know"
`oig_verify_all.py:51-62`

The loader retries 429/502/503 six times. The verifier — the thing whose `VERDICT: PASS` line
is the project's stated evidence standard (`CLAUDE.md:91-96`) — has **no retry at all**, and
swallows errors into `break`. A rate limit during verification manufactures a verdict:
truncated `granted` → spurious `FAIL`, or a truncated grants read that happens to align →
under-reported `extra`.

*Edit:* three-valued verdict — `PASS` / `FAIL` / `INCONCLUSIVE`, with `INCONCLUSIVE` exiting
non-zero and naming which check could not be evaluated. An auditor's first question is "how do
you know the check ran?"; today the answer is "the same way we know it passed."

### 2.5 Verifier independence is superficial; `--selftest` proves less than it claims
`oig_verify_all.py:42-43, 83-97, 183-187`

- `PRIORITY`, the email-lowercase join, and the per-principal role aggregation are
  **copy-pasted verbatim** from `oig_load_all.py`. The verifier re-derives the answer using
  the loader's assumptions, so any *shared* wrong assumption (email as identity key, the
  privilege ordering, `.strip().lower()` normalization) passes both. It is independent of the
  loader's *reported output*, which is real value — but not of its *logic*, which the module
  docstring implies.
- `--selftest` corrupts only `expected`, exercising exactly one of five checks. The EM check,
  the values-match check, the `extra` check, and the coverage check are never proven
  falsifiable.
- `ok = len(failures) >= len(manifest)` is a count over the whole run: one app emitting ten
  failures while nine emit zero **passes the selftest**.

*Edit:* (a) import the shared derivation from one module and be honest that independence
comes from re-reading the tenant, then add one genuinely independent cross-check (row counts
against an Admin Console export); (b) selftest each check with its own targeted corruption;
(c) assert per-app: `all(app produced >= 1 failure for app in manifest)`.

### 2.6 The dry run is not a plan
`oig_load_all.py:188-192` — `current` is populated only when `--apply`, so a dry run compares
against `{}` and reports every principal as `granted`. You cannot review what a run will
change before running it, which is the entire point of `--apply`-gating a mutating job.

*Edit:* always fetch current state; gate only the writes. The dry-run output should be the
exact diff the apply run will perform.

### 2.7 Bearer token lifetime is hardcoded and never refreshed on long runs
`okta_client.py:30` hardcodes `TOKEN_LIFETIME = 3600` instead of reading `expires_in` from
the token response (`okta_oauth.py:63` discards it). `run_all.py:120` mints once at startup
and never refreshes — combined with one `resolve_user` call *per roster row* (§4.9), a
multi-thousand-row app will exceed the token lifetime and 401 mid-mutation.

*Edit:* return `expires_in` from `get_access_token` and refresh on it (with the existing
300s margin); have every client refresh transparently rather than trusting a constant.

### 2.8 Closure keying makes an identity change look like a removal
`biweekly_recon.py:80, 191` — findings key on `upn or f"alias:{alias}"`. If a row that
previously had no UPN gains one (an app owner backfills email — precisely the outcome
`docs/ORPHAN_REDUCTION_PLAN.md` is driving toward), the old key vanishes and the new one
appears. The control records a **verified closure**, writes `REMOVAL VERIFIED` into the RITM
work notes as audit evidence, and opens a fresh age-1 finding. The person never lost access.

*Edit:* key on a stable identity — `employee_id` where present, else `(app, alias)` — and
carry `upn` as an attribute rather than as part of the key. Any finding whose key changes
shape between cycles should be reported as `IDENTITY_CHANGED`, never as a closure. Given
that orphan reduction is the next workstream, this will fire in production.

### 2.9 Point-in-time inputs are not pinned
`feed_ingest.py:101-103` — `exc_files[-1]` takes the lexicographically newest exception list
regardless of the cycle stamp. Re-running cycle `20260723` today evaluates it against
*today's* exceptions and produces a different answer. A SOX control must be reproducible from
its stated inputs.

*Edit:* select the exception file by stamp (newest ≤ cycle stamp), and record every input
file's path, size, and SHA256 in `state.json`. That hash list *is* the evidence that the
report matches what was reviewed.

### 2.10 The guard for the documented campaign landmine is a substring grep
`oig_run_campaigns.py` — `sample_entitlement_items()`:

```python
if "entitlement" in json.dumps(r).lower() or r.get("entitlementValue") or r.get("entitlement"):
    return True
```

`CLAUDE.md:135-138` calls this out as the highest-risk silent failure: missing either flag
creates app-assignment-level items with **no error**. The check that protects against it
matches the literal string "entitlement" anywhere in the serialized review object — which an
app-level item can easily contain (`"entitlements": []`, `includeEntitlements`, a bundle
key). The guard can return `True` for exactly the failure it was written to catch.

*Edit:* assert on structure, not text: `r["entitlementValue"]` is non-empty (or the item's
`type`/`resourceType` field equals the entitlement-level value) for **every** sampled item,
not any. Then confirm the count of entitlement-level items equals the expected grant count —
which the project already knows (20 items = 20 grants).

---

## 3. Structural — what I'd change before the next feature

### 3.1 Twenty-one hand-rolled HTTP clients
`grep -c 'urllib.request.Request' scripts/*.py` → 21 files. Nineteen hardcode
`https://demo-beige-haddock-4684.okta.com`. Each has its own retry policy, or none: the
loader retries 429/502/503; `okta_client` retries only 429; the verifier retries nothing;
`all_users_by_email()` — the largest paged read in the codebase, ~11 pages — has **no error
handling at all**, in both files where it's duplicated. Error contracts differ per file
(`raise SystemExit` / `raise RuntimeError` / `return (code, {})` / return `None`).

This is why §1.1, §2.1, §2.2, §2.3, and §2.4 are five separate findings instead of one.

*Edit:* one `okta/client.py` — pluggable auth (SSWS vs private-key-JWT), `timeout`,
retry-with-jitter on 429/5xx, `Retry-After`/`X-Rate-Limit-Reset` honored, `raise` on
unexpected status with a typed `OktaApiError`, and `paged()` using `get_all("Link")` (already
correct in the good copies — see `CLAUDE.md:40-42`). Delete the other twenty. Config
(`org`, `client_id`, key path, ORGID) comes from `config.json` — which already exists as
`config.example.json` and is honored by exactly one legacy script.

### 3.2 No tests, no dependency manifest, no CI
No `tests/`, no `requirements.txt`/`pyproject.toml`, no CI. `okta_oauth.py:18` imports
`jwt` (PyJWT) — an undeclared runtime dependency, on a system python that `CLAUDE.md:115-118`
says has no pip.

`classify()`, `closure_pass()`, `ownership_review()`, and `expected_highest()` are pure
functions over plain dicts. They are trivially testable with zero infrastructure, and they
are where the control's correctness lives.

*Edit:* a `tests/` directory whose first eight cases are the control's stated rules —
terminated user is flagged; an exception never suppresses a termination; an expired exception
flags; a terminated *owner* flags; an unknown status is loud, not passing; a shrunken export
freezes closures; a DRY cycle is not a baseline; a disappeared account closes *only* when its
export is healthy. Add a `requirements.txt` pinning PyJWT, and document that the runtime is
system python + stdlib + PyJWT.

### 3.3 No logging, no run record for mutating scripts
Zero `import logging` in the repo. Everything is `print()` to stdout/stderr with no run id,
timestamp, or level. The mutating scripts (`oig_load_all.py`, `oig_saml_rollout.py`,
`seed_tenant.py`) leave **no artifact** of what they changed — only console output that
scrolls away. The read-only verifier produces better evidence than the writers do.

*Edit:* `logging` with a run id, stderr handler, and a per-run JSONL file. Every mutating
call appends `{ts, run_id, actor, method, path, target, before, after, status}`. That file is
the change evidence an auditor asks for, and it makes §2.3's silent partial writes visible
after the fact.

### 3.4 Library modules call `sys.exit`
`okta_client.api` raises `SystemExit` on any non-404/429 (`okta_client.py:64`);
`okta_oauth.get_access_token` does the same on a token failure; `okta_bookmark_sync`'s
helpers `sys.exit` from inside functions. A library cannot decide to kill the process — a
transient 502 during the ticketing loop terminates the cycle after tickets exist but before
`state.json` is written, which is the exact trigger for §1.5.

*Edit:* raise typed exceptions (`OktaApiError`, `OktaAuthError`); only `main()` exits.

### 3.5 The control imports its domain constants from the privileged seeder
`biweekly_recon.py:38` and `feed_ingest.py:19` import `STARS_TABS`, `NO_UPN`,
`APP_LABEL_PREFIX` from `seed_tenant.py` — a tool `CLAUDE.md:78-85` describes as disposable
scaffolding that SCIM retires. Deleting the seeder breaks the control; editing the seeder
changes the control's behavior.

*Edit:* `domain.py` (or `constants.py`) holding the population list, label prefix, HR status
sets, and privilege ordering. Everything imports from there, including the seeder. This also
kills the duplicated `PRIORITY` table (§2.5) and the duplicated `LEGIT`/`TERM` sets.

### 3.6 `NO_UPN` is overloaded across a module boundary
`"Not found in TalentHub"` is a *UPN* sentinel in `seed_tenant.py:44`, but `feed_ingest.py:92`
stores it in the **`hr` (employment status)** field, and `biweekly_recon.py:157` tests
`hr == NO_UPN`. It works, but a constant named "no UPN" carrying an HR status is the kind of
thing that survives until someone changes the string.

*Edit:* an explicit `HR_NOT_FOUND = "Not found in TalentHub"` distinct from the UPN sentinel,
even if the literals are identical today. Better: an enum, so a typo is a `NameError`.

### 3.7 Hand-rolled argv parsing on the control itself
`biweekly_recon.py:438-443`, `oig_load_all.py:222-224`, `oig_verify_all.py:169-171`,
`oig_run_campaigns.py`:

```python
create_tickets = "--create-tickets" in args
rosters = Path(args[args.index("--rosters") + 1]) if "--rosters" in args else ...
```

- `--rosters` with no value → `IndexError` traceback.
- **A typo'd `--create-ticket` silently produces a DRY run.** An operator believes tickets
  were filed; nothing was. On a SOX control that is a missed cycle discovered at audit.
- Unknown flags are ignored entirely — `--dry-run` passed to `biweekly_recon.py` does nothing
  and gives no warning, while it *is* the flag `seed_tenant.py` uses.
- Flag vocabulary is inconsistent across the repo: `--apply`, `--create-tickets`,
  `--dry-run`, `--live/--dormant`.

*Edit:* `argparse` everywhere — `run_all.py` already demonstrates the house style, including
`--apply` defaulting to dry. Standardize on `--apply` as the single write gate across all
scripts, and reject unknown arguments.

### 3.8 Environment and personal identifiers hardcoded in the control
`biweekly_recon.py:45-49`: `SN_INSTANCE`, `SN_CATALOG_ITEM` (a raw sys_id), `SN_GROUP`, and
`SN_ASSIGNEE = "brandon.chue"` — one named individual as the fulfiller for every ticket the
control ever files. Plus `ORGID = "00o159zwmhz6L5eo4698"` duplicated across OIG scripts.

*Edit:* into config, with the assignee defaulting to the *group* rather than a person
(`assignment_group` is already set; `assigned_to` should be optional). A control that stops
working when one employee leaves has a single point of failure named after them.

### 3.9 Credential handling is positional and unchecked
`biweekly_recon.py:217-219` parses the ServiceNow credential file by structure —
`next(l for l in lines if "=" not in l)` for the username — raising bare `StopIteration` if
the file gains a comment line. `oig_load_all._token()` takes `splitlines()[0]`. And
`_token()` **re-reads the file from disk on every single API call**.

`run_all.py:49-58` gets this right: it refuses to run if the PEM is group/other-readable.
Nothing else does that check, including the scripts holding a full-admin SSWS token.

*Edit:* one `secrets.py` — load once, cache, validate `0600`, parse by explicit key, and
raise a clear "credential file malformed: expected `password=`" instead of `StopIteration`.

---

## 4. Medium — things I'd flag in review comments

1. **Email as identity key, last-write-wins.** `oig_load_all.py:100-102` /
   `oig_verify_all.py:73-75`: `emails[e] = u["id"]` silently collapses duplicate emails. In an
   IGA load, two people sharing an email means one is never certified. Detect and report
   duplicates loudly.
2. **`xlsx_min.py` rows are positional, not `r`-indexed.** Rows are appended in document
   order (`:64`); a sparse or absent `<row>` shifts everything, and `rows[1]` is used as the
   header row (`biweekly_recon.py:69`). Read `row_el.get("r")` and place rows by index.
3. **`xlsx_min.py` ignores number formats.** Date cells return serials (root cause of §1.6).
   Also `ZipFile` is never closed (no context manager), and `shared[int(v_el.text)]` is
   unguarded against a malformed index.
4. **Alias column resolved by suffix heuristic.** `biweekly_recon.py:70`:
   `[c for c in cols if c.endswith(("_NetworkAlias","_USERNAME"))][0]` → `IndexError` on a
   renamed column, with no message naming the tab.
5. **Excel sheet-name collision.** `write_report` uses `app[:31]` (`:395`) with no
   de-duplication; two apps sharing a 31-char prefix produce duplicate sheet names and a
   workbook Excel may refuse to open.
6. **The Okta orphan leg is computed and discarded.** `okta_state()` builds `assigns`
   (`:109-113`), `main()` binds it to `_okta_assigns` (`:470`) and never uses it. Either wire
   it into the report — the module docstring advertises a 3-way join — or delete the pull and
   save ~10 app-user pagination passes per cycle.
7. **Unknown Okta ids collapse to `"?"`.** `id_to_login.get(au["id"], "?")` (`:112`) makes
   every unresolvable assignee identical and indistinguishable in a set.
8. **`sweep_flow_stage_tasks` doesn't paginate.** `sysparm_limit=200` (`:342`); stray task 201+
   is silently left open, blocking its RITM lifecycle.
9. **N+1 user resolution.** `run_all.py:86` calls `resolve_user` once per roster row;
   `oig_load_all.py:91` pages the directory once and joins locally. Adopt the second pattern
   everywhere — it is the difference between 3 requests and 3,000.
10. **Partial ticket chains are unrecoverable.** `sn_create_ticket` performs 5-7 sequential
    writes; a failure after `order_now` (`:247`) leaves an orphan REQ in ServiceNow, and the
    `except Exception` at `:497` records only `"SN-ERROR"` — not the REQ number that was
    created. Capture and persist whatever was created before re-raising.
11. **Fixed `time.sleep(1)` polling for the async fulfillment task** (`:267-272`) — 10 seconds
    then a fallback create. Use exponential backoff, and log when the fallback path fires (it
    means the SN flow is misbehaving and someone should know).
12. **Inconsistent bookmark sign-on mode:** `"BOOKMARK"` (`seed_tenant.py:206`) vs
    `"BOOKMARK_SSO"` (`okta_bookmark_sync.py:93`). One of these is wrong for the API version
    in use; both are in the repo.
13. **Dead default in the privilege lookup.** `PRIORITY.get(r, -1)` (`oig_load_all.py:197`) is
    unreachable — unknown roles are filtered at `:181-183`. Either drop the default or, better,
    make an unknown role a hard error: an unrecognized role in a privilege-certification load
    is a finding, not a skip. Note `unknown_role` rows are counted but nothing fails on them.
14. **No `newline=""`/encoding discipline on the ServiceNow side**, and `urllib.parse.quote`
    is used at `biweekly_recon.py:237` while only `urllib.error`/`urllib.request` are imported
    — it resolves only because `urllib.request` happens to import `urllib.parse`. Add the
    explicit import.
15. **`sn_call` has no retry, no timeout, and no non-200 handling** — it raises raw
    `HTTPError` with the response body unread, so the error message loses ServiceNow's
    explanation.
16. **`am_team_okta.json`, `seed_manifest.json`, and `oig_run_campaigns.json` are committed**
    with live tenant object ids. Fine for a demo tenant; would need scrubbing before this
    pattern moves to the real org. Worth a note in `CLAUDE.md` now rather than at migration.
17. **No package structure.** `sys.path.insert` in `run_all.py:34`, sibling imports
    everywhere, scripts that are also libraries (`seed_tenant`, `feed_ingest`, `xlsx_min`).
    A `biterm/` package with console entry points removes the path hacks and makes the
    library/script boundary explicit.

---

## 5. What I would do first

Ordered by risk removed per hour spent:

| # | Change | Removes |
|---|--------|---------|
| 1 | Fail-closed `resolve_user` + blast-radius guard + fix/retire `parse_xlsx` (§1.1-1.3) | Accidental mass deprovisioning |
| 2 | Header-name lookup + real date parsing for the exception register (§1.6-1.7) | Silent control failure — lapsed exceptions passing |
| 3 | Idempotency key on SN tickets + incremental atomic state writes (§1.5) | Duplicate tickets, lost ticket linkage |
| 4 | Durable, hashed cycle evidence outside `.gitignore` (§1.8, §2.9) | Unreproducible audit evidence |
| 5 | One HTTP client: timeouts, uniform retry, raise-on-unexpected (§3.1, §2.1-2.3) | Five separate silent-failure classes |
| 6 | `tests/` covering the eight stated control rules (§3.2) | "How do you know the classifier is right?" |
| 7 | Fix the confirmation prompt; `argparse` + `--apply` everywhere (§1.4, §3.7) | Typo'd flag silently skipping a live cycle |
| 8 | Three-valued verdict + per-check selftest + structural campaign assertion (§2.4-2.5, §2.10) | Verification that can't distinguish "passed" from "didn't run" |

Items 1-4 I would treat as release-blocking for anything touching the real work tenant.
Items 5-8 are what makes the next six months of changes safe.

---

## 6. The one architectural note

`CLAUDE.md:78-85` argues the automation footprint *shrinks* as SCIM onboards, and that the
entitlement loader is disposable scaffolding. I agree with that and it's the right story for
leadership — but it currently reads as a reason to hold the code to script standards.

It shouldn't. The loader is disposable; **the reconciliation, the closure ledger, and the
evidence trail are not** — they are explicitly the detective control that keeps running after
SCIM lands (`CLAUDE.md:70-77`). Roughly 900 lines across `biweekly_recon.py`,
`feed_ingest.py`, `xlsx_min.py`, and the shared client are permanent, and they are the ones
carrying §1.5-1.8. I'd draw that line explicitly in `CLAUDE.md` — *permanent control* vs
*disposable scaffolding* — and apply the engineering standard only to the first. That keeps
the shrinking-footprint story intact while removing the excuse it currently provides.
