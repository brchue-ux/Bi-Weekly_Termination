"""Single source of environment configuration for every BiTerm script.

Before this module, 19 scripts hardcoded the tenant hostname and the ORGID, and the
ServiceNow instance / catalog item / fulfiller were literals inside the control itself.
Pointing the control at the real work org meant editing code, and one named individual
(`SN_ASSIGNEE`) was compiled into every ticket the control would ever file.

Resolution order, lowest priority first:
  1. DEFAULTS below — the demo tenant, so an unconfigured checkout behaves exactly as it
     did before this module existed. Nothing silently changes environment.
  2. `config.json` at the project root (git-ignored; `config.example.json` is the template).
  3. `BITERM_*` environment variables, for CI / one-off overrides.

`require()` is the accessor for anything that must not fall back to a demo default when a
real environment is configured — it raises rather than letting a run proceed against the
wrong tenant with a silently-defaulted value.
"""
import json
import os
from pathlib import Path

PROJ = Path(__file__).resolve().parent.parent
CONFIG_FILE = PROJ / "config.json"

# Demo-tenant defaults. These are the values previously hardcoded across the scripts; they
# are kept so behaviour is byte-identical without a config.json, and so the seeded demo
# tenant stays runnable. A real deployment overrides all of them.
DEFAULTS = {
    "org": "https://demo-beige-haddock-4684.okta.com",
    "org_id": "00o159zwmhz6L5eo4698",

    # OAuth service app — the least-privilege runtime identity of the detective control.
    "client_id": "0oa15jbaw6sllCbVB698",
    "private_key_file": str(Path.home() / ".secrets" / "term_revamp_oauth_demo_private.pem"),
    "kid": "biterm-2026-07",
    "scopes": ["okta.users.read", "okta.apps.read",
               "okta.governance.accessCertifications.read"],

    # Privileged SSWS token, used ONLY by scaffolding (seeding, entitlement loading,
    # campaign creation) — never by the reconciliation control.
    "admin_token_file": str(Path.home() / ".secrets" / "claude_3rd_party.txt"),

    "servicenow": {
        "instance": "https://dev336362.service-now.com",
        "credentials_file": str(Path.home() / ".secrets" / "Service Now.txt"),
        "catalog_item": "b02e8afc839a8310d89511b6feaad3c8",  # "Terminated User Access Removal"
        "assignment_group": "Access Management",
        # Deliberately empty by default: tickets route to the GROUP. Naming an individual
        # here makes the control fail when that person leaves. Set it only if a named
        # fulfiller is genuinely required.
        "assignee": "",
    },

    "http": {
        "timeout_seconds": 30,
        "max_attempts": 6,
        "backoff_base_seconds": 1.0,
        "backoff_cap_seconds": 60.0,
    },

    # Blast-radius ceiling for any script that can REMOVE access. See run_all.py.
    "removal_guard": {
        "max_absolute": 10,
        "max_fraction": 0.10,
    },

    "roster_dir": str(PROJ / "App User Lists"),
    "column": None,
    "apps": [],
}

_cache = None


class ConfigError(RuntimeError):
    """Configuration is missing or unusable.

    Typed rather than SystemExit: a library must not decide to kill the process. Entrypoints
    catch this and exit cleanly; tests can assert on it.
    """


def _deep_merge(base, override):
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _from_env():
    """BITERM_ORG, BITERM_ORG_ID, BITERM_SERVICENOW_INSTANCE, BITERM_HTTP_TIMEOUT_SECONDS…

    Nested keys use a single underscore-joined path; only keys that already exist in
    DEFAULTS are accepted, so a typo'd variable is ignored rather than inventing config.
    """
    env = {}
    for section, default in DEFAULTS.items():
        if isinstance(default, dict):
            for key in default:
                raw = os.environ.get(f"BITERM_{section}_{key}".upper())
                if raw is not None:
                    env.setdefault(section, {})[key] = _coerce(default[key], raw)
        else:
            raw = os.environ.get(f"BITERM_{section}".upper())
            if raw is not None:
                env[section] = _coerce(default, raw)
    return env


def _coerce(default, raw):
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes")
    if isinstance(default, int) and not isinstance(default, bool):
        return int(raw)
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        return raw.split()
    return raw


def load(path=None, refresh=False):
    """Merged configuration dict. Cached; `refresh=True` re-reads (used by tests)."""
    global _cache
    if _cache is not None and not refresh and path is None:
        return _cache
    cfg = dict(DEFAULTS)
    src = Path(path) if path else CONFIG_FILE
    if src.exists():
        try:
            cfg = _deep_merge(cfg, json.loads(src.read_text()))
        except json.JSONDecodeError as e:
            raise ConfigError(f"{src}: malformed JSON — {e}") from e
    cfg = _deep_merge(cfg, _from_env())
    cfg["org"] = cfg["org"].rstrip("/")
    cfg["_source"] = str(src) if src.exists() else "(defaults)"
    if path is None:
        _cache = cfg
    return cfg


def get(*keys, default=None):
    """Read a (possibly nested) config value, falling back to `default`."""
    node = load()
    for k in keys:
        if not isinstance(node, dict) or k not in node:
            return default
        node = node[k]
    return node


def require(*keys):
    """Read a config value that must be present and non-empty."""
    val = get(*keys)
    if val in (None, "", [], {}):
        raise ConfigError(
            f"Missing required configuration '{'.'.join(keys)}'. "
            f"Set it in {CONFIG_FILE} (copy config.example.json) or via "
            f"BITERM_{'_'.join(keys).upper()}."
        )
    return val


def org():
    return require("org")


def is_demo_tenant():
    """True when running against the seeded demo org.

    Scripts that write use this to decide whether a confirmation prompt is mandatory:
    an unrecognised (i.e. real) tenant always requires one.
    """
    return "demo-beige-haddock-4684" in get("org", default="")


def describe():
    """One line for run logs and confirmation prompts — never print secrets."""
    cfg = load()
    return (f"org={cfg['org']} org_id={cfg['org_id']} "
            f"servicenow={cfg['servicenow']['instance']} config={cfg['_source']}")
