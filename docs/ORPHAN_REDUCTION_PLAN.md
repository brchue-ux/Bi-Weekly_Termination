# Reducing orphaned accounts — a plan to map real people to app accounts

**Orphan** here = an app account whose email does not resolve to an Okta user. In OIG's CSV-fed
model these cannot be granted an entitlement (grants attach to Okta principals), so they fall
entirely to the reconciliation. Driving the number down means *attributing each account to a real
identity* — or proving it legitimately has none.

Measured on the current 10-app cycle: **431 orphans of 4,404 rows.** They are not one problem.
Before proposing tooling, the population has to be split, because each slice needs a different
technique and the mix decides the effort.

## What the 431 actually are (measured, not assumed)

| Slice | Count | What it is | Mapping approach |
|---|---:|---|---|
| **Service / non-human** (role = Service Account) | **145** | Not a person. A daemon, integration, or shared seat. | **Owner attribution**, not identity resolution — assign a human *owner* + expiry, don't map to a user. |
| **Name matches an existing Okta person** | **62** | A real employee whose app email ≠ their Okta login (alias, domain change, contractor→FTE, name change). | **Highest-confidence remap** — propose the Okta user, human confirms, fix the identifier. |
| **No email at all** | **207** | Can't join on email by definition. Overlaps heavily with service accounts. | Secondary keys (employee_id, account_id convention) or owner attestation. |
| **Privileged** (subset, cross-cutting) | **157** | Admin/Service on a governed app. | **Do these first** regardless of slice — highest risk. |
| **Disabled** | **20** | Already inert. | Lowest urgency; document and close, don't chase. |
| **Logged in ≤90d of cycle** | **41** | Demonstrably a live human using the account. | Activity confirms it's real → worth attributing; prioritize with the privileged set. |

The headline: **~34% are service accounts** (an ownership problem, not an identity problem) and
**~14% are almost certainly known employees under a different email** (the quick wins). The rest
need a real join key the current exports don't carry.

## The single highest-leverage fix: ask app owners for `employee_id`

The mock drops carry `account_id, display_name, email, account_status, app_role, privileged,
created_date, last_login_date` — **no employee_id.** Email is the *only* join key today, and it's
the one that's missing or aliased for exactly the accounts we can't resolve.

If every export carried the app account's **employee_id** (and, for service accounts, a stable
**owner** field), the largest share of orphans converts from fuzzy-guess to a deterministic join
against HR/Okta. This is one column, and it's the difference between "match by name and hope" and
"match by key and know." **It is the first thing to request from each app owner** — same
conversation as the role-vocabulary ask, and more valuable.

## Mapping techniques, in confidence order

Run them as a cascade — an account resolved by a higher tier never reaches a lower one.

1. **Deterministic key join** (needs employee_id). Account.employee_id → HR/Okta. No ambiguity.
   Not possible on today's exports; enabled the moment owners add the field.
2. **Email normalization / alias match.** Strip domain variants, dots, casing; try
   `firstname.lastname` permutations against Okta login + email + *secondary* emails. Resolves the
   "same person, different domain" case (224 orphans carry a `bitermtest.com` email that simply
   isn't their Okta login).
3. **Name-based fuzzy match** (first + last from `display_name`) → Okta person, with a confidence
   score. **62 already match exactly.** Auto-propose above a threshold, queue the rest for a human.
   Never auto-apply — a name collision must not silently mis-attribute an account.
4. **Activity correlation.** A recent `last_login_date` (41 accounts) proves a live human; combine
   with SSO/auth context where the app federates, to point at who signs in.
5. **Owner / manager attestation.** The residual that no key resolves goes to the app owner or the
   most-likely manager as a "name this account's human, or declare it a service account" task —
   the same ServiceNow ticketing the terminations already use.

## The loop that makes the number fall (and stay auditable)

Reducing orphans is the same detective-control discipline as the termination review: propose,
confirm, write back, verify next cycle.

1. **Classify** every orphan each cycle into: service · mappable-human · unknown-human · disabled,
   with an immutable per-cycle snapshot (so a later change is provable).
2. **Auto-propose** mappings via the cascade, each with a confidence and the evidence that
   produced it. Nothing is applied on a machine's say-so.
3. **Human confirms.** On confirm, **write back the fix** — add the alias email to the Okta user,
   or correct the account's identifier at the app — so the email resolves. **Next cycle the
   account leaves the orphan bucket on its own.** That drop is the measurable proof, exactly like
   a termination disappearing from the next export.
4. **Service accounts** go to the owner registry (owner UPN + expiry + justification) already in
   the exception-list design. Ownerless or owner-terminated → flag for reassignment. These never
   "resolve to a person" — success is a *named accountable owner*, not a mapping.
5. **Residual unknown-humans** get an attestation ticket. Aging + escalation if unanswered.
6. **Trend the count.** The metric is *unattributed orphans*. It should fall cycle over cycle
   toward a floor of genuine service accounts (with owners) and legitimately external accounts.
   A floor is fine; a flat-high number or silent churn is the alarm.

## Two horizons — why this is the interim, not the end

- **Now (CSV / disconnected):** everything above. Attribute-and-remap by join keys and attestation.
  Okta's picture stays an *assertion*, so the reconciliation is load-bearing and orphans are chased
  externally.
- **Later (SCIM / import-capable apps):** an imported app gives Okta the app's *own* account list,
  so drift surfaces on its own. The untested `includeAllAppServiceAccounts` campaign flag implies
  Okta can model app accounts **not tied to a user** — which is precisely the orphan bucket. If
  that holds for import-capable apps, SCIM onboarding may make orphans **first-class governable
  objects** rather than an external spreadsheet problem. That is a *lead to test on the first app
  with a real connector*, not a promise — but it's why per-app SCIM is the strategic reduction
  lever, on top of the manual attribution that starts today.

## What to do first (this order)

1. **Ask each app owner for `employee_id` in the export** (and an `owner` field for service
   accounts). Biggest single lever; unblocks tier-1 deterministic matching.
2. **Work the 62 name-matches + the 157 privileged accounts first** — quick, high-confidence wins
   and the highest-risk accounts, respectively.
3. **Stand up the owner registry** for the 145 service accounts (most of the "no email" 207).
4. **Route the residual to attestation tickets**, and start trending unattributed-orphan count.
5. **Carry the SCIM/`includeAllAppServiceAccounts` test** into the first app that gets a real
   connector — it could move the whole service-account slice inside Okta.
