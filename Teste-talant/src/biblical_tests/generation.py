from __future__ import annotations

import random
import re
from collections import defaultdict

from .models import Fact, MatchingQuestion, MultiChoiceQuestion, SingleChoiceQuestion, TestDefinition, TrueFalseQuestion
from .repository import BibleRepository

# Objects that „Cine?" can ask about — a place or a thing needs a different
# question word, so those are left to the colon-completion shape. "Israel"
# and "Filistenii" are geographic/national terms in PLACES, but they act as
# collective people-group subjects ("Filistenii au adus înapoi chivotul" —
# "Cine au adus înapoi chivotul?" reads naturally), so they're added here too.
_PERSONAL = BibleRepository.PEOPLE | BibleRepository.DEITY | {"Israel", "Filistenii"}


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
# The blank shape quotes a whole sentence rather than a prefix of one, so it
# needs the same headroom `_concise` gives Section I's full-sentence quotes.
_BLANK_MAX_CHARS = 175
_BLANK = "__________"
# Real words a blanked stem must keep, so the gap sits inside a recognisable
# verse rather than a fragment that could be almost anything.
_BLANK_MIN_WORDS = 6
# Two stems sharing this fraction of their words are the same question wearing
# different verse ids — 1 Samuel repeats formulaic clauses often enough that
# exact-match deduping alone lets visible near-twins through.
_STEM_OVERLAP_LIMIT = 0.7
# No more than this many of Section II's ten questions may share one answer.
_MAX_SAME_ANSWER = 3


def _sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCES.split(text) if part.strip()]


def _dedup(values) -> list[str]:
    """First-seen values, case-insensitively deduped — a small corpus repeats
    the same few names/deity terms across many verses, so building a
    distractor list straight from `facts` without this can hand back the
    same term twice (`add()`'s own dedup check then rejects every candidate,
    which reads as "not enough distractors" when there actually were plenty,
    just not distinct ones)."""
    seen: set[str] = set()
    result = []
    for value in values:
        key = value.lower()
        if key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _mentions(text: str, value: str) -> bool:
    return bool(re.search(rf"(?<!\w){re.escape(value)}(?!\w)", text))


def _inflection(value: str, answers: list[str]) -> bool:
    """True for forms like „Domnul" against „Domnului", which are not real choices."""
    for answer in answers:
        short, long = sorted((value.lower(), answer.lower()), key=len)
        if len(short) >= 4 and long.startswith(short):
            return True
    return False


class _StemLedger:
    """Remembers every stem already issued, so two verses that phrase the same
    thing don't both become questions.

    Deduping on `fact.id` — all the sections used to do — misses this
    entirely: 1 Samuel repeats formulaic clauses ("chivotul Domnului", "fiii
    lui Israel") across many distinct verses, so two different facts can
    render as the same question and still look unique by id. Comparing the
    stems themselves is what actually catches it, and comparing them by word
    overlap rather than exact text also catches the near-twins that differ by
    a connective or two.
    """

    def __init__(self) -> None:
        self._seen: list[set[str]] = []
        # Near-duplicate rejection is a quality preference, so the sections
        # turn it off for their relaxed second pass; outright identical stems
        # stay rejected either way, since that is a defect at any corpus size.
        self.strict = True

    def claim(self, stem: str) -> bool:
        """Records `stem` and returns True, or returns False if it repeats one."""
        # The blank itself is a word character run, and every blanked stem
        # carries it — counting it would inflate every pair's overlap alike.
        words = {word for word in re.findall(r"\w+", stem.lower()) if word.strip("_")}
        if not words:
            return False
        limit = _STEM_OVERLAP_LIMIT if self.strict else 1.0
        if any(len(words & earlier) / max(len(words), len(earlier)) >= limit for earlier in self._seen):
            return False
        self._seen.append(words)
        return True


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


# "Israel"/"Filistenii" are plural/collective nouns living in PLACES, not the
# singular place names beside them — swapping one in where a singular clitic
# expects it ("l-au luat pe Dagon" -> "l-au luat pe Filistenii") mismatches
# in number, the same way DEITY terms mismatch in declension (see
# `_swap_class`).
_COLLECTIVE = {"Israel", "Filistenii"}


