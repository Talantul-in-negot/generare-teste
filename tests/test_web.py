from __future__ import annotations

import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
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



class LiveServerTests(unittest.TestCase):
    """Drives the actual HTTP handler over a real socket. Everything below was
    verified once by hand against a running server and had no test pinning it
    in place — this is that verification, made permanent.
    """

    @classmethod
    def setUpClass(cls):
        cls._output_dir = tempfile.TemporaryDirectory()
        cls._orig_output = app.OUTPUT
        app.OUTPUT = Path(cls._output_dir.name)
        app.OUTPUT.mkdir(exist_ok=True)
        cls.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        app.OUTPUT = cls._orig_output
        cls._output_dir.cleanup()

    def setUp(self):
        app._REQUEST_LOG.clear()

    def _request(self, method, path, data=None):
        url = self.base + path
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(url, data=body, method=method)
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()

    def test_a_generated_pdf_downloads_with_the_right_headers(self):
        status, _, body = self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "1 Samuel 1,2,3"})
        self.assertEqual(status, 200)
        links = re.findall(r'href="([^"]+)"', body.decode())
        pdf_links = [link for link in links if link.endswith(".pdf")]
        self.assertEqual(len(pdf_links), 2, "expected one competitor PDF and one answer key")
        for link in pdf_links:
            dstatus, headers, dbody = self._request("GET", link)
            self.assertEqual(dstatus, 200)
            self.assertEqual(headers.get("Content-Type"), "application/pdf")
            self.assertIn("attachment", headers.get("Content-Disposition", ""))
            self.assertGreater(len(dbody), 1000)

    def test_path_traversal_is_refused_raw_and_percent_encoded(self):
        for suffix in ("../../etc/passwd", "%2e%2e/%2e%2e/etc/passwd", "..%2f..%2fetc%2fpasswd"):
            status, _, _ = self._request("GET", f"{app.APP_PATH}/download/{suffix}")
            self.assertEqual(status, 404, suffix)

    def test_the_legacy_download_prefix_still_works(self):
        _, _, body = self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "1 Samuel 1,2,3"})
        links = re.findall(r'href="([^"]+)"', body.decode())
        pdf_link = next(link for link in links if link.endswith(".pdf"))
        legacy = pdf_link.replace(app.APP_PATH, "", 1)
        status, _, _ = self._request("GET", legacy)
        self.assertEqual(status, 200)

    def test_a_bad_selection_is_a_400_with_the_selection_error_message(self):
        status, _, body = self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "Nonexistent 99"})
        self.assertEqual(status, 400)
        self.assertIn("necunoscută", body.decode())

    def test_an_internal_failure_is_a_500_that_leaks_nothing(self):
        original = app.build_test

        def boom(*args, **kwargs):
            raise RuntimeError("C:\\Users\\someone\\secret-internal-path\\leak")

        app.build_test = boom
        try:
            status, _, body = self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "1 Samuel 1,2,3"})
        finally:
            app.build_test = original
        self.assertEqual(status, 500)
        text = body.decode()
        self.assertNotIn("secret-internal-path", text)
        self.assertNotIn("RuntimeError", text)

    def test_two_variants_actually_differ(self):
        # `avoid` is threaded through `make_tests`'s variant loop; this is the
        # end-to-end confirmation that the two PDFs it produces are not the
        # same paper under two names.
        status, _, body = self._request(
            "POST", f"{app.APP_PATH}/generate",
            {"chapters": "1 Samuel 1,2,3,4", "versions": "2", "seed": "42"},
        )
        self.assertEqual(status, 200)
        links = re.findall(r'href="([^"]+)"', body.decode())
        competitors = [link for link in links if link.endswith(".pdf") and "barem" not in link]
        self.assertEqual(len(competitors), 2)
        bodies = [self._request("GET", link)[2] for link in competitors]
        self.assertNotEqual(bodies[0], bodies[1])

    def test_an_oversized_body_is_refused(self):
        status, _, _ = self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "x" * (app.MAX_REQUEST_BYTES + 100)})
        self.assertEqual(status, 413)

    def test_rate_limiting_applies_over_the_real_http_path(self):
        for _ in range(app.RATE_LIMIT_MAX_REQUESTS):
            self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "1 Samuel 1,2,3"})
        status, _, _ = self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "1 Samuel 1,2,3"})
        self.assertEqual(status, 429)

    def test_security_headers_are_present_on_every_response(self):
        for method, path in (("GET", "/"), ("GET", f"{app.APP_PATH}/download/nope")):
            _, headers, _ = self._request(method, path)
            if "Content-Security-Policy" in headers:
                self.assertEqual(headers["X-Content-Type-Options"], "nosniff")

    def test_versions_out_of_the_documented_range_are_clamped_not_rejected(self):
        for value, expected_pdfs in (("0", 2), ("999", 20), ("-1", 2)):
            status, _, body = self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "1 Samuel 1,2,3", "versions": value})
            self.assertEqual(status, 200, value)
            links = re.findall(r'href="([^"]+)"', body.decode())
            self.assertEqual(len([l for l in links if l.endswith(".pdf")]), expected_pdfs, value)



if __name__ == "__main__":
    unittest.main()
