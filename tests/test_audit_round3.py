from __future__ import annotations

import builtins
import copy
import json
import os
import secrets
import shutil
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

import generate
from src.biblical_tests.generation import _LINKERS, _concise, build_test
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.selection import MAX_CHAPTER, SelectionError, parse_selection
from src.biblical_tests.validation import ValidationError, validate_evidence, validate_test
from src.web import app


REPOSITORY = BibleRepository("data")
SCORING = {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}


def _build(chapters: str, seed: int = 12345, version: int = 1):
    selection = parse_selection(chapters)
    return build_test(REPOSITORY.facts_for(selection), selection, {}, SCORING, seed, version)


class ChapterRangeBoundTests(unittest.TestCase):
    """An unbounded range was expanded before anything checked that the
    chapters exist, so a request far under the body limit could ask for an
    arbitrarily large allocation."""

    def test_an_enormous_range_is_refused_before_it_is_expanded(self):
        # `range` is intercepted rather than exercised: reaching it at all is
        # the failure, and letting it run is what the fix exists to prevent.
        real_range = builtins.range

        def guard(*args):
            if len(args) == 2 and args[1] - args[0] > 10_000:
                raise AssertionError(f"expanded {args[1] - args[0]:,} chapters before validating")
            return real_range(*args)

        with mock.patch.object(builtins, "range", guard):
            for probe in ("1 Samuel 1-99999999999", "1 Samuel 1-100000000000000000000"):
                with self.subTest(probe=probe), self.assertRaises(SelectionError):
                    parse_selection(probe)

    def test_the_bound_is_the_longest_book_in_the_bible(self):
        self.assertEqual(len(parse_selection(f"1 Samuel 1-{MAX_CHAPTER}")["1 Samuel"]), MAX_CHAPTER)
        with self.assertRaises(SelectionError):
            parse_selection(f"1 Samuel 1-{MAX_CHAPTER + 1}")

    def test_real_selections_are_unaffected(self):
        self.assertEqual(parse_selection("1 Samuel 1,2,3")["1 Samuel"], [1, 2, 3])
        self.assertEqual(len(parse_selection("1 Samuel 1-31")["1 Samuel"]), 31)


