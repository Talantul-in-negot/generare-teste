from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path

import generate
from src.biblical_tests.generation import build_test
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.selection import MIN_SELECTION_CHAPTERS, SelectionError, parse_selection


PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = BibleRepository(PROJECT_ROOT / "data")


def _independent_overlap(selection: dict[str, list[int]]) -> int:
    """Facts two variants share when neither is told what the other spent.

    This is the old CLI behaviour, kept as the baseline the threaded `avoid`
    set has to beat — an absolute overlap number would only pin today's corpus
    in place, while the comparison stays meaningful as the corpus grows.
    """
    config = generate.DEFAULT_CONFIG
    facts = REPOSITORY.facts_for(selection)
    versions = [set(build_test(facts, selection, config["contest"], config["scoring"], int(config["seed"]), version).fact_ids) for version in (1, 2)]
    return len(versions[0] & versions[1])


class VariantOverlapTests(unittest.TestCase):
    """Sibling variants must be told what their siblings already spent.

    The web app threaded an `avoid` set between its variants; the CLI never
    did, so `--version 1` and `--version 2` drew from the same pool
    independently and shared 13 of 28 facts on `1 Samuel 1,2,3` — half of one
    room's paper handed to the room beside it.

    The property asserted is *reduction*, not disjointness. `build_test` defers
    a sibling's facts to the back of the pool rather than excluding them, so a
    selection barely able to fill one test can still fill the second; on this
    corpus that takes the same selection from 13 shared facts to 7.
    """

    def test_sibling_variants_overlap_less_than_independent_ones(self):
        for chapters in ("1 Samuel 1,2,3", "2 Samuel 1,2,3", "1 Samuel 4,5,6,7"):
            with self.subTest(chapters=chapters):
                selection = parse_selection(chapters)
                built = list(generate.build_versions(REPOSITORY, selection, generate.DEFAULT_CONFIG, 1, 2))
                self.assertEqual([version for version, _ in built], [1, 2])
                first, second = (set(test.fact_ids) for _, test in built)
                self.assertEqual(len(first), 28)
                self.assertLess(len(first & second), _independent_overlap(selection))

    def test_the_avoid_set_accumulates_across_every_earlier_variant(self):
        # Carrying only the previous variant's facts would leave V3 free to
        # rebuild V1. Recorded at the call, because the effect on the third
        # paper is a preference the generator is allowed to overrule.
        seen: list[set[str]] = []
        real_build_test = generate.build_test

        def recording_build_test(*args, avoid=None, **kwargs):
            seen.append(set(avoid or ()))
            return real_build_test(*args, avoid=avoid, **kwargs)

        generate.build_test = recording_build_test
        try:
            spent = [set(test.fact_ids) for _, test in generate.build_versions(REPOSITORY, parse_selection("1 Samuel 1,2,3"), generate.DEFAULT_CONFIG, 1, 3)]
        finally:
            generate.build_test = real_build_test
        self.assertEqual(seen[0], set())
        self.assertEqual(seen[1], spent[0])
        self.assertEqual(seen[2], spent[0] | spent[1])

    def test_the_first_variant_number_is_honoured(self):
        built = list(generate.build_versions(REPOSITORY, parse_selection("1 Samuel 1,2,3"), generate.DEFAULT_CONFIG, 4, 2))
        self.assertEqual([version for version, _ in built], [4, 5])


class SelectionFloorTests(unittest.TestCase):
    """The CLI enforces the same floor the web form does. It used to accept a
    2-chapter selection unwarned — and the README documented one."""

    def test_two_chapters_are_rejected(self):
        with self.assertRaises(SelectionError) as caught:
            generate.run(generate.parse_args(["--chapters", "1 Samuel 1,2"]))
        self.assertIn(str(MIN_SELECTION_CHAPTERS), str(caught.exception))

    def test_the_readme_example_passes_the_floor(self):
        # Pins the documented invocation to the rule the program enforces, so
        # the two cannot drift apart again.
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn('python generate.py --chapters "1 Samuel 1,2,3"', readme)
        selection = parse_selection("1 Samuel 1,2,3")
        self.assertGreaterEqual(sum(len(chapters) for chapters in selection.values()), MIN_SELECTION_CHAPTERS)


class ArgumentTests(unittest.TestCase):
    def test_a_version_number_below_one_is_refused(self):
        for argv in (["--versions", "0"], ["--version", "0"]):
            with self.subTest(argv=argv), self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
                generate.parse_args(["--chapters", "1 Samuel 1,2,3", *argv])

    def test_defaults_are_one_variant_starting_at_one(self):
        args = generate.parse_args(["--chapters", "1 Samuel 1,2,3"])
        self.assertEqual((args.version, args.versions), (1, 1))


class ErrorReportingTests(unittest.TestCase):
    """A fixable mistake gets its message; a defect keeps its traceback.

    Both cases below used to fail on Windows before the output streams were
    reconfigured: the Romanian message went out through a cp1252 stdout, which
    mangled its diacritics and then raised UnicodeEncodeError while reporting
    the original error, so the person running it saw neither.
    """

    def _cli(self, *args: str) -> subprocess.CompletedProcess:
        environment = {key: value for key, value in os.environ.items() if key != "PYTHONIOENCODING"}
        return subprocess.run(
            [sys.executable, "generate.py", *args],
            cwd=PROJECT_ROOT, capture_output=True, text=True, encoding="utf-8",
            # The encoding a Romanian Windows console reports. Python adopts it
            # for stdout unless the program says otherwise, which is exactly
            # the condition being tested.
            env={**environment, "PYTHONIOENCODING": "cp1252"},
        )

    def test_an_absent_book_reports_its_message_not_a_traceback(self):
        result = self._cli("--chapters", "Rut 1,2,3")
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("nu există în corpusul local", result.stderr)
        self.assertIn("Rut", result.stderr)

    def test_diacritics_survive_a_stream_that_is_not_utf8(self):
        result = self._cli("--chapters", "1 Samuel 1,2")
        self.assertEqual(result.returncode, 1)
        self.assertIn("Selecția are 2 capitole", result.stderr)
        # Neither mojibake nor the escaped form the old path produced.
        self.assertNotIn(chr(92) + "u", result.stderr)
        self.assertNotIn("?", result.stderr)


if __name__ == "__main__":
    unittest.main()
