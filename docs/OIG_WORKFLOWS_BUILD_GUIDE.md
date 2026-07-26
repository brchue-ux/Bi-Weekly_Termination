# Building the biweekly flow by hand in Okta Workflows

There is **no API that builds a flow** — `/api/v1/workflows`, `/api/v1/flows` and `/automations/*`
all return 405/404. Flow construction is Console-only. That is not a limitation for you: prod is
UI-driven anyway. It does mean this part is typed, not scripted.

This guide assumes the ComSat pilot pattern already proven on the tenant: an app with Entitlement
Management enabled, an entitlement named `Role`, and grants carrying each person's role value.

---

## Before you open Workflows

You need three IDs. Get them from the Admin Console (Applications → the app → the URL contains
the app ID) or keep the pilot's `oig_pilot_*.json` files handy.

| Thing | Pilot value | Where it's used |
|---|---|---|
| App ID | `0oa15k4h5x3yZneqN698` | grant target |
| Entitlement ID | `esp119gd9dqVhRIdA697` | which entitlement to set |
| Entitlement value IDs | 5 of them (one per role) | which value to grant |

Fetch the value IDs once: `GET /governance/api/v1/entitlements/{entitlementId}/values`.
They never change unless you edit the entitlement, so paste them into a Workflows **table**
rather than looking them up on every run.

---

## Step 1 · Get into Workflows and make a folder

**Admin Console → Workflow → Workflows** (or the Workflows tile). Create a folder named
`BiTerm Termination Review`.

Do this before building anything. **Workflows Folder Access Control** is enabled on your tenant,
and the folder is the unit of permission — it's how you stop someone editing the control logic
without going through change management. Once the classifier lives in a flow, **the flow is the
SOX control**, and it needs the same edit restrictions and change history any prod code would get.

---

## Step 2 · Build the lookup table

**Tables → New Table**, name it `ComSat Role Values`, columns:

| roleName | valueId |
|---|---|
| Standard User | `ent...` |
| Read Only | `ent...` |
| Power User | `ent...` |
| Administrator | `ent...` |
| Service Account | `ent...` |

Rationale: the drop CSV carries a human role name; the API needs an opaque value ID. Doing that
translation with a table lookup keeps the flow readable and means adding a role later is a table
row, not a flow edit.

---

## Step 3 · Create the scheduled flow

**New Flow** inside your folder. Name it `Biweekly — ComSat entitlement sync`.

**The trigger card:** click *Add event* → **Schedule → Run on a schedule**. Set it to run every
**14 days** at a fixed hour. This card is the entire answer to "how does it kick off with nobody
clicking anything" — there is no server, no cron, no person remembering.

Turn on **Save all data** while you're building so you can inspect what each card actually
returned; consider turning it off later if the drop contains anything sensitive.

---

## Step 4 · Read the drop file

Add a card for wherever the file lands:

- **SharePoint / OneDrive** → *Get File Content* (or *List Files in Folder* first, if you want the
  newest file rather than a fixed name)
- **Box / Google Drive** → equivalent *Download File* card
- **SFTP** → an HTTP/connector card, or have the drop land somewhere with a native connector

Then **CSV → Parse CSV** (Workflows has a CSV helper card) to turn the text into rows.

**If you take one piece of advice from this guide:** have each app drop to a **predictable path
with the date in the filename**, exactly like the mock folders. "Newest file in the folder" logic
is where these flows quietly break — a leftover file from a failed run becomes this cycle's truth.

---

## Step 5 · Loop the rows

Add **Flow Control → For Each** over the parsed rows. Inside the loop:

1. **Okta → Read User** using the row's `email`.
   - **Branch on failure.** No Okta user = an orphan. It cannot be granted an entitlement and it
     must not silently vanish — send it to a "cannot govern" list you write out at the end.
     On the ComSat pilot that was **12 of 32 rows**, so this branch is the common case, not an
     edge case.
2. **Table → Lookup Row** in `ComSat Role Values` on the row's `app_role` → gives you `valueId`.
   - No match = an unknown role. Same treatment: collect it, don't skip it.
3. **API Connector → POST** to `/governance/api/v1/grants` with:

```json
{
  "grantType": "CUSTOM",
  "target":          { "externalId": "<APP_ID>",  "type": "APPLICATION" },
  "targetPrincipal": { "externalId": "<USER_ID>", "type": "OKTA_USER" },
  "action": "ALLOW",
  "entitlements": [ { "id": "<ENTITLEMENT_ID>", "values": [ { "id": "<VALUE_ID>" } ] } ]
}
```

Use the **Okta API Connector** card so it authenticates as the tenant rather than you pasting a
token into a flow. Never hardcode a token in a card.

---

## Step 6 · Handle re-runs

