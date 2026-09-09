from __future__ import annotations

import re
from collections import defaultdict


class SelectionError(ValueError):
    pass


BOOK_ALIASES = {
    "1 samuel": "1 Samuel", "1samuel": "1 Samuel", "1 sam": "1 Samuel", "1sam": "1 Samuel",
    "2 samuel": "2 Samuel", "2samuel": "2 Samuel", "2 sam": "2 Samuel", "2sam": "2 Samuel",
    "rut": "Rut", "ioan": "Ioan",
}


def canonical_book(value: str) -> str:
    key = re.sub(r"\s+", " ", value.strip().lower())
    try:
        return BOOK_ALIASES[key]
    except KeyError as exc:
        raise SelectionError(f"Carte necunoscută sau ambiguă: {value!r}") from exc


# No book of the Bible has more chapters than Psalms. A number above this is
# not a chapter anyone can mean, so it is refused rather than expanded.
#
# The bound has to be applied *before* `range()`, not after: `1-99999999999`
# is thirteen characters that ask for a hundred billion integers, and a
# selection is parsed before anything checks whether those chapters exist. The
# web app's 64 KB body limit does not help — the request is tiny; it is the
# expansion that is enormous. Bounding the endpoints bounds the expansion.
MAX_CHAPTER = 150


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
        if end > MAX_CHAPTER:
            raise SelectionError(
                f"Capitol inexistent: {end}. Nicio carte biblică nu are mai mult de {MAX_CHAPTER} de capitole."
            )
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


# A complete test spends 28 *distinct* verses across four sections that all
# draw from the same pool, several of them competing directly for the same
# shapes. Measured across every contiguous chapter window in both books:
# 2-chapter selections fail to produce a test about 23% of the time
# (arithmetic scarcity, not a bug — see generation.py's Section II/III
# shortfall messages), 3-chapter and up never do. Checking it here turns that
# into an immediate, specific message instead of a generation attempt that
# fails several steps in. It is a practical floor calibrated from that data,
# not a guarantee for every possible combination — a selection can still be
# too sparse (e.g. three widely scattered chapters) and hit the accurate
# downstream message instead.
#
# It lives beside the parser rather than in either entrypoint because both of
# them need it and they must not disagree about it: the web form enforced this
# floor while the CLI let the same selection through unwarned, and the README
# then documented a 2-chapter CLI example the form would have rejected.
MIN_SELECTION_CHAPTERS = 3


def require_minimum_chapters(selection: dict[str, list[int]]) -> None:
    """Rejects a selection too thin to build a complete test from."""
    total_chapters = sum(len(chapters) for chapters in selection.values())
    if total_chapters < MIN_SELECTION_CHAPTERS:
        raise SelectionError(
            f"Selecția are {total_chapters} capitol{'e' if total_chapters != 1 else ''}; "
            f"sunt necesare cel puțin {MIN_SELECTION_CHAPTERS} pentru un test complet."
        )
