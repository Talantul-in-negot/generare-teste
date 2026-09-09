from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import pdfplumber

from src.biblical_tests.generation import GenerationError, build_test
from src.biblical_tests.rendering import render_pair
from src.biblical_tests.repository import BibleRepository
from src.biblical_tests.validation import ValidationError, validate_evidence, validate_test


# Deliberately larger than the 28 questions a test needs. At 30 facts this
# fixture had no slack at all — every verse in it had to be spent — so it
# silently doubled as an assertion that no section may ever return short of its
# quota, and broke the moment Section III was taught to leave Section II's
# candidates alone and recover afterwards. No real selection is that tight.
def _corpus(path):
    facts = []
    chapters = {"1": {}, "2": {}}
    for number in range(1, 61):
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

    def test_correct_answer_counts_are_always_valid(self):
        # `_enumeration`'s three-member case can only place a list item ahead of
        # `mid`/`tail` when a genuine word-count boundary is findable; when the
        # verb that introduces the list is glued to it with no delimiter (as in
        # 1 Samuel 1:24, "...și a luat trei tauri, o efă de făină..."), guessing
        # a fixed word count risks grabbing the verb into the "member" instead
        # of leaving it in the stem. `_enumeration` now declines that guess
        # rather than emit a grammatically broken option, so a 3-correct item
        # isn't guaranteed for every chapter range.
        #
        # Nor is a *mix* of counts: the enumeration tier now exhausts every
        # candidate at each count (not just the first) before falling back to
        # the single-answer shape, because that fallback draws from exactly
        # the same scarce pool Section II needs (`_completion_stem`/
        # `_wh_question`) while enumeration candidates never do — so on a
        # chapter range where enumeration alone can cover all 3 slots, every
        # item legitimately ends up the same count (e.g. [2, 2, 2]) rather
        # than reaching into Section II's pool just for variety.
        from src.biblical_tests.selection import parse_selection
        repo = BibleRepository(Path("data"))
        selection = parse_selection("1 Samuel 1-4")
        test = build_test(
            repo.facts_for(selection), selection,
            {"title": "T", "stage": "F", "edition": 2027, "date": "x", "category": "6_7"},
            {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}, 777, 1,
        )
        counts = sorted(len(q.correct) for q in test.section_iv)
        self.assertTrue(all(1 <= count <= 3 for count in counts), counts)

    def test_section_ii_does_not_repeat_one_answer_across_the_section(self):
        # The corpus leans hard on a few subjects (the deity terms above all),
        # so without a cap a run of verses about the same one turned into a run
        # of questions with the same answer — guessable without reading them.
        from collections import Counter
        from src.biblical_tests.generation import _MAX_SAME_ANSWER
        from src.biblical_tests.selection import parse_selection
        repo = BibleRepository(Path("data"))
        selection = parse_selection("1 Samuel 5,6,7")
        test = build_test(
            repo.facts_for(selection), selection,
            {"title": "T", "stage": "F", "edition": 2027, "date": "x", "category": "6_7"},
            {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}, 12345, 1,
        )
        counts = Counter(question.options[question.correct] for question in test.section_ii)
        self.assertLessEqual(max(counts.values()), _MAX_SAME_ANSWER, counts)
        stems = [question.question for question in test.section_ii]
        self.assertEqual(len(stems), len(set(stems)), stems)

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

    def test_validator_rejects_reused_section_iv_fact(self):
        first = self.test_definition.section_iv[0]
        self.test_definition.section_iv[1] = replace(
            self.test_definition.section_iv[1],
            fact_id=first.fact_id,
            fact_ids=list(first.fact_ids),
            evidence=first.evidence,
            supporting_evidence=list(first.supporting_evidence),
        )
        with self.assertRaises(ValidationError):
            validate_test(self.test_definition)

    def test_evidence_validation_rejects_unsupported_answer(self):
        question = self.test_definition.section_ii[0]
        question.options[question.correct] = "răspuns inventat"
        with self.assertRaises(ValidationError):
            validate_evidence(self.test_definition, self.repo)

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
        from src.biblical_tests.generation import _BLANK
        for question in self.test_definition.section_iv:
            # A colon made these look exactly like Section II's single-answer
            # completions, so nothing distinguished an item that may have one,
            # two, three or no correct options. The reference marks the gap with
            # a blank instead, and offers short parallel completions rather than
            # whole statements.
            self.assertIn(_BLANK, question.question, question.question)
            self.assertFalse(question.question.endswith(":"), question.question)
            self.assertNotIn("sunt menționate", question.question)
            for value in question.options.values():
                self.assertLess(len(value), 60, value)
            # Every option marked correct must be supported by the cited verse.
            self.assertTrue(question.correct, f"{question.id} nu are niciun răspuns corect")
            for letter in question.correct:
                self.assertIn(question.options[letter], question.evidence.text)

    def test_section_ii_keeps_context_on_both_sides_of_the_blank(self):
        from src.biblical_tests.generation import _BLANK
        for question in self.test_definition.section_ii:
            if _BLANK not in question.question:
                continue  # a „Cine ...?" item, which carries its own context
            # A stem that opens on the blank has no left context at all, and one
            # that simply stops at it has thrown away the words that said what
            # the missing term relates to — the defect the colon shape had.
            self.assertFalse(question.question.startswith(_BLANK), question.question)
            self.assertNotIn(":" + _BLANK, question.question.replace(" ", ""))

    def test_no_two_questions_repeat_the_same_stem(self):
        stems = [q.question for q in self.test_definition.section_ii] + [q.question for q in self.test_definition.section_iv]
        self.assertEqual(len(stems), len(set(stems)), stems)

    def test_section_i_answer_pattern_is_not_fixed_alternation(self):
        # A strict odd-True/even-False pattern let a student answer half the
        # section from position alone, without reading a statement.
        pattern = [q.answer for q in self.test_definition.section_i]
        self.assertEqual(pattern.count("A"), 5)
        self.assertEqual(pattern.count("F"), 5)
        self.assertNotEqual(pattern, list("AFAFAFAFAF"))

    def test_no_question_depends_on_context_outside_itself(self):
        from src.biblical_tests.generation import _self_contained
        texts = (
            [q.statement for q in self.test_definition.section_i]
            + [q.question for q in self.test_definition.section_ii]
            + [q.question for q in self.test_definition.section_iv]
        )
        for text in texts:
            self.assertTrue(_self_contained(text), text)

    def test_object_clitics_need_the_patient_spelled_out(self):
        # An „o"/„îl"/„l-a"/„i-a" is a grammatical slot, not a name: it points
        # at whoever the narrative was last about. „Cine i-a istorisit tot?"
        # cannot be answered — told whom? — while the same shape with the
        # object doubled is complete, which is what separates the two here.
        from src.biblical_tests.generation import _self_contained
        for text in (
            "Cine i-a istorisit tot?",
            "L-au căutat, dar nu l-au găsit.",
            "Penina o înțepa la fel.",
            "L-a dus în Casa Domnului, la Silo.",
        ):
            self.assertFalse(_self_contained(text), text)
        for text in (
            "Cine l-a chemat pe Samuel?",
            "Samuel i-a zis bucătarului: „Adu porția!”",
            "David le-a trimis soli oamenilor din Iabesul Galaadului.",
            "Care este cuvântul pe care ți l-a vorbit Domnul?",
            # The article „o", not the clitic — the word after it is a noun.
            "Ana a luat o efă de făină și un burduf cu vin.",
        ):
            self.assertTrue(_self_contained(text), text)

    def test_demonstratives_need_the_noun_they_point_at_identified(self):
        from src.biblical_tests.generation import _self_contained
        # Which child? Which vision? The verse never says, so the statement is
        # not gradeable on its own.
        self.assertFalse(_self_contained("Pentru copilul acesta mă rugam."))
        self.assertFalse(_self_contained("Samuel s-a temut să istorisească vedenia aceea."))
        # Identified elsewhere in the same verse, which is why the check reads
        # the whole fact and not just the sentence it extracted.
        self.assertTrue(_self_contained(
            "Pentru copilul acesta mă rugam.",
            "Copilul Samuel creștea. Pentru copilul acesta mă rugam.",
        ))
        # A time word carries no subject matter, and the word beside a
        # demonstrative is not always a noun at all.
        self.assertTrue(_self_contained("Domnul a tunat în ziua aceea cu mare vuiet."))
        self.assertTrue(_self_contained("Și Eli a zis: „Domnul este acesta!”"))

    def test_wh_questions_have_only_one_defensible_answer(self):
        # 1 Samuel 3 has both Eli and the Lord call Samuel in nearly the same
        # words, so „Cine l-a chemat pe Samuel?" is right either way and the
        # answer key only picks one because of which verse it was built from.
        from src.biblical_tests.generation import _rival_predicates, _uniquely_answered
        from src.biblical_tests.selection import parse_selection

        repo = BibleRepository("data")
        selection = parse_selection("1 Samuel 3,4")
        facts = [fact for fact in repo.facts_for(selection) if fact.quality]
        rivals = _rival_predicates(facts)
        self.assertFalse(_uniquely_answered("l-a chemat pe Samuel", "Domnul", rivals))
        self.assertTrue(_uniquely_answered("era în vârstă de nouăzeci și opt de ani", "Eli", rivals))

    def test_wh_questions_include_the_quoted_speech_they_ask_about(self):
        # Truncating "s-a rugat și a zis" right at the colon that introduces
        # the quotation left nothing for the student to reason from — the
        # quote is the whole point of the question.
        for question in self.test_definition.section_ii:
            if not question.question.startswith("Cine "):
                continue
            if question.question.rstrip("?").rstrip().endswith(("a zis", "a răspuns", "a strigat")):
                self.fail(f"{question.id} promises reported speech but quotes none: {question.question!r}")

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


