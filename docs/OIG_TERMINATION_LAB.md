# Lab: Governing a Disconnected App End-to-End — Entitlements, Campaigns, and ServiceNow Closure

**Audience:** anyone on the team running this by hand in the Okta Admin Console for the first
time. **No scripts, no API calls, no CLI, and no assumed prior knowledge** — every step below is
one physical click, one field to fill in, or one button to press. If a step doesn't say to click
something, you don't need to click anything.

**A note on screens:** this lab describes exact menu paths, tab names, and button labels as they
exist at the time of writing. Okta ships UI updates regularly, so if a label doesn't match
*exactly* what you see, look for the nearest equivalent nearby — the order of steps and what each
one accomplishes will still be correct. There are no screenshots in this version; each step tells
you precisely where to look and what should happen instead.

**Companion docs** (read alongside this one, don't re-derive their content):
- [`TERM_FLOW_EXPLAINER.md`](./TERM_FLOW_EXPLAINER.md) — *why* the biweekly review exists. Read
  this first if you're new to the process itself, before you start clicking.
- [`OIG_WORKFLOWS_BUILD_GUIDE.md`](./OIG_WORKFLOWS_BUILD_GUIDE.md) — the companion flow that keeps
  entitlement *grants* in sync with an app export on a schedule.
- [`OIG_FEASIBILITY_BRIEF.md`](./OIG_FEASIBILITY_BRIEF.md) — the "is this actually real" briefing
  for colleagues who weren't in the room.

**Worked example throughout:** every step below uses the proven pilot app, **NA Saturn ComSat**,
so you can compare your own screen against a known-good result. When you run this for a different
app, swap in that app's own name and role list — nothing else about the steps changes.

---

## Before you begin

Check these off before you start clicking. If any box can't be checked, stop and resolve it first
— every lab below depends on it.

- [ ] You can log in to the **Okta Admin Console** for your tenant (URL shape:
      `https://your-org.okta.com/admin/dashboard`).
- [ ] Your Okta admin role includes **Application Administrator** (or Super Admin). If you're not
      sure, ask whoever manages Okta admin roles at your organization — don't guess.
- [ ] Your Okta admin role also includes rights to **Identity Governance** (sometimes called
      **Okta Governance Engine** or **OIG** depending on your tenant's release). If you don't see
      a **Governance** item in the left navigation menu after logging in, this is missing — ask
      your Super Admin to add it before continuing to Lab 2.
- [ ] You have a login to your organization's **ServiceNow** instance with the **itil** role or
      better (needed for Lab 3 and Lab 4).
- [ ] The people who need access to this app already exist as **Okta users** (this lab assumes
      that — it does not cover creating new Okta users).

---

## Two things to understand before you touch anything

Read both of these once, all the way through, before Exercise 1.1. They explain *why* certain
steps below exist — skipping them is the most common way to get confused halfway through.

> **Fact 1 — the type of app decides whether it can be governed at all.** Okta's Entitlement
> Management feature can only be switched on for certain kinds of app. **"Bookmark" apps (a plain
> shortcut tile with no real sign-in configuration) cannot be switched on, ever** — the option
> simply will not appear for them. If the app you're working with today is a Bookmark app, your
> very first step is creating a brand-new **SAML** app to replace it — not trying to change a
> setting on the Bookmark app you already have.

> **Fact 2 — a closed ticket is a claim, not proof.** Later in this lab (Lab 4), you'll build a
> process that notices when a ServiceNow ticket is marked "Closed." A closed ticket only tells you
> that a person clicked a button in ServiceNow. It does **not** tell you whether the access was
> actually removed from the real application. Every closed ticket in this lab gets double-checked
> against real, current data before anyone treats it as done.

---

## Lab 0 — Words you'll see on screen

A short glossary. You don't need to memorize this — just come back to it if a term in a later step
doesn't make sense.

| Term you'll see on screen | What it means |
|---|---|
| **Entitlement** | One governable "thing" an app tracks about a person's access — in this lab, always called `Role`. |
| **Entitlement value** | One specific answer for that entitlement — e.g. `Administrator`, `Standard User`. |
| **Grant** | The record that says "this specific person holds this specific entitlement value on this specific app." |
| **Entitlement Management** | The on/off switch, per app, that turns this whole feature on. |
| **Campaign** | A scheduled review where a real person (the "reviewer") looks at a list and decides, for each row, whether access should stay or be revoked. |
| **Principal** | Okta's word for "the person being reviewed." In this lab, always an Okta user. |

---

## Lab 1 — Bring the app under governance

### Exercise 1.1 — Create the app

**What you'll do:** Create a new application object in Okta so there's something to attach users,
entitlements, and a campaign to. **If your app already exists as a real SAML or OIDC app (not a
Bookmark app), skip to Exercise 1.2.**

1. Log in to the Okta Admin Console.
2. In the left navigation menu, click **Applications**.
3. In the menu that expands, click **Applications** again. You should now see a page titled
   **Applications**, listing every app already in the tenant.
4. Near the top-right of that page, click the button labeled **Create App Integration**.
5. A small window titled **Create a new app integration** appears. Under **Sign-in method**,
   click the circle (radio button) next to **SAML 2.0**.
6. Click **Next** at the bottom-right of that window.
7. You're now on a page with three steps shown across the top: **General Settings**,
   **Configure SAML**, **Feedback**. You're on step 1, **General Settings**.
8. Find the field labeled **App name**. Click inside it, delete whatever text is there by
   default, and type your app's name. Use a naming pattern that won't collide with an app that
   already exists — for example, if a Bookmark app called `BiTerm - NA Saturn ComSat` already
   exists, name this new one `BiTerm OIG - NA Saturn ComSat` instead. **Write this exact name
   down somewhere** — you'll need to search for it again in Exercise 1.3.
9. There is a field labeled **App logo** with a **Browse...** button next to it. You can leave
   this blank and skip it — it has no effect on anything in this lab.
10. Click **Next** at the bottom-right of the page.
11. You're now on step 2, **Configure SAML**.
12. Find the field labeled **Single sign-on URL**. Click inside it and type a web address
    starting with `https://`. If you don't have this app's real login page address handy, type a
    placeholder like `https://placeholder.example.com/sso` — Okta only requires that the text
    look like a valid web address; a real SSO connection is not needed for anything else in this
    lab.
13. Find the field labeled **Audience URI (SP Entity ID)** just below it. Type a similar
    placeholder if you don't have a real value, e.g. `https://placeholder.example.com`.
14. Leave the **Name ID format** dropdown set to its default value, **EmailAddress**.
15. Leave the **Application username** dropdown set to its default value, **Email**.
16. Scroll down past the section called **Attribute Statements (optional)**. Leave it completely
    empty — do not click **Add Another** in that section.
17. Click **Next** at the bottom-right of the page.
18. You're now on step 3, **Feedback**. Click the circle next to the option that reads
    **I'm an Okta customer adding an internal app**.
19. Click **Finish** at the bottom-right of the page.

**What you should see:** the page changes to your new app's own page. The name at the top of the
page matches exactly what you typed in step 8. Just below the name, look for a small colored
label — it should read **Active** in green. If instead it reads **Inactive**, look for a link or
button labeled **Activate** near that same label and click it.

- [ ] **Checkpoint:** go back to **Applications → Applications**, and search for your app's name
      in the search box at the top of the list. Confirm it appears with a status of **Active**
      and a sign-on method of **SAML 2.0**.

### Exercise 1.2 — Add the existing users to the app

**What you'll do:** attach the people who already have Okta accounts to this app, so they can
later receive an entitlement grant. This lab assumes these people already exist as Okta users —
it does not cover creating new ones.

1. From your app's own page (where Exercise 1.1 left off), find the row of tabs near the top:
   **General**, **Sign On**, **Assignments**, and others. Click the tab labeled **Assignments**.
2. Click the button labeled **Assign** (usually top-left of the Assignments tab).
3. A small menu appears with two options: **Assign to People** and **Assign to Groups**.
   - If you only have a handful of people to add, click **Assign to People**.
   - **If you have more than a few dozen people, click Assign to Groups instead** — see the note
     below before you do this.
4. **If you clicked Assign to People:** a search box appears. Type each person's name, click
   their name when it appears in the results, then click the **Assign** button next to their
   name. Repeat for every person, then click **Done** when finished.
5. **If you clicked Assign to Groups:** a search box appears listing Okta groups. Find (or, if
   one doesn't exist yet, go create first) a group whose membership matches this app's real
   population. Click the group's name, click **Assign**, then click **Done**.

> **Why groups, not people, at scale:** assigning one person at a time does not scale to a roster
> in the thousands, and it means every future addition/removal is a manual click here too.
> Assigning a group once, and managing who's in that group through whatever process already adds
> and removes people from it, is the only version of this step that scales.

**What you should see:** back on the **Assignments** tab, every person (or every member of the
group you assigned) now appears in a list, each with a status of **Active**.

- [ ] **Checkpoint:** pick three names from your real roster at random and confirm each one
      appears in this list. If someone from your real roster is missing here, they most likely
      have no Okta account at all — **do not create one to force this checklist to pass.** A
      person with no Okta account is, for this app, an **orphan** — a separate, expected category
      that a different control (the biweekly reconciliation) is responsible for catching. Move on.

### Exercise 1.3 — Turn on Entitlement Management for the app

**What you'll do:** flip the one switch that turns on entitlement tracking for this specific app.

1. In the left navigation menu, click **Governance** (or, on some tenants, **Identity
   Governance**). If you don't see this menu item at all, stop — go back to the "Before you
   begin" checklist; your admin role is missing the Governance permission.
2. In the menu that expands, click **Entitlement Management**.
3. You'll see a list of applications. Use the search box at the top of this list and type the
   exact app name you wrote down in step 8 of Exercise 1.1.
4. Click on your app's name in the search results.
5. Look for a toggle switch labeled **Entitlement Management** (it may say **Enable entitlement
   management for this application**, or appear as a simple on/off switch near the top of the
   app's governance page). Click it so it moves to the **on** position.
6. If a **Save** button appears after you flip the toggle, click it.

**What you should see:** the toggle now shows as **on** / **Enabled**, and stays that way after
you leave the page and come back.

- [ ] **Checkpoint:** navigate away (click **Governance → Entitlement Management** again) and
      re-open your app. Confirm the toggle is still **on**. If the toggle was greyed out and you
      could not click it at all in step 5, you are very likely still looking at a Bookmark app —
      go back to Exercise 1.1 and confirm you created a real SAML app.

> **No shortcut exists for this step.** There is no script, no API call, and no Workflows action
> that can do this for you — a person must click this toggle, once per app, in the Console.
> Everything automated later in this lab (Labs 3–4) assumes this step is already done by a human.

### Exercise 1.4 — Create the entitlement and its values

**What you'll do:** tell Okta what "Role" means for this app, and list every value it can hold.

1. Still on your app's page inside **Governance → Entitlement Management**, look for a tab or
   section labeled **Entitlements**. Click it.
2. Click the button labeled **Create Entitlement** (or **Add Entitlement**).
3. In the field labeled **Name**, type `Role`.
4. Find the field or dropdown labeled **Data type**. Select **String**.
5. Find a toggle or checkbox labeled **Multi-value** (it may be phrased as **Allow multiple
   values**). **Read the decision box below before you touch this toggle** — get this wrong and
   you may need to delete and recreate the entitlement later.
6. Below that, find a section for adding values — usually a button labeled **Add Value** or a
   growing list of blank rows. Click it once for each role this app actually has, and type the
   exact role name into each new row — for example, for NA Saturn ComSat: `Standard User`,
   `Read Only`, `Power User`, `Administrator`, `Service Account`. **Type these exactly as the app
   itself names them.** Do not copy this exact list for a different app — check that app's own
   roles first.
7. Click **Save** (or **Create**) at the bottom of the page.

> **Decision point — Multi-value, on or off?** Think about your actual source data before you
> answer this.
> - Turn it **off** (single-value) if you're confident each person holds exactly one role in this
>   app, always.
> - Turn it **on** (multi-value) if the same person can show up with more than one role for this
>   app in your source data (for example, duplicate accounts, or a mid-cycle role change that left
>   two rows behind). **This matters more than it sounds like it should:** if you leave it off and
>   later load grants using a process that only looks at "the first row it finds" for each person,
>   someone who appears as both `Power User` and `Administrator` will get recorded as only
>   `Power User` — and a reviewer looking at that grant later will never see the Administrator
>   access at all. If you're not sure, open your app's own user export and check whether any
>   single person appears more than once before choosing.

**What you should see:** back on the **Entitlements** tab, you now see one entitlement named
`Role`, and clicking on it shows the exact list of values you typed in step 6.

- [ ] **Checkpoint:** the values listed under `Role` match the app's real role names exactly —
      same spelling, same capitalization, nothing extra, nothing missing.

### Exercise 1.5 — Grant the entitlement to your users

**What you'll do:** record, for each person, which `Role` value they actually hold.

**If you only have a handful of people (grant by hand):**

1. Still inside **Governance → Entitlement Management → your app**, click the tab labeled
   **Assignments** (or **Grants**).
2. Click the button labeled **Grant** (or **Add Grant**).
3. In the search box that appears, type the person's name or email and click it when it appears.
4. In the dropdown or list that appears next, click the one `Role` value this person actually
   holds.
5. Click **Save** (or **Grant**).
6. Repeat steps 2–5 for every remaining person.

**If you have a larger roster (bulk import):**

1. From the same **Assignments** (or **Grants**) tab, look for a button labeled **Import** (this
   may instead appear on the **Entitlements** tab, next to the `Role` entitlement itself —
   look in both places).
2. Click **Import**, and look for an option to **download a template** or **CSV format** —
   click it first if it's offered, so you know exactly which columns Okta expects rather than
   guessing.
3. Open that template and fill in one row per person: the column that identifies the person
   (this is very likely their **email address**, but confirm from the template — do not assume)
   and the column for their `Role` value.
4. **Before you upload anything: make sure no single person appears more than once in your file.**
   If your source data has duplicates (see the decision box in Exercise 1.4), collapse them down
   to one row per person first, keeping whichever value(s) your multi-value decision calls for.
   Uploading a file with duplicate people is the single most common way this step goes wrong.
5. Click **Choose File** (or drag your completed file into the upload area), select your file,
   and click **Import** (or **Upload**).
6. Wait for the page to show a result — usually a count of rows successfully imported, and a
   separate count (and often a downloadable list) of rows that failed. **Open the failed-rows
   list if one appears** — don't just note the success count and move on.

> **Doing this on a schedule, not once:** if this app gets a fresh export every two weeks and you
> want the grants kept in sync automatically instead of re-importing a CSV by hand every cycle,
> that's a separate build using Okta Workflows — see `OIG_WORKFLOWS_BUILD_GUIDE.md`. Come back to
> that after this lab; it assumes everything in Lab 1 already exists.

**What you should see:** back on the **Assignments** tab, every person you granted now appears in
the list with the correct `Role` value shown next to their name.

- [ ] **Checkpoint:** pick three names — including, if possible, someone you know had duplicate
      rows in your source data — and confirm the `Role` value shown for each one matches the
      source export exactly.

---

## Lab 2 — Build the certification campaign

A campaign is what actually puts this list of grants in front of a human reviewer.

### Exercise 2.1 — Create the campaign

1. In the left navigation menu, click **Governance**.
2. In the menu that expands, click **Access Certifications**.
3. Click the tab or menu item labeled **Campaigns**.
4. Click the button labeled **Create Campaign**.
5. You're asked to choose a campaign type — you'll see options that include something like
   **Resource** and **User**.
   - Click **Resource** if this campaign should review "everyone who has access to this one app"
     — the normal choice for a routine per-app review.
   - Click **User** instead if this campaign should review "this specific, named list of people,"
     regardless of which app they're on — use this only for a targeted population (for example,
     everyone this cycle's reconciliation flagged), not for a routine app review.
6. In the field labeled **Campaign name**, type a clear name following your program's own naming
   pattern — for example `BiTerm — Quarterly UAR: NA Saturn ComSat` or
   `BiTerm — Targeted Resource Review: NA Saturn ComSat`.
7. Click **Next**.

### Exercise 2.2 — The one setting that silently ruins the campaign if you skip it

**Read this whole exercise before clicking anything.** Getting this wrong produces a campaign
that looks completely normal and gives you no error — the only way to catch the mistake is to
check a review item afterward, which step 5 below tells you to do.

1. You should now be on a step of the wizard labeled **Resources** (or **Scope**).
2. Find your app in the list (search for its name if the list is long) and click the checkbox
   next to it to select it.
3. Look for a toggle or checkbox labeled **Include entitlements** somewhere on this same step.
   Turn it **on**.
4. Still on this step, find the specific row for the app you just selected, and look for a
   second, separate toggle on that row labeled something like **Include all entitlements and
   bundles** (sometimes phrased as **Include all entitlement values**). Turn this **on** as well.
   **This is a different switch from step 3 — both must be on.**
5. Click **Next**.

> **Why this is worth reading twice:** if you leave either switch in step 3 or step 4 off, Okta
> will still build the campaign successfully — no error, no warning. But every review item in it
> will just say the person "has access to" the app, with no mention of which `Role` value they
> hold. Your reviewer ends up certifying "should this person have the app" instead of "should this
> person be an Administrator" — which defeats the entire purpose of this lab. There is no
> notification if you get this wrong; the only way to catch it is Exercise 2.4's checkpoint below.

### Exercise 2.3 — Reviewer, schedule, and remediation settings

1. You're now on a step labeled **Reviewers** (or similar). Choose who reviews this campaign —
   click the option matching your program's norms (a specific named person, the principal's
   manager, or the resource owner) and fill in whichever name/search field appears for that
   choice.
2. Click **Next**.
3. You're now on a step labeled **Schedule**. For a first run, click **One-time** (sometimes
   labeled **Does not repeat**). Once you're confident everything works, come back and change
   this to a recurring schedule — the same screen supports **Biweekly**, **Quarterly**, and other
   intervals as a dropdown option.
4. Click **Next**.
5. You're now on a step labeled **Remediation** (or **Remediation settings**). Select
   **No automatic action** (sometimes labeled **Manual remediation**).
6. Click **Next**.

> **Say this out loud to whoever you're training on this:** for an app with no live connection to
> Okta (no SCIM, no provisioning), a reviewer choosing **Revoke** here is only recording a
> decision — it does **not** delete anything in the real application. Someone still has to go
> remove the access by hand. That's exactly what Labs 3 and 4 automate the tracking of.

### Exercise 2.4 — Launch and verify

1. You should now be on a final **Review** (or **Summary**) step, showing everything you
   configured. Confirm the app, reviewer, schedule, and remediation settings all match what you
   intended.
2. Click **Launch Campaign** (or **Create Campaign**, depending on your tenant's exact wording).
3. Click back into the campaign you just launched (find it in the **Campaigns** list from
   Exercise 2.1, step 3, and click its name).
4. Find the list of review items inside it and click on **any one** of them, for any single
   person.
5. **Look at what that review item actually shows you.** It must display the `Role` entitlement
   name and the specific value (for example, "Role: Standard User"). If it only shows the app's
   name with no mention of `Role` at all, go back to Exercise 2.2 — one of the two toggles there
   was missed, and you likely need to end this campaign and rebuild it.
6. If you're running this as a pilot rather than a live review, ask your reviewer to certify at
   least one item as **Revoke** so that Lab 3 has a real decision to react to.

- [ ] **Checkpoint:** the campaign is active, and the one review item you opened names both the
      entitlement and its value — not just the app.

---

## Lab 3 — Automatically open a ServiceNow ticket when access is revoked

A reviewer clicking **Revoke** in Lab 2 does not remove anything by itself (Fact re-stated: it's
only a decision). This lab builds the process, using **Okta Workflows** (Console-only — there is
no script or API for building a Workflows flow), that turns a confirmed termination finding into a
real, tracked removal task in ServiceNow.

> **Why we go through the Service Catalog, and not a simpler-looking direct table write:** on
> this project's ServiceNow instance, trying to write directly into the raw ticket tables
> (`sc_request`, `sc_req_item`, `sc_task`) was blocked by ServiceNow's own access rules, even for
> an account that could otherwise read those same tables fine. Ordering from a proper **Service
> Catalog item** instead is the version that reliably works — it builds the whole ticket chain
> (a request, a request item, and a task) for you in one action.

### Exercise 3.1 — Create a folder for this automation

1. In the Okta Admin Console left navigation menu, click **Workflow**.
2. Click **Workflows** in the menu that expands (or click the **Workflows** tile if that's what
   your tenant shows instead of a menu item — this opens a separate Workflows console in a new
   area).
3. Look for a **Folders** panel, usually on the left side of the Workflows console. Click the
   button to create a new folder (often a **+** icon or a button labeled **New Folder**).
4. Type a name for the folder — for example `BiTerm Termination Review` — and confirm/save it.
5. If your tenant has **Workflows Folder Access Control** turned on, open that folder's
   permission settings (often a gear icon or right-click menu on the folder) and restrict who can
   edit it to a small, named list of people. **Do this now, before building anything inside it** —
   once this flow creates real tickets, it is part of your control, and it needs the same
   "who can change this" discipline as anything else in the control.

### Exercise 3.2 — Connect Workflows to your ServiceNow instance

1. Still inside the Workflows console, find the **Connections** panel (usually accessible from
   the left side or a top toolbar icon).
2. Click **New Connection** (or **+**).
3. Search for **ServiceNow** in the connector list. If a ServiceNow connector option appears,
   select it; if you don't see one, search instead for a generic **HTTP** or **API Connector** —
   either can work, but confirm which one supports the specific action you need before building
   the whole flow around it (see the note below).
4. Fill in your ServiceNow instance's base web address (e.g. `https://your-instance.service-
   now.com`) and the credentials for a **service account** — not a person's own login. Ask
   whoever manages ServiceNow accounts for one if it doesn't already exist.
5. Click **Create** (or **Save**) to finish the connection.

> **Confirm the exact action you need is supported before building further.** The action this
> lab needs is "order an item from the Service Catalog," which is not the same as a plain
> "create a record" action some connectors offer by default. If the ServiceNow connector's
> built-in actions don't cover it, use the generic API Connector to send a request directly to
> `/api/sn_sc/servicecatalog/items/{catalog_item_sys_id}/order_now` on your instance — this is
> the path proven to work on this project.

### Exercise 3.3 — Build the flow

1. Back in the **Workflows** console, inside the folder you made in Exercise 3.1, click
   **New Flow** (or the **+** button).
2. Name the flow — for example `Open removal ticket on confirmed termination`.
3. Click the **+** on the trigger card (the very first card, usually already on the canvas) to
   choose how this flow starts.
   - If this flow should run on the same recurring drop-file schedule as the grant-sync flow in
     `OIG_WORKFLOWS_BUILD_GUIDE.md`, **add this ticket-creation logic as extra cards inside that
     existing flow's loop, instead of building a second, separate flow.**
   - If you're building this as its own flow reacting to campaign decisions instead, choose
     **Schedule → Run on a schedule** as the trigger, since there is no proven, ready-made trigger
     card in Workflows for "a campaign decision was made" — treat this as something to test
     yourself in your own tenant rather than something this lab can promise works out of the box.
4. Add a card that loops over each finding needing a ticket — search the card library for
   **For Each** (under **Flow Control**) and drag it onto the canvas, connected after your
   trigger.
5. Inside the loop, add a card that builds the request body ServiceNow needs — search for
   **Compose** or **Object** in the card library, and fill in the fields this project's catalog
   item expects: application, account/alias, UPN, employee ID, HR status, Okta status, reason,
   and a cycle/review identifier. Adjust this list if your own catalog item's fields differ.
6. Add your ServiceNow (or API Connector) action card next in the loop, and configure it to
   **POST** to the order-now endpoint from Exercise 3.2, sending the object you built in step 5
   as the request body.
7. Add a card immediately after that captures the response — specifically the new request
   number ServiceNow hands back — so you have a record of exactly which ticket was created for
   which person.
8. **Before you trust this with real data, add one more check inside the loop, before the ticket-
   creation card:** a lookup against a table (or the ticket system itself) confirming this exact
   finding hasn't already been ticketed this cycle. If it has, skip creating a second ticket for
   it. Search the card library for **Table → Lookup Row** if you're keeping this record in a
   Workflows table.
9. Add an error-handling branch on the ticket-creation card (most connector cards have a built-in
   **On Error** output) that sends a notification — an email or chat message card works — to a
   real person if the ticket creation fails. **A flow that fails silently here is worse than doing
   this by hand**, because now the finding has no ticket and nobody knows it.
10. Click **Save** (or the flow saves automatically, depending on your tenant), then click
    **Turn On** (or **Activate**) once you're ready to test it.

**What you should see:** run the flow manually against one test finding (most Workflows canvases
have a **Test** or **Run once** button for exactly this purpose). After it finishes, log in to
ServiceNow and confirm a request now exists with the right ticket chain underneath it (a request,
a request item, and a task), showing the details you sent in step 5.

- [ ] **Checkpoint:** the ServiceNow ticket exists, carries the correct details, and is assigned
      to the right team.

---

## Lab 4 — Recognize ticket closure, but verify before you believe it

This is where Fact 2 from the top of this document becomes an actual, built process. A closed
ServiceNow task means a person clicked a button. It does not, by itself, mean the access is gone.

### Exercise 4.1 — Build a flow that notices closed tickets

1. In the same Workflows folder, click **New Flow**.
2. Name it — for example `Verify closed removal tickets`.
3. Set its trigger card to **Schedule → Run on a schedule**, matching your review cycle (biweekly
   is the natural default here). This is the reliable, Console-only way to check in on
   ServiceNow's state regularly.
4. Add a ServiceNow (or API Connector) action card that reads tickets in a closed state that
   this flow hasn't looked at yet — for example, tasks with a **state** of "Closed Complete" that
   don't yet carry a note or flag saying this flow already processed them.

> If your ServiceNow instance can send Okta Workflows a message the moment a ticket's status
> changes (an "outbound REST message"), that's a valid alternative to polling on a schedule — but
> confirm that capability actually exists on your own instance first; this lab only vouches for
> the scheduled-check version above as proven.

### Exercise 4.2 — For each closed ticket, check reality before trusting it

Add these cards inside a **For Each** loop over the closed tickets found in Exercise 4.1:

1. Add a card that looks up the original finding this ticket was created for (the same
   application, account, and cycle/review identifier you recorded when the ticket was created in
   Lab 3, Exercise 3.3 step 7).
2. Add a card that re-checks current reality — read the freshest available export for this app,
   or the person's live Okta status, whichever is the actual source of truth for this app.
3. Add a decision/branch card (search the card library for **If/Else** or **Condition**) that
   compares the two:
   - **Branch A — the access really is gone now:** add a card that writes an evidence note back
     onto the ServiceNow ticket (most ServiceNow connectors have an **Add Work Note** or
     **Update Record** action for this) recording the before/after fact, and mark the underlying
     finding as closed in wherever your team tracks findings.
   - **Branch B — the access is still there:** **do not treat this as done.** Add a card that
     writes a work note reading something like "Removal not verified — access still present as of
     [date]," then add a second card that re-opens the ticket (an **Update Record** action setting
     its state back to an active/open value) and reassigns it. Leave the underlying finding open
     and let it age/escalate per your program's normal rules for something that survived past its
     expected closure cycle.
4. **Add one more guard before Branch A can ever fire:** compare the size of the fresh export you
   pulled in step 2 against a recent, healthy export for the same app. If it's dramatically
   smaller or looks malformed, route to a third branch that does neither A nor B — just flags
   "cannot verify, export looks broken" for a human to look at. **A broken export must never be
   allowed to look like a mass removal.**

### Exercise 4.3 — Feed real results back into governance

1. For anyone whose access was **verified** gone in Branch A, and who still holds an Okta account
   and an entitlement grant for this app from Lab 1, that grant is now stale. Go check, inside the
   Entitlement Management screens from Exercise 1.5, whether you can select and remove that grant
   directly. If no such option exists in your tenant's version of the Console, the practical
   fallback is simply making sure this person still appears in the *next* campaign cycle so a
   reviewer can issue a fresh Revoke decision against current reality.
2. Record the verified closure wherever your program already tracks review results over time, so
   that "findings going down cycle over cycle" — the actual health signal for this whole control —
   reflects real, double-checked outcomes, not just tickets someone marked closed.

> **Before trusting either flow with real data, run this test once:** in ServiceNow, close a
> ticket **without** actually removing the underlying access (leave the person's access in place
> in the real app or export). Then run the Exercise 4.1 flow and confirm two things: it does
> **not** write a "verified" note, and it **does** reopen the ticket with a clear explanation.
> **If your flow can be fooled by a ticket that's closed while access still exists, it is not
> ready to run for real** — this exact test, run against deliberately altered data, is what
> proved the earlier script-based version of this same idea on this project.

- [ ] **Checkpoint:** the deliberate false-closure test above produces a reopened ticket, not a
      false "verified" note.

---

## Appendix A — Things that will trip you up (read before you build, not after)

- **Bookmark apps can never have Entitlement Management turned on.** There is no setting to
  change on the Bookmark app itself — you must create a new SAML (or OIDC) app instead.
- **The Entitlement Management toggle (Exercise 1.3) can only be flipped by a person clicking it
  in the Console.** No script or API call can do this, ever — for any app, before anything else in
  this lab can happen.
- **The two toggles in Exercise 2.2 are the single easiest way to build a campaign that looks
  fine but certifies the wrong thing, with no error to warn you.** Always open one review item
  after launching a campaign and confirm it names the entitlement and its value.
- **A `Role` value list is specific to one app.** Never copy one app's list of roles onto another
  app to save time — check the second app's real roles first.
- **Leaving "Multi-value" off when a person can hold more than one role in the same app can hide
  their real, higher-privileged access from a reviewer.** Check your source data for duplicate
  people before deciding this in Exercise 1.4.
- **A closed ServiceNow ticket only proves someone clicked a button.** It never proves the access
  is actually gone — that's the entire reason Lab 4 exists.
- **A person with no Okta account can never receive an entitlement grant, and will never appear in
  a campaign.** That's expected, not a bug — it's the job of the separate reconciliation control
  described in `TERM_FLOW_EXPLAINER.md`, not something to fix by creating an Okta account just to
  make this lab's checklists pass.
- **Workflows flows can only be built by hand in the Console**, on every release checked so far —
  there is no script or API shortcut for building the flow itself.

## Appendix B — What to check before calling this production-ready

Direct answer to "is there anything I'm missing": yes — a handful of things this lab intentionally
didn't cover. Decide on each of these before treating this as a finished control, not just a
working walkthrough.

- **Who is allowed to edit the Workflows folder from Lab 3–4, and what's the approval process for
  changing it?** Once real removal tickets are created and verified by a flow, that flow *is* part
  of your control and needs the same change-approval rigor as anything else in it.
- **How long does Workflows keep flow-run history, and how long do campaign decisions stay
  available?** Compare both against how long your organization is actually required to keep this
  evidence. If either is shorter, export the evidence somewhere durable rather than assuming the
  platform keeps it forever.
- **Who gets told about the cases a human has to look at** — a high-risk finding, an ambiguous
  employment status, a ticket that failed to reopen cleanly? This lab writes notes and reopens
  tickets, but a person still needs to be alerted to go look. Pick a real notification method
  (email, a chat channel, a dashboard someone actually checks) before relying on this for real.
- **How are non-human and service accounts reviewed?** A campaign reviewer model built around a
  single person doesn't fit a service account well. If your app census includes these, you likely
  need an ownership record (who owns this account, and until when) to decide who reviews it in
  Lab 2.
- **If any app later gets a real provisioning/SCIM connection, it's worth testing (not assuming)
  whether Okta can then represent that app's orphaned accounts as reviewable objects in a
  campaign.** There's a setting name that hints this might be possible
  (`includeAllAppServiceAccounts`), but it has not been tested and should not be treated as a
  working feature until someone tries it against a real connected app.
- **Reducing how much needs reviewing in the first place** (access requests, pre-approved
  entitlement bundles) is a separate, worthwhile follow-on project once Labs 1–4 are running
  smoothly — not covered here.
- **If you're rehearsing this in a non-production sandbox** (a ServiceNow Developer instance, a
  demo Okta org), remember that sandbox has its own housekeeping needs that don't apply to a real
  production system but will break your demo if ignored — instances that get wiped after a period
  of inactivity, credentials that need periodic rotation, and demo data that shouldn't linger
  past its purpose.