Grants accumulate. Before the POST, either:

- **GET** `/governance/api/v1/grants?filter=targetResourceOrn eq "<ORN>"` once *before* the loop
  and skip principals you already granted (this is what the Python loader does), or
- accept duplicates and reconcile later — **not recommended**, it makes the certification queue
  noisy and the numbers stop reconciling.

The ORN format is `orn:okta:idp:{ORG_ID}:apps:{app.name}:{app.id}` — note that's the **org ID**
(`00o1...`), not your subdomain. Getting this wrong returns a confusing 404.

---

## Step 7 · Write the exception list out

After the loop, take everything the branches collected — no Okta user, unknown role, API errors —
and send it somewhere a human reads: an email card, a Slack/Teams card, or a file written back
to the drop location.

**A flow that silently drops rows is worse than no flow.** This is the same "loud unknown" rule
the reconciliation already enforces; it has to survive the move into Workflows.

---

## Step 8 · Kick off the campaign

Either let the campaign run on its own recurrence (**Certifications → the campaign → Schedule →
Recurrence**, which supports biweekly natively), or have the flow create one via
`POST /governance/api/v1/campaigns`.

**If the flow creates it, you must set both flags or you get the wrong campaign silently:**

```json
"resourceSettings": {
  "type": "APPLICATION",
  "includeEntitlements": true,
  "targetResources": [
    { "resourceId": "<APP_ID>", "resourceType": "APPLICATION",
      "includeAllEntitlementsAndBundles": true }
  ]
}
```

With **neither** flag, Okta creates the campaign happily and generates **app-level review items
with no error at all** — reviewers see "has the app" instead of "is an Administrator", and nothing
tells you. With only the inner flag you get a 400. This bit me during the pilot; check one review
item after the first run and confirm it carries `entitlementValue`.

---

## Step 9 · Turn on error handling before you trust it

- Add an **error-handling path** on the flow; on failure, notify a human. A control that fails
  silently is worse than a manual one.
- Check **Flow History** after the first few runs — it's your execution evidence. Confirm the
  retention period satisfies your audit evidence requirement; if it's shorter, export runs to
  wherever your SOX evidence actually lives.
- **Workflows Audit and Revert** is enabled on the tenant — that's your change history and
  rollback for the flow itself. Name it in the control narrative.

---

## Generalizing from the pilot to all 10 governed apps

The pilot guide above is written for one app. All 10 term-review apps are now SAML apps with
Entitlement Management enabled, a `Role` entitlement, and grants loaded — see `oig_apps.json` for
the full app→id→roles manifest, which is the source of truth for the IDs Step "Before you open
Workflows" tells you to gather. Two honest facts about scaling the flow:

- **The flow logic does not change per app — only the three IDs and the role table do.** The
  cleanest Console build is ONE flow with the app list (app_id, entitlement_id, drop path) in a
  Workflows **table**, looped with an outer *For Each* over apps wrapping the inner *For Each* over
  rows. Build it once against ComSat, confirm a review item carries `entitlementValue`, then add
  the other nine as table rows — not nine copies of the flow.
- **Role values are per-app, not shared.** NA Saturn Corp legitimately has 4 role values, not 5.
  Build the role-lookup table keyed on (app, roleName)→valueId, and populate it per app from
  `GET /governance/api/v1/entitlements/{id}/values` — never assume one taxonomy across apps. This
  is the exact trap the mock data hid (every mock app shared five labels); real app owners will
  give you different vocabularies, and the table must carry each app's own.

The Python scripts `oig_load_all.py` (grants) and `oig_build_campaigns.py` (campaigns) are the
**tested reference implementation of what the flow does by hand** — same endpoints, same payloads,
same idempotency (skip already-granted principals; both required entitlement flags on the
campaign). When the flow's output disagrees with these scripts on the same drop, the scripts are
the oracle. They are not the production control (prod is UI-driven), but they prove the API calls
the flow cards make are correct before you trust the flow.

## What this flow does NOT do

Be explicit about this with your team, because it's the part people assume away:

- It does **not** remove access. Disconnected apps have no provisioning; a Revoke is a
  certification decision, not enforcement.
- It does **not** replace the reconciliation. HR-status truth, orphan detection, ServiceNow
  ticketing and next-cycle closure verification all stay where they are.
- It does **not** see the orphans. Entitlement grants attach to Okta users, so accounts with no
  Okta identity cannot be granted anything by this flow. (Whether *import-capable* apps can
  surface them as app service accounts is an untested open question — see the OIG section of the
  project CLAUDE.md. It does not change anything for a CSV-fed app like this one.)

The flow's job is keeping Okta's picture of "who holds what" current, on a schedule, with nobody
typing anything. That's genuinely valuable — it just isn't the whole control.