class CleanupScopeTests(unittest.TestCase):
    """`output` is shared: the web app's sessions, the CLI's own variants, and
    whatever a person puts there. The sweep may only remove the first kind."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.expired = time.time() - app.OUTPUT_RETENTION_SECONDS - 3600

    def _age(self, path: Path) -> Path:
        os.utime(path, (self.expired, self.expired))
        return path

    def test_only_expired_web_sessions_are_removed(self):
        session = self.root / secrets.token_urlsafe(9) / "V1"
        session.mkdir(parents=True)
        (session / "test.json").write_text("{}", encoding="utf-8")
        self._age(session.parent)

        cli = self.root / "V1"
        cli.mkdir()
        (cli / "test.json").write_text("{}", encoding="utf-8")
        self._age(cli)

        folder = self.root / "notele mele"
        folder.mkdir()
        (folder / "plan.txt").write_text("x", encoding="utf-8")
        self._age(folder)

        loose = self.root / "raport.pdf"
        loose.write_bytes(b"%PDF-")
        self._age(loose)

        with mock.patch.object(app, "OUTPUT", self.root):
            app.cleanup_output()

        self.assertFalse(session.parent.exists(), "an expired session should be swept")
        self.assertTrue(cli.exists(), "the CLI's own output is not the web app's to delete")
        self.assertTrue((folder / "plan.txt").is_file(), "an unrelated folder must be left alone")
        self.assertTrue(loose.is_file())

    def test_a_session_shaped_name_without_generated_output_is_left_alone(self):
        impostor = self.root / secrets.token_urlsafe(9)
        impostor.mkdir()
        (impostor / "notes.txt").write_text("x", encoding="utf-8")
        self._age(impostor)
        with mock.patch.object(app, "OUTPUT", self.root):
            app.cleanup_output()
        self.assertTrue(impostor.exists())

    def test_a_fresh_session_survives(self):
        fresh = self.root / secrets.token_urlsafe(9) / "V1"
        fresh.mkdir(parents=True)
        (fresh / "test.json").write_text("{}", encoding="utf-8")
        with mock.patch.object(app, "OUTPUT", self.root):
            app.cleanup_output()
        self.assertTrue(fresh.exists())


class SectionOneAssertionTests(unittest.TestCase):
    """A true/false item has to be something that can be true or false."""

    def test_the_reported_question_is_no_longer_generated(self):
        statements = [question.statement for question in _build("1 Samuel 3-5").section_i]
        self.assertNotIn("Cine ne va izbăvi din mâna acestor dumnezei puternici?", statements)

    def test_no_selection_yields_a_bare_interrogative(self):
        for chapters in ("1 Samuel 1-3", "1 Samuel 3-5", "1 Samuel 4-7", "2 Samuel 1-4"):
            for seed in (12345, 777):
                with self.subTest(chapters=chapters, seed=seed):
                    for question in _build(chapters, seed).section_i:
                        self.assertFalse(question.statement.rstrip().endswith("?"), question.statement)

    def test_a_question_mark_inside_reported_speech_is_not_what_is_rejected(self):
        # The mark has to be the sentence's own. "X a zis: «...?»" asserts what
        # X said and is true or false in the ordinary way, so the guard keys on
        # a bare trailing "?", not on the character appearing anywhere.
        reported = 'Omul i-a zis lui Eli: „Ce s-a întâmplat, fiule?"'
        self.assertIsNotNone(__import__("re").search(r'\?[\s]*$', "Cine ne va izbăvi?"))
        self.assertIsNone(__import__("re").search(r'\?[\s]*$', reported))


class SectionThreeDanglingTests(unittest.TestCase):
    def test_the_reported_association_is_no_longer_generated(self):
        match = _build("1 Samuel 23-26").section_iii
        self.assertNotIn("a luat cuvântul și", set(match.right.values()))

    def test_no_half_ends_on_a_conjunction(self):
        for chapters in ("1 Samuel 23-26", "1 Samuel 1-3", "2 Samuel 5-8", "2 Samuel 10-13"):
            for seed in (12345, 777):
                with self.subTest(chapters=chapters, seed=seed):
                    match = _build(chapters, seed).section_iii
                    for half in list(match.left) + list(match.right.values()):
                        self.assertNotIn(half.split()[-1].lower().strip("-,"), _LINKERS, half)


class SectionFourAnswerKeyTests(unittest.TestCase):
    """The barem is checked in both directions: what it credits must be
    supported, and what the evidence supports must be credited."""

    @classmethod
    def setUpClass(cls):
        cls.test = _build("2 Samuel 5-8")
        cls.index = next(i for i, question in enumerate(cls.test.section_iv) if len(question.correct) > 1)

    def _with_correct(self, correct):
        test = copy.deepcopy(self.test)
        test.section_iv[self.index] = replace(test.section_iv[self.index], correct=list(correct))
        return test

    def test_the_generated_key_is_accepted(self):
        validate_test(self.test)
        validate_evidence(self.test, REPOSITORY)

    def test_an_empty_key_is_rejected(self):
        with self.assertRaises(ValidationError):
            validate_evidence(self._with_correct([]), REPOSITORY)

    def test_an_omitted_answer_is_rejected(self):
        original = self.test.section_iv[self.index].correct
        with self.assertRaises(ValidationError):
            validate_evidence(self._with_correct(original[:-1]), REPOSITORY)

    def test_a_question_of_another_shape_from_a_listing_verse_is_not_forced(self):
        # A fact that happens to carry a coordinated list can also be the
        # source of a differently shaped item, whose options have no reason to
        # be members of that list. Checking the list against every item built
        # from such a fact rejected good tests.
        for version in (1, 2, 7):
            with self.subTest(version=version):
                test = _build("1 Samuel 1,2,3", version=version)
                validate_test(test)
                validate_evidence(test, REPOSITORY)


class StagedWriteTests(unittest.TestCase):
    """A variant is replaced only once it is complete."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        self.selection = parse_selection("1 Samuel 1,2,3")

    def _write(self, version: int):
        _, test = next(iter(generate.build_versions(REPOSITORY, self.selection, generate.DEFAULT_CONFIG, version, 1)))
        return generate.write_version(test, REPOSITORY, self.root / "V1")

    def test_a_failed_rerun_leaves_the_previous_variant_untouched(self):
        self._write(1)
        before = {path.name: path.read_bytes() for path in (self.root / "V1").iterdir()}
        with mock.patch.object(generate, "render_pair", side_effect=RuntimeError("rendering a eșuat")):
            with self.assertRaises(RuntimeError):
                self._write(2)
        after = {path.name: path.read_bytes() for path in (self.root / "V1").iterdir()}
        self.assertEqual(before, after)

    def test_no_staging_directory_is_left_behind(self):
        self._write(1)
        with mock.patch.object(generate, "render_pair", side_effect=RuntimeError("rendering a eșuat")):
            with self.assertRaises(RuntimeError):
                self._write(2)
        self.assertEqual([path.name for path in self.root.iterdir()], ["V1"])

    def test_a_successful_rerun_replaces_the_variant(self):
        self._write(1)
        first = json.loads((self.root / "V1" / "test.json").read_text(encoding="utf-8"))
        competitor, answer_key = self._write(2)
        second = json.loads((self.root / "V1" / "test.json").read_text(encoding="utf-8"))
        self.assertNotEqual(first["section_i"][0]["statement"], second["section_i"][0]["statement"])
        self.assertTrue(competitor.is_file() and answer_key.is_file())
        self.assertEqual([path.name for path in self.root.iterdir()], ["V1"])


if __name__ == "__main__":
    unittest.main()
