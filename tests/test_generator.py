from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import pdfplumber

from src.biblical_tests.generation import build_test
from src.biblical_tests.rendering import render_pair
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.validation import ValidationError, validate_evidence, validate_test


def _corpus(path):
    facts = []
    chapters = {"1": {}, "2": {}}
    for number in range(1, 31):
        chapter = 1 if number % 2 else 2
        verse = (number + 1) // 2
        # Alternate two shapes: odd numbers trail the object with a predicate
        # (what Section III's name-to-predicate matching needs), even numbers
        # end the sentence right on the object (what Section II/IV's
        # fill-in-the-blank completion needs — the object must close its own
        # clause, or the stem left after cutting it out reads as unfinished).
        if number % 2:
            text = f"În ziua aceea, Personajul {number} s-a suit la casa Domnului și a adus lucrul {number} înaintea preotului din cetatea {number}."
        else:
            text = f"În ziua aceea, Personajul {number} s-a suit la casa Domnului și a adus lucrul {number}."
        chapters[str(chapter)][str(verse)] = text
        facts.append({"id": f"fact-{number}", "statement": text, "subject": f"Personajul {number}", "predicate": "a făcut", "object": f"lucrul {number}", "evidence": {"book": "1 Samuel", "chapter": chapter, "verse_start": verse, "text": text}})
    path.write_text(json.dumps({"translation": "Synthetic test corpus", "books": {"1 Samuel": chapters}, "facts": facts}), encoding="utf-8")


class RealCorpusSectionIVTests(unittest.TestCase):
    """The real corpus is what actually has multi-member coordinated lists;
    the synthetic fixture above is too uniform to exercise that shape."""

    def test_spans_one_two_and_three_correct_answers(self):
        from src.biblical_tests.selection import parse_selection
        repo = BibleRepository(Path("data"))
        selection = parse_selection("1 Samuel 1-4")
        test = build_test(
            repo.facts_for(selection), selection,
            {"title": "T", "stage": "F", "edition": 2027, "date": "x", "category": "6_7"},
            {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}, 777, 1,
        )
        counts = sorted(len(q.correct) for q in test.section_iv)
        self.assertEqual(counts, [1, 2, 3])

    def test_distractors_match_the_correct_answers_register(self):
        # A distractor pulled from an unrelated verse must open the same way
        # the correct options do (e.g. all "El ..." or all lowercase verb
        # forms) — otherwise grammar alone gives the right answer away.
        from src.biblical_tests.generation import _register
        from src.biblical_tests.selection import parse_selection
        repo = BibleRepository(Path("data"))
        selection = parse_selection("1 Samuel 1,2")
        test = build_test(
            repo.facts_for(selection), selection,
            {"title": "T", "stage": "F", "edition": 2027, "date": "x", "category": "6_7"},
            {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}, 12345, 1,
        )
        for question in test.section_iv:
            correct_values = [question.options[letter] for letter in question.correct]
            if not correct_values:
                continue
            registers = {_register(value) for value in correct_values}
            for letter, value in question.options.items():
                self.assertIn(_register(value), registers, f"{question.id} option {letter} breaks register: {value!r}")


class GeneratorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        file = Path(self.temp.name) / "bible.json"
        _corpus(file)
        self.repo = BibleRepository(file)
        self.test_definition = build_test(self.repo.facts_for({"1 Samuel": [1, 2]}), {"1 Samuel": [1, 2]}, {"title": "TALANTUL ÎN NEGOȚ", "stage": "Faza", "edition": 2026, "date": "azi", "category": "6_7"}, {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}, 12345, 1)

    def tearDown(self):
        self.temp.cleanup()

    def test_sections_and_scope(self):
        validate_test(self.test_definition)
        validate_evidence(self.test_definition, self.repo)
        self.assertEqual(len(self.test_definition.section_i), 10)
        self.assertEqual(len(self.test_definition.section_ii), 10)
        self.assertEqual(len(self.test_definition.section_iii.left), 5)
        self.assertEqual(len(self.test_definition.section_iii.right), 5)
        self.assertEqual(len(self.test_definition.section_iv), 3)
        refs = [q.evidence for q in self.test_definition.section_i + self.test_definition.section_ii + self.test_definition.section_iv] + self.test_definition.section_iii.evidence
        self.assertTrue(all(ref.book == "1 Samuel" and ref.chapter in {1, 2} for ref in refs))

    def test_validator_rejects_reference_outside_selection(self):
        self.test_definition.source = {"1 Samuel": [1]}
        with self.assertRaises(ValidationError):
            validate_test(self.test_definition)

    def test_paired_pdfs(self):
        competitor, answer_key = render_pair(self.test_definition, self.temp.name)
        self.assertGreater(competitor.stat().st_size, 1000)
        self.assertGreater(answer_key.stat().st_size, 1000)
        for file in (competitor, answer_key):
            with pdfplumber.open(file) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
            self.assertIn("II", text)
            self.assertIn("III", text)
            self.assertIn("IV", text)
            self.assertNotIn("V Marcați ordinea", text)

    def test_section_iv_matches_reference_completion_format(self):
        for question in self.test_definition.section_iv:
            # The reference documents end every Section IV stem with a colon and
            # offer short, parallel completions rather than whole statements.
            self.assertTrue(question.question.endswith(":"), question.question)
            self.assertNotIn("sunt menționate", question.question)
            for value in question.options.values():
                self.assertLess(len(value), 60, value)
            # Every option marked correct must be supported by the cited verse.
            self.assertTrue(question.correct, f"{question.id} nu are niciun răspuns corect")
            for letter in question.correct:
                self.assertIn(question.options[letter], question.evidence.text)

    def test_section_iii_matches_name_to_verified_predicate(self):
        match = self.test_definition.section_iii
        for index, (name, evidence) in enumerate(zip(match.left, match.evidence), 1):
            letter = match.answers[str(index)]
            predicate = match.right[letter]
            self.assertIn(name, evidence.text)
            self.assertIn(predicate, evidence.text)
            # The predicate must not repeat the name, or the match gives itself away.
            self.assertNotIn(name, predicate)

    def test_multi_choice_accepts_zero_to_three_correct_options(self):
        original = self.test_definition.section_iv[0]
        for answers in ([], ["A"], ["A", "B"], ["A", "B", "C"]):
            self.test_definition.section_iv[0] = replace(original, correct=answers)
            validate_test(self.test_definition)
