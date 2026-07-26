# Entitlement-level access certification in Okta OIG — feasibility brief

**Audience:** IAM/security colleagues who need to judge whether this is real before we commit.
**Claim:** every statement below was executed against a live Okta tenant on 2026-07-23 and
verified by reading state back out of the API. Nothing here is from documentation or vendor
marketing. Where something is *not* proven, it says so.

---

## 1 · What was actually demonstrated

An application with **no SCIM, no provisioning, and no API of its own** was made governable in
Okta Identity Governance, and a certification campaign was produced in which the reviewer
certifies **what a person holds inside the app**, not merely that they have it.

The proof chain, end to end:

```
CSV export from the app
        │
        ▼  (per-user role read from the file)
Okta OIG entitlement grant        POST /governance/api/v1/grants
        │
        ▼
Certification campaign            20 review items, each carrying entitlementValue
        │
        ▼
Reviewer sees: "Basim Uchida — Role: Standard User"    (not "has NA Saturn ComSat")
```

Numbers from the run, on a 32-row app export:

| Outcome | Count |
|---|---|
| Users granted an entitlement in OIG | **20** |
| Accounts with **no Okta identity** — structurally ungovernable | **12** |
| Campaign review items generated | **20** (exactly one per grant) |
| Review items carrying entitlement detail | **20 / 20** |

An independent verification script rebuilds the expectation from the source CSV and the live
tenant and confirms the reviewer sees the role the file specified. It passes, and it was
deliberately made to fail (one role altered in the source → mismatch detected) to prove the check
is real rather than decorative.

---

## 2 · The single most important finding

**Entitlement management does not require SCIM or any provisioning integration.**

This is the objection that kills the idea in most rooms — "we can't govern those apps, they have
no connector." Two applications on the tenant (`CRM`, `FinWorld`) are plain SAML 2.0 apps with
`features: []` and provisioning explicitly unsupported, yet both carry real entitlements with
real values. The pilot app built for this test is likewise a bare SAML app that nobody ever signs
into — it exists purely as a governance container.

**What actually determines whether an app can be governed is its TYPE, not its connectivity.**

| App type | Can hold entitlements? |
|---|---|
| SAML 2.0 / custom | **Yes** — proven, this is the pilot |
| Bookmark | **No** — the option is unavailable in the Console |
| SCIM-provisioned | Yes, and additionally supports real deprovisioning |

---

## 3 · What must be enabled in the tenant

**Feature flags** (Admin Console → Settings → Features). Confirmed ENABLED on our tenant:

| Feature | Why it matters |
|---|---|
| **Import user entitlements from CSV** | Entitlements for apps that can't be auto-discovered |
| **Certify resource collections — Resource campaigns** | The certification campaign itself |
| **Certify resource collections — User campaigns** | User-scoped campaigns (flagged populations) |
| **Governance for Workflows** | Lets Workflows drive governance objects |
| **Workflows Audit and Revert** | Change history on the flow — needed if the flow is the control |
| **Workflows Folder Access Control** | Restricts who can edit the control logic |
| **Public API Support for Access Certification Campaign Decisions** | Pull decisions out as evidence |

**Licensing:** Okta Identity Governance, and Okta Workflows if you want the recurring trigger.
Workflows is a separate SKU on some contracts — confirm before designing around it. On our tenant
it is present and enabled.

**Per-application, and this is the operational catch:** each app must have **Entitlement
Management** switched on individually (`settings.emOptInStatus` = `ENABLED`).

> **This is a UI-only action.** There is no public API for it. `PUT /api/v1/apps/{id}` with the
> field changed returns **HTTP 200 and silently ignores it**. `PUT .../features/ENTITLEMENT_MANAGEMENT`
> returns 404 — the name is not in the feature enum. An admin must click it, per app.

Plan for that in the rollout: it is N clicks for N applications, and it cannot be automated.

---

## 4 · What this does not do

Stating the limits plainly is what makes the rest credible.

**It does not remove access.** On an app with no provisioning, a "Revoke" decision in a campaign
is a recorded certification decision — not enforcement. Someone still removes the account in the
app, and proof of removal comes from the account's absence in the *next* export. Any control
narrative claiming otherwise is wrong.

