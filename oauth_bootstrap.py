"""
One-time privileged bootstrap: register the detective control's OAuth 2.0 API
Services app on the Okta tenant, enterprise-pattern (private_key_jwt, least-
privilege read-only scopes). This script IS the corporate ask — everything it
does is what a tenant admin would execute once in production; the pipeline
never holds admin credentials afterward.

Privileged actions performed (SSWS admin token, bootstrap only):
  1. Create app (signOnMode OPENID_CONNECT, application_type service,
     token_endpoint_auth_method private_key_jwt, public key registered as JWK).
  2. Grant scopes: okta.users.read, okta.apps.read,
     okta.governance.accessCertifications.read.
  3. Assign admin roles to the client principal: READ_ONLY_ADMIN +
     ACCESS_CERTIFICATIONS_ADMIN. This org enforces effective permission =
     granted scopes INTERSECT assigned roles (E0000006 with scopes alone);
     both layers are load-bearing.

Idempotent: re-running finds the existing app by label and skips existing
grants/roles.
"""

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

from cryptography.hazmat.primitives import serialization

ORG = "https://demo-beige-haddock-4684.okta.com"
ADMIN_TOKEN_PATH = os.path.expanduser("~/.secrets/claude_3rd_party.txt")
PRIVATE_KEY_PATH = os.path.expanduser("~/.secrets/term_revamp_oauth_demo_private.pem")
APP_LABEL = "BiTerm Detective Control - Service"
KID = "biterm-2026-07"
SCOPES = ["okta.users.read", "okta.apps.read", "okta.governance.accessCertifications.read"]


def api(method, path, token, body=None):
    req = urllib.request.Request(f"{ORG}{path}", method=method,
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Authorization", f"SSWS {token}")
    req.add_header("Accept", "application/json")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "{}")


def b64url_uint(n):
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def ensure_keypair():
    """RSA-2048 private key, 0600, in ~/.secrets (never the LAN share). Returns public JWK."""
    if not os.path.exists(PRIVATE_KEY_PATH):
        subprocess.run(["openssl", "genrsa", "-out", PRIVATE_KEY_PATH, "2048"],
                       check=True, capture_output=True)
        os.chmod(PRIVATE_KEY_PATH, 0o600)
        print(f"generated keypair -> {PRIVATE_KEY_PATH}")
    with open(PRIVATE_KEY_PATH, "rb") as f:
        pub = serialization.load_pem_private_key(f.read(), password=None).public_key().public_numbers()
    return {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": KID,
            "n": b64url_uint(pub.n), "e": b64url_uint(pub.e)}


def find_app(token):
    status, apps = api("GET", f"/api/v1/apps?q={urllib.parse.quote(APP_LABEL)}", token)
    if status != 200:
        sys.exit(f"app search failed ({status}): {apps}")
    return next((a for a in apps if a.get("label") == APP_LABEL), None)


def main():
    with open(ADMIN_TOKEN_PATH) as f:
        admin = f.read().strip()

    jwk = ensure_keypair()

    app = find_app(admin)
    if app:
        print(f"app exists: {app['id']} ({APP_LABEL})")
    else:
        status, app = api("POST", "/api/v1/apps", admin, {
            "name": "oidc_client",
            "label": APP_LABEL,
            "signOnMode": "OPENID_CONNECT",
            "credentials": {"oauthClient": {"token_endpoint_auth_method": "private_key_jwt"}},
            "settings": {"oauthClient": {
                "application_type": "service",
                "grant_types": ["client_credentials"],
                "response_types": ["token"],
                "jwks": {"keys": [jwk]},
            }},
        })
        if status not in (200, 201):
            sys.exit(f"app create failed ({status}): {json.dumps(app)}")
        print(f"app created: {app['id']} ({APP_LABEL})")

    client_id = app["credentials"]["oauthClient"]["client_id"]

    status, existing = api("GET", f"/api/v1/apps/{app['id']}/grants", admin)
    if status != 200:
        sys.exit(f"grant listing failed ({status}): {existing}")
    have = {g["scopeId"] for g in existing}
    for scope in SCOPES:
        if scope in have:
            print(f"grant exists: {scope}")
            continue
        status, body = api("POST", f"/api/v1/apps/{app['id']}/grants", admin,
                           {"scopeId": scope, "issuer": ORG})
        if status not in (200, 201):
            sys.exit(f"grant failed for {scope} ({status}): {json.dumps(body)}")
        print(f"granted: {scope}")

    status, roles = api("GET", f"/oauth2/v1/clients/{client_id}/roles", admin)
    if status != 200:
        sys.exit(f"role listing failed ({status}): {roles}")
    have_roles = {r["type"] for r in roles}
    for role in ("READ_ONLY_ADMIN", "ACCESS_CERTIFICATIONS_ADMIN"):
        if role in have_roles:
            print(f"role exists: {role}")
            continue
        status, body = api("POST", f"/oauth2/v1/clients/{client_id}/roles", admin, {"type": role})
        if status not in (200, 201):
            sys.exit(f"role assignment failed for {role} ({status}): {json.dumps(body)}")
        print(f"role assigned: {role}")

    print(f"\nclient_id: {client_id}")
    print(f"private key: {PRIVATE_KEY_PATH} (kid={KID})")
    print("bootstrap complete — pipeline needs only client_id + private key from here on.")


if __name__ == "__main__":
    main()