def _swap_class(name: str) -> str:
    """Deity and collective/plural terms decline or agree irregularly —
    "Domnul" self-inflects to "Domnului" rather than taking "lui" the way an
    ordinary indeclinable name does, and "Filistenii"/"Israel" are plural
    where the clitic pronoun/marker beside an ordinary name usually assumes
    singular. Restricting a swap to the same class keeps whatever marker or
    clitic the sentence already has correct, without needing to detect and
    rewrite it."""
    if name in _DEITY:
        return "deity"
    if name in _COLLECTIVE:
        return "collective"
    return "ordinary"


# "Domnul" and "Filistenii" each carry their own built-in definite-article
# ending ("-ul", "-ii") — unlike an ordinary indeclinable name, they clash
# when dropped in right after "lui" or a demonstrative, which already
# supplies definiteness of its own ("lui Domnul", "acestui Domnul sfânt",
# "lui Filistenii" all double up on it).
_ARTICLED = {"Domnul", "Filistenii"}
_DEFINITE_LEAD = {"lui", "acest", "acesta", "această", "aceasta", "acestui", "acestei", "aceste", "acești", "acestor"}


def _wrong_object(fact: Fact, pool: list[Fact], lead: str = "") -> str:
    # Only names the selected chapters actually use, so a 2 Samuel test never
    # swaps in a character who appears nowhere in it.
    inside = {candidate.object for candidate in pool}
    # A swap that changes grammatical gender breaks whatever adjective/verb
    # agreed with the original name (e.g. "Eli era foarte bătrân" swapped to
    # "Ana era foarte bătrân" — "bătrân" needed to become "bătrână"). Picking
    # a same-gender, same-class replacement keeps the sentence grammatical
    # without having to detect and rewrite the agreeing word at all.
    # "Domnul" vs "Domnului" are the same entity in different grammatical
    # cases, not a distinct wrong answer — swapping one in for the other
    # both fails to change the claim and breaks whatever case the sentence
    # needed ("Vrăjmașii Domnului" needs the genitive, not "Vrăjmașii Domnul").
    lead_word = lead.rstrip().split()[-1].lower() if lead.split() else ""
    double_definite = lead_word in _DEFINITE_LEAD
    # "Domnului" is never a safe drop-in either direction: same reasoning as
    # _safe_to_swap banning it as the *source* — it's a case-inflected form,
    # not a name, so it only fits back into the exact genitive/dative slot
    # it came from, which isn't guaranteed here.
    safe = lambda value: value != fact.object and value != "Domnului" and not _inflection(value, [fact.object]) and not (double_definite and value in _ARTICLED)
    same_gender = lambda value: _gender(value) == _gender(fact.object)
    same_class = lambda value: _swap_class(value) == _swap_class(fact.object)
    # Tried in order from safest to riskiest: matching both class and gender
    # first, then relaxing gender, then relaxing class, and only using a
    # mismatched fallback if this chapter selection genuinely has nothing
    # better — rather than fail generation outright.
    for match in (lambda v: same_class(v) and same_gender(v), same_class, same_gender, lambda v: True):
        for option in fact.options:
            if safe(option) and option in inside and match(option):
                return option
        for candidate in pool:
            if candidate.id != fact.id and safe(candidate.object) and match(candidate.object):
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
    """Quotes the verse with the answer blanked out where it actually stands.

    The earlier shape cut the verse off at the answer and closed the stem with
    ':'. That forced the answer to be the last thing in its clause and threw
    away everything after it, so the stem often lost the very words that said
    what it was about — "Locuitorii din Chiriat-Iearim au venit și au suit
    chivotul:" gives no clue which of three names belongs there, because the
    part of the verse that would have told you was the part that got cut.

    The reference baremuri never truncate. They quote the verse whole and
    blank the answer in place ("Cât este ziuă, trebuie să __________; vine
    noaptea, când nimeni nu mai poate să lucreze."), which keeps context on
    both sides of the gap and, as a bonus, fits the many verses whose answer
    word sits mid-clause rather than at its end.
    """
    for sentence in _sentences(fact.statement):
        # Whole-word match only: "Domnul" must not be cut out of "Domnului",
        # which would leave the stem with no correct completion at all.
        hits = list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", sentence))
        # Blanking one occurrence while an identical word stays visible
        # elsewhere in the same sentence hands the student the answer.
        if len(hits) != 1:
            continue
        hit = hits[0]
        # A quotation split across the blank reads as an unterminated fragment;
        # the same balance check `_concise` already applies to Section I.
        if sentence.count('"') % 2 or sentence.count("„") != sentence.count("”"):
            continue
        if not sentence[:1].isupper():
            continue
        stem = (sentence[:hit.start()] + _BLANK + sentence[hit.end():]).strip()
        # A blank opening the sentence has no left context at all — that is a
        # bare "who?", which the wh-question shape phrases properly instead.
        if stem.startswith(_BLANK):
            continue
        if not _STEM_MIN_CHARS <= len(stem) <= _BLANK_MAX_CHARS:
            continue
        # Character count alone lets a stem through that is long only because
        # of the blank itself: "Și s-au strâns la __________." clears 25
        # characters while naming neither who gathered nor when, so the student
        # has nothing to reason from. Counting the real words instead is what
        # actually measures how much of the verse survived around the gap.
        if len(stem.split()) - 1 < _BLANK_MIN_WORDS:
            continue
        return stem, sentence
    return None


