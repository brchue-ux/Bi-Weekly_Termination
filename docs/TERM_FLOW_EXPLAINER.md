# Termination Review — Flow Explainer (raw, for trimming)

> Status: RAW OUTPUT saved 2026-07-23 at user request — Claude's full explanation of the
> before/during/after-Okta flow, control logic, review method, rules, and OIG improvement path.
> User intends to trim this down themselves. Do not rewrite without being asked.

## 1. What happens when someone is terminated (the event flow)

**Before Okta (HR layer):** TalentHub flips the person's status to `Terminated`/`Retired`. This
is the *only* authoritative fact in the whole system. Everything else — Okta status, app
rosters — is downstream state that may or may not have reacted.

**In Okta (identity layer):** Today, for these 19 apps, *nothing happens automatically*. There's
no HR→Okta feed driving deprovisioning (the TalentHub join comes via SailPoint, which is being
retired). Someone may manually deactivate the Okta account, or not. The seeded tenant
deliberately models all three fates: terminated person still **ACTIVE** in Okta (~40% — the
failure mode the control exists to catch), **SUSPENDED**, or **never had an Okta account** at all.

**After Okta (app layer):** The 19 apps are disconnected — no SCIM, no provisioning integration.
Even if Okta deactivates, the app-local account survives. Removal is a human logging into each app.

**The whole problem is the two unwired seams:** HR→Okta and Okta→app. A termination fires at the
top and nothing propagates. Accounts that outlive their humans accumulate in the gaps. That's
the entire reason a biweekly review exists.

## 2. Why the process works (the control logic)

Two distinct controls, and being able to name them separately is the key explanatory move:

