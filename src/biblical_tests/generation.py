from __future__ import annotations

import random
import re
from collections import defaultdict

from .models import Fact, MatchingQuestion, MultiChoiceQuestion, SingleChoiceQuestion, TestDefinition, TrueFalseQuestion
from .repository import BibleRepository

# Objects that „Cine?" can ask about — a place or a thing needs a different
# question word, so those are left to the colon-completion shape.
_PERSONAL = BibleRepository.PEOPLE | BibleRepository.DEITY


class GenerationError(ValueError):
    pass


# Shapes taken from the reference answer keys in data/:
#   I   short affirmations, one clause long;
#   II  a stem that breaks off at a colon, three short completions, one correct;
#   III two halves of the same short clause, matched across five rows;
#   IV  a stem plus parallel phrases, of which one, two or three may be correct.
_SENTENCES = re.compile(r"(?<=[.!?])\s+")
_CONJUNCTION = re.compile(r"\s+(?:și|sau)\s+")
_TRIM = " ,;:-–—„”\"'!?."
_DEITY = {"Domnul", "Domnului", "Dumnezeu", "Dumnezeul"}
_STEM_MIN_CHARS = 25
_STEM_MAX_CHARS = 150


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCES.split(text) if part.strip()]


def _mentions(text: str, value: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text))


def _inflection(value: str, answers: list[str]) -> bool:
    """True for forms like „Domnul" against „Domnului", which are not real choices."""
    for answer in answers:
        short, long = sorted((value.lower(), answer.lower()), key=len)
        if len(short) >= 4 and long.startswith(short):
            return True
    return False


