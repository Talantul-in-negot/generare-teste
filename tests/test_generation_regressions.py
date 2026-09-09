import unittest
from pathlib import Path

from generate import DEFAULT_CONFIG, load_config
from src.biblical_tests.generation import _concise, _enumeration, build_test
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.selection import parse_selection


class GenerationRegressions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = BibleRepository(Path("data"))

    def test_section_i_never_uses_a_dangling_fragment(self):
        fact = next(f for f in self.repo.facts if f.id == "1 Samuel-2-13")
        self.assertNotEqual(_concise(fact, False), "Ținând în mână o furculiță cu trei coarne,")
        test = build_test(
            self.repo.facts_for(parse_selection("1 Samuel 1-3")),
            parse_selection("1 Samuel 1-3"), DEFAULT_CONFIG["contest"],
            DEFAULT_CONFIG["scoring"], 12345, 1,
        )
        self.assertNotIn("Ținând în mână", " ".join(q.statement for q in test.section_i))

    def test_clause_conjunction_is_not_misread_as_a_multi_answer_list(self):
        fact = next(f for f in self.repo.facts if f.id == "1 Samuel-18-8")
        self.assertIsNone(_enumeration(fact))

    def test_top_level_seed_is_not_nested_under_the_previous_section(self):
        path = Path("tests") / "_seed-regression.yaml"
        try:
            path.write_text("contest:\n  title: T\nscoring:\n  section_1: 2\nseed: 999\n", encoding="utf-8")
            config = load_config(path)
        finally:
            path.unlink(missing_ok=True)
        self.assertEqual(config["seed"], 999)
        self.assertNotIn("seed", config["scoring"])


if __name__ == "__main__":
    unittest.main()
