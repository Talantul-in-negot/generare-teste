from __future__ import annotations

import time
import unittest
from types import SimpleNamespace

from src.biblical_tests.selection import SelectionError
from src.web import app


def _request(remote: str, forwarded: str | None = None):
    headers = {"X-Forwarded-For": forwarded} if forwarded else {}
    return SimpleNamespace(client_address=(remote, 51234), headers=headers)


class ClientKeyTests(unittest.TestCase):
    """Behind a platform router every visitor shares one source address, so
    counting `client_address` there turns a per-visitor limit into a global
    one — ten generations an hour for everybody put together."""

    def setUp(self):
        self.trusted = app.TRUST_PROXY

    def tearDown(self):
        app.TRUST_PROXY = self.trusted

    def test_direct_connection_uses_the_socket_address(self):
        app.TRUST_PROXY = False
        self.assertEqual(app.client_key(_request("203.0.113.9")), "203.0.113.9")

    def test_forwarded_header_is_ignored_when_no_proxy_is_trusted(self):
        # Otherwise a caller reaching the process directly picks its own
        # rate-limit identity, and the limit stops existing.
        app.TRUST_PROXY = False
        self.assertEqual(app.client_key(_request("203.0.113.9", "1.2.3.4")), "203.0.113.9")

    def test_behind_a_trusted_proxy_the_rightmost_hop_wins(self):
        # The router appends the address it saw; anything to the left of it is
        # whatever the caller chose to send.
        app.TRUST_PROXY = True
        self.assertEqual(app.client_key(_request("10.0.0.1", "1.2.3.4, 198.51.100.7")), "198.51.100.7")

    def test_falls_back_to_the_socket_when_the_header_is_absent(self):
        app.TRUST_PROXY = True
        self.assertEqual(app.client_key(_request("10.0.0.1")), "10.0.0.1")


class RateLimitTests(unittest.TestCase):
    def setUp(self):
        app._REQUEST_LOG.clear()

    tearDown = setUp

    def test_limit_is_per_caller(self):
        for _ in range(app.RATE_LIMIT_MAX_REQUESTS):
            self.assertFalse(app.rate_limited("198.51.100.1"))
        self.assertTrue(app.rate_limited("198.51.100.1"))
        self.assertFalse(app.rate_limited("198.51.100.2"), "one caller's quota must not consume another's")

    def test_expired_callers_are_evicted_from_the_table(self):
        app.rate_limited("198.51.100.1")
        app._REQUEST_LOG["198.51.100.1"][0] = time.monotonic() - app.RATE_LIMIT_WINDOW_SECONDS - 1
        app.rate_limited("198.51.100.2")
        self.assertNotIn("198.51.100.1", app._REQUEST_LOG, "the table must not grow for every address ever seen")


class NumericFieldTests(unittest.TestCase):
    def test_blank_and_missing_fall_back_to_the_default(self):
        self.assertEqual(app._whole_number({}, "edition", 2027, "Ediția"), 2027)
        self.assertEqual(app._whole_number({"edition": "  "}, "edition", 2027, "Ediția"), 2027)

    def test_a_typo_is_reported_in_the_form_s_own_words(self):
        with self.assertRaises(SelectionError) as caught:
            app._whole_number({"edition": "douăzeci"}, "edition", 2027, "Ediția")
        self.assertIn("Ediția", str(caught.exception))
        self.assertNotIn("invalid literal", str(caught.exception))

    def test_a_bad_field_is_a_user_error_not_an_internal_one(self):
        self.assertTrue(issubclass(SelectionError, app.USER_ERRORS))


class EmptySelectionTests(unittest.TestCase):
    def test_missing_chapters_field_is_a_user_error(self):
        # Previously a KeyError, which the handler rendered to the browser as
        # the literal text "Generarea a eșuat: 'chapters'".
        with self.assertRaises(app.USER_ERRORS):
            app.make_tests({})


if __name__ == "__main__":
    unittest.main()
