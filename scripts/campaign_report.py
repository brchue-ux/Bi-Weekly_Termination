#!/usr/bin/env python3
"""Certification campaign results report — provable facts only.

Pulls every review item from the live governance API for each named campaign and
reports what Okta actually records: item counts, decisions, per-app coverage, and
the cross-reference between campaign principals and the biweekly reconciliation's
open ticket findings. Deliberately NO remediation claims: on disconnected apps a
REVOKED decision is a certification outcome, not proof of in-app removal (that
proof stays with the reconciliation's next-cycle export check).

Usage: campaign_report.py  → prints a summary and writes reports/campaign_results_<ts>.xlsx
"""
import json
import sys
import time
import urllib.parse
from collections import Counter
from pathlib import Path

from okta_client import api, paged  # OAuth service app (least privilege), NOT seed_tenant's SSWS
from xlsx_write import write_xlsx

PROJ = Path(__file__).parent.parent
REPORTS = PROJ / "reports"
CAMPAIGN_PREFIX = "BiTerm — "  # every campaign this suite owns


def reviews(campaign_id):
    """Yield all review items for a campaign, following cursor pagination."""
    f = urllib.parse.quote(f'campaignId eq "{campaign_id}"')
    url = f"/governance/api/v1/reviews?filter={f}&limit=200"
    while url:
        r, _ = api("GET", url)
        yield from r.get("data", [])
        nxt = (r.get("_links") or {}).get("next", {}).get("href", "")
        url = nxt if nxt else None


def main():
    apps = {a["id"]: a["label"] for a in paged("/api/v1/apps?limit=200")
            if a["label"].startswith("BiTerm")}
    camps, _ = api("GET", "/governance/api/v1/campaigns?limit=50")
    camps = [c for c in camps.get("data", []) if c["name"].startswith(CAMPAIGN_PREFIX)
             and c.get("status") not in ("ENDED", "COMPLETED", "DELETED")]
    if not camps:
        sys.exit("no BiTerm campaigns found")

    state_files = sorted((PROJ / "cycles").glob("cycle_*/state.json"))
    flagged = set()
    if state_files:
        s = json.loads(state_files[-1].read_text())
        flagged = {f["upn"] for f in s["findings"] if f["cls"] == "ticket" and f["upn"]}

    sheets, summary = [], [["Campaign certification results", time.strftime("%Y-%m-%d %H:%M")],
                           ["Cross-referenced against reconciliation cycle", state_files[-1].parent.name if state_files else "(none)"],
                           [],
                           ["Campaign", "items", "UNREVIEWED", "APPROVED", "REVOKED", "other",
                            "items on recon-flagged identities"]]
    for c in camps:
        rows = [["Reviewer", "Principal", "App", "Decision", "Remediation status",
                 "Flagged by reconciliation", "Last updated"]]
        decisions, flagged_hits = Counter(), 0
        for it in reviews(c["id"]):
            login = (it.get("principalProfile") or {}).get("email", "").lower()
            dec = it.get("decision", "?")
            decisions[dec] += 1
            hit = login in flagged
            flagged_hits += hit
            rows.append([(it.get("reviewerProfile") or {}).get("email", ""), login,
                         apps.get(it.get("resourceId"), it.get("resourceId", "")),
                         dec, it.get("remediationStatus", ""),
                         "YES" if hit else "", it.get("lastUpdated", "")])
        total = sum(decisions.values())
        summary.append([c["name"], total, decisions.get("UNREVIEWED", 0), decisions.get("APPROVED", 0),
                        decisions.get("REVOKED", 0),
                        total - sum(decisions.get(k, 0) for k in ("UNREVIEWED", "APPROVED", "REVOKED")),
                        flagged_hits])
        sheets.append((c["name"].removeprefix(CAMPAIGN_PREFIX)[:31], rows))
        print(f"{c['name']}: {total} items | {dict(decisions)} | recon-flagged: {flagged_hits}",
              file=sys.stderr)
    summary += [[], ["NOTE", "REVOKED is a certification decision, not proof of in-app removal; "
                             "removal is proven only by the reconciliation's next-cycle export check."]]

    REPORTS.mkdir(exist_ok=True)
    out = REPORTS / time.strftime("campaign_results_%Y%m%d_%H%M%S.xlsx")
    write_xlsx(out, [("Summary", summary)] + sheets)
    print(f"report: {out}", file=sys.stderr)


if __name__ == "__main__":
    main()
