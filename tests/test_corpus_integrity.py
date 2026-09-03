from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from src.biblical_tests.generation import _concise
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.selection import SelectionError

DATA = Path("data")


def _headings(path: Path) -> list[str]:
    """The source's section headings: the first line of every paragraph."""
    lines = path.read_text(encoding="utf-8").splitlines()[1:]
    return [line.strip() for index, line in enumerate(lines)
            if line.strip() and index and not lines[index - 1].strip()]


class CorpusStructureTests(unittest.TestCase):
    """Checks the parse against something that is not the parse.

    `validate_evidence` compares a question's evidence with `get_verse`, and
    both come out of `BibleRepository` — so it certifies that the generator
    quoted the corpus faithfully, never that the corpus says what the source
    text says. Every check here has its ground truth outside the parser: a
    hand-written verse-count table, or the raw Markdown.
    """

    @classmethod
    def setUpClass(cls):
        cls.repo = BibleRepository(DATA)
        cls.expected = json.loads((DATA / "verse-counts.json").read_text(encoding="utf-8"))["chapters"]

    def test_every_chapter_has_exactly_the_verses_it_should(self):
        for book, counts in self.expected.items():
            self.assertIn(book, self.repo.books)
            self.assertEqual(len(self.repo.books[book]), len(counts), f"{book}: wrong chapter count")
            for index, count in enumerate(counts, 1):
                found = sorted(int(number) for number in self.repo.books[book][str(index)])
                self.assertEqual(found, list(range(1, count + 1)), f"{book} {index}")

    def test_no_verse_ends_with_a_section_heading(self):
        # The old shape-based stripper left any heading containing a comma
        # („Saul, ales împărat prin sorți") glued onto the end of the verse
        # before it, which then carried a reference covering words it did not
        # contain. A heading appearing *inside* a verse is not this defect —
        # „Ionatan și David" is also an ordinary phrase — so this asserts on
        # the position the glue actually produced.
        for path in DATA.glob("*samuel-reference-text.md"):
            for heading in _headings(path):
                for book, chapters in self.repo.books.items():
                    for chapter, verses in chapters.items():
                        for number, text in verses.items():
                            self.assertFalse(text.endswith(heading), f"{book} {chapter}:{number} ends with {heading!r}")

    def test_verses_the_old_heuristic_deleted_are_present(self):
        # A verse whose text happened to contain no punctuation matched the
        # heading pattern and was dropped entirely.
        self.assertTrue(self.repo.get_verse("1 Samuel", 10, 17).startswith("Samuel a chemat poporul"))
        self.assertTrue(self.repo.get_verse("2 Samuel", 1, 17).startswith("Iată cântarea de jale"))

    def test_verse_opening_on_a_guillemet_is_its_own_verse(self):
        # 1 Samuel 24:13 opens on „«", which the marker lookahead did not
        # list, so it was absorbed into verse 12.
        self.assertIn("Răul de la cei răi vine", self.repo.get_verse("1 Samuel", 24, 13))
        self.assertNotIn("vechea zicală", self.repo.get_verse("1 Samuel", 24, 12))

    def test_unknown_book_and_chapter_are_reported_as_such(self):
        with self.assertRaises(SelectionError):
            self.repo.facts_for({"Rut": [1]})
        with self.assertRaises(SelectionError):
            self.repo.facts_for({"1 Samuel": [99]})

    def test_structure_check_rejects_a_corpus_that_lost_a_verse(self):
        # The guard itself has to fail when the parse is wrong, or it is
        # decoration. Drop a verse and confirm it is caught.
        books = {"1 Samuel": {str(chapter): dict(verses) for chapter, verses in self.repo.books["1 Samuel"].items()}}
        del books["1 Samuel"]["3"]["7"]
        with self.assertRaises(ValueError) as caught:
            self.repo._verify_structure(books, DATA)
        self.assertIn("1 Samuel 3", str(caught.exception))


class FalseStatementSwapTests(unittest.TestCase):
    """Section I builds a false statement by swapping the fact's object for a
    different name. The match has to respect word boundaries: „Domnul" is a
    substring of „Domnului", and replacing the tail of an inflected form
    („unsul Domnului" -> „unsul Dumnezeului") leaves a statement that is
    neither the verse nor a clean falsehood — the claim stays true, merely
    misspelled, and the answer key still marks it F."""

    def setUp(self):
        self.repo = BibleRepository(DATA)

    def _candidates(self):
        for fact in self.repo.facts:
            if not fact.quality:
                continue
            statement = _concise(fact, True)
            if statement:
                yield fact, statement

    def test_the_swap_point_is_never_inside_a_longer_word(self):
        for fact, statement in self._candidates():
            hits = list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", statement))
            self.assertTrue(hits, f"{fact.id}: no whole-word occurrence of {fact.object!r}")
            hit = hits[-1]
            self.assertTrue(hit.start() == 0 or not re.match(r"\w", statement[hit.start() - 1]), fact.id)
            self.assertFalse(re.match(r"\w", statement[hit.end():hit.end() + 1] or ""), fact.id)

    def test_a_naive_substring_search_would_still_get_this_wrong(self):
        # Pins the regression: if this ever finds nothing, the corpus no
        # longer exercises the case and the boundary check above has stopped
        # proving anything.
        divergent = [
            fact.id for fact, statement in self._candidates()
            if statement.rfind(fact.object) != list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", statement))[-1].start()
        ]
        self.assertTrue(divergent, "no corpus fact distinguishes rfind from a word-boundary match any more")


if __name__ == "__main__":
    unittest.main()
