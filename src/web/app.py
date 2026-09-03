from __future__ import annotations

import html
import json
import os
import secrets
import shutil
import threading
import time
import traceback
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

from src.biblical_tests.generation import GenerationError, build_test
from src.biblical_tests.rendering import render_pair
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.selection import SelectionError, parse_selection
from src.biblical_tests.validation import ValidationError, coverage_report, validate_evidence, validate_test


# Everything the caller can get wrong: an unparseable chapter range, a book the
# corpus lacks, a selection too thin to build a test from. These carry messages
# written for the person filling in the form, so they are shown as-is. Anything
# else is a defect in this program and must not be echoed back to a browser.
USER_ERRORS = (SelectionError, GenerationError, ValidationError)


# Resolve storage from the project, not from the process working directory.  A
# platform may start the web process from a different directory than the repo.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT = PROJECT_ROOT / "output"
REPOSITORY = BibleRepository(PROJECT_ROOT / "data")

# Anti-abuse: max requests to /generate per IP within the rolling window below.
RATE_LIMIT_MAX_REQUESTS = 10
RATE_LIMIT_WINDOW_SECONDS = 60 * 60
_REQUEST_LOG: dict[str, deque[float]] = defaultdict(deque)
_RATE_LIMIT_LOCK = threading.Lock()
MAX_REQUEST_BYTES = 64 * 1024
OUTPUT_RETENTION_SECONDS = 24 * 60 * 60
APP_PATH = "/generare-teste"
# Set by the Procfile, where exactly one platform router sits in front of this
# process. Off by default so a directly-reachable instance never lets a caller
# pick its own rate-limit identity by sending the header itself.
TRUST_PROXY = os.environ.get("TRUST_PROXY", "").strip().lower() in {"1", "true", "yes"}
_CLEANUP_LOCK = threading.Lock()


def client_key(handler: BaseHTTPRequestHandler) -> str:
    """The address the rate limiter counts against.

    Behind a platform router every request arrives from the router's own
    address, so counting `client_address` there makes the limit global: ten
    generations an hour for all visitors put together, rather than per
    visitor. The router appends the real caller to X-Forwarded-For, so with
    exactly one trusted proxy in front the rightmost entry is the one the
    router itself wrote — the left of it is whatever the caller may have made
    up, and is deliberately ignored.
    """
    forwarded = handler.headers.get("X-Forwarded-For", "") if TRUST_PROXY else ""
    if forwarded.strip():
        return forwarded.rsplit(",", 1)[-1].strip()
    return handler.client_address[0]


def rate_limited(client_ip: str) -> bool:
    """True if client_ip has hit the generation limit for the current window."""
    now = time.monotonic()
    with _RATE_LIMIT_LOCK:
        log = _REQUEST_LOG[client_ip]
        while log and now - log[0] > RATE_LIMIT_WINDOW_SECONDS:
            log.popleft()
        if len(log) >= RATE_LIMIT_MAX_REQUESTS:
            return True
        log.append(now)
        # Callers that have gone quiet keep an empty deque under their address
        # forever otherwise, so the table grows without bound for as long as
        # the process lives. Sweeping the other entries here keeps it the size
        # of the active caller set; it is O(callers) under a lock already held,
        # and the limit above caps how often this runs.
        for address in [key for key, seen in _REQUEST_LOG.items()
                        if key != client_ip and (not seen or now - seen[-1] > RATE_LIMIT_WINDOW_SECONDS)]:
            del _REQUEST_LOG[address]
        return False


def cleanup_output() -> None:
    """Remove expired generated sessions, keeping the output directory bounded.

    Single-flight: two POSTs served on different threads would otherwise walk
    the same directory at once and the loser would `stat` an entry the winner
    had just removed, surfacing to its caller as a spurious generation
    failure. A skipped sweep costs nothing — the next request runs one.
    """
    if not OUTPUT.exists() or not _CLEANUP_LOCK.acquire(blocking=False):
        return
    try:
        cutoff = time.time() - OUTPUT_RETENTION_SECONDS
        for item in OUTPUT.iterdir():
            try:
                if item.is_dir() and item.stat().st_mtime < cutoff:
                    shutil.rmtree(item, ignore_errors=True)
            except OSError:
                continue
    except OSError:
        return
    finally:
        _CLEANUP_LOCK.release()


