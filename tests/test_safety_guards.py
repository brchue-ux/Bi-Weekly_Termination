"""The guards that stand between a bad input and a real access removal.

Every test here corresponds to a blocker in docs/CODE_REVIEW_2026-07-26.md. They are the
cheapest possible regression net for the one code path in this repo that can take access
away from a real person.
"""
import unittest
from unittest import mock

import tests  # noqa: F401

import biterm_http
import okta_bookmark_sync as sync
import run_all


class FakeClient:
    """Minimal stand-in for biterm_http.Client: scripted per-path responses."""

    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request(self, method, path, body=None, **kw):
        self.calls.append((method, path))
        r = self.responses.get(path)
        if isinstance(r, Exception):
            raise r
        if r is None:
            if 404 in kw.get("allow_statuses", ()):
                return 404, {}, {}
            raise biterm_http.OktaApiError(method, path, 404, {})
        return 200, r, {}

    def get_json(self, path, **kw):
        return self.request("GET", path, **kw)[1]


class FailClosedResolution(unittest.TestCase):
    """A transport failure must never be read as 'this user does not exist'."""

    def test_404_is_not_found(self):
        client = FakeClient({})
        state, _ = sync.resolve_user(client, "ghost@wm.com")
        self.assertEqual(state, sync.NOT_FOUND)

    def test_rate_limit_is_unknown_not_absent(self):
        client = FakeClient({"/api/v1/users/limited%40wm.com":
                             biterm_http.TransientError("429 exhausted")})
        state, _ = sync.resolve_user(client, "limited@wm.com")
        self.assertEqual(state, sync.UNKNOWN)

    def test_server_error_is_unknown_not_absent(self):
        client = FakeClient({"/api/v1/users/broken%40wm.com":
                             biterm_http.OktaApiError("GET", "/x", 503, {})})
        state, _ = sync.resolve_user(client, "broken@wm.com")
        self.assertEqual(state, sync.UNKNOWN)

    def test_one_unknown_aborts_the_whole_run_before_any_write(self):
        client = FakeClient({
            "/api/v1/users/ok%40wm.com": {"id": "u1"},
            "/api/v1/users/broken%40wm.com": biterm_http.TransientError("boom"),
        })
        with self.assertRaises(sync.UnsafeSyncError) as cm:
            sync.resolve_all(client, ["ok@wm.com", "broken@wm.com"])
        self.assertIn("removal set", str(cm.exception))

    def test_clean_resolution_separates_found_from_absent(self):
        client = FakeClient({"/api/v1/users/ok%40wm.com": {"id": "u1"}})
        resolved, unmatched = sync.resolve_all(client, ["ok@wm.com", "ghost@wm.com"])
        self.assertEqual(resolved, {"u1": "ok@wm.com"})
        self.assertEqual(unmatched, ["ghost@wm.com"])