# Prepositions/genitive markers that put the following name in an oblique
# role (possessor, direct/indirect object, prepositional complement) instead
# of the sentence's subject.
_OBLIQUE_MARKERS = {"lui", "pe", "cu", "din", "la", "în", "de", "pentru", "despre", "asupra", "către", "printre", "peste", "sub", "fără", "ca"}


def _safe_to_swap(sentence: str, obj: str) -> bool:
    """True only if a bare-name swap of `obj` inside `sentence` stays grammatical.

    A False statement is built by dropping a different name in place of `obj`
    verbatim — no article or case ending gets added. That only works when
    `obj` itself isn't a word that carries its own case inflection.
    "Domnului" is the deity's genitive/dative *form* — the word itself
    changes, not a marker beside it — and has no plain-form stand-in in the
    name pool, so it's never swappable.

    A name that merely sits after an invariant preposition or "lui" ("Casa
    lui Eli", "pe Dagon", "la Ecron") is fine to swap: the marker word stays
    exactly as written regardless of which name follows it — unlike
    "Domnului", nothing about the marker itself needs to change. The
    remaining risk there is gender/number agreement with an earlier clitic
    ("l-au luat pe Dagon" needs the singular masculine "l-", which
    "Filistenii" — plural — would break) and declension class ("lui Domnul"
    is wrong because "Domnul" self-inflects to "Domnului" instead of taking
    "lui" the way an ordinary name does). `_wrong_object` already guards
    both by preferring a same-class, same-gender replacement first.
    """
    return obj != "Domnului"


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
        # Reference predicates are usually short, but a plain, clean single
        # clause ("au luat chivotul lui Dumnezeu ... la Asdod") stays
        # readable well past 9 words — the punctuation truncation above
        # already guarantees it's one clause, not a run-on.
        if not 2 <= len(words) <= 14:
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
    if any(mark in value for mark in ('.', ',', ';', ':', '"', '„', '”', '«', '»', '!', '?', '(', ')')):
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
_VERB_OPENERS = {"a", "au", "am", "ai", "este", "sunt", "era", "erau", "va", "vor", "s-a", "l-a", "le-a", "i-a", "ne-a", "v-a", "s-au", "le-au", "se", "s"}


# Determiners/quantifiers/numerals that need a following noun to mean
# anything — splitting right after one leaves a dangling "Cei cinci" with its
# head noun ("domnitori") stranded on the other side.
_DETERMINERS = {"cei", "cele", "niște", "toți", "toate", "fiecare", "alt", "altă", "alți", "alte", "doi", "două", "trei", "patru", "cinci", "șase", "șapte", "opt", "nouă", "zece"}