- **Preventive control** (mostly doesn't exist yet): access is removed *at termination time*,
  automatically. This is what SCIM/lifecycle onboarding buys, per app.
- **Detective control** (the biweekly review): every two weeks, independently verify that
  everyone with access is still legitimate per HR, and evidence what isn't.

Today the detective control **doubles as the removal mechanism** — findings are how removals get
triggered — which is why it hurts. The end-state isn't "automate the review away"; it's build
the preventive control app-by-app, and the review keeps running and **trends toward zero
findings**, which is the *evidence the preventive control works*. An auditor loves this framing:
the review never disappears, it just gets boring.

Why it's trustworthy as a SOX control:

1. **It re-derives everything from source, every cycle.** No state is carried on trust. The
   report is rebuilt from fresh app exports + fresh HR status + live Okta each time.
2. **It never believes a removal claim.** A closed ServiceNow task means nothing until the *next
   cycle's fresh export* shows the account gone. Verified disappearance is the only closure.
   (Demoed live in inverse: task closed, account still present → auto-reopened with a
   NOT-VERIFIED note.)
3. **The pipeline never touches access.** Detection, evidence, tracking only. Humans remove.
   This keeps the control's scope clean and its testing simple — never blur this in control docs.

## 3. How it works (the mechanics)

Per cycle, `biweekly_recon.py` does a **3-way join per account: app roster ↔ TalentHub ↔ Okta**,
then classifies every row down one of these branches:

| Branch | Meaning | Action |
|---|---|---|
| HR Active/Paid Leave/Unpaid Leave | Legitimate | Pass (auto-clear) |
| HR Terminated/Retired, exact match | Confirmed finding | **Auto-create SN REQ→RITM→task** |
| Unexpired exception, living owner | Known non-human/admin account | Pass, logged |
| Exception expired, or owner terminated | Exception no longer self-justifying | Flag — renew/reassign |
| Not found in TalentHub, no Okta account | Strongest **orphan** signal | Human review |
| Anything unparseable/ambiguous | **Loud unknown** | Human review — never silently passes |

Then the closure loop: finding → immutable source-row snapshot in `state.json` → SN ticket →
next cycle, diff against fresh export → verified closure, or **aging + escalation** if still
present. Guard rails: a missing or suspiciously short export (below the 50% sanity ratio) never
auto-closes its findings — absence of evidence ≠ removal.

**Why Okta status is NOT the source of truth** (comes up in every explanation): it breaks both
directions. Unpaid-leave people are often suspended in Okta but legitimately keep app access →
false positives every cycle. And a terminated person whose Okta account was never deprovisioned
looks *clean* in Okta → false negative, which is precisely the failure the control exists to catch.

## 4. How to review the data

The real census tells you what the job actually is: **30 clear terminations vs. 478 ambiguous
rows — 16:1**. The control's cost isn't processing known terminations (those are mechanical,
auto-ticketed); it's adjudicating *"I can't tell."* So when reviewing a cycle report:

1. **Confirmed findings first** — sanity-check the exact matches, confirm tickets exist, done.
   This bucket should be boring.
2. **The loud unknowns are the main event.** The data has 70 rows with a *person's name sitting
   in the status column* — a join defect upstream. A naive `if status == 'Terminated'` check
   passes all 70 silently. That's the concrete story for why unknowns must be loud.
3. **"Not found in TalentHub" (408 rows) = the ownership question.** Service account, vendor,
   contractor, or orphan? The exception list with `owner` + `expiry` converts this from a
   recurring 408-row headache into a one-time adjudication plus ongoing expiry/owner checks.
4. **Cycle-over-cycle is the health metric.** New findings per cycle, closure rate, aged
   findings. Trending down = preventive controls landing. A finding that ages past 2 cycles is
   the escalation trigger.
5. **Distrust anomalous exports.** A tab that shrank 50% didn't have a mass remediation — the
   export broke.

## 5. The rules worth being able to recite

1. **HR employment status is truth; Okta status is evidence.**
2. **Three-branch classifier with a loud unknown — never default-to-fine.**
3. **HR check runs on everyone; an exception never suppresses a termination hit.** (The sim
   found this: avery.gonzalez was Terminated *and* exception-listed, and exception-first
   ordering silently swallowed a terminated privileged account. Rule inverted the order.)
4. **Closure only by verified disappearance in a fresh export.** Claims, screenshots, and
   closed tickets are not proof.
5. **Absence of evidence ≠ removal** — export anomalies freeze closure.
6. **Every exception has an owner and an expiry; a dead owner or lapsed expiry reopens the
   question.**
7. **Certainty + non-privileged = auto; everything else = human.** (The one-line risk-tier rule.)
8. **The detective control never removes access.** Removal automation belongs to the lifecycle
   control with its own testing.

## 6. Where OIG improves the existing process

Sequenced, because that's how you'd actually pitch it:

**Now (Okta as data source — already built):** the orphan leg of the 3-way join.
Enabled/disabled/nonexistent Okta account is signal you get for free that VLOOKUP never gave.

**Campaigns (already demoed on the tenant):**
- **Quarterly UAR** stays a campaign — that's a genuine attestation question ("should this
  person have this access?"), which is different from the biweekly question ("is this person
  employed?"). Articulating *that* distinction is why the biweekly review is a reconciliation
  and not a campaign.
- **Targeted resource campaigns** for high-risk apps or post-incident sweeps.
- **Flagged-population campaigns** — the cycle's findings feed a user-scoped campaign, giving
  managers a formal attest/revoke record on exactly the risky population. (Third demo campaign
  is this.)
- **Ownership registry → reviewer routing**: exception-list owners become the campaign
  reviewers for their accounts, so the 408-row bucket gets certified by the people who
  actually know.

**Lifecycle/SCIM per app (the real prize):** HR term → Okta lifecycle → automatic deprovision.
Each app onboarded converts its manual-removal step into a preventive control, and the review's
findings for that app trend to zero — measurable, per-app ROI you can show cycle over cycle.

**Further OIG levers:** access requests + entitlement bundles clean up the *joiner* side (fewer
over-provisioned accounts to review later); Okta Workflows can bridge apps that have an API but
no SCIM (term event → API call or auto-ticket) as a middle rung before full lifecycle; and
OIG's audit trail gives certification evidence natively instead of hand-assembled workbooks.

**Caveat (provable-results-only):** campaigns/lifecycle/workflows claims are grounded in what
was actually proven on the tenant; the access-request and workflow bridges are standard OIG
capability but *not yet demoed* in this environment — flag them as roadmap, not results, when
presenting.