class _RealCorpusTest:
    """Shared fixture: the real corpus, and one call that builds a test from it."""

    CONTEST = {"title": "T", "stage": "F", "edition": 2027, "date": "x", "category": "6_7"}
    SCORING = {"section_1": 2, "section_2": 4, "section_3": 2, "section_4": 5}

    @classmethod
    def setUpClass(cls):
        cls.repo = BibleRepository(Path("data"))

    def _build(self, chapters, version=1, seed=12345):
        from src.biblical_tests.selection import parse_selection
        selection = parse_selection(chapters)
        facts = self.repo.facts_for(selection)
        return facts, build_test(facts, selection, self.CONTEST, self.SCORING, seed, version)


class SemanticSoundnessTests(_RealCorpusTest, unittest.TestCase):
    """Whether an item is *answerable*, which the structural checks never see.

    Every defect below shipped past a green suite: the validator confirms ten
    questions, a bijection, in-scope references and evidence matching the
    corpus exactly, and none of that notices a False statement that is true, a
    question with two correct answers, or a stem that is not a question.
    """

    def test_a_false_statement_never_swaps_one_divine_title_for_another(self):
        # "Domnul", "Dumnezeu" and "Dumnezeul" are one being under three
        # spellings and share no common prefix, so the `_inflection` guard let
        # them through: "Dumnezeul saraceste si El imbogateste" (1 Samuel 2:7)
        # was keyed F while saying exactly what the verse says.
        from src.biblical_tests.generation import _DEITY, _same_referent, _wrong_object
        facts, _ = self._build("1 Samuel 1-4")
        quality = [fact for fact in facts if fact.quality]
        divine = [fact for fact in quality if fact.object in _DEITY]
        self.assertTrue(divine, "selection should contain at least one fact about the Lord")
        for fact in divine:
            replacement = _wrong_object(fact, quality, statement=fact.statement)
            self.assertFalse(
                _same_referent(replacement, fact.object),
                f"{fact.id}: {fact.object!r} -> {replacement!r} leaves the claim true",
            )

    def test_a_false_statement_never_reuses_a_name_the_sentence_already_has(self):
        # "Saul i-a zis lui Saul" and "David, fata lui Saul, il iubea pe David"
        # are not plausible falsehoods, they are visibly broken sentences - the
        # student answers F without reading. Sections II and IV already refuse a
        # distractor the quoted verse offers; this is the same rule for the one
        # shape that rewrites the verse instead of quoting it.
        from src.biblical_tests.generation import _mentions, _wrong_object
        facts, _ = self._build("1 Samuel 17-20")
        quality = [fact for fact in facts if fact.quality]
        for fact in quality:
            replacement = _wrong_object(fact, quality, statement=fact.statement)
            self.assertFalse(
                _mentions(fact.statement, replacement),
                f"{fact.id}: swapped in {replacement!r}, which the sentence already names",
            )

    def test_three_options_always_name_three_different_answers(self):
        # "Cine saraceste si El imbogateste?" offered A Domnul and C Dumnezeul:
        # two correct answers, one of them keyed wrong. The same collision
        # between two *distractors* hands the student a two-way guess instead.
        from src.biblical_tests.generation import _same_referent
        for chapters in ("1 Samuel 1-4", "1 Samuel 15-18", "2 Samuel 5-8", "2 Samuel 21-23"):
            _, test = self._build(chapters)
            for question in test.section_ii + test.section_iv:
                values = list(question.options.values())
                for index, one in enumerate(values):
                    for other in values[index + 1:]:
                        self.assertFalse(
                            _same_referent(one, other),
                            f"{chapters} {question.id}: {one!r} and {other!r} name the same answer",
                        )

    def test_section_iv_applies_the_same_ambiguity_guard_as_section_ii(self):
        # 1 Samuel 3 has both Eli and the Lord call Samuel in nearly the same
        # words. `_uniquely_answered` exists for exactly that, and Section II
        # consulted it - Section IV's fallback did not, and shipped "Cine l-a
        # chemat din nou pe Samuel?" with both of them among the options.
        from src.biblical_tests.generation import _rival_predicates, _uniquely_answered
        for chapters in ("1 Samuel 3-4", "1 Samuel 19-20", "1 Samuel 2-3"):
            facts, test = self._build(chapters)
            rivals = _rival_predicates([fact for fact in facts if fact.quality])
            objects = {fact.id: fact.object for fact in facts}
            for question in test.section_ii + test.section_iv:
                if not question.question.startswith("Cine "):
                    continue
                predicate = question.question[len("Cine "):].rstrip("?")
                self.assertTrue(
                    _uniquely_answered(predicate, objects[question.fact_id], rivals),
                    f"{chapters} {question.id}: {question.question!r} has more than one answer",
                )

    def test_a_wh_predicate_never_opens_on_a_bare_linker_or_subordinator(self):
        # "Saul si oamenii lui erau..." names a compound subject, so the clause
        # after the name starts on "si" and "Cine" lands in front of it. The
        # results were not questions at all: "Cine si fratele sau?", "Cine ce te
        # lasa inima?", "Cine sa pun mana pe unsul Domnului?".
        from src.biblical_tests.generation import _LINKERS, _SUBORDINATE
        for chapters in ("1 Samuel 13-16", "1 Samuel 23-27", "2 Samuel 2-5", "2 Samuel 15-19"):
            _, test = self._build(chapters)
            for question in test.section_ii + test.section_iv:
                if not question.question.startswith("Cine "):
                    continue
                opener = question.question[len("Cine "):].split()[0].lower()
                self.assertNotIn(opener, _LINKERS | _SUBORDINATE, f"{chapters} {question.id}: {question.question!r}")

    def test_the_reflexive_clitic_is_not_mistaken_for_the_conjunction(self):
        # The check above must test the bare word, not `_parallel_member`'s
        # pre-hyphen stem: Romanian glues the reflexive clitic on with a hyphen,
        # so the opener of "Saul si-a ales trei mii de barbati" is not the
        # conjunction, and that question reads perfectly well.
        from src.biblical_tests.generation import _name_predicate
        from src.biblical_tests.models import Evidence, Fact
        evidence = Evidence("1 Samuel", 13, 2, 2, "x")
        good = Fact("t1", "Saul și-a ales trei mii de bărbați din Israel.", "s", "p", "Saul", evidence)
        bad = Fact("t2", "Saul și oamenii lui erau în fundul peșterii.", "s", "p", "Saul", evidence)
        self.assertEqual(_name_predicate(good), "și-a ales trei mii de bărbați din Israel")
        self.assertIsNone(_name_predicate(bad))

    def test_quote_balance_follows_the_corpus_own_convention(self):
        # The corpus opens with the low quote and closes with a plain ASCII " -
        # the right double quote never appears in it. Counting only " let a
        # fragment carrying the opener but not the closer through; comparing the
        # two curly marks rejected every verse containing reported speech.
        from src.biblical_tests.generation import _quotes_balanced
        opener, curly_closer = "„", "”"
        guill_open, guill_close = "«", "»"
        self.assertTrue(_quotes_balanced(f'A zis: {opener}Iata-ma!"'))
        self.assertFalse(_quotes_balanced(f"A zis: {opener}Iata-ma!"))
        self.assertFalse(_quotes_balanced('" Elcana a raspuns.'))
        self.assertTrue(_quotes_balanced("Preotul Eli sedea pe un scaun."))
        self.assertTrue(_quotes_balanced(f'A zis: {opener}Domnul a spus {guill_open}Du-te!{guill_close} astazi."'))
        self.assertFalse(_quotes_balanced(f'A zis: {opener}Domnul a spus {guill_open}Du-te! astazi."'))
        self.assertTrue(_quotes_balanced(f"A zis: {opener}Iata-ma!{curly_closer}"))
        # A corpus quoting with plain ASCII " on both sides has no opener to
        # count against; there an even number of them is the whole test.
        self.assertTrue(_quotes_balanced('He said "go" today.'))
        self.assertFalse(_quotes_balanced('He said "go today.'))

    def test_no_section_i_statement_is_printed_with_an_unclosed_quotation(self):
        from src.biblical_tests.generation import _quotes_balanced
        for chapters in ("1 Samuel 1-4", "1 Samuel 9-12", "2 Samuel 1-4", "2 Samuel 11-14"):
            for version in (1, 2, 3):
                _, test = self._build(chapters, version=version)
                for question in test.section_i:
                    self.assertTrue(
                        _quotes_balanced(question.statement),
                        f"{chapters} v{version} {question.id}: {question.statement!r}",
                    )

    def test_one_answer_cannot_dominate_section_ii(self):
        # Balancing the letters A/B/C is not the same property as balancing what
        # those letters say: 2 Samuel 11-12 answered "David" nine times out of
        # ten, spread evenly across the three letters - 36 of Section II's 40
        # points to a student who writes one name down the page without reading.
        from collections import Counter
        from src.biblical_tests.generation import _MAX_SAME_ANSWER_RELAXED
        for chapters in ("2 Samuel 10-13", "1 Samuel 26-29"):
            for version in (1, 2, 3):
                _, test = self._build(chapters, version=version)
                counts = Counter(question.options[question.correct] for question in test.section_ii)
                self.assertLessEqual(max(counts.values()), _MAX_SAME_ANSWER_RELAXED, f"{chapters} v{version}: {counts}")

    def test_the_validator_rejects_a_dominated_section_ii(self):
        from src.biblical_tests.generation import _MAX_SAME_ANSWER_RELAXED
        _, test = self._build("1 Samuel 1-4")
        dominant = test.section_ii[0]
        test.section_ii = [
            replace(question, options=dict(dominant.options), correct=dominant.correct)
            for question in test.section_ii
        ]
        self.assertGreater(len(test.section_ii), _MAX_SAME_ANSWER_RELAXED)
        with self.assertRaises(ValidationError):
            validate_test(test)

    def test_seed_and_version_are_separate_axes(self):
        # `seed + version` made (seed=100, version=2) the same draw as
        # (seed=101, version=1), so an off-by-one in the seed silently reissued
        # a paper already handed out.
        _, first = self._build("1 Samuel 1-4", version=2, seed=100)
        _, second = self._build("1 Samuel 1-4", version=1, seed=101)
        self.assertNotEqual(
            [question.question for question in first.section_ii],
            [question.question for question in second.section_ii],
        )
        # The same (seed, version) must still reproduce exactly.
        _, again = self._build("1 Samuel 1-4", version=2, seed=100)
        self.assertEqual(
            [question.question for question in first.section_ii],
            [question.question for question in again.section_ii],
        )


