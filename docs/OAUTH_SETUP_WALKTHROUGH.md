# OAuth Service-App Auth — Step-by-Step (what + why, for re-creating)

How the detective control's Okta access was moved from a personal SSWS token to an
enterprise-pattern OAuth service app, proven on `demo-beige-haddock-4684` 2026-07-23.
Each step names WHO holds the privilege, because that separation is the whole point.

## Step 0 — Why bother (the one-paragraph justification)

An SSWS token impersonates the human admin who minted it: all of their permissions, no
scoping, dies with their account, and every pipeline action is attributed to a person.
For a SOX control that's three findings: excessive privilege, individual-bound credential,
broken attribution. The fix is a **service principal**: its own identity, its own
least-privilege grants, its own audit trail, surviving personnel changes.

## Step 1 — Generate a key pair (pipeline owner)

```
openssl genrsa -out ~/.secrets/term_revamp_oauth_demo_private.pem 2048
chmod 600 <file>
```

**Why a key pair and not a client secret:** `private_key_jwt` means no shared secret ever
crosses the wire or sits in Okta's config — Okta stores only the PUBLIC key. Authentication
= proving possession of the private key by signing a JWT. Rotation = register a new public
key; compromise of Okta's side reveals nothing usable.

**Custody rules:** private key in `~/.secrets/` (0600, never a LAN share), public half is
exported as a JWK (`kty/alg/use/kid/n/e` — modulus + exponent base64url). The `kid` names
the key so rotation can stage old + new simultaneously.

## Step 2 — Register the API Services app (privileged: tenant admin, ONE TIME)

`POST /api/v1/apps` with:

```json
{
  "name": "oidc_client",
  "label": "BiTerm Detective Control - Service",
  "signOnMode": "OPENID_CONNECT",
  "credentials": {"oauthClient": {"token_endpoint_auth_method": "private_key_jwt"}},
  "settings": {"oauthClient": {
    "application_type": "service",
    "grant_types": ["client_credentials"],
    "response_types": ["token"],
    "jwks": {"keys": [<public JWK>]}
  }}
}
```

**Why `application_type: service` + `client_credentials`:** no human login, no browser
redirect — machine-to-machine only. The response's `client_id` is the principal's name.

**GOTCHA (cost a 400):** `token_endpoint_auth_method` goes under `credentials.oauthClient`,
NOT `settings.oauthClient`. Wrong placement = E0000003 "not well-formed".

## Step 3 — Grant scopes (privileged: tenant admin, ONE TIME)

`POST /api/v1/apps/{appId}/grants` with `{"scopeId": "<scope>", "issuer": "<org URL>"}`,
once per scope:

- `okta.users.read` — the recon's user-status pull
- `okta.apps.read` — app + assignment pull (orphan leg)
- `okta.governance.accessCertifications.read` — campaign results reporting

**Why read-only:** the detective control detects/evidences/tracks — it never removes
access, so it must not be *able* to. The grant list IS the privilege statement an approver
reads.

## Step 4 — Assign admin roles to the client (privileged: tenant admin, ONE TIME)

**The discovery:** scope grants alone still returned `E0000006` permission-denied. On this
org (current Okta behavior), a service app's effective permission =
**granted scopes ∩ admin roles assigned to the client principal**. Two independent layers,
both load-bearing.

`POST /oauth2/v1/clients/{clientId}/roles` with `{"type": "<role>"}`:

- `READ_ONLY_ADMIN` — unlocks the users/apps reads
- `ACCESS_CERTIFICATIONS_ADMIN` — unlocks the governance campaign reads

**Why this is good news for limited-admin life:** the service app sits under the same RBAC
as human admins — same roles, same resource-set machinery, reviewable the same way. A prod
access request must name BOTH layers (scopes + role), and can use a scoped custom role
instead of the standard ones for even narrower fit.

## Step 5 — Mint tokens at runtime (pipeline, every run — NO privilege needed)

1. Build a **client assertion**: a JWT signed with the private key —
   `iss` = `sub` = client_id, `aud` = `<org>/oauth2/v1/token`, `exp` ≈ now+5min,
   `jti` = unique (replay protection), header `kid` = the registered key id.
2. `POST /oauth2/v1/token` with `grant_type=client_credentials`,
   `scope=<space-separated requested scopes>`, `client_assertion_type=urn:ietf:params:
   oauth:client-assertion-type:jwt-bearer`, `client_assertion=<the JWT>`.
3. Receive a Bearer access token (~1h). Cache it; re-mint with a safety margin
   (`okta_client.py` refreshes 5 min early). Every API call sends
   `Authorization: Bearer <token>`.