def _clause_halves(fact: Fact) -> tuple[str, str] | None:
    """Section III's other reference shape: a short clause split into two
    matched halves, as in the 8_9/10_11 baremuri ("bogăția aduce" -> "mare
    număr de prieteni"), rather than `_name_predicate`'s name -> attribute.

    `_name_predicate` only fits verses where a *recognised* name is the
    clause's subject, which is scarce on chapters that mostly use common
    nouns and pronouns as subjects ("Filistenii au adus", "El a lovit"). This
    tries every split point of a short, clean clause and keeps the one
    closest to the middle whose right half doesn't open on a dangling
    conjunction/preposition and whose left half doesn't end on one either —
    the same signals already used to keep `_enumeration`'s guesses honest.

    The reference's own examples are terse poetic verses (Psalms, Proverbs);
    1 Samuel's narrative prose runs long, comma-heavy sentences instead, so a
    whole-sentence length/cleanliness check almost never passes here. Working
    clause-by-clause (split on comma/semicolon, same as `_completion_stem`'s
    `clause_tail`) finds the same short, clean fragments those long sentences
    are actually built from.
    """
    for sentence in _sentences(fact.statement):
        for raw_clause in re.split(r"[,;]\s*", sentence):
            clause = raw_clause.strip(_TRIM)
            if not clause or not _clean_member(clause) or not 12 <= len(clause) <= 70:
                continue
            words = clause.split()
            if not 3 <= len(words) <= 10:
                continue
            midpoint = len(words) / 2
            for split in sorted(range(1, len(words)), key=lambda i: abs(i - midpoint)):
                left, right = words[:split], words[split:]
                if not 1 <= len(left) <= 8 or not 1 <= len(right) <= 8:
                    continue
                opener, closer = right[0].lower(), left[-1].lower()
                if opener in _SUBORDINATE or opener in _OBLIQUE_MARKERS or closer in _OBLIQUE_MARKERS:
                    continue
                if opener in _DETERMINERS or closer in _DETERMINERS:
                    continue
                # A word this short at the seam is almost always a clitic or
                # bound particle ("se", "să", "-l", "și") rather than content
                # — splitting there orphans it from the verb/noun it belongs
                # to ("care se" / "sculaseră..." instead of a clean pair).
                if len(opener) <= 2 or len(closer) <= 2:
                    continue
                left_text, right_text = " ".join(left), " ".join(right)
                if len(left_text) < 6 or len(right_text) < 6:
                    continue
                return left_text, right_text
    return None


# Prepositions that can open a list member. Kept separate from
# `_OBLIQUE_MARKERS`, which drives Section III's subject detection — widening
# that set to serve this check would quietly change which verses III accepts.
_PREPOSITIONS = {"împotriva", "înaintea", "asupra", "lângă", "după", "prin", "spre", "către", "peste", "sub", "fără", "din", "dintre", "de", "la", "în", "cu", "pentru", "despre", "printre", "până"}


def _parallel_member(value: str) -> bool:
    """True for a phrase that can stand on its own beside the other options.

    A coordinated „și"/„sau" joins list items ("trei tauri" / "o efă de
    făină") just as readily as it joins two whole *clauses* ("pe care au pus
    chivotul Domnului" / "care este astăzi în câmpul lui Iosua"). Splitting the
    second kind produces fragments that answer no question at all and only
    look like options because they were printed under A/B/C — which is exactly
    what makes such an item unanswerable. A real list member is a noun phrase:
    it never opens with a finite verb, nor with a relative/subordinating word
    that introduces a clause of its own.
    """
    words = value.split()
    if not words:
        return False
    head = words[0].lower()
    # Romanian glues clitic pronouns onto the opening word with a hyphen
    # ("să-I aducă", "s-a suit", "și-au luat"), so the bare word won't match
    # these sets — "să-i" isn't "să". Testing the pre-hyphen stem as well
    # catches the clause openers that would otherwise pass as noun phrases.
    heads = {head, head.split("-")[0]}
    return not (heads & _VERB_OPENERS or heads & _SUBORDINATE or heads & _LINKERS)


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
        if not _clean_member(tail) or not _parallel_member(tail) or not 2 <= size <= 8 or len(head_words) < size + 4:
            continue
        mid = " ".join(head_words[-size:]).strip(_TRIM)
        if not _clean_member(mid) or not _parallel_member(mid) or not 2 <= len(mid.split()) <= 8:
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
                elif _clean_member(first) and _parallel_member(first) and 2 <= len(first.split()) <= 8 and len(first) >= 6:
                    members.insert(0, first)
                    head = " ".join(earlier[:-size])
        stem = head.strip(_TRIM)
        if not _STEM_MIN_CHARS <= len(stem) <= _STEM_MAX_CHARS:
            continue
        if len(mid) < 6 or len(tail) < 6:
            continue
        if len({member.lower() for member in members}) != len(members):
            continue
        # Options the student is asked to judge one by one have to be
        # comparable to each other; a capitalised name sitting beside two bare
        # noun phrases is answerable on shape alone, and more importantly it
        # signals the split found two different kinds of thing rather than one
        # list.
        if len({_register(member) for member in members}) != 1:
            continue
        # A prepositional phrase and a bare clause are not alternatives for the
        # same gap — "împotriva lui Israel" beside "lupta a început" reads as two
        # unrelated things rather than two entries in one list, which is what
        # makes such an item hard to answer even when both are in the verse.
        if len({member.split()[0].lower() in _PREPOSITIONS for member in members}) != 1:
            continue
        # A head ending on a genitive marker or preposition promises a noun next
        # ("...împotriva casei lui __________"), so the blank reads as asking for
        # a name while the options are in fact the clause that followed a
        # semicolon. The stem has to end somewhere the list can actually attach.
        if head.strip(_TRIM).split()[-1].lower() in _OBLIQUE_MARKERS | _PREPOSITIONS:
            continue
        # A colon here made the item look exactly like Section II's
        # single-answer completions, so nothing on the page told the student
        # this one may have one, two, three or no correct options. The blank
        # matches the reference's own multi-answer items and reads as a gap to
        # be filled rather than a sentence that simply stops.
        return f"{stem} {_BLANK}", members
    return None


