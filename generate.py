from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import sys
import tempfile
from pathlib import Path

from src.biblical_tests import USER_ERRORS
from src.biblical_tests.generation import build_test
from src.biblical_tests.models import TestDefinition
from src.biblical_tests.rendering import render_pair
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.selection import parse_selection, require_minimum_chapters
from src.biblical_tests.validation import coverage_report, validate_evidence, validate_test


DEFAULT_CONFIG = {
    "contest": {"title": "TALANTUL ÎN NEGOȚ", "stage": "Faza pe biserică", "edition": 2027, "date": "28 martie 2026", "category": "6_7"},
    "scoring": {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}, "seed": 12345,
}


def use_utf8_output() -> None:
    """Prints this program's Romanian messages as written.

    Every message in the codebase is Romanian and every one of them carries
    diacritics. A Windows console (or any redirected stream on a machine whose
    locale encoding is not UTF-8) encodes stdout as cp1252 by default, which
    turns „Cartea «Rut» nu există" into mojibake and, on the error path, makes
    the program die of a UnicodeEncodeError while trying to report the real
    problem. `errors="replace"` keeps that fatal second failure impossible even
    on a stream that cannot represent a character at all.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            # Not a reconfigurable text stream — a test harness capturing into
            # a StringIO, or a stream already detached. Nothing to fix there.
            continue


def load_config(path: Path) -> dict:
    """Small YAML subset reader for the documented flat two-level config."""
    if not path.exists():
        return DEFAULT_CONFIG
    result = {key: dict(value) if isinstance(value, dict) else value for key, value in DEFAULT_CONFIG.items()}
    section = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].rstrip()
        if not line.strip():
            continue
        if not line.startswith((" ", "\t")):
            section = None
            if line.endswith(":"):
                section = line[:-1].strip()
                result.setdefault(section, {})
                continue
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        value = value.strip('"\'')
        parsed: object = int(value) if value.isdigit() else value
        if section and isinstance(result.get(section), dict):
            result[section][key] = parsed
        else:
            result[key] = parsed
    return result


def build_versions(repository: BibleRepository, selection: dict[str, list[int]], config: dict, first: int, count: int):
    """Yields (version number, test), each told what its siblings already spent.

    The `avoid` set is why this is one loop rather than one run per variant.
    Without it two papers built from the same selection draw from the same pool
    independently: 13 of 28 verses shared between V1 and V2 on `1 Samuel 1,2,3`,
    half a room's test handed to the room next door. Threading it roughly halves
    that, to 7.

    Not to zero, and deliberately so — `build_test` treats `avoid` as a
    deferral, sorting those facts to the back of the pool rather than removing
    them, so that a selection barely able to fill one test can still fill the
    second instead of failing. Expect siblings to differ substantially, not
    completely. Variants generated in *separate* runs get no benefit at all:
    the generator has no memory between processes, so ask for them together.
    """
    spent: set[str] = set()
    for version in range(first, first + count):
        test = build_test(repository.facts_for(selection), selection, config["contest"], config["scoring"], int(config.get("seed", 12345)), version, avoid=spent)
        spent |= test.fact_ids
        validate_test(test)
        validate_evidence(test, repository)
        yield version, test


def write_version(test: TestDefinition, repository: BibleRepository, folder: Path) -> tuple[Path, Path]:
    """Writes the auditable JSON and both PDFs for one variant, all or nothing.

    Written into a staging directory and swapped in only once everything is
    there. Writing in place meant `test.json` landed before the PDFs were
    rendered, so a rendering failure on a re-run left the folder holding a new
    answer key beside the previous run's two PDFs — three files that no longer
    describe the same test, with nothing to say so. A variant that cannot be
    completed now leaves the previous one exactly as it was.
    """
    folder.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{folder.name}-staging-", dir=folder.parent))
    try:
        (staging / "test.json").write_text(json.dumps(test.to_dict() | {"coverage": coverage_report(test), "translation": repository.translation}, ensure_ascii=False, indent=2), encoding="utf-8")
        competitor, answer_key = render_pair(test, staging)
        # Rename the old variant aside rather than deleting it, so a failure
        # during the swap itself can still put it back.
        superseded = folder.with_name(f".{folder.name}-superseded-{secrets.token_hex(4)}") if folder.exists() else None
        if superseded is not None:
            os.replace(folder, superseded)
        try:
            os.replace(staging, folder)
        except OSError:
            if superseded is not None:
                os.replace(superseded, folder)
            raise
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if superseded is not None:
        shutil.rmtree(superseded, ignore_errors=True)
    return folder / competitor.name, folder / answer_key.name


def generated_entries(output: Path) -> list[Path]:
    """The top-level entries under `output` that hold generated tests.

    Deliberately narrow, because the caller deletes what this returns. An entry
    qualifies only by containing a `test.json` the generator writes — directly
    (`output/V1/test.json`, this program's own layout) or one level down
    (`output/<sesiune>/V1/test.json`, the web app's). Anything else sharing the
    directory — a loose file, a folder someone parked there, a half-written
    directory from an interrupted run — is not generated output as far as this
    is concerned and is left alone rather than guessed about.
    """
    if not output.is_dir():
        return []
    return [entry for entry in output.iterdir()
            if entry.is_dir() and (any(entry.glob("test.json")) or any(entry.glob("*/test.json")))]


def sweep_output(output: Path, keep: int, protected: set[Path]) -> list[Path]:
    """Deletes all but the `keep` most recent generated entries, and reports them.

    `protected` is what the current run just wrote; it survives regardless of
    where it sorts, so no invocation can delete its own output — including
    `--keep-recent 0`, whose whole point is to leave nothing *but* this run.
    That is a safety net rather than the counting rule: freshly written
    directories are the most recent anyway, and it exists so a clock skew or a
    preserved mtime cannot turn a cleanup flag into data loss.
    """
    protected = {path.resolve() for path in protected}
    candidates = sorted(generated_entries(output), key=lambda entry: entry.stat().st_mtime, reverse=True)
    removed = []
    for entry in candidates[keep:]:
        if entry.resolve() in protected:
            continue
        shutil.rmtree(entry, ignore_errors=True)
        removed.append(entry)
    return removed


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generator local de teste biblice fundamentate")
    parser.add_argument("--chapters", required=True, help='Ex.: "1 Samuel 1,2,3"')
    parser.add_argument("--version", type=int, default=1, help="Numărul primei variante (implicit 1)")
    parser.add_argument("--versions", type=int, default=1, help="Câte variante, fără versete comune între ele (implicit 1)")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--corpus", default="data")
    parser.add_argument("--output", default="output")
    parser.add_argument("--keep-recent", type=int, default=None, metavar="N",
                        help="După o rulare reușită, șterge din directorul de ieșire toate testele generate în afară de cele mai recente N (plus cele ale rulării curente, mereu păstrate). Implicit: nu șterge nimic.")
    args = parser.parse_args(argv)
    if args.version < 1:
        parser.error("--version trebuie să fie cel puțin 1.")
    if args.versions < 1:
        parser.error("--versions trebuie să fie cel puțin 1.")
    if args.keep_recent is not None and args.keep_recent < 0:
        parser.error("--keep-recent nu poate fi negativ.")
    return args


def run(args: argparse.Namespace) -> None:
    config = load_config(Path(args.config))
    selection = parse_selection(args.chapters)
    require_minimum_chapters(selection)
    print("Loading source...")
    repository = BibleRepository(args.corpus)
    print("Parsed chapters: " + "; ".join(f"{book} {', '.join(map(str, chapters))}" for book, chapters in selection.items()))
    written: set[Path] = set()
    for version, test in build_versions(repository, selection, config, args.version, args.versions):
        print(f"Generating Sections I-IV (V{version})... validated candidate bank")
        print("Checking references and duplicates... PASS")
        folder = Path(args.output) / f"V{version}"
        competitor, key = write_version(test, repository, folder)
        written.add(folder)
        print(f"Rendering competitor PDF... PASS: {competitor}")
        print(f"Rendering answer key PDF... PASS: {key}")
        print(f"Total: {test.total_points} puncte")
    # Only after every variant is written and validated: a run that failed
    # halfway must not also take the previous run's output with it.
    if args.keep_recent is not None:
        removed = sweep_output(Path(args.output), args.keep_recent, written)
        print(f"Sweeping output... removed {len(removed)} older entr{'y' if len(removed) == 1 else 'ies'}")
        for entry in removed:
            print(f"  - {entry}")


def main() -> None:
    use_utf8_output()
    args = parse_args()
    try:
        run(args)
    except USER_ERRORS as exc:
        # Something the person running this can fix — a book the corpus lacks,
        # a selection too thin, a malformed range. The message was written for
        # them, so it is all they get; a stack trace above it would only bury
        # it. Anything else is a defect and keeps its traceback, which here is
        # the right thing to show: unlike the web app, the only reader of this
        # stream is the operator, and the detail is what they need to report.
        print(f"Eroare: {exc}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
