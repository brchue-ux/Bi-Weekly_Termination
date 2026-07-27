"""The one HTTP client. Timeouts, uniform retry, typed errors, honest pagination.

Twenty-one scripts each hand-rolled `urllib.request.Request` with its own retry policy — or
none — and its own error contract. That single fact produced five separate findings in the
2026-07-26 review, because each copy failed differently:

  * `urlopen()` defaults to NO timeout. Exactly one file in the repo passed `timeout=`, so a
    half-open socket could hang the biweekly control indefinitely, mid-ticket-run.
  * The entitlement loader retried 429/502/503; the verifier — whose VERDICT line IS the
    project's evidence standard — retried nothing; `all_users_by_email()`, the largest paged
    read in the codebase, had no error handling at all.
  * Error contracts differed per file: `raise SystemExit` / `raise RuntimeError` /
    `return (code, {})` / return `None`. A library that calls `sys.exit` takes the decision
    to abort away from the caller — and `SystemExit` raised from inside the ticketing loop
    killed the cycle after tickets existed but before state.json was written.

Contract here:
  * every request has a timeout;
  * 429 and 5xx retry with Retry-After / X-Rate-Limit-Reset, then exponential backoff with
    full jitter (unjittered backoff synchronises parallel runs onto the same retry instant);
  * network errors (URLError, socket timeout, incomplete read) retry on the same ladder;
  * an unexpected status raises `OktaApiError` carrying status, method, URL and response
    body. Callers decide what to do. Nothing here exits the process.
  * `paged()` reads `headers.get_all("Link")`. Okta returns TWO Link headers; `get("Link")`
    returns the `rel="self"` one, and the pager silently truncates at 200 users — the
    project's most expensive historical bug.
"""
import http.client
import json
import random
import socket
import time
import urllib.error
import urllib.request

import biterm_config

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})


class HttpError(RuntimeError):
    """Base for every transport/API failure raised by this module."""


class ApiError(HttpError):
    """A definitive non-success response from the API."""

    def __init__(self, method, url, status, body):
        self.method, self.url, self.status = method, url, status
        self.body = body
        snippet = body if isinstance(body, str) else json.dumps(body)
        super().__init__(f"{method} {url} -> HTTP {status}: {snippet[:400]}")


class OktaApiError(ApiError):
    pass


class ServiceNowApiError(ApiError):
    pass


class TransientError(HttpError):
    """Retries were exhausted without a definitive answer.

    Distinct from ApiError on purpose: "the API said no" and "I could not reach the API" are
    different facts, and a verifier must be able to tell them apart to report INCONCLUSIVE
    rather than manufacture a PASS or a FAIL from a rate limit.
    """


class AuthError(HttpError):
    """Credential/assertion rejected (401/403 on the token or the call)."""


def ssws(token_provider):
    """Auth strategy: privileged SSWS admin token (scaffolding only)."""
    return lambda: f"SSWS {token_provider()}"


def bearer(token_provider):
    """Auth strategy: short-lived OAuth bearer (the control's runtime identity)."""
    return lambda: f"Bearer {token_provider()}"


def basic(user_provider):
    import base64

    def header():
        user, pw = user_provider()
        return "Basic " + base64.b64encode(f"{user}:{pw}".encode()).decode()
    return header


