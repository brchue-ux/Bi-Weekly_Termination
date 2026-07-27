"""Domain vocabulary for the termination control — the rules, not the plumbing.

Everything here was previously either duplicated (the privilege order lived verbatim in
both the entitlement loader and its verifier, so a shared wrong assumption passed both) or
imported from `seed_tenant.py`, which project CLAUDE.md describes as disposable scaffolding
that SCIM retires. The permanent control must not depend on the disposable seeder for its
definition of "terminated".

This module is pure: no I/O, no network, no config. That is what makes the control's rules
unit-testable without a tenant.
"""
import datetime as dt
import hashlib

# ---------------------------------------------------------------- populations

STARS_TABS = [
    "NA Apollo", "NA Stellar", "NA Orion", "NA Saturn East", "NA Saturn Central",
    "NA Saturn West", "NA Saturn ComSat", "NA Saturn Corp", "CloudForce HQ",
    "CloudForce Canada",
]
SFDC_APP = "SFDC 3rd Party (DocuSign)"
APP_LABEL_PREFIX = "BiTerm - "   # namespaces seeded apps away from pre-existing demo apps

# ---------------------------------------------------------------- HR status

# HR employment status is the ONLY legitimacy authority; Okta status is enrichment.
# Unpaid-leave users are often suspended in Okta but legitimately keep app access.
LEGIT = frozenset({"Active", "Paid Leave", "Unpaid Leave"})
TERM = frozenset({"Terminated", "Retired"})

# The literal a roster carries when an identity has no TalentHub match. It is used in two
# distinct roles that were previously collapsed onto one constant named for only one of
# them (`NO_UPN`), so an edit to the string silently changed the other meaning:
#   NO_UPN_SENTINEL   — appears in the UPN column, meaning "no usable identity"
#   HR_NOT_FOUND      — appears in the HR-status field, meaning "absent from TalentHub"
# They are equal today by construction; they are named apart so they can diverge safely.
NO_UPN_SENTINEL = "Not found in TalentHub"
HR_NOT_FOUND = "Not found in TalentHub"

# Back-compat alias for modules still importing the old name.
NO_UPN = NO_UPN_SENTINEL


def is_legit(hr_status):
    return (hr_status or "").strip() in LEGIT


def is_terminated(hr_status):
    return (hr_status or "").strip() in TERM


def valid_login(s):
    """A usable Okta login: an address, no spaces, not the not-found sentinel."""
    s = (s or "").strip().lower()
    return "@" in s and " " not in s and s != NO_UPN_SENTINEL.lower()


def normalise_upn(raw):
    """Roster UPN -> canonical login, or "" when the cell carries no usable identity."""
    upn = (raw or "").strip().lower()
    return upn if valid_login(upn) else ""


# ---------------------------------------------------------------- privilege

# Higher number = more privileged. Explicit, not alphabetical: this is a risk judgement.
# Single definition, imported by both the loader and the verifier — see the honesty note in
# oig_verify_all.py about what the verifier's independence does and does not cover.
PRIVILEGE_ORDER = {
    "Administrator": 4,
    "Power User": 3,
    "Standard User": 2,
    "Read Only": 1,
    "Service Account": 0,
}


class UnknownRoleError(ValueError):
    """A role string that carries no privilege ranking.

    Raised rather than defaulted: an unrecognised role in a privilege-certification load is
    a finding, not a row to skip. Silently ranking it lowest is precisely the masking the
    highest-privilege-wins rework exists to prevent.
    """


def highest_privilege(roles):
    """The most privileged role among those a person holds in one app.

    Ties are impossible by construction (the order is a bijection), and the sort is made
    total by the role name so the result never depends on set iteration order.
    """
    roles = list(roles)
    if not roles:
        raise ValueError("highest_privilege() requires at least one role")
    unknown = sorted(r for r in roles if r not in PRIVILEGE_ORDER)
    if unknown:
        raise UnknownRoleError(f"unranked role(s): {unknown}")
    return max(sorted(roles), key=lambda r: PRIVILEGE_ORDER[r])


# ---------------------------------------------------------------- dates

MIN_EXCEL_SERIAL = 1
MAX_EXCEL_SERIAL = 2958465          # 9999-12-31
EXCEL_EPOCH = dt.date(1899, 12, 30)  # accounts for Excel's 1900 leap-year bug