def _section_iv(pool: list[Fact], facts: list[Fact], used: set[str], rng: random.Random, stems: _StemLedger) -> list[MultiChoiceQuestion]:
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
        if not stems.claim(stem):
            return False
        multis.append(MultiChoiceQuestion(f"IV-{len(multis) + 1}", stem, options, correct, fact.evidence, fact.id, [fact.evidence], [fact.id]))
        used.add(fact.id)
        return True

    # Both the enumeration filters above and the stem ledger can, on a thin or
    # very repetitive chapter range, refuse every remaining candidate. They are
    # quality preferences rather than correctness rules, so the whole selection
    # runs twice: once holding them, once with near-duplicate rejection relaxed
    # to identical-only, which beats failing to produce a test at all.
    for enforce in (True, False):
        stems.strict = enforce
        # Three, then two, then one correct answer, matching how the reference varies.
        # Enumeration candidates never overlap with Section II's shapes (they need
        # a coordinated "și"/"sau" list, not a clause-ending or subject-led verse),
        # so exhausting every candidate at each count before falling back to the
        # single-answer shape below (which does overlap) keeps IV out of II's way
        # whenever there happen to be enough enumerations to cover all 3 alone.
        for wanted in (3, 2, 1):
            for fact, stem, members in candidates:
                if len(multis) == 3:
                    break
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
                add(fact, stem, [*correct_values, *picks], correct_values)
            if len(multis) == 3:
                break
        # A verse that merely names a person still makes a sound single-answer item.
        # Same shape pair as Section II: the verse quoted with its answer blanked
        # out, or a „Cine ...?" question for the verses that read better as one.
        for fact in pool:
            if len(multis) == 3:
                break
            if fact.id in used or not (built := _completion_stem(fact) or _wh_question(fact)):
                continue
            stem, segment = built
            distractors = _dedup(f.object for f in facts if f.object != fact.object and not _mentions(segment, f.object) and not _inflection(f.object, [fact.object]))
            if len(distractors) < 2:
                continue
            add(fact, stem, [fact.object, *distractors[:2]], [fact.object])
        if len(multis) == 3:
            break
    if len(multis) != 3:
        raise GenerationError("Nu s-au putut construi trei intrebari verificabile pentru Sectiunea IV.")
    return multis


def _section_iii_named(pool: list[Fact], used: set[str], rng: random.Random) -> list[tuple[Fact, str, str]]:
    """The scarcer of Section III's two shapes: a recognised name as the
    clause's subject, paired with its predicate. Claims facts before Section
    II runs, same as the enumeration shape in Section IV — a name needing to
    be the clause's *subject* is a tighter constraint than anything Section
    II's shapes require."""
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
    return rows