class Client:
    """A configured HTTP client for one base URL.

    `error_class` lets ServiceNow failures be distinguishable from Okta failures at the
    `except` site without inspecting the URL.
    """

    def __init__(self, base_url, auth, *, timeout=None, max_attempts=None,
                 error_class=ApiError, on_write=None, logger=None):
        http_cfg = biterm_config.get("http", default={})
        self.base_url = base_url.rstrip("/")
        self.auth = auth
        self.timeout = timeout if timeout is not None else http_cfg.get("timeout_seconds", 30)
        self.max_attempts = max_attempts if max_attempts is not None else http_cfg.get("max_attempts", 6)
        self.backoff_base = http_cfg.get("backoff_base_seconds", 1.0)
        self.backoff_cap = http_cfg.get("backoff_cap_seconds", 60.0)
        self.error_class = error_class
        self.on_write = on_write        # called with the change record for mutating calls
        self.logger = logger

    # ------------------------------------------------------------ internals

    def _url(self, path):
        return path if path.startswith("http") else self.base_url + path

    def _sleep_for(self, attempt, headers):
        """Server-directed wait when offered, else exponential backoff with full jitter."""
        for name in ("Retry-After", "X-Rate-Limit-Reset"):
            raw = headers.get(name) if headers else None
            if not raw:
                continue
            try:
                val = float(raw)
            except (TypeError, ValueError):
                continue
            # X-Rate-Limit-Reset is an absolute epoch; Retry-After is a delta.
            wait = val - time.time() if val > 10 ** 9 else val
            if 0 < wait <= self.backoff_cap:
                return wait + 1
        return min(self.backoff_cap, self.backoff_base * (2 ** attempt)) * random.random()

    def _log(self, msg):
        if self.logger:
            self.logger.debug(msg)

    # ------------------------------------------------------------ public

    def request(self, method, path, body=None, *, ok_statuses=(200, 201, 202, 204),
                allow_statuses=(), headers=None):
        """Perform one request, retrying transient failures.

        Returns `(status, parsed_json, response_headers)`.
        Raises `error_class` for a definitive unexpected status, `TransientError` when the
        retry ladder is exhausted, `AuthError` on 401/403.
        `allow_statuses` are returned to the caller instead of raising (e.g. 404 probes).
        """
        url = self._url(path)
        data = json.dumps(body).encode() if body is not None else None
        last = None
        for attempt in range(self.max_attempts):
            req = urllib.request.Request(url, method=method, data=data)
            req.add_header("Authorization", self.auth())
            req.add_header("Accept", "application/json")
            req.add_header("User-Agent", "biterm-control/1.0")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            for k, v in (headers or {}).items():
                req.add_header(k, v)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    parsed = json.loads(raw) if raw else {}
                    if self.on_write and method in ("POST", "PUT", "PATCH", "DELETE"):
                        self.on_write({"method": method, "url": url, "status": resp.status,
                                       "request": body, "response": parsed})
                    return resp.status, parsed, resp.headers
            except urllib.error.HTTPError as e:
                raw = e.read()
                try:
                    parsed = json.loads(raw) if raw else {}
                except json.JSONDecodeError:
                    parsed = raw.decode(errors="replace")
                if e.code in allow_statuses:
                    return e.code, parsed, e.headers
                if e.code in RETRY_STATUSES and attempt < self.max_attempts - 1:
                    wait = self._sleep_for(attempt, e.headers)
                    self._log(f"{method} {url} -> {e.code}; retry {attempt + 1}/"
                              f"{self.max_attempts} in {wait:.1f}s")
                    time.sleep(wait)
                    last = self.error_class(method, url, e.code, parsed)
                    continue
                if e.code in (401, 403):
                    raise AuthError(f"{method} {url} -> HTTP {e.code}: "
                                    f"{json.dumps(parsed)[:300]}") from e
                raise self.error_class(method, url, e.code, parsed) from e
            except (urllib.error.URLError, socket.timeout, TimeoutError,
                    http.client.IncompleteRead, ConnectionError) as e:
                if attempt < self.max_attempts - 1:
                    wait = self._sleep_for(attempt, None)
                    self._log(f"{method} {url} -> {e!r}; retry {attempt + 1}/"
                              f"{self.max_attempts} in {wait:.1f}s")
                    time.sleep(wait)
                    last = e
                    continue
                raise TransientError(f"{method} {url}: {e!r} after {self.max_attempts} attempts") from e
        raise TransientError(f"{method} {url}: exhausted {self.max_attempts} attempts "
                             f"(last: {last})")

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def get_json(self, path, **kw):
        return self.request("GET", path, **kw)[1]

    def exists(self, path):
        """True/False for a resource, without conflating 404 with a transport failure."""
        status, _, _ = self.request("GET", path, allow_statuses=(404,))
        return status != 404

    def paged(self, path, *, limit_pages=None):
        """Yield items across Okta Link-header pagination.

        A page that fails raises. A paginated read that cannot complete must never return a
        truncated collection as if it were whole — that is how a partial read became "these
        principals hold no grants" and triggered a mass re-POST.
        """
        url = self._url(path)
        pages = 0
        while url:
            _, items, headers = self.request("GET", url)
            if not isinstance(items, list):
                raise self.error_class("GET", url, 200,
                                       {"error": "expected a JSON array for a paged read"})
            yield from items
            pages += 1
            if limit_pages and pages >= limit_pages:
                return
            url = _next_link(headers)


def _next_link(headers):
    """Extract rel="next" from ALL Link headers. Okta sends two; get() returns the wrong one."""
    for link in headers.get_all("Link") or headers.get_all("link") or []:
        for part in link.split(","):
            if 'rel="next"' in part and "<" in part and ">" in part:
                return part[part.index("<") + 1:part.index(">")]
    return None


def paged_governance(client, path, *, page_size=200):
    """Yield items from a governance endpoint, which pages via `_links.next.href`.

    Separate from `paged()` because the governance API uses a body cursor rather than Link
    headers; conflating them is how the grants reader ended up with its own bespoke loop in
    two files.
    """
    url = path if "limit=" in path else f"{path}{'&' if '?' in path else '?'}limit={page_size}"
    while url:
        body = client.get_json(url)
        yield from body.get("data", [])
        nxt = ((body.get("_links") or {}).get("next") or {}).get("href")
        url = nxt if nxt else None


def okta_client(auth, **kw):
    return Client(biterm_config.org(), auth, error_class=OktaApiError, **kw)