class AllocationAndAgreementTests(_RealCorpusTest, unittest.TestCase):
    """The three defects the first remediation pass named but left standing:
    gender agreement, sibling-variant overlap, and the pool contention that
    made a third of two-chapter selections fail outright.
    """

    def test_a_swap_never_breaks_gender_agreement_when_it_need_not(self):
        # "o iubea pe Ana" -> "o iubea pe Elcana" leaves a feminine clitic
        # beside a masculine name: broken rather than false. `_wrong_object`
        # had a same-gender tier but a final tier that ignored it, and Section I
        # reached that tier because it reserved verses without ever checking one
        # could be falsified cleanly.
        from src.biblical_tests.generation import _gender, _wrong_object
        facts, _ = self._build("1 Samuel 1-4")
        quality = [fact for fact in facts if fact.quality]
        kept = 0
        for fact in quality:
            try:
                replacement = _wrong_object(fact, quality, statement=fact.statement, require_gender=True)
            except GenerationError:
                continue
            kept += 1
            self.assertEqual(_gender(replacement), _gender(fact.object), f"{fact.id}: {fact.object!r} -> {replacement!r}")
        self.assertGreater(kept, 0)

    def test_section_i_only_reserves_a_verse_it_can_actually_falsify(self):
        # `_falsifiable` reproduces the emit path exactly, so a verse it accepts
        # cannot fail at emit time and a verse with no same-gender stand-in is
        # reported rather than refused - it sorts last and is reached only when
        # the clean ones cannot fill the five.
        from src.biblical_tests.generation import _concise, _falsifiable
        facts, _ = self._build("1 Samuel 1-4")
        quality = [fact for fact in facts if fact.quality]
        checked = 0
        for fact in quality:
            found = _falsifiable(fact, quality)
            if found is None:
                continue
            statement, keeps_gender = found
            checked += 1
            self.assertEqual(statement, _concise(fact, True))
            self.assertIsInstance(keeps_gender, bool)
        self.assertGreater(checked, 0)

    def test_no_section_i_statement_breaks_gender_agreement(self):
        from src.biblical_tests.generation import _concise, _gender
        import re as _re
        by_id = {fact.id: fact for fact in self.repo.facts}
        for chapters in ("1 Samuel 1-4", "1 Samuel 17-20", "2 Samuel 11-14"):
            for version in (1, 2, 3):
                _, test = self._build(chapters, version=version)
                for question in test.section_i:
                    if question.answer != "F":
                        continue
                    fact = by_id[question.fact_id]
                    original = _concise(fact, True)
                    hits = list(_re.finditer(rf"(?<!\w){_re.escape(fact.object)}(?!\w)", original or ""))
                    if not hits:
                        continue
                    hit = hits[-1]
                    before, after = original[:hit.start()], original[hit.end():]
                    if not (question.statement.startswith(before) and question.statement.endswith(after)):
                        continue
                    swapped = question.statement[len(before):len(question.statement) - len(after)] if after else question.statement[len(before):]
                    self.assertEqual(
                        _gender(swapped), _gender(fact.object),
                        f"{chapters} v{version} {question.id}: {fact.object!r} -> {swapped!r}",
                    )

    def test_sibling_variants_are_told_what_the_earlier_ones_spent(self):
        # Two variants handed to neighbouring students shared a median 6 of
        # their 10 Section II questions, because a different shuffle of the same
        # small eligible set keeps reaching the same verses.
        from src.biblical_tests.selection import parse_selection
        selection = parse_selection("1 Samuel 1-6")
        facts = self.repo.facts_for(selection)
        first = build_test(facts, selection, self.CONTEST, self.SCORING, 12345, 1)
        blind = build_test(facts, selection, self.CONTEST, self.SCORING, 12345, 2)
        aware = build_test(facts, selection, self.CONTEST, self.SCORING, 12345, 2, avoid=first.fact_ids)
        overlap = lambda a, b: len({q.question for q in a.section_ii} & {q.question for q in b.section_ii})
        self.assertLess(overlap(first, aware), overlap(first, blind))
        # Deferring, never excluding: a selection that can barely fill one test
        # must still fill the second.
        self.assertEqual(len(aware.section_ii), 10)

    def test_avoiding_everything_still_produces_a_test(self):
        # `avoid` only reorders the pool; handed every fact it must still build.
        from src.biblical_tests.selection import parse_selection
        selection = parse_selection("1 Samuel 1-4")
        facts = self.repo.facts_for(selection)
        test = build_test(facts, selection, self.CONTEST, self.SCORING, 12345, 1, avoid={fact.id for fact in facts})
        validate_test(test)
        validate_evidence(test, self.repo)

    def test_fact_ids_reports_every_verse_the_test_spent(self):
        _, test = self._build("1 Samuel 1-4")
        ids = test.fact_ids
        self.assertEqual(len(ids), 10 + 10 + 5 + len(test.section_iv))
        for question in test.section_i + test.section_ii:
            self.assertIn(question.fact_id, ids)
        self.assertTrue(set(test.section_iii.fact_ids) <= ids)

    def test_section_iii_leaves_section_ii_the_verses_it_cannot_spare(self):
        # Section III's shape is `_name_predicate`, which is also Section II's
        # "Cine ...?" shape, so the two want nearly the same verses - and III
        # picks first. These selections could not produce a test at all until
        # III was taught to defer while Section II is near its quota and to
        # recover from `_clause_halves` (and from II's leftovers) afterwards.
        for chapters, versions in (("1 Samuel 27-29", (1, 2, 3)), ("2 Samuel 10-12", (1,))):
            for version in versions:
                _, test = self._build(chapters, version=version)
                validate_test(test)
                validate_evidence(test, self.repo)
                self.assertEqual(len(test.section_ii), 10)
                self.assertEqual(len(test.section_iii.left), 5)

    def test_every_three_chapter_selection_produces_a_test(self):
        # The contention fix took three-chapter selections from 3% failing to
        # none. Two-chapter selections can still genuinely run out, which is why
        # the error names the shortfall and says to add a chapter.
        from src.biblical_tests.selection import parse_selection
        for book, last in (("1 Samuel", 31), ("2 Samuel", 24)):
            for start in range(1, last - 1):
                chapters = f"{book} {start}-{start + 2}"
                selection = parse_selection(chapters)
                test = build_test(self.repo.facts_for(selection), selection, self.CONTEST, self.SCORING, 12345, 1)
                self.assertEqual(len(test.section_ii), 10, chapters)

    def test_a_thin_selection_says_what_to_do_about_it(self):
        from src.biblical_tests.selection import parse_selection
        selection = parse_selection("1 Samuel 5-6")
        with self.assertRaises(GenerationError) as caught:
            build_test(self.repo.facts_for(selection), selection, self.CONTEST, self.SCORING, 12345, 1)
        self.assertIn("capitol", str(caught.exception))


