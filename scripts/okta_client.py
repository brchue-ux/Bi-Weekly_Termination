"""Okta client for the DETECTIVE CONTROL — OAuth service-app auth, least privilege.

This is the runtime identity of the pipeline: app "BiTerm Detective Control -
Service", private_key_jwt, read-only scopes (users/apps/governance-certifications).
Registered + role-bound by oauth_bootstrap.py; proven by verify_oauth.py.
seed_tenant.py's SSWS credential is NOT used here — that is privileged scaffolding
(seeding), and the control must never run under it.

Transport now comes from `biterm_http.Client`: timeouts on every call, uniform retry on
429/5xx and network errors, and typed exceptions instead of `SystemExit` raised from inside
a library. Tenant coordinates come from `biterm_config`, so pointing the control at the real
work org is configuration rather than a code edit.

Token handling: the lifetime is read from the token endpoint's `expires_in` (it used to be
hardcoded to 3600) and refreshed transparently with a 5-minute margin, so a long run cannot
401 partway through.

`api()` / `paged()` keep their original signatures so existing consumers are unaffected.
"""

import time

import biterm_config
import biterm_http
import okta_oauth

_cfg = biterm_config.load()
ORG = _cfg["org"]
CLIENT_ID = _cfg["client_id"]
PRIVATE_KEY = _cfg["private_key_file"]
KID = _cfg["kid"]
SCOPES = _cfg["scopes"]

REFRESH_MARGIN = 300

_token = None          # (bearer, refresh_after_epoch)
_client = None


def _bearer():
    global _token
    if _token is None or time.time() >= _token[1]:
        payload = okta_oauth.fetch_token(ORG, CLIENT_ID, str(PRIVATE_KEY),
                                         scopes=SCOPES, kid=KID)
        lifetime = int(payload.get("expires_in", 3600))
        # Margin never exceeds half the lifetime: a hypothetical 60s token would otherwise
        # compute a refresh deadline in the past and re-mint on every single call.
        margin = min(REFRESH_MARGIN, lifetime // 2)
        _token = (payload["access_token"], time.time() + lifetime - margin)
    return _token[0]


def client():
    """The shared configured client. Read-only by design — nothing here writes."""
    global _client
    if _client is None:
        _client = biterm_http.okta_client(biterm_http.bearer(_bearer))
    return _client


def api(method, path, body=None, ok404=False):
    """Single Okta call. Returns (parsed JSON, headers); (None, headers) on 404 when ok404.

    Raises biterm_http.OktaApiError on an unexpected status, biterm_http.TransientError when
    retries are exhausted, biterm_http.AuthError on 401/403. It no longer raises SystemExit:
    the caller decides whether a failure is fatal.
    """
    status, parsed, headers = client().request(
        method, path, body, allow_statuses=(404,) if ok404 else ())
    if ok404 and status == 404:
        return None, headers
    return parsed, headers


def paged(path):
    """Yield items across Okta link-header pagination (reads ALL Link headers)."""
    yield from client().paged(path)


def paged_governance(path, page_size=200):
    """Yield items from a governance endpoint (body-cursor pagination)."""
    yield from biterm_http.paged_governance(client(), path, page_size=page_size)