def page(message: str = "", links: list[tuple[str, str]] | None = None) -> str:
    buttons = "".join(f'<a class="download" href="{html.escape(url)}">⬇ {html.escape(label)}</a>' for label, url in (links or []))
    results = f'<div class="results"><h2>Teste generate</h2>{buttons}</div>' if buttons else ""
    return f"""<!doctype html><html lang='ro'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>
    <title>Generator teste — 1 și 2 Samuel</title>
    <style>
    :root{{color-scheme:light}}
    *{{box-sizing:border-box}}
    body{{
      font:16px/1.45 'Segoe UI',Calibri,Arial,sans-serif;margin:0;min-height:100vh;color:#2a231a;
      background:
        radial-gradient(circle at 15% 10%, rgba(201,164,90,.35), transparent 45%),
        radial-gradient(circle at 85% 90%, rgba(120,80,40,.30), transparent 50%),
        linear-gradient(160deg,#3b2f22 0%,#5a4630 45%,#7a6142 100%);
      background-attachment:fixed;
      padding:32px 16px;
    }}
    main{{max-width:720px;margin:0 auto;background:rgba(255,252,246,.97);padding:36px 40px;border-radius:16px;box-shadow:0 12px 40px rgba(0,0,0,.35);border:1px solid rgba(201,164,90,.4)}}
    h1{{margin:0 0 4px;font-size:1.7rem;color:#3b2f22}}
    .subtitle{{margin:0 0 26px;color:#6b5a42;font-size:.98rem}}
    fieldset{{border:1px solid #e4d9c4;border-radius:10px;padding:16px 18px 18px;margin:0 0 18px}}
    legend{{padding:0 8px;font-weight:600;color:#8a6a2e;font-size:.85rem;text-transform:uppercase;letter-spacing:.04em}}
    .row{{display:grid;grid-template-columns:1fr 1fr;gap:14px}}
    label{{display:block;font-weight:600;margin-top:12px;color:#3b2f22;font-size:.93rem}}
    label:first-child{{margin-top:0}}
    .hint{{font-weight:400;color:#8a7c65;font-size:.82rem;margin-top:2px}}
    input,textarea,select{{box-sizing:border-box;width:100%;padding:10px 12px;margin-top:5px;font:inherit;border:1px solid #d8cbb0;border-radius:8px;background:#fffdf9;transition:border-color .15s,box-shadow .15s}}
    input:focus,textarea:focus,select:focus{{outline:none;border-color:#a3782f;box-shadow:0 0 0 3px rgba(163,120,47,.18)}}
    textarea{{height:64px;resize:vertical}}
    button{{display:block;width:100%;background:linear-gradient(160deg,#a3782f,#7a5a22);color:#fff;border:0;border-radius:9px;padding:14px 16px;margin-top:26px;font:inherit;font-weight:600;font-size:1.02rem;letter-spacing:.02em;cursor:pointer;box-shadow:0 4px 14px rgba(122,90,34,.35);transition:transform .1s,box-shadow .15s}}
    button:hover{{transform:translateY(-1px);box-shadow:0 6px 18px rgba(122,90,34,.45)}}
    button:active{{transform:translateY(0)}}
    .ok{{color:#147a35;font-weight:600;background:#e9f7ee;border:1px solid #bfe6cc;border-radius:8px;padding:10px 14px;margin:0 0 18px}}
    .err{{color:#9c2b1f;font-weight:600;background:#fbeceb;border:1px solid #f0c4bf;border-radius:8px;padding:10px 14px;margin:0 0 18px}}
    .results{{margin-top:22px;padding-top:18px;border-top:1px dashed #e4d9c4}}
    .results h2{{margin:0 0 10px;font-size:1rem;color:#8a6a2e;text-transform:uppercase;letter-spacing:.04em}}
    .download{{display:block;background:#fff;border:1px solid #d8cbb0;color:#3b2f22;border-radius:8px;padding:11px 14px;margin-top:8px;text-decoration:none;font-weight:600;transition:border-color .15s,background .15s}}
    .download:hover{{border-color:#a3782f;background:#fdf8ef}}
    footer{{text-align:center;color:rgba(255,252,246,.65);font-size:.82rem;margin-top:22px}}
    @media (max-width:520px){{.row{{grid-template-columns:1fr}}main{{padding:26px 22px}}}}
    </style>
    <main>
    <h1>📜 Generator teste — 1 și 2 Samuel</h1>
    <p class="subtitle">Teste biblice pentru Talantul în Negoț, generate exclusiv din corpusul local verificat (Secțiunile I–IV).</p>
    {message}
    <form method='post' action='{APP_PATH}/generate'>
      <fieldset>
        <legend>Material</legend>
        <label>Capitole biblice
          <span class="hint">ex: „1 Samuel 1,2,3" sau un interval de capitole</span>
        </label>
        <textarea name='chapters'>1 Samuel 1,2,3</textarea>
      </fieldset>
      <fieldset>
        <legend>Detalii concurs</legend>
        <div class="row">
          <div><label>Categorie</label><input name='category' value='' placeholder='ex: 6-7'></div>
          <div><label>Ediție</label><input name='edition' value='2027'></div>
        </div>
        <div class="row">
          <div><label>Etapă</label><input name='stage' value='' placeholder='ex: Faza pe biserică'></div>
          <div><label>Data</label><input name='date' value='' placeholder='ex: 12 aprilie 2027'></div>
        </div>
      </fieldset>
      <fieldset>
        <legend>Generare</legend>
        <div class="row">
          <div><label>Număr variante</label><input type='number' min='1' max='10' name='versions' value='1'></div>
          <div><label>Seed <span class="hint">(opțional, pentru reproducere)</span></label><input name='seed' value='12345'></div>
        </div>
      </fieldset>
      <button>✦ GENEREAZĂ TESTUL</button>
    </form>
    {results}
    </main>
    <footer>Fiecare întrebare provine dintr-un verset real, validat automat.</footer>
    </html>"""