def _section_iii_fill(pool: list[Fact], used: set[str], rng: random.Random, rows: list[tuple[Fact, str, str]]) -> MatchingQuestion:
    """Tops `rows` up to 5 with the looser clause-split shape (`_clause_halves`,
    no named-subject requirement) and builds the matching question. Called
    only after Section II has already claimed what it needs — this shape is
    loose enough to otherwise compete with Section II for the same verses."""
    if len(rows) < 5:
        seen_lower = {name.lower() for _, name, _ in rows}
        for fact in pool:
            if len(rows) == 5:
                break
            if fact.id in used or not (halves := _clause_halves(fact)):
                continue
            left, right = halves
            if left.lower() in seen_lower or any(right == other[2] for other in rows):
                continue
            rows.append((fact, left, right))
            seen_lower.add(left.lower())
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


def _section_ii(pool: list[Fact], facts: list[Fact], used: set[str], rng: random.Random, stems: _StemLedger) -> list[SingleChoiceQuestion]:
    singles: list[SingleChoiceQuestion] = []
    letters = _balanced_letters(10, rng)
    answers: dict[str, int] = defaultdict(int)
    shapes: dict[str, int] = defaultdict(int)

    # Both quotas below are preferences, not correctness rules, so they get a
    # relaxed second pass: on a corpus too thin to satisfy them, ten questions
    # that repeat an answer beat a GenerationError. The stem ledger is *not*
    # relaxed — a duplicate question is a defect at any corpus size.
    for enforce in (True, False):
        stems.strict = enforce
        for fact in pool:
            if len(singles) == 10:
                break
            if fact.id in used:
                continue
            # Both reference shapes are equally valid here, and the blank shape
            # now fits nearly every verse, so trying it first unconditionally
            # would make all ten questions look alike. Offering the currently
            # under-used shape first keeps the section mixed the way the
            # reference tests are.
            builders = [("blank", _completion_stem), ("wh", _wh_question)]
            if shapes["blank"] > shapes["wh"]:
                builders.reverse()
            shape, built = "", None
            for name, builder in builders:
                if built := builder(fact):
                    shape = name
                    break
            if not built:
                continue
            # „Cine?" only ever answers with a name, and the corpus leans hard
            # on a few of them (the deity terms above all), so without a cap a
            # run of verses about the same subject turns into a run of
            # questions with the same answer — guessable without reading them.
            if enforce and answers[fact.object] >= _MAX_SAME_ANSWER:
                continue
            stem, segment = built
            # A distractor present in the quoted verse could also fill the blank,
            # so only terms the verse does not offer at all are safe to mark wrong.
            safe = lambda value: value != fact.object and not _mentions(segment, value) and not _inflection(value, [fact.object])
            choices = [value for value in fact.options if safe(value)]
            choices += [f.object for f in facts if safe(f.object) and f.object not in choices]
            values = [fact.object, *choices[:2]]
            if len(set(values)) != 3:
                continue
            if not stems.claim(stem):
                continue
            letter = letters[len(singles)]
            rng.shuffle(values)
            values.remove(fact.object)
            values.insert("ABC".index(letter), fact.object)
            singles.append(SingleChoiceQuestion(f"II-{len(singles) + 1}", stem, dict(zip("ABC", values)), letter, fact.evidence, fact.id))
            used.add(fact.id)
            answers[fact.object] += 1
            shapes[shape] += 1
        if len(singles) == 10:
            break
    if len(singles) != 10:
        raise GenerationError("Nu sunt suficiente versete potrivite pentru Secțiunea II.")
    return singles


