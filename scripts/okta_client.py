"""
Okta client for the DETECTIVE CONTROL — OAuth service-app auth, least privilege.

This is the runtime identity of the pipeline: app "BiTerm Detective Control -
Service", private_key_jwt, read-only scopes (users/apps/governance-certifications).
Registered + role-bound by oauth_bootstrap.py; proven by verify_oauth.py.
seed_tenant.py's SSWS client is NOT used here — that credential is privileged
scaffolding (seeding), and the control must never run under it.

Same api()/paged() signatures as seed_tenant so consumers swap by import alone.
Bearer tokens are short-lived: cached until near expiry, re-minted transparently.
"""

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from okta_oauth import get_access_token

ORG = "https://demo-beige-haddock-4684.okta.com"
CLIENT_ID = "0oa15jbaw6sllCbVB698"
PRIVATE_KEY = Path.home() / ".secrets" / "term_revamp_oauth_demo_private.pem"
KID = "biterm-2026-07"
SCOPES = ["okta.users.read", "okta.apps.read", "okta.governance.accessCertifications.read"]

_token = None          # (bearer, refresh_after_epoch)
TOKEN_LIFETIME = 3600  # Okta org AS access tokens live 1h; refresh with 5 min margin


def _bearer():
    global _token
    if _token is None or time.time() >= _token[1]:
        _token = (get_access_token(ORG, CLIENT_ID, str(PRIVATE_KEY), scopes=SCOPES, kid=KID),
                  time.time() + TOKEN_LIFETIME - 300)
    return _token[0]


def api(method, path, body=None, ok404=False):
    """Single Okta call with 429 backoff. Returns (parsed JSON, headers); None on 404 when ok404."""
    url = path if path.startswith("http") else ORG + path
    for _ in range(6):
        req = urllib.request.Request(url, method=method,
                                     data=json.dumps(body).encode() if body is not None else None)
        req.add_header("Authorization", f"Bearer {_bearer()}")
        req.add_header("Accept", "application/json")
        if body is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                data = resp.read()
                return (json.loads(data) if data else {}), resp.headers
        except urllib.error.HTTPError as e:
            if e.code == 404 and ok404:
                return None, e.headers
            if e.code == 429:
                reset = int(e.headers.get("X-Rate-Limit-Reset", time.time() + 30))
                wait = max(reset - time.time(), 1) + 1
                print(f"    429; sleeping {wait:.0f}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise SystemExit(f"{method} {url} -> {e.code}: {e.read().decode(errors='replace')[:400]}")
    raise SystemExit(f"{method} {url}: exhausted retries")


def paged(path):
    """Yield items across Okta link-header pagination."""
    url = ORG + path
    while url:
        items, headers = api("GET", url)
        yield from items
        url = None
        for link in headers.get_all("link") or []:
            if 'rel="next"' in link:
                url = link[link.index("<") + 1:link.index(">")]