def _whole_number(data: dict[str, str], key: str, default: int | None, label: str) -> int | None:
    """Reads a numeric form field, reporting a bad one in the caller's words.

    Left to bare `int()` a typo raises a ValueError whose message ("invalid
    literal for int() with base 10: 'douăzeci'") is Python's, not the form's,
    and would now be classed as an internal fault rather than something the
    person filling in the field can fix.
    """
    raw = (data.get(key) or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        raise SelectionError(f"{label} trebuie să fie un număr întreg.") from None


def make_tests(data: dict[str, str]) -> list[tuple[str, str]]:
    selection = parse_selection(data.get("chapters", ""))
    repo = REPOSITORY
    seed = _whole_number(data, "seed", None, "Seed-ul")
    base_seed = secrets.randbelow(2**31) if seed is None else seed
    versions = max(1, min(10, _whole_number(data, "versions", 1, "Numărul de variante")))
    contest = {"title": "TALANTUL ÎN NEGOȚ", "category": data.get("category", "6_7"), "edition": _whole_number(data, "edition", 2027, "Ediția"), "stage": data.get("stage", "Faza pe biserică"), "date": data.get("date", "")}
    scoring = {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}
    session_id = secrets.token_urlsafe(9)
    links = []
    for version in range(1, versions + 1):
        test = build_test(repo.facts_for(selection), selection, contest, scoring, base_seed, version)
        validate_test(test)
        validate_evidence(test, repo)
        folder = OUTPUT / session_id / f"V{version}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "test.json").write_text(json.dumps(test.to_dict() | {"coverage": coverage_report(test), "translation": repo.translation}, ensure_ascii=False, indent=2), encoding="utf-8")
        candidate, answer_key = render_pair(test, folder)
        candidate_url = quote(candidate.relative_to(OUTPUT).as_posix(), safe="/")
        answer_url = quote(answer_key.relative_to(OUTPUT).as_posix(), safe="/")
        links.extend([(f"Descarcă Test Concurenți V{version}", f"{APP_PATH}/download/{candidate_url}"), (f"Descarcă Barem Corectori V{version}", f"{APP_PATH}/download/{answer_url}")])
    return links


# The page loads nothing from anywhere: no scripts, no fonts, no images. The
# policy says exactly that, so a future edit that reaches for a CDN fails
# visibly here instead of quietly widening what the page can talk to.
CONTENT_SECURITY_POLICY = "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
CONTENT_TYPES = {".pdf": "application/pdf", ".json": "application/json; charset=utf-8"}


class Handler(BaseHTTPRequestHandler):
    def _html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", APP_PATH, f"{APP_PATH}/"}:
            return self._html(page())
        download_prefix = f"{APP_PATH}/download/"
        legacy_download_prefix = "/download/"
        if parsed.path.startswith(download_prefix) or parsed.path.startswith(legacy_download_prefix):
            prefix = download_prefix if parsed.path.startswith(download_prefix) else legacy_download_prefix
            file = (OUTPUT / unquote(parsed.path.removeprefix(prefix))).resolve()
            if OUTPUT not in file.parents or not file.is_file():
                return self.send_error(HTTPStatus.NOT_FOUND)
            data = file.read_bytes()
            self.send_response(HTTPStatus.OK)
            # Declared from the suffix: this route also serves the session's
            # test.json, which was previously labelled application/pdf.
            self.send_header("Content-Type", CONTENT_TYPES.get(file.suffix.lower(), "application/octet-stream"))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if urlparse(self.path).path not in {"/generate", f"{APP_PATH}/generate", f"{APP_PATH}/generate/"}:
            return self.send_error(HTTPStatus.NOT_FOUND)
        client_ip = client_key(self)
        if rate_limited(client_ip):
            message = f"<p class='err'><strong>Prea multe cereri.</strong> Poți genera din nou peste puțin timp (limită: {RATE_LIMIT_MAX_REQUESTS}/oră per utilizator).</p>"
            return self._html(page(message), HTTPStatus.TOO_MANY_REQUESTS)
        try:
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                return self._html(page("<p class='err'>Cerere invalidă.</p>"), HTTPStatus.BAD_REQUEST)
            if length < 0 or length > MAX_REQUEST_BYTES:
                return self._html(page("<p class='err'>Cerere prea mare.</p>"), HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
            raw = self.rfile.read(length).decode("utf-8")
            values = {key: value[-1] for key, value in parse_qs(raw, max_num_fields=32).items()}
            cleanup_output()
            links = make_tests(values)
        except USER_ERRORS as exc:
            return self._html(page(f"<p class='err'><strong>Generarea a eșuat:</strong> {html.escape(str(exc))}</p>"), HTTPStatus.BAD_REQUEST)
        except Exception:
            # A defect here, not a bad request: the browser gets nothing but
            # the fact that it failed, and the detail goes to the server log
            # where it belongs. Echoing `str(exc)` handed out whatever the
            # exception happened to carry — a filesystem path, a stack of
            # internals — to anyone who could make the generator throw.
            traceback.print_exc()
            return self._html(page("<p class='err'><strong>Eroare internă.</strong> Încercați din nou; dacă persistă, raportați ora exactă a încercării.</p>"), HTTPStatus.INTERNAL_SERVER_ERROR)
        # Outside the try: a failure while writing this response must not lead
        # to a second, complete response being written onto the same connection.
        self._html(page("<p class='ok'>✓ Test generat și validat.</p>", links))


def main() -> None:
    port = int(os.environ.get("PORT", 8000))
    # Local runs stay on the loopback address the README documents; binding
    # 0.0.0.0 unconditionally published the generator to every machine on the
    # network the moment someone ran it on a laptop. A platform sets PORT, and
    # there the bind has to be on all interfaces for the router to reach it.
    host = os.environ.get("HOST") or ("0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Interfața este disponibilă pe http://{'127.0.0.1' if host == '0.0.0.0' else host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
