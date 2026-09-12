from __future__ import annotations

import json
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

    def test_behind_a_trusted_render_proxy_the_real_caller_wins(self):
        # Render writes the real caller first; later entries are proxy hops.
        app.TRUST_PROXY = True
        self.assertEqual(app.client_key(_request("10.0.0.1", "198.51.100.7, 10.26.204.136")), "198.51.100.7")

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


class MinimumSelectionSizeTests(unittest.TestCase):
    """A test spends 28 distinct verses across four sections that compete for
    the same pool. Measured across every contiguous chapter window in both
    books: 2-chapter selections fail to produce a test about 23% of the time,
    3-chapter and up never do. This turns that into an immediate, specific
    message instead of a generation attempt that fails several steps in."""

    def test_one_and_two_chapter_selections_are_rejected_immediately(self):
        for chapters in ("1 Samuel 1", "1 Samuel 1,2"):
            with self.assertRaises(SelectionError) as caught:
                app.make_tests({"chapters": chapters})
            self.assertIn(str(app.MIN_SELECTION_CHAPTERS), str(caught.exception))

    def test_the_count_is_summed_across_books_not_per_book(self):
        # "1 Samuel 1" plus "2 Samuel 1" is two chapters total, not two
        # separate one-chapter selections that would each pass alone.
        with self.assertRaises(SelectionError):
            app.make_tests({"chapters": "1 Samuel 1\n2 Samuel 1"})

    def test_three_chapters_is_the_floor_not_a_ceiling(self):
        # No exception; the generation itself may still succeed or fail on its
        # own terms further down the pipeline.
        selection = app.parse_selection("1 Samuel 1,2,3")
        total = sum(len(chapters) for chapters in selection.values())
        self.assertGreaterEqual(total, app.MIN_SELECTION_CHAPTERS)

    def test_the_pluralisation_is_correct_for_a_single_chapter(self):
        with self.assertRaises(SelectionError) as caught:
            app.make_tests({"chapters": "1 Samuel 1"})
        message = str(caught.exception)
        self.assertIn("1 capitol;", message)
        self.assertNotIn("1 capitole", message)



