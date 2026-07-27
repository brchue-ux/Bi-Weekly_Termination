"""OAuth 2.0 client-credentials auth for the Okta API, using a private-key JWT
client assertion instead of a long-lived SSWS admin token.

Requires an Okta API Services app (application_type=service,
token_endpoint_auth_method=private_key_jwt) whose public key is registered
on the app, and OAuth grants + a scoped custom-role/resource-set binding
narrowing what the resulting access token can actually do.

Two fixes over the original:
  * the token endpoint's `expires_in` is now RETURNED instead of discarded. The client
    hardcoded a 3600s lifetime, so an org that issues shorter tokens would 401 mid-run —
    and a long mutating run under a once-minted token 401s partway through its writes.
  * failures raise `OAuthError` instead of `SystemExit`. A library must not decide to kill
    the process; `SystemExit` from inside the ticketing loop is what killed cycles after
    tickets existed but before the state file was written.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

import biterm_creds

DEFAULT_SCOPES = ["okta.apps.read", "okta.apps.manage", "okta.users.read"]
ASSERTION_TYPE = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
TOKEN_REQUEST_TIMEOUT = 30
ASSERTION_LIFETIME = 300


class OAuthError(RuntimeError):
    """The token endpoint refused the assertion, or could not be reached."""

    def __init__(self, message, status=None):
        self.status = status
        super().__init__(message)


def _build_client_assertion(org, client_id, private_key_path, kid):
    # PyJWT is the pipeline's only non-stdlib dependency (see requirements.txt). Imported
    # lazily so `--help`, the unit tests, and any read-only path that never mints a token
    # do not require it to be installed.
    try:
        import jwt as pyjwt
    except ImportError as e:
        raise OAuthError(
            "PyJWT is required to sign the client assertion: pip install -r requirements.txt"
        ) from e

    private_key = biterm_creds.private_key(private_key_path)
    now = int(time.time())
    claims = {
        "iss": client_id,
        "sub": client_id,
        "aud": f"{org}/oauth2/v1/token",
        "iat": now,
        "exp": now + ASSERTION_LIFETIME,
        "jti": uuid.uuid4().hex,
    }
    headers = {"kid": kid} if kid else {}
    return pyjwt.encode(claims, private_key, algorithm="RS256", headers=headers)


def fetch_token(org, client_id, private_key_path, scopes=None, kid=None):
    """Exchange a signed client assertion for a token. Returns the full token response.

    Callers that need the lifetime read `expires_in`; `get_access_token` wraps this for the
    common case.
    """
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
        with urllib.request.urlopen(req, timeout=TOKEN_REQUEST_TIMEOUT) as resp:
            payload = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise OAuthError(f"OAuth token request failed ({e.code}): {detail}", status=e.code) from e
    except urllib.error.URLError as e:
        raise OAuthError(f"OAuth token endpoint unreachable: {e.reason!r}") from e

    if "access_token" not in payload:
        raise OAuthError(f"token response carried no access_token: {json.dumps(payload)[:300]}")
    payload.setdefault("expires_in", 3600)
    return payload


def get_access_token(org, client_id, private_key_path, scopes=None, kid=None):
    """Bearer token string only — the shape every existing caller expects."""
    return fetch_token(org, client_id, private_key_path, scopes=scopes, kid=kid)["access_token"]