def _balanced_letters(count: int, rng: random.Random) -> list[str]:
    letters = (["A", "B", "C"] * ((count + 2) // 3))[:count]
    rng.shuffle(letters)
    return letters


def _round_robin(facts: list[Fact], count: int, rng: random.Random) -> list[Fact]:
    by_chapter: dict[tuple[str, int], list[Fact]] = defaultdict(list)
    for fact in facts:
        by_chapter[(fact.evidence.book, fact.evidence.chapter)].append(fact)
    for group in by_chapter.values():
        rng.shuffle(group)
    keys = list(by_chapter)
    rng.shuffle(keys)
    selected = []
    while len(selected) < count:
        progressed = False
        for key in keys:
            if by_chapter[key] and len(selected) < count:
                selected.append(by_chapter[key].pop())
                progressed = True
        if not progressed:
            break
    return selected


# Feminine names in the corpus (repository.PEOPLE); everything else — the
# other people, places, and deity terms — takes masculine agreement.
_FEMININE_NAMES = {"Ana", "Penina", "Mical", "Batșeba"}


def _gender(name: str) -> str:
    return "f" if name in _FEMININE_NAMES else "m"


def _wrong_object(fact: Fact, pool: list[Fact]) -> str:
    # Only names the selected chapters actually use, so a 2 Samuel test never
    # swaps in a character who appears nowhere in it.
    inside = {candidate.object for candidate in pool}
    # A swap that changes grammatical gender breaks whatever adjective/verb
    # agreed with the original name (e.g. "Eli era foarte bătrân" swapped to
    # "Ana era foarte bătrân" — "bătrân" needed to become "bătrână"). Picking
    # a same-gender replacement keeps the sentence grammatical without having
    # to detect and rewrite the agreeing word at all.
    # "Domnul" vs "Domnului" are the same entity in different grammatical
    # cases, not a distinct wrong answer — swapping one in for the other
    # both fails to change the claim and breaks whatever case the sentence
    # needed ("Vrăjmașii Domnului" needs the genitive, not "Vrăjmașii Domnul").
    safe = lambda value: value != fact.object and not _inflection(value, [fact.object])
    same_gender = lambda value: _gender(value) == _gender(fact.object)
    for option in fact.options:
        if safe(option) and option in inside and same_gender(option):
            return option
    for candidate in pool:
        if candidate.id != fact.id and safe(candidate.object) and same_gender(candidate.object):
            return candidate.object
    # No same-gender candidate exists in this chapter selection — fall back
    # to any distinct, non-inflected name rather than fail generation outright.
    for option in fact.options:
        if safe(option) and option in inside:
            return option
    for candidate in pool:
        if candidate.id != fact.id and safe(candidate.object):
            return candidate.object
    raise GenerationError("Nu există suficiente fapte distincte pentru un distractor sigur.")


def _concise(fact: Fact, need_object: bool) -> str | None:
    """Section I keeps one clean clause; a verse that offers none is skipped."""
    for sentence in sorted(_sentences(fact.statement), key=len):
        if not 30 <= len(sentence) <= 165 or not sentence[:1].isupper():
            continue
        if sentence.count('"') % 2 or "(" in sentence or "«" in sentence:
            continue
        if need_object and not _mentions(sentence, fact.object):
            continue
        return sentence
    return None


def _completion_stem(fact: Fact) -> tuple[str, str] | None:
    """Splits a verse into a stem ending in ':' and the segment that answers it."""
    # Whole-word match only: "Domnul" must not be cut out of "Domnului",
    # which would leave the stem with no correct completion at all.
    hits = list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", fact.statement))
    for hit in reversed(hits):
        prefix, rest = fact.statement[:hit.start()], fact.statement[hit.end():]
        # The object must close its own clause — if real words follow it before
        # the next comma/semicolon/sentence end, a stem cut off there loses the
        # rest of the clause and stops making sense (e.g. "...pentru că:" when
        # the verse continues "Domnul o făcuse stearpă"). Try an earlier
        # occurrence of the same object instead of accepting a dangling stem.
        punct = re.search(r"[,;:.!?]", rest)
        clause_tail = rest[:punct.start()] if punct else rest
        if clause_tail.strip(_TRIM):
            continue
        sentences = list(re.finditer(r"[.!?]\s+", prefix))
        if sentences:
            prefix = prefix[sentences[-1].end():]
        stem = prefix.strip().rstrip(_TRIM)
        if not _STEM_MIN_CHARS <= len(stem) <= _STEM_MAX_CHARS:
            continue
        return stem + ":", fact.object + rest
    return None


# Prepositions/genitive markers that put the following name in an oblique
# role (possessor, direct/indirect object, prepositional complement) instead
# of the sentence's subject.
_OBLIQUE_MARKERS = {"lui", "pe", "cu", "din", "la", "în", "de", "pentru", "despre", "asupra", "către", "printre", "peste", "sub", "fără", "ca"}


def _safe_to_swap(sentence: str, obj: str) -> bool:
    """True only if a bare-name swap of `obj` inside `sentence` stays grammatical.

    A False statement is built by dropping a different name in place of `obj`
    verbatim — no article or case ending gets added. That only works when
    `obj` itself sits in a plain, uninflected slot (typically the subject).
    "Domnului" is the deity's genitive/dative form and has no plain-form
    stand-in in the name pool, so it's never swappable. A name right after a
    preposition/genitive marker ("Vrăjmașii Domnului", "Casa lui Eli") is in
    the same oblique position — swapping in another bare name there drops the
    case marking the sentence needs, the same way "lui Eli" or "Domnului"
    would.
    """
    if obj == "Domnului":
        return False
    hit = re.search(rf"(?<!\w){re.escape(obj)}(?!\w)", sentence)
    if not hit:
        return True
    lead = sentence[:hit.start()].rstrip().lower()
    return not any(lead.endswith(f" {marker}") or lead == marker for marker in _OBLIQUE_MARKERS)


def _name_predicate(fact: Fact) -> str | None:
    """Section III pairs a name with what the verse says about it, as barem 2_3 does."""
    for sentence in _sentences(fact.statement):
        hit = re.search(rf"(?<!\w){re.escape(fact.object)}(?!\w)", sentence)
        if not hit:
            continue
        # A name right after a preposition/genitive marker is an oblique
        # object, not the sentence's subject — e.g. "Fiii lui Eli erau niște
        # oameni răi" is about his sons, not Eli, and "...acelora din Israel
        # care veneau la Silo" describes "acelora" (those people), not
        # Israel. Pairing the name with what follows would misattribute
        # a predicate that actually belongs to a different subject.
        lead = sentence[:hit.start()].rstrip().lower()
        if any(lead.endswith(f" {marker}") or lead == marker for marker in _OBLIQUE_MARKERS):
            continue
        # "Domnului" is the genitive/dative case form ("of/to the Lord") — it
        # is never itself a sentence's grammatical subject, unlike "Domnul".
        if fact.object == "Domnului":
            continue
        # Only the clause right after the name belongs to it; anything past
        # the next comma/semicolon may already be a different clause.
        tail = sentence[hit.end():]
        punct = re.search(r"[,;:.!?]", tail)
        predicate = (tail[:punct.start()] if punct else tail).strip(_TRIM)
        words = predicate.split()
        if not 2 <= len(words) <= 9:
            continue
        # If the name sits after its verb ("se suia Ana la Casa Domnului"),
        # what follows is the verb's own complement, not a fresh predicate
        # about the name — a real predicate opens with a verb, not another
        # preposition continuing the earlier phrase.
        if words[0].lower() in _OBLIQUE_MARKERS:
            continue
        # The name must not reappear, or the association gives itself away.
        if len(predicate) < 10 or _mentions(predicate, fact.object):
            continue
        return predicate
    return None


# A phrase opening with one of these is a dependent fragment ("ce plângi",
# "care veneau"): it only reads as an option beside other fragments of the
# same kind, never beside a full clause such as "El smerește".
_SUBORDINATE = {"ce", "cum", "cine", "unde", "când", "care", "cui", "dacă", "că", "să", "ca"}
_LINKERS = {"și", "sau", "dar", "iar"}


def _wh_question(fact: Fact) -> tuple[str, str] | None:
    """Section II's other reference shape: „Cine a zis ...?" answered by a name.

    The colon-completion shape (`_completion_stem`) only fits verses whose answer
    word happens to close its own clause, which is a minority of them. The
    reference tests mix that shape with plain wh-questions — "Cine a zis despre
    Isus: «Eu nu găsesc nicio vină în El»?" — which impose no such constraint,
    so most verses naming a person can carry one.
    """
    # Only people answer „Cine?"; a place would need „Unde?"/„În ce localitate?"
    # and a thing „Ce?", so those objects are left to the completion shape.
    if fact.object not in _PERSONAL:
        return None
    # `_name_predicate` already verifies the name is the clause's subject rather
    # than a possessor or prepositional object, which is exactly the condition
    # for „Cine <predicate>?" to be asking about the right person.
    predicate = _name_predicate(fact)
    if not predicate:
        return None
    return f"Cine {predicate}?", predicate


def _clean_member(value: str) -> bool:
    if any(mark in value for mark in ('.', ',', ';', ':', '"', '„', '”', '«', '»', '!', '?')):
        return False
    first_word = value.split()[0].lower() if value.split() else ""
    return first_word not in _LINKERS


def _register(value: str) -> str:
    """Groups options by the shape of their opening word, so they stay parallel."""
    words = value.split()
    if not words:
        return "empty"
    head = words[0]
    if head.lower() in _SUBORDINATE:
        return "subordinate"
    if head[:1].isupper():
        return "capitalised"
    return "plain"


# Common openers of a Romanian finite/compound verb form — "a luat", "au
# zis", "s-a suit" — versus a noun phrase, which opens with an article,
# adjective, or noun instead.
_VERB_OPENERS = {"a", "au", "am", "ai", "este", "sunt", "era", "erau", "va", "vor", "s-a", "l-a", "le-a", "i-a", "ne-a", "v-a", "s-au", "le-au"}


def _enumeration(fact: Fact) -> tuple[str, list[str]] | None:
    """Finds the coordinated list behind the reference's multi-answer items."""
    for sentence in _sentences(fact.statement):
        links = list(_CONJUNCTION.finditer(sentence))
        if not links:
            continue
        link = links[-1]
        tail = sentence[link.end():].strip(_TRIM)
        head_words = sentence[:link.start()].split()
        size = len(tail.split())
        if not _clean_member(tail) or not 2 <= size <= 8 or len(head_words) < size + 4:
            continue
        mid = " ".join(head_words[-size:]).strip(_TRIM)
        if not _clean_member(mid) or not 2 <= len(mid.split()) <= 8:
            continue
        members = [mid, tail]
        head = " ".join(head_words[:-size])
        # "a, b si c": a comma right before the second member marks a third one.
        # There's no punctuation between the verb that introduces the list and
        # this first member ("...și a luat trei tauri, o efă de făină..."), so
        # its word count can't be read off the same way `mid`/`tail` were —
        # guessing `size` words back can just as easily grab the verb itself
        # ("a luat trei tauri" instead of "trei tauri"). Reject that guess
        # outright when it starts with a common finite-verb/auxiliary opener;
        # a real noun-phrase member never does, and leaving the ambiguous
        # word(s) in the stem instead is always grammatically safe.
        if head.rstrip().endswith(","):
            earlier = head.rstrip().rstrip(",").split()
            if len(earlier) >= size + 4:
                first = " ".join(earlier[-size:]).strip(_TRIM)
                opener = first.split()[0].lower() if first.split() else ""
                if opener in _VERB_OPENERS:
                    pass
                elif _clean_member(first) and 2 <= len(first.split()) <= 8 and len(first) >= 6:
                    members.insert(0, first)
                    head = " ".join(earlier[:-size])
        stem = head.strip(_TRIM)
        if not _STEM_MIN_CHARS <= len(stem) <= _STEM_MAX_CHARS:
            continue
        if len(mid) < 6 or len(tail) < 6:
            continue
        if len({member.lower() for member in members}) != len(members):
            continue
        return stem + ":", members
    return None


def _section_iv(pool: list[Fact], facts: list[Fact], used: set[str], rng: random.Random) -> list[MultiChoiceQuestion]:
    """Mirrors the reference mix: items with three, two and one correct answer."""
    candidates = [(fact, *found) for fact in pool if (found := _enumeration(fact))]
    foreign = [member for _, _, members in candidates for member in members]
    multis: list[MultiChoiceQuestion] = []

    def add(fact: Fact, stem: str, values: list[str], correct_values: list[str]) -> bool:
        if len({value.lower() for value in values}) != 3:
            return False
        rng.shuffle(values)
        options = dict(zip("ABC", values))
        correct = [letter for letter, value in options.items() if value in correct_values]
        if len(correct) != len(correct_values):
            return False
        multis.append(MultiChoiceQuestion(f"IV-{len(multis) + 1}", stem, options, correct, fact.evidence, fact.id, [fact.evidence], [fact.id]))
        used.add(fact.id)
        return True

    # Three, then two, then one correct answer, matching how the reference varies.
    for wanted in (3, 2, 1):
        for fact, stem, members in candidates:
            if fact.id in used or len(members) < wanted:
                continue
            correct_values = members[:wanted]
            # A distractor has to match the correct answers in register as well as
            # in length, or grammar alone gives it away.
            registers = {_register(value) for value in correct_values}
            outside = [
                value for value in foreign
                if not _mentions(fact.statement, value) and value not in members
                and _register(value) in registers
            ]
            target = sum(len(value.split()) for value in correct_values) / len(correct_values)
            outside.sort(key=lambda value: abs(len(value.split()) - target))
            picks = []
            for value in outside:
                if len(picks) == 3 - wanted:
                    break
                if value.lower() not in {item.lower() for item in picks}:
                    picks.append(value)
            if len(picks) != 3 - wanted:
                continue
            if add(fact, stem, [*correct_values, *picks], correct_values):
                break
    # A verse that merely names a person still makes a sound single-answer item.
    # Same fallback order as Section II: the colon-completion shape needs the
    # answer to close its own clause, so a „Cine ...?" question covers most of
    # what it can't.
    for fact in pool:
        if len(multis) == 3:
            break
        if fact.id in used or not (built := _completion_stem(fact) or _wh_question(fact)):
            continue
        stem, segment = built
        distractors = [f.object for f in facts if f.object != fact.object and not _mentions(segment, f.object) and not _inflection(f.object, [fact.object])]
        if len(distractors) < 2:
            continue
        add(fact, stem, [fact.object, *distractors[:2]], [fact.object])
    if len(multis) != 3:
        raise GenerationError("Nu s-au putut construi trei intrebari verificabile pentru Sectiunea IV.")
    return multis


def _section_iii(pool: list[Fact], used: set[str], rng: random.Random) -> MatchingQuestion:
    rows: list[tuple[Fact, str, str]] = []
    seen: list[str] = []
    # Named characters read best; divine forms are a fallback because "Domnul",
    # "Domnului" and "Dumnezeu" would otherwise fill the column with one name.
    for allow_deity in (False, True):
        for fact in pool:
            if len(rows) == 5:
                break
            if fact.id in used or fact.object in seen or not (predicate := _name_predicate(fact)):
                continue
            if not allow_deity and fact.object in _DEITY:
                continue
            if _inflection(fact.object, seen) or any(predicate == other[2] for other in rows):
                continue
            rows.append((fact, fact.object, predicate))
            seen.append(fact.object)
            used.add(fact.id)
    if len(rows) != 5:
        raise GenerationError("Nu sunt suficiente asocieri distincte pentru Sectiunea III.")
    shuffled = list(rows)
    rng.shuffle(shuffled)
    right_column = {letter: item[2] for letter, item in zip("ABCDE", shuffled)}
    answers = {
        str(index): next(letter for letter, item in zip("ABCDE", shuffled) if item[0].id == fact.id)
        for index, (fact, _, _) in enumerate(rows, 1)
    }
    return MatchingQuestion(
        "III-1", [name for _, name, _ in rows], right_column, answers,
        [fact.evidence for fact, _, _ in rows], [fact.id for fact, _, _ in rows],
    )


def _section_ii(pool: list[Fact], facts: list[Fact], used: set[str], rng: random.Random) -> list[SingleChoiceQuestion]:
    singles: list[SingleChoiceQuestion] = []
    letters = _balanced_letters(10, rng)
    for fact in pool:
        if len(singles) == 10:
            break
        # Either reference shape works here: a stem broken off at a colon, or a
        # „Cine ...?" question. The colon shape is tried first because it needs
        # the answer word to close its clause and so fits far fewer verses.
        if fact.id in used or not (built := _completion_stem(fact) or _wh_question(fact)):
            continue
        stem, segment = built
        # A distractor present in the answer segment could also complete the stem,
        # so only terms the verse does not offer at all are safe to mark wrong.
        safe = lambda value: value != fact.object and not _mentions(segment, value) and not _inflection(value, [fact.object])
        choices = [value for value in fact.options if safe(value)]
        choices += [f.object for f in facts if safe(f.object) and f.object not in choices]
        values = [fact.object, *choices[:2]]
        if len(set(values)) != 3:
            continue
        letter = letters[len(singles)]
        rng.shuffle(values)
        values.remove(fact.object)
        values.insert("ABC".index(letter), fact.object)
        singles.append(SingleChoiceQuestion(f"II-{len(singles) + 1}", stem, dict(zip("ABC", values)), letter, fact.evidence, fact.id))
        used.add(fact.id)
    if len(singles) != 10:
        raise GenerationError("Nu sunt suficiente versete potrivite pentru Secțiunea II.")
    return singles


def build_test(facts: list[Fact], source: dict[str, list[int]], contest: dict, scoring: dict[str, int], seed: int, version: int) -> TestDefinition:
    facts = [fact for fact in facts if fact.quality]
    if len(facts) < 20:
        raise GenerationError("Corpusul selectat necesită cel puțin 20 de facts verificate pentru un test complet.")
    rng = random.Random(seed + version - 1)
    pool = _round_robin(facts, len(facts), rng)
    used: set[str] = set()

    def take(count: int) -> list[Fact]:
        choices = [f for f in pool if f.id not in used]
        if len(choices) < count:
            raise GenerationError("Nu sunt suficiente facts distincte pentru a evita duplicarea între secțiuni.")
        result = choices[:count]
        used.update(f.id for f in result)
        return result

    # The sections are built from the most constrained verse shape to the least:
    # enumerations are scarce, split clauses less so, and a plain affirmation
    # can be made from almost any verse.
    multis = _section_iv(pool, facts, used, rng)
    matching = _section_iii(pool, used, rng)
    singles = _section_ii(pool, facts, used, rng)

    # Only a False statement has a name swapped into it, so only false_pool is
    # constrained by _safe_to_swap; a True statement is quoted verbatim and any
    # concise verse will do. Applying the swap filter to both would discard
    # perfectly good True candidates for a rewrite they never undergo.
    false_pool = [fact for fact in pool if fact.id not in used and (stmt := _concise(fact, True)) and _safe_to_swap(stmt, fact.object)]
    true_pool = [fact for fact in pool if fact.id not in used and _concise(fact, False)]
    # The pools overlap, and false_pool is the scarcer of the two. Spending a
    # shared verse on a True item can therefore starve the False branch while
    # True-only verses sit unused, so the True branch takes the verses
    # false_pool also wants last.
    false_ids = {fact.id for fact in false_pool}
    tf: list[TrueFalseQuestion] = []
    for index in range(1, 11):
        is_true = index % 2 == 1
        source_pool = true_pool if is_true else false_pool
        available = [item for item in source_pool if item.id not in used]
        if is_true:
            available.sort(key=lambda item: item.id in false_ids)
        fact = next(iter(available), None)
        if fact is None:
            # Falling back across branches must respect the same constraint: a
            # verse with no swappable name cannot carry a False statement.
            spare = true_pool if is_true else false_pool
            other = false_pool if is_true else true_pool
            fact = next((item for item in spare + other if item.id not in used and (is_true or item.id in false_ids)), None)
        if fact is None:
            raise GenerationError("Nu sunt suficiente versete potrivite pentru Sectiunea I.")
        used.add(fact.id)
        statement = _concise(fact, not is_true) or _concise(fact, False)
        if not is_true:
            before, _, after = statement.rpartition(fact.object)
            statement = before + _wrong_object(fact, facts) + after
        tf.append(TrueFalseQuestion(f"I-{index}", statement, "A" if is_true else "F", fact.evidence, fact.id))

    return TestDefinition(source, seed, version, contest, scoring, section_i=tf, section_ii=singles, section_iii=matching, section_iv=multis)
