from __future__ import annotations

import html
import json
import os
import secrets
import time
from collections import defaultdict, deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from src.biblical_tests.generation import build_test
from src.biblical_tests.rendering import render_pair
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.selection import parse_selection
from src.biblical_tests.validation import coverage_report, validate_evidence, validate_test


OUTPUT = Path("output").resolve()
REPOSITORY = BibleRepository("data")

# Anti-abuse: max requests to /generate per IP within the rolling window below.
RATE_LIMIT_MAX_REQUESTS = 5
RATE_LIMIT_WINDOW_SECONDS = 60 * 60
_REQUEST_LOG: dict[str, deque[float]] = defaultdict(deque)


def rate_limited(client_ip: str) -> bool:
    """True if client_ip has hit the generation limit for the current window."""
    now = time.monotonic()
    log = _REQUEST_LOG[client_ip]
    while log and now - log[0] > RATE_LIMIT_WINDOW_SECONDS:
        log.popleft()
    if len(log) >= RATE_LIMIT_MAX_REQUESTS:
        return True
    log.append(now)
    return False


def page(message: str = "", links: list[tuple[str, str]] | None = None) -> str:
    buttons = "".join(f'<p><a class="download" href="{html.escape(url)}">{html.escape(label)}</a></p>' for label, url in (links or []))
    return f"""<!doctype html><html lang='ro'><meta charset='utf-8'><title>Teste biblice</title>
    <style>body{{font:16px Calibri,Arial,sans-serif;background:#f5f5f5;margin:0}}main{{max-width:760px;margin:38px auto;background:white;padding:32px;border-radius:12px;box-shadow:0 4px 22px #bbb}}label{{display:block;font-weight:bold;margin-top:14px}}input,textarea,select{{box-sizing:border-box;width:100%;padding:9px;margin-top:4px;font:inherit}}textarea{{height:76px}}button,.download{{display:inline-block;background:#202020;color:white;border:0;border-radius:5px;padding:11px 16px;margin-top:22px;text-decoration:none;cursor:pointer}}.ok{{color:#147a35;font-weight:bold}}</style>
    <main><h1>Generator teste biblice</h1><p>Folosește numai corpusul local verificabil și păstrează I-IV.</p>{message}
    <form method='post' action='/generate'><label>Material biblic</label><textarea name='chapters'>1 Samuel 1,2</textarea>
    <label>Categorie</label><input name='category' value=''><label>Ediție</label><input name='edition' value='2027'>
    <label>Etapă</label><input name='stage' value=''><label>Data</label><input name='date' value=''>
    <label>Dificultate</label><select name='difficulty'><option>mixed</option><option>easy</option><option>medium</option><option>hard</option></select>
    <label>Număr variante</label><input type='number' min='1' max='10' name='versions' value=''><label>Seed (opțional)</label><input name='seed' value='12345'>
    <button>GENEREAZĂ TESTUL</button></form>{buttons}</main></html>"""


def make_tests(data: dict[str, str]) -> list[tuple[str, str]]:
    selection = parse_selection(data["chapters"])
    repo = REPOSITORY
    base_seed = int(data["seed"]) if data.get("seed", "").strip() else secrets.randbelow(2**31)
    versions = max(1, min(10, int(data.get("versions", "").strip() or "1")))
    contest = {"title": "TALANTUL ÎN NEGOȚ", "category": data.get("category", "6_7"), "edition": int(data.get("edition", "2027")), "stage": data.get("stage", "Faza pe biserică"), "date": data.get("date", "")}
    scoring = {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}
    session_id = secrets.token_urlsafe(9)
    links = []
    for version in range(1, versions + 1):
        test = build_test(repo.facts_for(selection), selection, contest, scoring, base_seed, version)
        validate_test(test)
        validate_evidence(test, repo)
        folder = OUTPUT / session_id / f"V{version}"
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "test.json").write_text(json.dumps(test.to_dict() | {"coverage": coverage_report(test), "translation": repo.translation, "difficulty": data.get("difficulty", "mixed")}, ensure_ascii=False, indent=2), encoding="utf-8")
        candidate, answer_key = render_pair(test, folder)
        links.extend([(f"Descarcă Test Concurenți V{version}", f"/download/{candidate.relative_to(OUTPUT).as_posix()}"), (f"Descarcă Barem Corectori V{version}", f"/download/{answer_key.relative_to(OUTPUT).as_posix()}")])
    return links


class Handler(BaseHTTPRequestHandler):
    def _html(self, body: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        content = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            return self._html(page())
        if parsed.path.startswith("/download/"):
            file = (OUTPUT / unquote(parsed.path.removeprefix("/download/"))).resolve()
            if OUTPUT not in file.parents or not file.is_file():
                return self.send_error(HTTPStatus.NOT_FOUND)
            data = file.read_bytes()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Disposition", f'attachment; filename="{file.name}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            return self.wfile.write(data)
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path != "/generate":
            return self.send_error(HTTPStatus.NOT_FOUND)
        client_ip = self.headers.get("X-Forwarded-For", self.client_address[0]).split(",")[0].strip()
        if rate_limited(client_ip):
            message = f"<p style='color:#b00'><strong>Prea multe cereri.</strong> Poți genera din nou peste puțin timp (limită: {RATE_LIMIT_MAX_REQUESTS}/oră per utilizator).</p>"
            return self._html(page(message), HTTPStatus.TOO_MANY_REQUESTS)
        length = int(self.headers.get("Content-Length", 0))
        values = {key: value[-1] for key, value in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
        try:
            links = make_tests(values)
            self._html(page("<p class='ok'>✓ Test generat și validat.</p>", links))
        except Exception as exc:
            self._html(page(f"<p style='color:#b00'><strong>Generarea a eșuat:</strong> {html.escape(str(exc))}</p>"), HTTPStatus.BAD_REQUEST)


def main() -> None:
    port = int(os.environ.get("PORT", 8000))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(f"Interfața este disponibilă pe portul {port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