class DateFormatError(ValueError):
    """An expiry/date cell that cannot be read unambiguously.

    Deliberately fatal at load time rather than row-level. Before this existed, an expiry
    was compared as a raw STRING: an Excel date-formatted cell arrives as the serial
    "46234", and "46234" < "2026-07-26" is False — so a lapsed exception silently passed
    the control. Guessing is worse than stopping.
    """


def parse_date(value, field="date"):
    """Parse a spreadsheet/CSV date into a `datetime.date`, or raise DateFormatError.

    Accepts: ISO (YYYY-MM-DD), YYYY/MM/DD, DD-Mon-YYYY, and Excel serial numbers.
    REJECTS ambiguous slash dates such as 01/12/2026, where 1 Dec and 12 Jan are equally
    defensible readings — a control must not pick one silently.
    """
    if value is None:
        raise DateFormatError(f"{field}: empty")
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    s = str(value).strip()
    if not s:
        raise DateFormatError(f"{field}: empty")

    # Excel serial (integer, or a float from a date-time cell)
    try:
        serial = float(s)
    except ValueError:
        serial = None
    if serial is not None:
        if not (MIN_EXCEL_SERIAL <= serial <= MAX_EXCEL_SERIAL):
            raise DateFormatError(f"{field}: numeric value {s!r} is not a plausible date serial")
        return EXCEL_EPOCH + dt.timedelta(days=int(serial))

    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass

    parts = s.replace("-", "/").split("/")
    if len(parts) == 3 and all(p.isdigit() for p in parts):
        a, b, c = (int(p) for p in parts)
        if len(parts[2]) == 4:                       # ?/?/YYYY
            if a > 12 and b <= 12:
                return dt.date(c, b, a)              # unambiguously D/M/Y
            if b > 12 and a <= 12:
                return dt.date(c, a, b)              # unambiguously M/D/Y
            raise DateFormatError(
                f"{field}: {s!r} is ambiguous (D/M/Y vs M/D/Y). "
                f"Re-export this column as ISO YYYY-MM-DD.")
    raise DateFormatError(f"{field}: unrecognised date format {s!r} (expected ISO YYYY-MM-DD)")


def is_expired(expiry, today):
    """True when an exception has lapsed. Both arguments are `datetime.date`."""
    return expiry < today


# ---------------------------------------------------------------- finding identity

def identity_key(row):
    """Stable per-account key for a roster row, used to track a finding across cycles.

    Ordered by stability, most stable first:
      1. the app-side account identifier (alias) — an app export's own primary key
      2. the HR employee id
      3. the UPN
      4. a hash of the source row, so a row with no identifier at all is still trackable

    The UPN is deliberately NOT first. Findings used to be keyed on `upn or alias:<x>`, so
    when an app owner backfilled a missing email — the explicit goal of the orphan-reduction
    workstream — the old key vanished and a new one appeared. The control recorded a
    verified CLOSURE, wrote "REMOVAL VERIFIED" into the ticket as audit evidence, and opened
    a fresh finding. Nobody had lost access.
    """
    alias = (row.get("alias") or "").strip()
    if alias:
        return f"acct:{alias.lower()}"
    empid = (row.get("empid") or "").strip()
    if empid:
        return f"emp:{empid.lower()}"
    upn = (row.get("upn") or "").strip().lower()
    if upn:
        return f"upn:{upn}"
    blob = "|".join(f"{k}={v}" for k, v in sorted((row.get("src") or {}).items()))
    return "row:" + hashlib.sha256(blob.encode()).hexdigest()[:16]


def legacy_identity_key(row):
    """The pre-2026-07-26 key formula, kept ONLY to match findings in prior state files.

    Without it, the first cycle after the key change would read every open finding as
    remediated and emit a full set of false closures — the exact failure the change fixes.
    """
    upn = (row.get("upn") or "").strip().lower()
    alias = (row.get("alias") or "").strip()
    return upn or f"alias:{alias}"


def correlation_id(app, key, cls, first_cycle):
    """Deterministic idempotency key stamped on every ServiceNow ticket.

    Lets a re-run ask ServiceNow "does a ticket for this finding already exist?" instead of
    trusting a state file that may not have survived the previous run.
    """
    blob = f"{app}|{key}|{cls}|{first_cycle}"
    return "biterm-" + hashlib.sha256(blob.encode()).hexdigest()[:24]


def file_digest(path, chunk=1 << 20):
    """SHA256 of an input or evidence file, for the per-cycle integrity manifest."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()
