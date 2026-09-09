import copy
import unittest
from dataclasses import replace

from src.biblical_tests.generation import build_test
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.validation import ValidationError, validate_evidence, validate_test


class ValidationRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = BibleRepository("data")
        cls.selection = {"1 Samuel": [1, 2, 3, 4]}
        cls.original = build_test(cls.repo.facts_for(cls.selection), cls.selection, {},
                                  {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}, 777, 1)

    def setUp(self):
        self.test = copy.deepcopy(self.original)

    def test_generated_answers_remain_valid(self):
        validate_test(self.test)
        validate_evidence(self.test, self.repo)

    def test_flipped_true_and_false_keys_are_rejected(self):
        for answer in ("A", "F"):
            with self.subTest(answer=answer):
                test = copy.deepcopy(self.original)
                index = next(i for i, q in enumerate(test.section_i) if q.answer == answer)
                test.section_i[index] = replace(test.section_i[index], answer="F" if answer == "A" else "A")
                with self.assertRaises(ValidationError):
                    validate_evidence(test, self.repo)

    def test_unrelated_false_statement_is_rejected(self):
        index = next(i for i, q in enumerate(self.test.section_i) if q.answer == "F")
        self.test.section_i[index] = replace(self.test.section_i[index], statement="Această afirmație nu este o substituție verificată.")
        with self.assertRaises(ValidationError):
            validate_evidence(self.test, self.repo)

    def test_json_statement_cannot_certify_itself(self):
        index = next(i for i, q in enumerate(self.test.section_i) if q.answer == "A")
        question = self.test.section_i[index]
        invented = "Samuel a construit o corabie foarte mare în pustiu."
        repo = copy.copy(self.repo)
        repo.facts = [replace(fact, statement=invented) if fact.id == question.fact_id else fact for fact in repo.facts]
        self.test.section_i[index] = replace(question, statement=invented)
        with self.assertRaises(ValidationError):
            validate_evidence(self.test, repo)

    def test_missing_matching_evidence_or_ids_is_rejected(self):
        for field in ("evidence", "fact_ids"):
            with self.subTest(field=field):
                test = copy.deepcopy(self.original)
                test.section_iii = replace(test.section_iii, **{field: []})
                with self.assertRaises(ValidationError):
                    validate_test(test)


if __name__ == "__main__":
    unittest.main()
