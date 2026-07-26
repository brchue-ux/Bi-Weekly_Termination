#!/usr/bin/env python3
"""
One-shot rollout: create a Bookmark app for each remaining non-integrated app
from List of Apps.xlsx, each populated with a random 5-7 of the 10 sandbox
test users. Sandbox-only pilot data, not a real roster sync.

Shells out to okta_bookmark_sync.py per app so the same reviewed, dry-run-
capable path is used for every assignment (no separate write logic here).
"""

import csv
import random
import subprocess
import sys
import time
from pathlib import Path

ORG = "https://integrator-2343242.okta.com"
TOKEN_FILE = str(Path.home() / ".secrets" / "Okta_Dev_ApiToken")
SYNC_SCRIPT = str(Path(__file__).parent / "okta_bookmark_sync.py")
ROSTER_DIR = Path(__file__).parent / "test_rosters"

ALL_USERS = [
    "test001@example.com", "test002@example.com", "test003@example.com",
    "salesforce@finance.com", "salesforce@contractor.com", "bchue@wm.com",
    "soraya.esfeh@example.com", "kay.west@example.com", "Nina.Shah@org1.com",
    "chad@powers.com",
]

# Remaining apps from List of Apps.xlsx: 19 total, minus Dynamics AX NA,
# AWS VDI, BPC, Diligent, Docusign, Dynamics AX UK, Efax (already created
# in a prior run that hit the sandbox's 50-req/min rate limit partway
# through) and ServiceNow (marked "No longer in Scope").
APPS = [
    "FAS", "PowerPlan", "Roadnet", "SFDC RWCS 3rd Party", "SFDC RWCS CAN",
    "SFDC RWCS UK", "SFDC SID EMEA", "SID SAP", "StARS", "Steriworks NA",
    "Steriworks UK",
]


def main():
    random.seed(42)
    ROSTER_DIR.mkdir(exist_ok=True)

    for app in APPS:
        n = random.randint(5, 7)
        users = random.sample(ALL_USERS, n)
        label = f"{app} (Bi-Weekly Term Test)"
        roster_path = ROSTER_DIR / f"{app.replace(' ', '_').replace('/', '_')}.csv"

        with open(roster_path, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["UPN"])
            for u in users:
                w.writerow([u])

        print(f"\n=== {label}: {n} users -> {roster_path.name} ===")
        result = subprocess.run(
            [
                sys.executable, SYNC_SCRIPT,
                "--org", ORG,
                "--token-file", TOKEN_FILE,
                "--app-label", label,
                "--export", str(roster_path),
                "--column", "UPN",
                "--apply",
            ],
            capture_output=True, text=True,
        )
        print(result.stdout)
        if result.returncode != 0:
            print(f"FAILED ({result.returncode}): {result.stderr}", file=sys.stderr)
        time.sleep(3)  # stay under the sandbox's 50-req/min rate limit


if __name__ == "__main__":
    main()