**Why short-lived tokens matter:** a leaked bearer dies within the hour; the only durable
secret is the private key, which never leaves the box.

## Step 6 — Verify independently, with negative proofs (`verify_oauth.py`)

Never claim "working" from the setup script's own logs. The verifier proves four things
and ends in one `VERDICT:` line:

1. **TOKEN** — assertion round-trip yields a bearer.
2. **READS** — each granted scope exercised against a real endpoint (200s).
3. **DENIED** — a write attempt 403s. *The denial is the least-privilege proof*: an
   auditor doesn't just want to see what it can do, but what it provably cannot.
4. **UNGRANTED** — asking the token endpoint for `okta.users.manage` is refused
   (`consent_required`) — the grant list, not the request, bounds the token.

## Step 7 — Wire it in at one seam (`okta_client.py`)

One module exposes `api()`/`paged()` with the SAME signatures as the old SSWS client, so
consumers swap by import alone:

- `biweekly_recon.py` and `campaign_report.py` → `okta_client` (OAuth, least privilege).
- `seed_tenant.py` **stays SSWS on purpose** — seeding creates users/apps, a privileged
  scaffolding activity. Prod analog: tenant IAM acts with its own credentials; the control
  never inherits them. Keeping the two clients separate makes the separation of privilege
  structural, not just policy.

## Step 8 — Prove the swap changed nothing but the identity

Equivalence test: pull the pipeline's full Okta leg (user→status map + per-app assignment
sets) through BOTH clients and diff. Result 2026-07-23: 7,523 users, 10 apps, **0
mismatches → PASS**. Then `campaign_report.py` ran end-to-end under OAuth (all 3 campaigns).
"Same data, different identity" is the claim — so test exactly that claim.

## The same setup in the Admin Console (no code)

The API steps above are the automatable form; in a real org this is console clickwork by
whoever holds each privilege. The console flow is organized around the app's own tabs.

1. **Create the service app** *(Application Administrator+)*: Applications → Applications →
   Create App Integration → **API Services** → name → Save. The General tab shows the
   Client ID. API Services apps have no users/login flow.
2. **Key-based auth** *(same page)*: General → Client Credentials → Edit → Client
   authentication = **Public key / Private key**; PUBLIC KEYS → Add key. Two options:
   **paste your own public key** (corporate-correct — the private key never existed outside
   your custody) or let Okta generate the pair (private key shown once; it transited Okta's
   UI + a browser — IAM teams usually forbid this). Note the key's KID; leave DPoP off
   unless mandated.
3. **Grant scopes** *(Super Admin — tenant-IAM click)*: app's **Okta API Scopes** tab →
   Grant exactly `okta.users.read`, `okta.apps.read`,
   `okta.governance.accessCertifications.read`. This tab IS the privilege statement an
   access reviewer reads.
4. **Assign admin roles** *(Super Admin)*: app's **Admin roles** tab → Edit assignments →
   Read-only Administrator + Access Certifications Administrator. Afterward the app appears
   in Security → Administrators alongside human admins — the two-layer model made visible,
   reviewable like any admin.
5. **Runtime is always code** (sign assertion → token endpoint). The console's role is
   observability: Reports → **System Log**, filter the app as actor — token grants and every
   read attributed to the service identity, nothing to a person. That attribution is the
   SOX payoff and the console-side verification.

| Console action | Minimum role | Who in prod |
|---|---|---|
| Create API Services app, add public key | Application Administrator | Possibly you |
| Grant Okta API scopes | Super Admin | Tenant IAM |
| Assign admin roles to the app | Super Admin | Tenant IAM |
| Review footprint (scopes tab, admin list, System Log) | Read-only | You, ongoing |

Probe your own standing in a tenant by attempting step 1 and looking for Grant buttons on
the scopes tab — wherever the console stops you is exactly the line an access request must
cross; steps 3–4 being Super-Admin-gated is normal (the one-time IAM handoff), not a blocker.

## Translating to production (the access request)

> Create an API Services app "<control name>" with private_key_jwt (public key attached);
> grant scopes okta.users.read, okta.apps.read, okta.governance.accessCertifications.read;
> assign roles Read-Only Administrator + Access Certifications Administrator (or a scoped
> custom role covering the same reads). Key custody + rotation owned by <team>; setup is
> one-time; the integration holds no admin credential thereafter.

Attach `oauth_bootstrap.py` (the exact privileged actions) and a `verify_oauth.py` PASS run
as evidence the footprint is complete and minimal.