class EmptyRosterRefusal(unittest.TestCase):
    """An empty parse is a parse failure, never an instruction to unassign everyone."""

    def test_empty_csv_raises(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "roster.csv"
            p.write_text("UPN\n")
            with self.assertRaises(sync.RosterError):
                sync.parse_export(str(p), "UPN")

    def test_missing_column_names_what_is_available(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "roster.csv"
            p.write_text("Email\na@wm.com\n")
            with self.assertRaises(sync.RosterError) as cm:
                sync.parse_export(str(p), "UPN")
            self.assertIn("Email", str(cm.exception))


class RemovalBlastRadius(unittest.TestCase):
    """The ceiling that turns a catastrophic mistake into an aborted run."""

    def _current(self, n):
        return {f"u{i}": {"profile": {"login": f"u{i}@wm.com"}} for i in range(n)}

    def test_small_removal_is_allowed(self):
        run_all.guard_removals("App", {"u1"}, self._current(100), {"u2": "x"}, None)

    def test_mass_removal_is_refused(self):
        with self.assertRaises(run_all.AbortRun) as cm:
            run_all.guard_removals("App", set(self._current(100)), self._current(100), {}, None)
        self.assertIn("refusing to unassign", str(cm.exception))

    def test_exact_expected_count_unlocks_it(self):
        current = self._current(100)
        run_all.guard_removals("App", set(current), current, {}, 100)

    def test_wrong_expected_count_does_not_unlock_it(self):
        current = self._current(100)
        with self.assertRaises(run_all.AbortRun):
            run_all.guard_removals("App", set(current), current, {}, 99)

    def test_ceiling_scales_with_population(self):
        current = self._current(1000)
        # 10% of 1000 = 100 permitted; 101 refused.
        run_all.guard_removals("App", set(list(current)[:100]), current, {}, None)
        with self.assertRaises(run_all.AbortRun):
            run_all.guard_removals("App", set(list(current)[:101]), current, {}, None)


class ConfirmationExactness(unittest.TestCase):
    """`if typed not in org` passed for the single character 'o', and for ''."""

    def test_substring_no_longer_confirms(self):
        host = "demo-beige-haddock-4684.okta.com"
        for typed in ("o", "", "okta", "okta.com"):
            self.assertNotEqual(typed, host,
                                "a substring must not be accepted as confirmation")

    def test_exact_hostname_confirms(self):
        host = "demo-beige-haddock-4684.okta.com"
        self.assertEqual(host, host)


class NonInteractiveLiveRunIsRefused(unittest.TestCase):
    """Regression: a live run with no terminal must ABORT, not proceed unconfirmed.

    The first version of this guard read `if args.yes or not sys.stdin.isatty(): return`,
    treating "there is no terminal" as "no confirmation needed". On 2026-07-26 a piped
    diagnostic command therefore executed a full live cycle and created 29 unintended
    ServiceNow ticket chains. Absence of a human is a reason to STOP, never to proceed.
    """

    class _Args:
        def __init__(self, yes):
            self.yes = yes

    def test_no_tty_and_no_yes_exits(self):
        import biweekly_recon as recon
        with mock.patch("sys.stdin.isatty", return_value=False):
            with self.assertRaises(SystemExit) as cm:
                recon.confirm_apply(self._Args(yes=False))
        self.assertIn("Refusing a non-interactive", str(cm.exception))

    def test_explicit_yes_is_honoured(self):
        import biweekly_recon as recon
        with mock.patch("sys.stdin.isatty", return_value=False):
            recon.confirm_apply(self._Args(yes=True))   # must not raise

    def test_tty_with_wrong_hostname_aborts(self):
        import biweekly_recon as recon
        with mock.patch("sys.stdin.isatty", return_value=True), \
             mock.patch("builtins.input", return_value="o"):
            with self.assertRaises(SystemExit) as cm:
                recon.confirm_apply(self._Args(yes=False))
        self.assertIn("Confirmation did not match", str(cm.exception))

    def test_loader_and_campaign_runner_share_the_guard(self):
        import oig_load_all
        import oig_run_campaigns
        for mod, fn in ((oig_load_all, "confirm"),):
            with mock.patch("sys.stdin.isatty", return_value=False):
                with self.assertRaises(SystemExit):
                    getattr(mod, fn)(self._Args(yes=False))
        self.assertIn("Refusing to launch a LIVE attestation non-interactively",
                      __import__("inspect").getsource(oig_run_campaigns.main))


class HttpClientBehaviour(unittest.TestCase):
    def test_next_link_is_read_from_all_link_headers(self):
        """Okta sends TWO Link headers; get("Link") returns rel="self" and the pager
        silently truncated at 200 users."""
        class Headers:
            def get_all(self, name):
                if name.lower() != "link":
                    return None
                return ['<https://org/api/v1/users?after=A>; rel="self"',
                        '<https://org/api/v1/users?after=B>; rel="next"']
        self.assertEqual(biterm_http._next_link(Headers()),
                         "https://org/api/v1/users?after=B")

    def test_no_next_link_ends_pagination(self):
        class Headers:
            def get_all(self, name):
                return ['<https://org/api/v1/users>; rel="self"']
        self.assertIsNone(biterm_http._next_link(Headers()))

    def test_retry_ladder_covers_429_and_5xx(self):
        for status in (429, 500, 502, 503, 504):
            self.assertIn(status, biterm_http.RETRY_STATUSES)
        self.assertNotIn(400, biterm_http.RETRY_STATUSES)
        self.assertNotIn(403, biterm_http.RETRY_STATUSES)

    def test_client_always_sets_a_timeout(self):
        c = biterm_http.Client("https://example.com", lambda: "Bearer x")
        self.assertIsNotNone(c.timeout)
        self.assertGreater(c.timeout, 0)

    def test_backoff_is_jittered_and_capped(self):
        c = biterm_http.Client("https://example.com", lambda: "Bearer x")
        waits = {c._sleep_for(5, None) for _ in range(20)}
        self.assertGreater(len(waits), 1, "unjittered backoff synchronises parallel runs")
        self.assertTrue(all(w <= c.backoff_cap for w in waits))


class CredentialHandling(unittest.TestCase):
    def test_missing_field_names_what_is_missing(self):
        import tempfile, os
        import biterm_creds
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sn.txt"
            p.write_text("# comment only\nsomeuser\n")
            os.chmod(p, 0o600)
            with self.assertRaises(biterm_creds.CredentialError) as cm:
                biterm_creds.basic_auth(str(p))
            self.assertIn("password", str(cm.exception))

    def test_world_readable_secret_is_refused(self):
        import tempfile, os
        import biterm_creds
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "token.txt"
            p.write_text("token=abc\n")
            os.chmod(p, 0o644)
            with self.assertRaises(biterm_creds.CredentialError) as cm:
                biterm_creds.api_token(str(p))
            self.assertIn("chmod 600", str(cm.exception))

    def test_comment_lines_do_not_break_parsing(self):
        import tempfile, os
        import biterm_creds
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "sn.txt"
            p.write_text("# ServiceNow integration user\nbiterm.termination\npassword=s3cret\n")
            os.chmod(p, 0o600)
            self.assertEqual(biterm_creds.basic_auth(str(p)),
                             ("biterm.termination", "s3cret"))


if __name__ == "__main__":
    unittest.main()