class AnswerPlausibilityTests(_RealCorpusTest, unittest.TestCase):
    """An option has to be the same *kind* of thing as the answer it competes
    with, and a statement has to differ from the other nine on the page."""

    def test_a_place_never_stands_in_for_someone_who_acted(self):
        # "Efraim l-a chemat din nou pe Samuel" is false because Efraim is a
        # region - a student rules it out on category alone. `_swap_class`
        # sorted names by how they decline, which puts PEOPLE and PLACES in one
        # bucket; that is right about grammar and wrong about answers.
        from src.biblical_tests.generation import _compatible_kind, _entity_role
        self.assertEqual(_entity_role("Efraim"), "place")
        self.assertEqual(_entity_role("Samuel"), "agent")
        self.assertEqual(_entity_role("Domnul"), "agent")
        # Israel and Filistenii live in PLACES but act, so they group with people.
        self.assertEqual(_entity_role("Israel"), "agent")
        self.assertEqual(_entity_role("Filistenii"), "agent")
        self.assertFalse(_compatible_kind("Efraim", "Samuel"))
        self.assertTrue(_compatible_kind("Israel", "Samuel"))
        self.assertTrue(_compatible_kind("Silo", "Efraim"))
        # Permissive about anything the corpus never classified, or the rule
        # would discard far more than it protects.
        self.assertTrue(_compatible_kind("chivotul", "Samuel"))

    def test_no_section_i_swap_crosses_agent_and_place(self):
        from src.biblical_tests.generation import _compatible_kind
        for original, swapped in self._swaps():
            self.assertTrue(_compatible_kind(swapped, original), f"{original!r} -> {swapped!r}")

    def test_no_section_i_swap_breaks_number_agreement(self):
        # "Filistenii s-au asezat in linie de bataie" -> "Samuel s-au asezat"
        # leaves a plural verb beside a singular subject. `_swap_class` had
        # encoded the collective/singular split since it was written, but only
        # ever as a preference the lower tiers handed back.
        from src.biblical_tests.generation import _swap_class
        for original, swapped in self._swaps():
            self.assertEqual(_swap_class(swapped), _swap_class(original), f"{original!r} -> {swapped!r}")

    def test_no_two_section_i_statements_are_near_twins(self):
        # Section I had no near-duplicate check at all - `_StemLedger` guarded
        # Sections II and IV only - so one test could carry both "Efraim l-a
        # chemat din nou pe Samuel" and "Atunci Efraim l-a chemat pe Samuel".
        import itertools
        import re as _re
        from src.biblical_tests.generation import _STEM_OVERLAP_LIMIT
        for chapters in ("1 Samuel 1-3", "1 Samuel 2-4", "2 Samuel 5-7"):
            for version in (1, 2, 3):
                _, test = self._build(chapters, version=version)
                bags = [{w for w in _re.findall(r"\w+", q.statement.lower())} for q in test.section_i]
                for i, j in itertools.combinations(range(len(bags)), 2):
                    if not bags[i] or not bags[j]:
                        continue
                    share = len(bags[i] & bags[j]) / max(len(bags[i]), len(bags[j]))
                    self.assertLess(share, _STEM_OVERLAP_LIMIT, f"{chapters} v{version}: {test.section_i[i].statement!r} / {test.section_i[j].statement!r}")

    def test_options_within_one_item_are_the_same_kind_of_thing(self):
        from src.biblical_tests.generation import _compatible_kind
        for chapters in ("1 Samuel 1-4", "1 Samuel 13-16", "2 Samuel 5-8"):
            for version in (1, 2):
                _, test = self._build(chapters, version=version)
                for question in test.section_ii + test.section_iv:
                    values = list(question.options.values())
                    for index, one in enumerate(values):
                        for other in values[index + 1:]:
                            self.assertTrue(_compatible_kind(one, other), f"{chapters} {question.id}: {one!r} / {other!r}")

    def test_an_oblique_form_is_never_offered_as_a_distractor(self):
        # "Domnului" is the genitive/dative form, not a name, so it only fits
        # the slot it came from - offered against a subject-position blank it
        # reads as "Domnului au inceput lupta". `_wrong_object` refused it as a
        # replacement from the start; the distractor lists never did.
        for chapters in ("1 Samuel 1-4", "1 Samuel 4-7", "2 Samuel 5-8", "2 Samuel 20-23"):
            for version in (1, 2, 3):
                _, test = self._build(chapters, version=version)
                for question in test.section_ii + test.section_iv:
                    wrong = [v for letter, v in question.options.items() if letter not in question.correct]
                    self.assertNotIn("Domnului", wrong, f"{chapters} {question.id}")

    def _swaps(self):
        """Every Section I False item, as (original object, what replaced it)."""
        import re as _re
        from src.biblical_tests.generation import _concise
        by_id = {fact.id: fact for fact in self.repo.facts}
        found = []
        for chapters in ("1 Samuel 1-4", "1 Samuel 5-8", "1 Samuel 15-18", "2 Samuel 1-4", "2 Samuel 20-23"):
            for version in (1, 2, 3):
                _, test = self._build(chapters, version=version)
                for question in test.section_i:
                    if question.answer != "F":
                        continue
                    fact = by_id[question.fact_id]
                    original = _concise(fact, True)
                    hits = list(_re.finditer(rf"(?<!\w){_re.escape(fact.object)}(?!\w)", original or ""))
                    if not hits:
                        continue
                    hit = hits[-1]
                    before, after = original[:hit.start()], original[hit.end():]
                    if not (question.statement.startswith(before) and question.statement.endswith(after)):
                        continue
                    swapped = question.statement[len(before):len(question.statement) - len(after)] if after else question.statement[len(before):]
                    found.append((fact.object, swapped))
        self.assertGreater(len(found), 50)
        return found


