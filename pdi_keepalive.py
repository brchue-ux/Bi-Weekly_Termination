"""
Weekly ServiceNow PDI keepalive (2026-07-23). ServiceNow reclaims a Personal
Developer Instance after 10 days with no INTERACTIVE login — API activity does
NOT count (verified against ServiceNow docs). So this performs a real headless
browser login to the instance UI, which is what the reclaim clock tracks.

Success = silent (logged locally). ANY failure = ntfy.sh push, because a
keepalive that fails quietly is worse than none — the instance would be wiped
with all its seeding, tickets, campaigns, and the AM team.

Login identity MUST be the account the owner personally uses to open the
instance (PDI admin/owner) — an integration-user session may not reset the
owner's reclaim clock. Creds: ~/.secrets/sn_dev_portal_login.txt (user=/password=).
"""

import datetime
import sys
import urllib.request
from pathlib import Path

INSTANCE = "https://dev336362.service-now.com"
CREDS = Path.home() / ".secrets" / "sn_dev_portal_login.txt"
NTFY_TOPIC = "biterm-pdi-ea3c383b70d9"
LOG = Path(__file__).parent / "pdi_keepalive.log"
SHOT = Path(__file__).parent / "pdi_keepalive_failure.png"


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def notify_failure(detail):
    """Push a failure alert to ntfy.sh (curl-free: urllib POST)."""
    try:
        req = urllib.request.Request(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=f"PDI keepalive FAILED — log in to {INSTANCE} manually to avoid the "
                 f"10-day reclaim.\n{detail}".encode(),
            headers={"Title": "ServiceNow PDI keepalive failed", "Priority": "urgent",
                     "Tags": "warning"})
        urllib.request.urlopen(req, timeout=30).read()
        log("failure notification sent to ntfy")
    except Exception as e:
        log(f"ALSO failed to send ntfy notification: {e}")


def read_creds():
    # RuntimeError, never SystemExit: SystemExit is a BaseException and would slip
    # past main()'s handler, exiting silently — the exact quiet failure we must alert on.
    if not CREDS.exists():
        raise RuntimeError(f"creds file missing: {CREDS}")
    lines = [l.strip() for l in CREDS.read_text().splitlines()
             if l.strip() and not l.strip().startswith("#")]
    kv = {}
    for line in lines:
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()
    if "user" in kv and "password" in kv:
        return kv["user"], kv["password"]
    # tolerate a bare two-line file (user on line 1, password on line 2) — passwords
    # legitimately contain "=", so only fall back when the keyed form isn't present
    if len(lines) >= 2:
        return lines[0], lines[1]
    raise RuntimeError(f"{CREDS}: expected user=/password= lines, or user and password "
                       f"on two bare lines (found {len(lines)} usable line(s))")


def run():
    from playwright.sync_api import sync_playwright  # imported here so import errors notify

    user, password = read_creds()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        try:
            page.goto(f"{INSTANCE}/login.do", wait_until="domcontentloaded", timeout=60000)
            page.fill("#user_name", user)
            page.fill("#user_password", password)
            page.click("#sysverb_login")
            # NOT networkidle: the post-login workspace (/now/sow/home) is a polling SPA that
            # never goes idle, so networkidle just times out on a SUCCESSFUL login. The real
            # signal is leaving login.do.
            try:
                page.wait_for_url(lambda u: "login.do" not in u, timeout=45000)
            except Exception:
                pass  # fall through — the explicit checks below decide success/failure
            page.wait_for_load_state("domcontentloaded", timeout=30000)

            # Verify the UI session, not the REST API: /api/now/... demands REST auth headers
            # and rejects a browser cookie session, so checking it produced false failures.
            url, body = page.url, page.inner_text("body")
            if "login.do" in url or "invalid" in body.lower():
                page.screenshot(path=str(SHOT))
                raise RuntimeError(f"login rejected (still at {url[:80]}); "
                                   f"page: {' '.join(body.split())[:160]!r}")

            # second proof: an authenticated UI page must not bounce us back to login
            page.goto(f"{INSTANCE}/navpage.do", wait_until="domcontentloaded", timeout=60000)
            if "login.do" in page.url:
                page.screenshot(path=str(SHOT))
                raise RuntimeError("session did not persist — redirected to login on navpage.do")
            log(f"interactive login OK as {user} (landed {url[:60]}) — reclaim clock touched")
        finally:
            browser.close()


def main():
    try:
        run()
    except Exception as e:
        log(f"FAILURE: {e}")
        notify_failure(str(e)[:300])
        sys.exit(1)


if __name__ == "__main__":
    main()
