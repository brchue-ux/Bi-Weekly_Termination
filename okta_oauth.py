"""
OAuth 2.0 client-credentials auth for the Okta API, using a private-key JWT
client assertion instead of a long-lived SSWS admin token.

Requires an Okta API Services app (application_type=service,
token_endpoint_auth_method=private_key_jwt) whose public key is registered
on the app, and OAuth grants + a scoped custom-role/resource-set binding
narrowing what the resulting access token can actually do.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import jwt as pyjwt

DEFAULT_SCOPES = ["okta.apps.read", "okta.apps.manage", "okta.users.read"]
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"


def _build_client_assertion(org, client_id, private_key_path, kid):
    with open(private_key_path) as f:
        private_key = f.read()

    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": f"{org}/oauth2/v1/token",
        "iat": now,
        "exp": now + 300,
        "jti": uuid.uuid4().hex,
    }
    headers = {"kid": kid} if kid else {}
    return pyjwt.encode(claims, private_key, algorithm="RS256", headers=headers)


def get_access_token(org, client_id, private_key_path, scopes=None, kid=None):
    """Exchange a signed client assertion for a short-lived Bearer token."""
    scopes = scopes or DEFAULT_SCOPES
    assertion = _build_client_assertion(org, client_id, private_key_path, kid)

    body = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "scope": " ".join(scopes),
        "client_assertion_type": ASSERTION_TYPE,
        "client_assertion": assertion,
    }).encode()

    req = urllib.request.Request(f"{org}/oauth2/v1/token", data=body, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    req.add_header("Accept", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        raise SystemExit(f"OAuth token request failed ({e.code}): {e.read().decode(errors='replace')}")

    return payload["access_token"]
