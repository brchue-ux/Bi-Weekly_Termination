# Bi-weekly Term and App List — mock scheduled drops

Mock data for the **Okta Workflows** future-state design of the biweekly termination review.
Every file here is synthetic and matches the demo tenant `demo-beige-haddock-4684`.

Regenerate with `python3 make_mock_drops.py`; gate with `python3 verify_mock_drops.py`
(both live in the project root). The generator is deterministic — re-run it rather than
hand-editing any file, or the manifest stops describing reality.

---

## The model these files represent

Every two weeks, **two feeds land**. Okta is the third leg of the join and needs no file at
all, because Workflows already reads it natively:

```
app export  ─┐
HR export   ─┼─►  Workflows: normalise → 3-way join → classify → ServiceNow
Okta (live) ─┘
```

### The one thing that changed from today's format

The real STARS workbook arrives with `TH_EmployeeID / TH_EmployeeStatus / TH_UPN` columns
sitting **beside** each app's own columns. That means somebody already performed the app↔HR
join before the file existed — that pre-join *is* the manual labour the current process pays
for every cycle.

So these mocks are deliberately **unjoined**:

- **App export** = only what the application itself can know: accounts, roles, enabled/disabled,
  last login. An app has no idea who is terminated.
- **HR export** = TalentHub's own authoritative feed, standalone.
- **The join happens in Workflows**, every cycle, automatically.

`verify_mock_drops.py` enforces this — if an HR column ever leaks into an app export, the
gate fails.

---

## Layout

```
bi-weekly term and app list/
├── MANIFEST.json                 every seeded case, named, with example rows
├── _HR_TalentHub/                the HR feed (one drop per cycle)
│   ├── TalentHub_HR_20260723.csv
│   └── TalentHub_HR_20260806.csv
├── _reference/
│   └── exception_list_20260723.csv    → loads as a Workflows Table
└── <one folder per app>/         ← the drop zone for that app
    ├── NA_Apollo_users_20260723.csv
    └── NA_Apollo_users_20260806.csv
```

**Why one folder per app:** that folder is exactly the unit a SCIM connector replaces. Onboard
three apps and three folders stop receiving drops while the rest keep going — the migration is
visible in the file system. It also leaves room for extra test files per app without any
reshuffling.

Underscore-prefixed folders (`_HR_TalentHub`, `_reference`) are not applications.

### Row counts

| App | 2026-07-23 | 2026-08-06 |
|---|---|---|
| NA Stellar | 1,950 | 1,926 |
| NA Orion | 1,435 | 1,409 |
| CloudForce HQ | 257 | 254 |
| NA Saturn East / Central / West | 148 / 149 / 148 | 146 / 145 / 146 |
| CloudForce Canada | 132 | 132 |
| NA Apollo | 130 | 128 |
| NA Saturn ComSat | 32 | **12** ← deliberate export anomaly |
| NA Saturn Corp | 23 | 23 |
| **HR feed** | **2,035** | **2,035** |

---

## Schemas

**App export** (`account_id, display_name, email, account_status, app_role, privileged,
created_date, last_login_date`)

- `email` is **mixed case** on purpose — the join must normalise, and a demo where case
  already matches hides a real integration failure.
- `privileged` derives from `app_role`, the way a real export works: you get the role and
  compute privilege, rather than maintaining a separate flag by hand.
- `last_login_date` **does not exist in today's STARS export.** It is the enabler for orphan
  attribution (propose an owner from who last used the account) and it makes a terminated
  account that is still being *used* visible rather than merely present. Treat it as an ask
  for your app owners, not something you already have.

**HR export** (`employee_id, first_name, last_name, upn, business_email, employment_status,
worker_type, hire_date, termination_date, job_title, department, country, manager_upn`)

- Dates are **ISO**, and "no termination date" is **empty** — not the Excel serial `1`
  sentinel the current file uses. Carrying that landmine into a greenfield design would be
  modelling a bug as a requirement.
- `manager_upn` **does not exist today** and is what makes manager-routed certification
  campaigns possible at all. Without it every review lands on one central reviewer.
- `worker_type` (Employee/Contractor) converts part of the "not found in TalentHub" pile from
  a mystery into an answerable question.

---

## What each seeded case proves

Counts from `MANIFEST.json`; every one is re-derived independently by the gate, because a
branch with no data behind it is an unproven branch.

| Case | Count | Why it is in here |
|---|---|---|
| Confirmed termination with access | 75 rows → **64 accounts** → 30 people | The core finding — auto-ticket. Duplicate roster rows collapse to one account = one ticket, matching `biweekly_recon` |
| Terminated **and privileged** | 5 | High-risk tier: never auto-anything |
| **Login after termination date** | 4 | The most alarming row type; today's process cannot see it at all |
| Orphan, absent from HR | 408 | The ownership question — the control's real cost |
| App row unjoinable (blank email) | 207 | Loud unknown on the app side |
| Malformed HR status | 30 | Loud unknown on the HR side — never default-to-fine |
| Contractor worker type | 209 | Explains part of the not-in-HR population |
| Expired exception | 20 | Exception no longer self-justifying |
| Exception owner terminated | 4 | Reassign-or-revoke branch |
| No Okta account at all | 8 of the 30 | Third leg of the join; verified live against the tenant |

### Cycle 2 (2026-08-06) is the closure proof

| Outcome | Count | Meaning |
|---|---|---|
| Verified closure | 51 | Account genuinely gone from the fresh export |
| **False claim** | 2 | Task closed, account still there → auto-reopen |
| Aging, not yet remediated | 11 | Nobody worked it → escalation, *not* a false claim |
| New termination | 8 | Active → Terminated between cycles → new findings |
| Export anomaly | ComSat 32→12 | Trips the 50% sanity ratio → closures **frozen**, not auto-closed |

False claims and aging findings are counted separately on purpose. A survivor is only a false
claim if a task was closed against it; conflating the two would let the demo claim a detection
it never actually made. The two named false-claim accounts are asserted individually by the gate.

---

## The finding worth leading the demo with

The source data contains **30 terminated people and exactly 30 rows marked Terminated** — one
each. But **25 of those 30 people also hold seats on other app tabs where that tab's row says
"Active."**

Today's per-tab workbook only flags the single row that happens to say Terminated. The other
seats belonging to those same terminated humans are invisible to the current process.

A single authoritative HR feed flags **all 64 seats** — because one person has one employment
status, not one per spreadsheet tab. That is roughly **34 additional live app seats** held by
people who no longer work there, surfaced purely by fixing where truth comes from. No new
automation required to justify it; it falls out of the architecture.

---

## Honest limits

- **These are mocks.** Real app exports will disagree on column names, delimiters, encodings
  and date formats. Normalisation per app is real work and is the first thing to scope.
- **`last_login_date` and `manager_upn` are aspirational** — confirm each app and TalentHub can
  actually supply them before designing a demo around either.
- **No HR-only people.** Everyone in the HR feed holds at least one app account. Inventing
  extra identities risked colliding with the tenant's re-identity guarantee, so it was skipped.
- **The exception list is reference data, not a drop** — it changes rarely and becomes a
  Workflows Table, so it carries one date and is not regenerated per cycle.
- **Nothing here feeds `biweekly_recon.py`.** That pipeline still reads the STARS workbook.
  Pointing it at this shape is a separate, deliberate change with its own verification.