def build_test(facts: list[Fact], source: dict[str, list[int]], contest: dict, scoring: dict[str, int], seed: int, version: int) -> TestDefinition:
    all_facts = facts
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
    # enumerations are scarce, a named clause-subject less so, Section II's
    # shapes next, and a plain affirmation can be made from almost any verse.
    # Section III's other shape (_clause_halves, no named-subject requirement)
    # is looser than Section II's, so it's deferred until after Section II has
    # claimed what it needs — otherwise it competes for the same verses.
    #
    # Sections IV and III-named can often satisfy their own quota from several
    # different facts. When a fact also happens to be one of the few that
    # qualifies for Section II, spending it here instead of there can turn a
    # comfortable margin into a shortfall two steps later. Trying the pool in
    # an order that tries non-II-eligible facts first — falling back to
    # II-eligible ones only when nothing else works — costs those sections
    # nothing (they still get the same number of facts) and protects II's
    # much smaller candidate set.
    # Only a False statement has a name swapped into it, so only false_pool is
    # constrained by _safe_to_swap; a True statement is quoted verbatim and any
    # concise verse will do. Applying the swap filter to both would discard
    # perfectly good True candidates for a rewrite they never undergo.
    #
    # These five are claimed before any other section runs. Every other section
    # has somewhere else to go when its preferred shape runs out — IV falls
    # back to the single-answer shape, III to `_clause_halves`, II to a relaxed
    # second pass — but a False statement has no fallback at all: it is the one
    # requirement that raises rather than degrade. Leaving it until last (where
    # it used to sit) meant the sections with alternatives got first pick of the
    # only verses that satisfy the requirement without one.
    false_pool = [fact for fact in pool if (stmt := _concise(fact, True)) and _safe_to_swap(stmt, fact.object)]
    false_facts: list[Fact] = []
    for fact in false_pool:
        if len(false_facts) == 5:
            break
        false_facts.append(fact)
        used.add(fact.id)
    if len(false_facts) != 5:
        raise GenerationError("Nu sunt suficiente versete potrivite pentru Sectiunea I.")

    ii_eligible = {fact.id for fact in facts if _completion_stem(fact) or _wh_question(fact)}
    priority_pool = sorted(pool, key=lambda fact: fact.id in ii_eligible)
    stems = _StemLedger()
    multis = _section_iv(priority_pool, facts, used, rng, stems)
    iii_rows = _section_iii_named(priority_pool, used, rng)
    singles = _section_ii(pool, facts, used, rng, stems)
    matching = _section_iii_fill(pool, used, rng, iii_rows)

    # A True statement needs no recognised object at all — it's quoted as-is —
    # so restricting it to `quality`-filtered facts (needed by every other
    # section) excludes verses for no reason but happening to lack a known
    # name. Falls after the quality-filtered candidates so it's only reached
    # when they don't cover the need on their own.
    non_quality_true = [fact for fact in all_facts if not fact.quality and fact.id not in used and _concise(fact, False)]
    true_pool = [fact for fact in pool if fact.id not in used and _concise(fact, False)] + non_quality_true
    # _concise(fact, False) (True's requirement) is strictly weaker than
    # _concise(fact, True) + _safe_to_swap (False's requirement) — every
    # false_pool fact is also a true_pool fact, so on a small corpus
    # false_pool can be a *subset* of true_pool, not just an overlapping set.
    # That is the other half of why the False picks are reserved above: were
    # True chosen first it would eat the shared facts and starve False, no
    # matter how the sort inside either branch is biased.
    true_facts: list[Fact] = []
    for fact in true_pool:
        if len(true_facts) == 5:
            break
        if fact.id not in used:
            true_facts.append(fact)
            used.add(fact.id)
    if len(true_facts) != 5:
        raise GenerationError("Nu sunt suficiente versete potrivite pentru Sectiunea I.")
    # The True/False pattern across the 10 statements must not be predictable —
    # a fixed odd/even alternation would let a student answer half the section
    # from position alone, without reading a single statement. Shuffling the
    # sequence of True/False slots (independent of which facts filled them
    # above) keeps the layout random each time while still guaranteeing
    # exactly 5 of each.
    pattern = [True] * 5 + [False] * 5
    # Two of the 252 possible shuffles are the perfect alternations this
    # replaced a hardcoded one with. They're as unguessable as any other draw,
    # but a test that happens to land on one is indistinguishable from the bug,
    # so it's worth the reshuffle to never ship that page.
    while True:
        rng.shuffle(pattern)
        # With five of each, the odd slots being uniform forces the even slots
        # to be uniform too, so testing one of them identifies both alternations.
        if len(set(pattern[::2])) != 1:
            break
    tf: list[TrueFalseQuestion] = []
    for index, is_true in zip(range(1, 11), pattern):
        fact = (true_facts if is_true else false_facts).pop(0)
        statement = _concise(fact, not is_true) or _concise(fact, False)
        if not is_true:
            before, _, after = statement.rpartition(fact.object)
            statement = before + _wrong_object(fact, facts, lead=before) + after
        tf.append(TrueFalseQuestion(f"I-{index}", statement, "A" if is_true else "F", fact.evidence, fact.id))

    return TestDefinition(source, seed, version, contest, scoring, section_i=tf, section_ii=singles, section_iii=matching, section_iv=multis)