**In this CSV-fed model it cannot see accounts that have no Okta identity.** Entitlement grants
attach to principals — Okta users. Application accounts with no corresponding Okta user (service
accounts, vendor logins, genuine orphans) cannot be granted an entitlement. In our data that is
12 of 32 accounts on the pilot app and 431 across the full estate.

> **Open question, not yet tested.** The campaign API accepts a flag named
> `includeAllAppServiceAccounts`, which implies Okta models app accounts that are not tied to a
> user. That would almost certainly only populate for applications Okta can *import* from — i.e.
> SCIM/provisioning-enabled ones, since a CSV-fed app gives Okta nothing to discover. If that
> holds, connector onboarding may make this population governable rather than merely enforceable.
> **We have seen the flag and nothing else — treat it as a lead to test on the first connected
> app, not as a capability.**

**Therefore this does not replace the biweekly reconciliation.** The division is permanent:

| Question | Owner |
|---|---|
| "Should this person hold this role?" | **Okta OIG** — certification campaign |
| "Is this person still employed?" | **Reconciliation** — HR feed is the only authority |
| "Whose account is this?" | **Reconciliation** — orphan detection |
| "Was the removal actually done?" | **Reconciliation** — next-cycle export diff |

Two controls working together. Anyone presenting this as "Okta now does the termination review"
is overselling it and will get caught in an audit.

---

## 5 · Effort and dependencies, honestly

**Per application:** create a SAML app shell → an admin enables Entitlement Management (UI) →
define the entitlement and its values → load grants from the CSV → point a campaign at it.
Mechanical once the first one is proven, which it now is.

**The real cost we found:** applications currently represented as **Bookmark** apps must be
**rebuilt as SAML apps** — Bookmark cannot be opted in. For us that is 10 applications, plus
re-pointing existing campaigns. This is a rebuild, not a configuration change, and it should be
in any estimate from the start.

**Data dependency:** each app must produce a scheduled export containing, at minimum, an account
identifier, an email that resolves to an Okta user, and whatever field represents the role. Apps
that cannot produce this on a schedule cannot participate — that is a conversation with app
owners, not an Okta problem.

**Automation:** the recurring trigger is built by hand in the Okta Workflows canvas. There is no
API that constructs a flow (`/api/v1/workflows`, `/api/v1/flows`, `/automations/*` all return
405/404). Once built it runs on a schedule with no human involvement.

---

## 6 · How to satisfy yourself it's real

Don't take this document's word for it. Three checks, in ascending order of effort:

1. **Open the campaign** on the tenant and look at a review item. If it names a role rather than
   just an application, entitlement-level certification is working.
2. **Call the API:** `GET /governance/api/v1/entitlements?filter=parentResourceOrn eq "<app ORN>"`
   for a SAML app with entitlement management on, and for a Bookmark app. The first returns
   entitlements; the second returns 404 "Resource not found". That one comparison demonstrates the
   whole app-type constraint.
3. **Run the verifier** (`verify_oig_pilot.py`). It rebuilds expectations from the source CSV plus
   the live tenant and reports a single PASS/FAIL. Change a role in the CSV and it fails — which
   is how you know the check means something.

---

## 7 · Recommendation

Proceed, scoped honestly:

- **Do** adopt OIG entitlements for certification quality and native reviewer experience.
- **Do** budget for the Bookmark-to-SAML rebuild and the per-app UI enablement clicks.
- **Do not** retire the reconciliation. It permanently owns employment truth, orphans and closure
  verification.
- **Do** treat SCIM/OPP onboarding as the separate, higher-value track. The gain is bigger than
  enforcement alone: a CSV feed makes Okta's picture an **assertion** (it cannot be wrong in a way
  Okta can detect), whereas an import makes it an **observation** (drift surfaces on its own).
  That is why the reconciliation is load-bearing today and shrinks under SCIM — and it is where
  the `includeAllAppServiceAccounts` question above should be answered.
- **Note** that nothing here is throwaway: the entitlement model, campaigns, reviewers and
  evidence trail are identical under CSV and under SCIM. Onboarding a connector swaps the data
  source without remodelling the governance.

*All findings reproducible against tenant `demo-beige-haddock-4684`. Technical detail, exact API
payloads and known landmines are recorded in the project `CLAUDE.md`.*
