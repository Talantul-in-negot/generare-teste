from __future__ import annotations

import argparse
import json
from pathlib import Path

from src.biblical_tests.generation import build_test
from src.biblical_tests.rendering import render_pair
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.selection import parse_selection
from src.biblical_tests.validation import coverage_report, validate_evidence, validate_test


DEFAULT_CONFIG = {
    "contest": {"title": "TALANTUL ÎN NEGOȚ", "stage": "Faza pe biserică", "edition": 2026, "date": "28 martie 2026", "category": "6_7"},
    "scoring": {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}, "seed": 12345,
}


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
        if not line.startswith((" ", "\t")) and line.endswith(":"):
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Generator local de teste biblice fundamentate")
    parser.add_argument("--chapters", required=True, help='Ex.: "1 Samuel 1,2"')
    parser.add_argument("--version", type=int, default=1)
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--corpus", default="data/bible/bible.json")
    parser.add_argument("--output", default="output")
    args = parser.parse_args()
    config = load_config(Path(args.config))
    selection = parse_selection(args.chapters)
    print("Loading source...")
    repo = BibleRepository(args.corpus)
    print("Parsed chapters: " + "; ".join(f"{book} {', '.join(map(str, chapters))}" for book, chapters in selection.items()))
    test = build_test(repo.facts_for(selection), selection, config["contest"], config["scoring"], int(config.get("seed", 12345)), args.version)
    print("Generating Sections I-IV... validated candidate bank")
    validate_test(test)
    validate_evidence(test, repo)
    print("Checking references and duplicates... PASS")
    folder = Path(args.output) / f"V{args.version}"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "test.json").write_text(json.dumps(test.to_dict() | {"coverage": coverage_report(test), "translation": repo.translation}, ensure_ascii=False, indent=2), encoding="utf-8")
    competitor, key = render_pair(test, folder)
    print(f"Rendering competitor PDF... PASS: {competitor}")
    print(f"Rendering answer key PDF... PASS: {key}")
    print(f"Total: {test.total_points} puncte")


if __name__ == "__main__":
    main()