class _LiveServerMixin:
    """Shared server lifecycle for tests that drive the real HTTP handler over
    a socket. A mixin rather than a base `TestCase`: a subclass of a
    `TestCase` inherits its test methods too, which would silently re-run
    every `LiveServerTests` case a second time under each subclass's name.
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

    def _request(self, method, path, data=None, extra_headers=None):
        url = self.base + path
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        request = urllib.request.Request(url, data=body, method=method, headers=extra_headers or {})
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), exc.read()


class LiveServerTests(_LiveServerMixin, unittest.TestCase):
    """Drives the actual HTTP handler over a real socket. Everything below was
    verified once by hand against a running server and had no test pinning it
    in place — this is that verification, made permanent.
    """

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


class UsageLogTests(_LiveServerMixin, unittest.TestCase):
    """A successful generation is the only thing this app records about who
    used it. That record has to name the real caller (not the platform's own
    address), the selection actually generated, and how many variants — and
    it must never happen for a request that failed."""

    def setUp(self):
        super().setUp()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.log_path = Path(self.tmp.name) / "usage.log"
        self.original_path = app.USAGE_LOG_PATH
        app.USAGE_LOG_PATH = self.log_path
        self.addCleanup(lambda: setattr(app, "USAGE_LOG_PATH", self.original_path))

    def test_a_successful_generation_is_recorded(self):
        app.log_generation("203.0.113.9", {"1 Samuel": [1, 2, 3]}, 2)
        line = self.log_path.read_text(encoding="utf-8").strip()
        self.assertIn("203.0.113.9", line)
        self.assertIn("1 Samuel 1,2,3", line)
        self.assertIn("versions=2", line)

    def test_a_newline_in_a_field_cannot_forge_a_second_line(self):
        app.log_generation("1.2.3.4\nFAKED: 9.9.9.9\tstolen\tversions=1", {"1 Samuel": [1, 2, 3]}, 1)
        lines = self.log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("\n", lines[0])

    def test_a_failed_request_writes_nothing(self):
        with self.assertRaises(app.USER_ERRORS):
            app.make_tests({"chapters": "1 Samuel 1,2"})  # below the 3-chapter floor
        self.assertFalse(self.log_path.exists())

    def test_a_successful_request_over_http_is_recorded(self):
        status, _, _ = self._request("POST", f"{app.APP_PATH}/generate", {"chapters": "1 Samuel 1,2,3"})
        self.assertEqual(status, 200)
        line = self.log_path.read_text(encoding="utf-8")
        self.assertIn("127.0.0.1", line)
        self.assertIn("1 Samuel 1,2,3", line)
        self.assertIn("versions=1", line)

    def test_the_real_caller_is_recorded_not_the_router(self):
        app.TRUST_PROXY = True
        try:
            status, _, _ = self._request(
                "POST", f"{app.APP_PATH}/generate", {"chapters": "1 Samuel 1,2,3"},
                extra_headers={"X-Forwarded-For": "198.51.100.7"},
            )
        finally:
            app.TRUST_PROXY = False
        self.assertEqual(status, 200)
        self.assertIn("198.51.100.7", self.log_path.read_text(encoding="utf-8"))


class SupabaseUsageTests(unittest.TestCase):
    """The optional second half of usage logging. Never touches the network:
    every case here works by replacing `urlopen` itself, so a misconfigured
    or absent Supabase project can't turn into a real HTTP attempt."""

    def setUp(self):
        self.original = (app.SUPABASE_URL, app.SUPABASE_SERVICE_KEY, app.urlopen)
        self.addCleanup(self._restore)

    def _restore(self):
        app.SUPABASE_URL, app.SUPABASE_SERVICE_KEY, app.urlopen = self.original

    def test_unconfigured_supabase_makes_no_request_at_all(self):
        app.SUPABASE_URL, app.SUPABASE_SERVICE_KEY = "", ""
        app.urlopen = lambda *a, **k: (_ for _ in ()).throw(AssertionError("urlopen must not be called"))
        app._post_to_supabase("203.0.113.9", "1 Samuel 1,2,3", 1)  # must not raise

    def test_a_configured_project_receives_the_right_row(self):
        app.SUPABASE_URL, app.SUPABASE_SERVICE_KEY = "https://example.supabase.co", "service-key"
        calls = []

        class _FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        def fake_urlopen(request, timeout=None):
            calls.append((request, timeout))
            return _FakeResponse()

        app.urlopen = fake_urlopen
        app._post_to_supabase("203.0.113.9", "1 Samuel 1,2,3", 2)

        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(request.full_url, "https://example.supabase.co/rest/v1/usage_log")
        self.assertEqual(request.get_header("Apikey"), "service-key")
        self.assertEqual(request.get_header("Authorization"), "Bearer service-key")
        body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(body, {"client_ip": "203.0.113.9", "selection": "1 Samuel 1,2,3", "versions": 2})
        self.assertLessEqual(timeout, 5)

    def test_a_network_failure_does_not_propagate(self):
        app.SUPABASE_URL, app.SUPABASE_SERVICE_KEY = "https://example.supabase.co", "service-key"
        app.urlopen = lambda *a, **k: (_ for _ in ()).throw(urllib.error.URLError("offline"))
        app._post_to_supabase("203.0.113.9", "1 Samuel 1,2,3", 1)  # must not raise

    def test_log_generation_calls_supabase_too(self):
        original = app._post_to_supabase
        with tempfile.TemporaryDirectory() as tmp:
            app.USAGE_LOG_PATH = Path(tmp) / "usage.log"
            recorded = []
            app._post_to_supabase = lambda client_ip, chapters, versions: recorded.append((client_ip, chapters, versions))
            try:
                app.log_generation("203.0.113.9", {"1 Samuel": [1, 2, 3]}, 3)
            finally:
                app._post_to_supabase = original  # a `del` here would remove the real function from the module for good, not just undo this test's patch
        self.assertEqual(recorded, [("203.0.113.9", "1 Samuel 1,2,3", 3)])


if __name__ == "__main__":
    unittest.main()
