"""
Independent verification of the OAuth service-app setup (verification gate:
no claim of "working" from the bootstrap's own logs). Proves four things:

  1. TOKEN     — private_key_jwt round-trip yields a Bearer token for the read scopes.
  2. READS     — users / apps / governance-campaign reads return 200 with that token.
  3. DENIED    — a write attempt (POST /api/v1/apps) is rejected (403), because no
                 manage scope was granted: the least-privilege proof.
  4. UNGRANTED — requesting okta.users.manage at the token endpoint is refused.

Ends in a single line: VERDICT: PASS | FAIL (exit code matches).
"""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from okta_oauth import get_access_token

ORG = "https://demo-beige-haddock-4684.okta.com"
ADMIN_TOKEN_PATH = os.path.expanduser("~/.secrets/claude_3rd_party.txt")
PRIVATE_KEY_PATH = os.path.expanduser("~/.secrets/term_revamp_oauth_demo_private.pem")
APP_LABEL = "BiTerm Detective Control - Service"
KID = "biterm-2026-07"
READ_SCOPES = ["okta.users.read", "okta.apps.read", "okta.governance.accessCertifications.read"]

failures = []


def check(name, ok, detail):
    print(f"  [{'ok' if ok else 'FAIL'}] {name}: {detail}")
    if not ok:
        failures.append(name)


def http(method, path, bearer, body=None):
    req = urllib.request.Request(f"{ORG}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"Bearer {bearer}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read() or "null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "{}")


def lookup_client_id():
    """Resolve client_id from the app registration (admin read; not part of the proof)."""
    with open(ADMIN_TOKEN_PATH) as f:
        admin = f.read().strip()
    req = urllib.request.Request(f"{ORG}/api/v1/apps?q={urllib.parse.quote(APP_LABEL)}")
    req.add_header("Authorization", f"SSWS {admin}")
    with urllib.request.urlopen(req) as resp:
        apps = json.loads(resp.read())
    app = next((a for a in apps if a.get("label") == APP_LABEL), None)
    if not app:
        sys.exit(f"service app '{APP_LABEL}' not found — run oauth_bootstrap.py first")
    return app["credentials"]["oauthClient"]["client_id"]


def main():
    client_id = lookup_client_id()
    print(f"service app client_id: {client_id}\n")

    # 1. TOKEN — round-trip with exactly the granted read scopes.
    try:
        token = get_access_token(ORG, client_id, PRIVATE_KEY_PATH, scopes=READ_SCOPES, kid=KID)
        check("TOKEN", True, f"bearer issued ({len(token)} chars)")
    except SystemExit as e:
        check("TOKEN", False, str(e))
        print("\nVERDICT: FAIL")
        sys.exit(1)

    # 2. READS — each granted scope exercised against a real endpoint.
    status, body = http("GET", "/api/v1/users?limit=1", token)
    check("READ users", status == 200 and isinstance(body, list), f"HTTP {status}")
    status, body = http("GET", "/api/v1/apps?limit=1", token)
    check("READ apps", status == 200 and isinstance(body, list), f"HTTP {status}")
    status, body = http("GET", "/governance/api/v1/campaigns?limit=1", token)
    check("READ campaigns", status == 200, f"HTTP {status}")

    # 3. DENIED — no manage scope granted, so a write must be rejected before validation.
    status, body = http("POST", "/api/v1/apps", token, {"label": "should-never-exist"})
    check("WRITE denied", status in (401, 403),
          f"HTTP {status} ({body.get('errorSummary', body.get('error_description', ''))[:80]})")

    # 4. UNGRANTED — token endpoint must refuse a scope the app was never granted.
    try:
        get_access_token(ORG, client_id, PRIVATE_KEY_PATH, scopes=["okta.users.manage"], kid=KID)
        check("UNGRANTED scope refused", False, "token was issued — grant list is wider than intended")
    except SystemExit as e:
        check("UNGRANTED scope refused", "400" in str(e) or "401" in str(e), str(e)[:120])

    verdict = "PASS" if not failures else f"FAIL ({', '.join(failures)})"
    print(f"\nVERDICT: {verdict}")
    sys.exit(0 if not failures else 1)


if __name__ == "__main__":
    main()