class LeadingBlankTests(_RealCorpusTest, unittest.TestCase):
    """The blank may open the stem. It used to be refused outright, which was
    the largest single source of lost Section II candidates."""

    def _fact(self, statement, obj):
        from src.biblical_tests.models import Evidence, Fact
        return Fact("t1", statement, "s", "p", obj, Evidence("1 Samuel", 3, 15, 15, statement))

    def test_a_leading_blank_is_allowed_when_the_rest_carries_the_question(self):
        from src.biblical_tests.generation import _BLANK, _completion_stem, _rival_predicates
        fact = self._fact("Samuel a rămas culcat până dimineața, apoi a deschis ușile Casei Domnului.", "Samuel")
        self.assertIsNone(_completion_stem(fact), "without rivals the shape stays off")
        built = _completion_stem(fact, _rival_predicates([fact]))
        self.assertIsNotNone(built)
        self.assertTrue(built[0].startswith(_BLANK))

    def test_a_leading_blank_needs_more_of_the_verse_than_a_mid_sentence_one(self):
        # With nothing to the left, everything the student reasons from sits on
        # one side of the gap, so the word floor is higher.
        from src.biblical_tests.generation import _BLANK_MIN_WORDS, _LEADING_BLANK_EXTRA_WORDS, _completion_stem, _rival_predicates
        self.assertGreater(_LEADING_BLANK_EXTRA_WORDS, 0)
        short = self._fact("Samuel a rămas culcat până dimineața.", "Samuel")
        self.assertIsNone(_completion_stem(short, _rival_predicates([short])))

    def test_a_leading_blank_several_people_could_fill_is_refused(self):
        # The guard the wh shape already gets: without it "__________ a zis:
        # «...»" can be as true of one person as of another.
        from src.biblical_tests.generation import _completion_stem, _rival_predicates
        selection_facts = [
            self._fact("Domnul l-a chemat pe Samuel și i-a zis vorbele acelea.", "Domnul"),
            self._fact("Eli l-a chemat pe Samuel și i-a zis vorbele acelea.", "Eli"),
        ]
        rivals = _rival_predicates(selection_facts)
        self.assertIsNone(_completion_stem(selection_facts[0], rivals))

    def test_the_shape_is_kept_out_of_section_iis_reservation_set(self):
        # Counting these in `ii_eligible` measured worse than not having the
        # shape at all: they are the corpus's most formulaic stems, so Section
        # II's ledger rejects most as near-twins while the reservation had
        # already taken them from Sections III and IV.
        from src.biblical_tests.generation import _BLANK, _completion_stem
        facts, test = self._build("1 Samuel 1-4")
        for fact in facts:
            built = _completion_stem(fact)
            if built:
                self.assertFalse(built[0].startswith(_BLANK))
        self.assertEqual(len(test.section_ii), 10)


