from __future__ import annotations

import re
from collections import defaultdict


class SelectionError(ValueError):
    pass


BOOK_ALIASES = {
    "1 samuel": "1 Samuel", "1samuel": "1 Samuel", "1 sam": "1 Samuel", "1sam": "1 Samuel",
    "rut": "Rut", "ioan": "Ioan",
}


def canonical_book(value: str) -> str:
    key = re.sub(r"\s+", " ", value.strip().lower())
    try:
        return BOOK_ALIASES[key]
    except KeyError as exc:
        raise SelectionError(f"Carte necunoscută sau ambiguă: {value!r}") from exc


def _chapters(value: str) -> list[int]:
    result: list[int] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            raise SelectionError("Capitol gol în selecție.")
        match = re.fullmatch(r"(\d+)(?:\s*-\s*(\d+))?", part)
        if not match:
            raise SelectionError(f"Interval de capitole invalid: {part!r}")
        start, end = int(match.group(1)), int(match.group(2) or match.group(1))
        if start < 1 or end < start:
            raise SelectionError(f"Interval de capitole invalid: {part!r}")
        result.extend(range(start, end + 1))
    return sorted(set(result))


def parse_selection(value: str) -> dict[str, list[int]]:
    """Parses one book per line: `1 Samuel 1,3-5` or `Rut 1`."""
    result: dict[str, list[int]] = defaultdict(list)
    for raw in (line.strip() for line in value.replace(";", "\n").splitlines()):
        if not raw:
            continue
        match = re.fullmatch(r"(.+?)\s+(\d+(?:\s*[-,]\s*\d+)*)", raw)
        if not match:
            raise SelectionError(f"Selecție invalidă: {raw!r}")
        book = canonical_book(match.group(1))
        result[book].extend(_chapters(match.group(2)))
    if not result:
        raise SelectionError("Introduceți cel puțin o carte și un capitol.")
    return {book: sorted(set(chapters)) for book, chapters in result.items()}
