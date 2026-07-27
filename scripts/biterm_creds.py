"""Credential loading: read once, validate permissions, parse by key — never by position.

Replaces three separate ad-hoc parsers:
  * `splitlines()[0]`                                  (SSWS token, in 6 scripts)
  * `next(l for l in lines if "=" not in l)`            (ServiceNow username) — raised a bare
    StopIteration the moment the file gained a comment line
  * `read_text().strip()` at MODULE IMPORT time         (4 scripts) — importing the module
    required the secret to exist, even for `--help`

It also re-read the token file from disk on every single API call. Values are cached here
(keyed by resolved path + mtime) so a rotated file is picked up on the next process, but a
5,000-call run does not perform 5,000 reads of a secret.

Permission checking used to exist in exactly one place — run_all.py's PEM check — while the
scripts holding a full-admin SSWS token checked nothing. It now applies to every secret.
"""
import os
import stat
from pathlib import Path

_cache = {}


class CredentialError(RuntimeError):
    """A secret is missing, unreadable, wrongly permissioned, or malformed."""


def _read(path, allow_group_read=False):
    p = Path(path).expanduser()
    if not p.exists():
        raise CredentialError(f"credential file not found: {p}")
    mode = stat.S_IMODE(os.stat(p).st_mode)
    bad = mode & (0o007 if allow_group_read else 0o077)
    if bad:
        raise CredentialError(
            f"refusing to read {p}: readable by group/other (mode {oct(mode)}). "
            f"Fix with: chmod 600 {p}")
    key = (str(p), p.stat().st_mtime_ns)
    if key not in _cache:
        _cache.clear()          # only ever one live version of a given secret
        _cache[key] = p.read_text()
    return _cache[key]


def _fields(text):
    """Parse `key=value` lines, ignoring blanks and `#` comments."""
    out = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().lower()] = v.strip()
        else:
            out.setdefault("_bare", []).append(line)
    return out


def api_token(path):
    """Okta SSWS admin token.

    Accepts either a bare token on the first non-comment line or a `token=…` / `api_token=…`
    field, which is what the various files in ~/.secrets actually contain.
    """
    f = _fields(_read(path))
    for key in ("token", "api_token", "ssws", "apitoken"):
        if f.get(key):
            return f[key]
    bare = f.get("_bare") or []
    if bare:
        return bare[0]
    raise CredentialError(
        f"{path}: no token found. Expected a bare token on the first line or a 'token=' field.")


def basic_auth(path):
    """(username, password) for a Basic-auth integration account (ServiceNow).

    Named fields first; the historical layout (bare username line + `password=`) is still
    accepted, but a missing field now names what is missing instead of raising
    StopIteration from inside a generator expression.
    """
    f = _fields(_read(path))
    user = f.get("user") or f.get("username") or (f.get("_bare") or [None])[0]
    pw = f.get("password") or f.get("pass")
    missing = [n for n, v in (("username", user), ("password", pw)) if not v]
    if missing:
        raise CredentialError(
            f"{path}: missing {' and '.join(missing)}. Expected lines 'user=…' and 'password=…'.")
    return user, pw


def private_key(path):
    """PEM text for the OAuth private_key_jwt assertion."""
    text = _read(path)
    if "PRIVATE KEY" not in text:
        raise CredentialError(f"{path}: does not look like a PEM private key")
    return text


def check_readable(path, label="credential"):
    """Validate existence + permissions without loading the value (startup preflight)."""
    try:
        _read(path)
        return True, ""
    except CredentialError as e:
        return False, f"{label}: {e}"