class SelectionSizeTests(_RealCorpusTest, unittest.TestCase):
    def test_a_selection_too_small_for_a_test_says_so_immediately(self):
        # A complete test spends 28 distinct verses, 23 of them carrying a
        # recognised name. The floor was 20, so selections that could not
        # arithmetically produce a test failed several steps later on whichever
        # section happened to run out first - 1 Samuel 28 has 22 quality verses
        # and reported "Sectiunea II are nevoie de 10 intrebari ... doar 2".
        from src.biblical_tests.generation import _QUALITY_ITEMS, _TOTAL_ITEMS
        from src.biblical_tests.selection import parse_selection
        self.assertEqual(_TOTAL_ITEMS, 28)
        self.assertEqual(_QUALITY_ITEMS, 23)
        for chapters in ("1 Samuel 5", "1 Samuel 28", "2 Samuel 9"):
            selection = parse_selection(chapters)
            with self.assertRaises(GenerationError) as caught:
                build_test(self.repo.facts_for(selection), selection, self.CONTEST, self.SCORING, 12345, 1)
            self.assertIn("Un test complet folosește", str(caught.exception), chapters)


class HeaderTests(_RealCorpusTest, unittest.TestCase):
    """The three zones the reference papers carry. Two of them were built as
    empty cells, so category, stage, date and the variant number were collected
    by the web form, threaded through `contest`, and printed nowhere."""

    CONTEST = {"title": "TALANTUL ÎN NEGOȚ", "stage": "Faza pe biserică", "edition": 2027,
               "date": "28 martie 2026", "category": "6_7"}

    def _pages(self, version):
        import pdfplumber
        from src.biblical_tests.rendering import render_pair
        from src.biblical_tests.selection import parse_selection
        selection = parse_selection("1 Samuel 1-3")
        test = build_test(self.repo.facts_for(selection), selection, self.CONTEST, self.SCORING, 999, version)
        with tempfile.TemporaryDirectory() as folder:
            competitor, key = render_pair(test, folder)
            out = []
            for path in (competitor, key):
                with pdfplumber.open(path) as pdf:
                    out.append((path.name, pdf.pages[0].extract_text()))
            return out

    def test_the_header_carries_category_stage_date_edition_and_variant(self):
        (_, competitor), _ = self._pages(1)
        for expected in ("Categoria 6_7", "Faza pe biserică", "28 martie 2026", "TALANTUL ÎN NEGOȚ", "Ediția 2027", "Varianta 1"):
            self.assertIn(expected, competitor)

    def test_the_answer_key_announces_itself_and_the_paper_does_not(self):
        (_, competitor), (_, key) = self._pages(1)
        self.assertIn("BAREM CORECTORI", key)
        self.assertNotIn("BAREM", competitor)

    def test_variants_are_distinguishable_on_the_page_and_in_the_filename(self):
        # Two variants of one selection produced byte-identical headers and the
        # same filename, so a room handing out V1 and V2 had only the folder to
        # tell them apart and the browser named them "... (1).pdf".
        (first_name, first_text), _ = self._pages(1)
        (second_name, second_text), _ = self._pages(2)
        self.assertNotEqual(first_name, second_name)
        self.assertIn("V1", first_name)
        self.assertIn("V2", second_name)
        self.assertIn("Varianta 1", first_text)
        self.assertIn("Varianta 2", second_text)
