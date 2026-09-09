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
# What the cap relaxes *to* on a thin selection, rather than to no cap at all.
# Dropping it entirely let 2 Samuel 11-12 answer „David" nine times out of ten,
# worth 36 of Section II's 40 points to a student who writes one name down the
# page without reading a single stem. `validate_test` enforces this ceiling, so
# a future relaxation cannot quietly reintroduce that page.
_MAX_SAME_ANSWER_RELAXED = 5
# A handful of fixed Romanian phrases that point back at earlier, unquoted
# narrative ("like the other times", "as usual") rather than at anything in
# the sentence itself. A verse built around one reads as confusing on its own
# even though it's a perfectly accurate quote — e.g. "l-a chemat ca și în
# celelalte dăți" presumes the reader already knows about those other times,
# which a single isolated sentence never supplies.
_UNRESOLVED_ANAPHORA = re.compile(
    r"(?<!\w)ca\s+(?:și\s+)?(?:în\s+)?(?:celelalte|mai\s+înainte|de\s+obicei|alt[ăa]\s+dat[ăa])(?!\w)",
    re.IGNORECASE,
)
# Bare 3rd-person plural pronouns ("ei", "ele") stand in for a group named
# earlier in the narrative — 1 Samuel 1's "ei" is Elcana's whole household,
# introduced several verses before the sentence that uses it. A singular named
# subject can never itself be that antecedent, so a plural pronoun beside one
# is a reliable sign the sentence depends on context it doesn't carry.
#
# "ei" is also, confusingly, the genitive/dative of "ea" ("her/of her"):
# "la gura ei" is "at her mouth", not "at their mouth", and needs no
# antecedent beyond the noun sitting right next to it. That reading always
# closes its own clause — nothing but punctuation follows "ei" — while the
# plural "they" keeps going ("făceau ei tuturor...", "Ei vor căuta..."). That
# position is what tells the two apart without deeper parsing.
_PLURAL_PRONOUN = re.compile(r"(?<!\w)(?:ei|ele)(?!\w)", re.IGNORECASE)
# A 3rd-person object clitic ("îl", "o", "l-a", "le-a", "i-a") names nobody at
# all: it is a bare grammatical slot pointing at whoever the narrative was
# last talking about. "Cine i-a istorisit tot?" is unanswerable on its own —
# told *whom*? — while the very same shape with the patient spelled out,
# "Cine l-a chemat pe Samuel?", is complete. What separates them is clitic
# doubling: Romanian repeats the object beside the clitic ("pe Samuel", "lui
# Eli") exactly when it wants to name it, so whether that doubling appears
# anywhere in what the student is shown is the test for whether the sentence
# carries its own patient or borrows one from upstream.
_OBJECT_CLITIC = re.compile(
    r"(?<!\w)(?:îl|l-(?=a(?!\w)|au(?!\w)|o(?!\w))|le-(?=a(?!\w)|au(?!\w))|i-(?=a(?!\w)|au(?!\w)|o(?!\w)))",
    re.IGNORECASE,
)
# What a spelled-out patient looks like. Romanian marks it two ways: the
# accusative takes the preposition „pe" ("l-a chemat pe Samuel", "i-a lovit pe
# egipteni", "pe care ți l-a vorbit"), and the dative inflects the noun itself
# ("i-a zis bucătarului", "le-a trimis oamenilor din Iabes") — so a doubling
# test that only looked for „pe"/„lui" plus a capitalised name threw away a
# large number of perfectly readable verses.
_ACCUSATIVE_DOUBLING = re.compile(r"(?<!\w)pe\s+\w+", re.IGNORECASE)
_DATIVE_NOUN = re.compile(r"(?<!\w)(?:lui\s+\w+|\w{3,}(?:lui|ei|ii|lor))(?!\w)", re.IGNORECASE)
# The dative endings are also the genitive ones, and a genitive is not a
# patient: "Casa Domnului" names no recipient, so counting it would exempt
# exactly the sentences this check exists for ("L-a dus în Casa Domnului" —
# dus *whom*?). What separates them is the word in front: a genitive hangs off
# a noun carrying its own definite article ("Casa", "chivotul", "poporul"),
# while a dative recipient follows the verb ("zis", "trimis", "pus"). The
# "-ea"/"-ia" exclusion keeps imperfect verbs ("îi dădea", "o vedea") from
# being read as articled nouns just because they end in a vowel.
_GENITIVE_HEADS = ("ul", "ua", "ele", "le")


def _is_genitive_head(word: str) -> bool:
    return word.endswith(_GENITIVE_HEADS) or (word.endswith("a") and not word.endswith(("ea", "ia")))


_CLITIC_O = re.compile(r"(?<!\w)o\s+(\w+)", re.IGNORECASE)
_AUXILIARIES = {"voi", "vei", "va", "vom", "veți", "vor", "am", "ai", "ar", "aș", "ați", "au"}
_VERB_ENDINGS = ("use", "useră", "seră", "ește", "esc", "eau", "ea", "a")
# Demonstratives ("copilul acesta", "vedenia aceea") point at something the
# narrative established earlier; the noun beside them is a bare category word
# ("the child", "that vision") that identifies nobody on its own. Unlike the
# pronoun checks this one is deliberately narrow, because most demonstratives
# in the corpus are perfectly resolvable: it fires only when the noun is never
# tied to a name anywhere in the fact — not in the extracted sentence, but in
# the whole verse the sentence came from, since "copilul Samuel" earlier in
# the verse is what makes a later "copilul acesta" readable.
_DEMONSTRATIVE = re.compile(
    r"(?<!\w)(?:(?:acest|această|acești|aceste|acel|acea|acei|acele)\s+(\w+)"
    r"|(\w+)\s+(?:acesta|aceasta|aceștia|acestea|acela|aceea|aceia|acelea))(?!\w)",
    re.IGNORECASE,
)
_NAMES = BibleRepository.PEOPLE | BibleRepository.PLACES | BibleRepository.DEITY


# Time words: a demonstrative on one of these is an adjunct, not the subject
# matter, so it does not make the sentence depend on anything.
_TEMPORAL = {"ziua", "zilei", "zilele", "vremea", "vremii", "vremurile", "ceasul", "noaptea", "dimineața", "seara", "anul", "clipa", "data", "dată", "oară"}
# Words that can sit next to a demonstrative without being the thing it points
# at. The other closed classes the module already keeps (`_PREPOSITIONS`,
# `_VERB_OPENERS`, ...) cover most of them; these are the copula and quantifier
# forms that only turn up in this position.
_NOT_A_NOUN = {"este", "era", "sunt", "erau", "fost", "mare", "mari", "mult", "multă", "tot", "toată", "toți", "toate", "așa"}


def _looks_verbal(word: str) -> bool:
    lowered = word.lower()
    return lowered in _AUXILIARIES or lowered.endswith(_VERB_ENDINGS)


def _has_named_patient(text: str) -> bool:
    """True if `text` spells out an object a clitic could be doubling."""
    if _ACCUSATIVE_DOUBLING.search(text):
        return True
    for match in _DATIVE_NOUN.finditer(text):
        head = text[:match.start()].rstrip().rsplit(" ", 1)[-1].strip(_TRIM).lower()
        if head and _is_genitive_head(head):
            continue
        return True
    return False


def _clitic_without_patient(text: str) -> bool:
    """True if a clitic is the only thing naming who the action was done to.

    Judged over the whole extracted text rather than clause by clause: a
    resumptive clitic routinely picks up a noun from an earlier clause of the
    same sentence ("și-a apucat hainele și le-a sfâșiat"), and the student
    reads the whole of what is shown, so anything inside it counts as supplied.
    """
    if _has_named_patient(text):
        return False
    if _OBJECT_CLITIC.search(text):
        return True
    return any(_looks_verbal(match.group(1)) for match in _CLITIC_O.finditer(text))


def _unidentified_demonstrative(text: str, context: str) -> bool:
    for match in _DEMONSTRATIVE.finditer(text):
        noun = match.group(1) or match.group(2)
        # A demonstrative is only pointing at something when the word beside it
        # is actually a noun. "Domnul este acesta", "Prin aceasta", "Ce ne poate
        # ajuta acesta?" all put a verb, preposition or connective there
        # instead — the regex has no way to tell, so the closed classes the
        # module already maintains for the other shapes do it here.
        if noun.lower() in _NOT_A_NOUN | _PREPOSITIONS | _VERB_OPENERS | _SUBORDINATE | _LINKERS | _DETERMINERS:
            continue
        # A demonstrative on a time word is an adjunct, not the thing the
        # sentence is about: "Domnul a tunat în ziua aceea cu mare vuiet
        # împotriva filistenilor" is perfectly gradeable without knowing which
        # day, whereas "Pentru copilul acesta mă rugam" is not gradeable
        # without knowing which child. Only the second kind is the defect.
        if noun.lower() in _TEMPORAL:
            continue
        if noun in _NAMES or noun.capitalize() in _NAMES:
            continue
        # A name sitting right beside the same noun anywhere in the verse
        # ("copilul Samuel", "chivotul lui Dumnezeu") is what identifies it;
        # the demonstrative then merely points back at something the reader
        # has already been given.
        # The noun matches case-insensitively (it may open a sentence in one
        # place and sit mid-clause in another) while the name beside it must
        # stay capitalised — that capital is the whole signal.
        if re.search(rf"(?<!\w)(?i:{re.escape(noun)})\s+(?:lui\s+)?[A-ZȘȚĂÎÂ]", context):
            continue
        return True
    return False


def _self_contained(text: str, context: str = "") -> bool:
    """False if `text` leans on narrative context beyond itself to be understood.

    Applied to whatever a question or Section I statement will actually show
    the student — not the full verse, just the extracted piece — since context
    earlier in the same sentence (a name introduced before the pronoun, say)
    can still make a pronoun resolvable even where a bare heuristic like this
    can't tell the difference in general. The checks here are the narrow cases
    that come up in practice and are unambiguous when they do.

    `context` is the whole fact the text was extracted from, and only the
    demonstrative check consults it: unlike a pronoun, a demonstrative is
    resolvable from a naming that sits elsewhere in the same verse, so judging
    it on the extracted fragment alone would reject readable verses. It
    defaults to `text` for callers that have nothing wider to offer.

    An earlier version skipped the pronoun check whenever the fact's own
    object was a collective noun ("Israel", "Filistenii"), on the theory that
    a plural pronoun could then be referring back to it. That doesn't hold in
    general — "Israel" can sit in a clause of its own ("acelora din Israel")
    with no connection at all to an unrelated "ei" elsewhere in the same
    sentence — so the position check below is applied unconditionally instead.
    """
    if _UNRESOLVED_ANAPHORA.search(text):
        return False
    for match in _PLURAL_PRONOUN.finditer(text):
        tail = text[match.end():].lstrip()
        if not tail or tail[0] not in ",;.!?":
            return False
    if _clitic_without_patient(text):
        return False
    if _unidentified_demonstrative(text, context or text):
        return False
    return True


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


# A place is not a thing that acts. `_swap_class` below sorts names by how they
# *decline*, which is why PEOPLE and PLACES sit together in its "ordinary"
# bucket — grammatically they behave alike. What that misses is that they are
# not alike as answers: „Efraim l-a chemat din nou pe Samuel" is false because
# Efraim is a region, and a student rules it out on that alone without knowing
# the passage. Deity terms and the collective nouns act, so they group with
# people; only a true place name stands apart.
def _entity_role(name: str) -> str:
    if name in _DEITY or name in _COLLECTIVE or name in BibleRepository.PEOPLE:
        return "agent"
    if name in BibleRepository.PLACES:
        return "place"
    return "other"


def _compatible_kind(one: str, other: str) -> bool:
    """False only when one name is a known place and the other a known agent.

    Deliberately permissive about "other": most `fact.object` values are common
    nouns the corpus never classified, and refusing every pairing involving one
    would discard far more than it protects. The rule fires on the case that is
    actually a defect and stays quiet everywhere else.
    """
    first, second = _entity_role(one), _entity_role(other)
    return first == second or "other" in (first, second)


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


def _wrong_object(fact: Fact, pool: list[Fact], lead: str = "", statement: str = "", require_gender: bool = False) -> str:
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
    #
    # `_same_referent` rather than `_inflection`: the test is whether the swap
    # changes *who the claim is about*, and „Domnul"/„Dumnezeu"/„Dumnezeul"
    # share no prefix while naming the same being. Swapping one for another
    # leaves the statement true while the barem keys it False — the single
    # worst defect this generator can ship, and it did so on roughly half of
    # all tests („Dumnezeul sărăcește și El îmbogățește", 1 Samuel 2:7).
    #
    # A replacement already standing somewhere else in the sentence produces
    # „Saul i-a zis lui Saul" or „David, fata lui Saul, îl iubea pe David":
    # visibly broken rather than plausibly false, so the student answers F on
    # sight without reading. Sections II and IV already refuse a distractor the
    # quoted verse offers (`_mentions(segment, value)`); this is the same rule
    # for the one shape that rewrites the verse instead of quoting it.
    already_present = lambda value: bool(statement) and _mentions(statement, value)
    safe = lambda value: value != fact.object and value != "Domnului" and not _same_referent(value, fact.object) and not (double_definite and value in _ARTICLED) and not already_present(value) and _compatible_kind(value, fact.object)
    same_gender = lambda value: _gender(value) == _gender(fact.object)
    same_class = lambda value: _swap_class(value) == _swap_class(fact.object)
    # Tried in order from safest to riskiest: matching both class and gender
    # first, then relaxing gender, then relaxing class, and only using a
    # mismatched fallback if this chapter selection genuinely has nothing
    # better — rather than fail generation outright.
    #
    # `require_gender` keeps only the tier that breaks no agreement at all —
    # same declension class *and* same gender. Both halves matter and both fail
    # the same way, by producing a sentence that is broken rather than false:
    # „o iubea pe Ana" -> „o iubea pe Elcana" leaves a feminine clitic beside a
    # masculine name, and „Filistenii s-au așezat în linie de bătaie" ->
    # „Samuel s-au așezat" leaves a plural verb beside a singular subject.
    # `_swap_class` had encoded the second since it was written but only ever
    # expressed it as a preference, so the lower tiers handed it back.
    #
    # Section I asks for this because `_falsifiable` has already confirmed,
    # before the verse was reserved, that such a replacement exists — so raising
    # here means a caller asked to falsify a verse it never checked.
    tiers = (lambda v: same_class(v) and same_gender(v),) if require_gender         else (lambda v: same_class(v) and same_gender(v), same_class, same_gender, lambda v: True)
    for match in tiers:
        for option in fact.options:
            if safe(option) and option in inside and match(option):
                return option
        for candidate in pool:
            if candidate.id != fact.id and safe(candidate.object) and match(candidate.object):
                return candidate.object
    raise GenerationError("Nu există suficiente fapte distincte pentru un distractor sigur.")


def _quotes_balanced(text: str) -> bool:
    """True if every quotation opened in `text` also closes inside it.

    The corpus quotes with „ as the opener and a plain ASCII " as the closer —
    ” never appears in it at all — and uses «» for a quote inside a quote.
    Balance has to be measured against that actual convention. Counting only
    the ASCII mark, as `_concise` did, lets a fragment carrying the opener but
    not the closer straight through, which is how 28% of Section I statements
    came to be printed with a dangling „ („Ano, pentru ce plângi" with no end).
    Comparing „ against ”, as `_completion_stem` did, is the opposite error:
    ” is always zero here, so that test rejected *every* verse containing
    reported speech — a third of the corpus — and starved Section II.
    """
    if text.count("«") != text.count("»"):
        return False
    opened = text.count("„")
    if not opened:
        # A corpus that quotes with plain ASCII " on both sides offers no
        # opener to count against; there an even number of them is the test.
        return text.count('"') % 2 == 0
    return opened == text.count('"') + text.count("”")


def _concise(fact: Fact, need_object: bool) -> str | None:
    """Section I keeps one clean clause; a verse that offers none is skipped."""
    for sentence in sorted(_sentences(fact.statement), key=len):
        if not 30 <= len(sentence) <= 165 or not sentence[:1].isupper():
            continue
        if not _quotes_balanced(sentence) or "(" in sentence:
            continue
        # Verse boundaries can split a sentence. A comma/semicolon ending is
        # still a fragment, even when it is a faithful quotation of the verse.
        if not re.search(r'[.!?][\"”»]*$', sentence):
            continue
        # A question is not an assertion, so it cannot be keyed true or false.
        # „Cine ne va izbăvi din mâna acestor dumnezei puternici?" shipped as a
        # Section I item marked A: the verse it came from is inside a speech
        # whose opening quote sits in the *previous* verse, so nothing was left
        # around it to make it a report of what someone asked.
        #
        # The mark has to be the sentence's own, which is why a closing quote
        # after it is decisive rather than incidental: „Samuel i-a zis: «Ce
        # s-a întâmplat, fiule?»" asserts that Samuel said it, and that is
        # true or false in the ordinary way.
        if re.search(r'\?[\s]*$', sentence):
            continue
        if need_object and not _mentions(sentence, fact.object):
            continue
        if not _self_contained(sentence, fact.statement):
            continue
        return sentence
    return None


# Extra real words a stem must carry when the blank opens it, on top of
# `_BLANK_MIN_WORDS`: with nothing to the left, everything the student reasons
# from sits on one side of the gap.
_LEADING_BLANK_EXTRA_WORDS = 2
# Passing `rivals` is what enables the leading-blank shape, and only Section II
# does it. That is deliberate rather than incidental. Counting these candidates
# in `ii_eligible` was measurably worse than not having the shape at all (40
# failures against 37): they are the most formulaic stems in the corpus —
# „__________ a zis:", „__________ l-a chemat" — so Section II's own stem
# ledger rejects most of them as near-twins of each other, while `ii_eligible`
# had already reserved them away from Sections III and IV. Kept out of the
# reservation set they are what they actually are: a fallback Section II reaches
# when its strong shapes run out, costing the other sections nothing.


def _completion_stem(fact: Fact, rivals: list[tuple[str, set[str]]] | None = None) -> tuple[str, str] | None:
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
        if not _quotes_balanced(sentence):
            continue
        if not sentence[:1].isupper():
            continue
        # A pronoun or back-reference elsewhere in the sentence is exactly as
        # confusing here as it is in Section I's `_concise` — the reader still
        # only sees this one sentence, blank or not.
        if not _self_contained(sentence, fact.statement):
            continue
        stem = (sentence[:hit.start()] + _BLANK + sentence[hit.end():]).strip()
        # A blank opening the sentence has no left context, which used to be an
        # outright rejection on the grounds that it is a bare "who?" the
        # wh-question shape phrases better. It was the single largest source of
        # lost candidates — 238 of 1183 quality facts, 138 of them picked up by
        # no other shape either — and the premise was wrong: the reference
        # papers blank the opening word freely („__________ a crescut, și
        # Domnul era cu el"), because what an item needs is not context on the
        # left but enough of it somewhere.
        #
        # So the rule becomes a stricter version of the one below rather than a
        # refusal: more real words than a mid-sentence blank needs, and the
        # remainder must identify the subject uniquely. That second half is the
        # guard the wh shape already gets — without it „__________ a zis:
        # «...»" can be as true of one person as another, which is precisely
        # what the old comment was worried about.
        if stem.startswith(_BLANK):
            if len(stem.split()) - 1 < _BLANK_MIN_WORDS + _LEADING_BLANK_EXTRA_WORDS:
                continue
            if not rivals or not _uniquely_answered(sentence[hit.end():], fact.object, rivals):
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


def _falsifiable(fact: Fact, pool: list[Fact]) -> tuple[str, bool] | None:
    """The Section I statement for `fact`, and whether gender survives the swap.

    Reproduces exactly what the emit path does — the same sentence, the same
    last-occurrence split — and then asks `_wrong_object` for a replacement
    that keeps grammatical gender. Checking it here rather than at emit time is
    what lets a verse with no same-gender stand-in be passed over while there
    are still others to reserve; by the time the statement is being written the
    five have been committed and the only options left are a broken sentence or
    a failed generation.
    """
    statement = _concise(fact, True)
    if not statement or not _safe_to_swap(statement, fact.object):
        return None
    hits = list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", statement))
    if not hits:
        return None
    hit = hits[-1]
    before, after = statement[:hit.start()], statement[hit.end():]
    try:
        _wrong_object(fact, pool, lead=before, statement=f"{before} {after}", require_gender=True)
    except GenerationError:
        # Falsifiable, but only by a name of the other gender. Reported rather
        # than refused: demanding gender outright cost Section I five times as
        # many outright failures as the broken sentences it prevented, and a
        # selection with no second feminine name in it has nothing better to
        # offer. `build_test` sorts these last and reaches them only when the
        # clean ones cannot fill the five.
        return statement, False
    return statement, True


# The spelled-out numerals the corpus uses, each mapped to the value it names so
# two forms of one number never appear as rival options ("doi" and "două" are
# the masculine and feminine of 2, and offering both asks the student to pick a
# gender rather than a fact).
_NUMERAL_VALUES = {
    "doi": 2, "două": 2, "trei": 3, "patru": 4, "cinci": 5, "șase": 6, "șapte": 7,
    "opt": 8, "nouă": 9, "zece": 10, "unsprezece": 11, "doisprezece": 12,
    "treisprezece": 13, "paisprezece": 14, "cincisprezece": 15, "șaisprezece": 16,
    "șaptesprezece": 17, "optsprezece": 18, "nouăsprezece": 19, "douăzeci": 20,
    "treizeci": 30, "patruzeci": 40, "cincizeci": 50, "șaizeci": 60, "șaptezeci": 70,
    "optzeci": 80, "nouăzeci": 90,
}
# „sută"/„mie" and their plurals are deliberately absent. They are scale words
# that need a count in front of them („trei mii de oameni"), not quantities that
# can stand in a slot by themselves: offered as a distractor one reads „cei mii
# fii ai lui Eli", and blanked as an answer it leaves „trei __________ de
# oameni", which asks about the unit rather than the number. „treizeci" in
# „treizeci de mii" still matches, which is the part worth asking about.
_NUMERAL = re.compile(rf"(?<!\w)({'|'.join(sorted(_NUMERAL_VALUES, key=len, reverse=True))})(?!\w)")
# Used only when the selection itself offers too few numerals to choose from.
# Invariant forms, so they fit whatever noun the blank sits in front of.
_FALLBACK_NUMERALS = ("trei", "cinci", "șapte", "zece", "patruzeci")


def _blank_in_place(sentence: str, start: int, end: int, statement: str) -> str | None:
    """The shared body of every completion shape: the sentence with one span
    blanked, or None if what is left would not make a readable item.

    `_completion_stem` grew these checks for the one span it knew about — the
    fact's own object. They are not about *what* is blanked, only about how much
    verse survives around the gap, so the place and numeral shapes reuse them
    rather than restating them and drifting.
    """
    if not _quotes_balanced(sentence) or not sentence[:1].isupper():
        return None
    if not _self_contained(sentence, statement):
        return None
    stem = (sentence[:start] + _BLANK + sentence[end:]).strip()
    # No leading blank here: unlike the fact's own object, a place or a numeral
    # opening the sentence leaves the student guessing at a category rather than
    # recalling a verse, and `_uniquely_answered` cannot vouch for it — it
    # compares predicates against who performed them, not against where or how
    # many.
    if stem.startswith(_BLANK):
        return None
    if not _STEM_MIN_CHARS <= len(stem) <= _BLANK_MAX_CHARS:
        return None
    if len(stem.split()) - 1 < _BLANK_MIN_WORDS:
        return None
    return stem


def _sole_match(pattern: re.Pattern[str], sentence: str) -> re.Match[str] | None:
    """The one match in `sentence`, or None if there are none or several.

    Blanking one occurrence while an identical word stays visible hands the
    student the answer; blanking a numeral out of "trei tauri și trei oi" leaves
    an item with two defensible readings.
    """
    found = list(pattern.finditer(sentence))
    return found[0] if len(found) == 1 else None


def _numeral_stem(fact: Fact) -> tuple[str, str, str] | None:
    """„Câți/Câte ...?" as the reference papers actually ask it — the verse with
    its number blanked, rather than a question needing subject-verb inversion.

    Romanian forms a wh-question by inverting („Unde se suia omul acesta?", not
    „Unde omul acesta se suia?"), and reordering a clause safely is well beyond
    what this module can do from a regular expression. The blank needs no
    inversion, is grammatical by construction, and is the form the reference
    baremuri use for this anyway.

    The answer is a number rather than a name, which is what makes this worth
    having: every other Section II shape is answered by a person or a place, so
    a paper drawn only from those asks the same kind of question ten times.
    """
    for sentence in _sentences(fact.statement):
        if not (hit := _sole_match(_NUMERAL, sentence)):
            continue
        if stem := _blank_in_place(sentence, hit.start(), hit.end(), fact.statement):
            return stem, sentence, hit.group(0)
    return None


def _place_stem(fact: Fact) -> tuple[str, str, str] | None:
    """„Unde ...?" in the same completion form, answered by a place name.

    Distinct from `_completion_stem` even when a place is the fact's own object:
    this targets whichever place the sentence names, so it reaches verses whose
    extracted object is a person the blank shape could not use.
    """
    for sentence in _sentences(fact.statement):
        named = [place for place in BibleRepository.PLACES
                 if place not in _COLLECTIVE and _mentions(sentence, place)]
        if len(named) != 1:
            continue
        place = named[0]
        if not (hit := _sole_match(re.compile(rf"(?<!\w){re.escape(place)}(?!\w)"), sentence)):
            continue
        if stem := _blank_in_place(sentence, hit.start(), hit.end(), fact.statement):
            return stem, sentence, place
    return None


def _numeral_options(answer: str, facts: list[Fact]) -> list[str]:
    """Numerals from the selection that name a different quantity than `answer`."""
    target = _NUMERAL_VALUES.get(answer.lower())
    inside = _dedup(match.group(0) for fact in facts for match in _NUMERAL.finditer(fact.statement))
    pool = [value for value in inside if _NUMERAL_VALUES.get(value.lower()) != target]
    pool += [value for value in _FALLBACK_NUMERALS if _NUMERAL_VALUES[value] != target]
    # One form per quantity, so „doi" never stands beside „două".
    seen: set[int] = set()
    result = []
    for value in pool:
        number = _NUMERAL_VALUES[value.lower()]
        if number not in seen:
            seen.add(number)
            result.append(value)
    return result


def _place_options(answer: str, facts: list[Fact]) -> list[str]:
    """Place names the selection actually uses, other than `answer`."""
    return _dedup(place for fact in facts for place in BibleRepository.PLACES
                  if place != answer and place not in _COLLECTIVE and _mentions(fact.statement, place))


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


# Matching opening/closing marks for Romanian block quotes and plain ASCII
# quotes (used interchangeably across the corpus's verses).
# Each opener against every mark that may close it. „ is listed with the plain
# ASCII quote *first* because that is what this corpus actually closes with: ”
# appears zero times in it against 648 ASCII closers. Mapping „ to ” alone meant
# `_extend_through_quote` searched for a character that was never there, always
# returned None, and so `_name_predicate(allow_quote=True)` — the branch written
# specifically to recover reported speech — never fired once on this corpus.
# 288 verses carrying quoted speech were unusable for that reason, and the test
# guarding the branch only asserted the *absence* of a truncated stub, which is
# trivially true when the branch never runs.
#
# Same root cause as the `_quotes_balanced` defect: the module encoded „…” while
# the corpus writes „…". It was fixed in `_concise` and `_completion_stem` and
# missed here.
_QUOTE_PAIRS = {"„": ('"', "”"), "«": ("»",), '"': ('"',)}
# A quoted predicate can run well past the 14-word cap that keeps a plain
# clause readable as one line of a matching table; it just needs its own,
# more generous ceiling so a whole paragraph of dialogue doesn't slip through.
_PREDICATE_QUOTE_MAX_CHARS = 220


def _opens_quote(tail: str, cut: int) -> bool:
    """True if `cut` lands on a colon that introduces a quotation.

    A predicate truncated right there — "s-a rugat și a zis" — promises
    reported speech and then supplies none of it, which is what left
    `_wh_question`'s "Cine s-a rugat și a zis?" with nothing to ask about. The
    punctuation search that finds `cut` treats that colon exactly like one
    that ends a clause; this tells the caller it's a different case.
    """
    if tail[cut:cut + 1] != ":":
        return False
    stripped = tail[cut + 1:].lstrip()
    return bool(stripped) and stripped[0] in _QUOTE_PAIRS


def _extend_through_quote(tail: str, cut: int) -> int | None:
    """Pushes `cut` (already confirmed by `_opens_quote`) past the whole
    quotation, or returns None if it never closes within `tail` — the corpus
    sometimes trims a fact's statement mid-speech, before the matching mark.
    """
    rest = tail[cut + 1:]
    stripped = rest.lstrip()
    opener_index = cut + 1 + (len(rest) - len(stripped))
    # The earliest of the acceptable closers: a quotation that ends with the
    # ASCII mark must not run on to a later ” belonging to something else.
    found = [index for index in (tail.find(closer, opener_index + 1) for closer in _QUOTE_PAIRS[stripped[0]]) if index != -1]
    if not found:
        return None
    close_index = min(found)
    end = close_index + 1
    # A sentence-ending mark right after the closing quote still belongs to
    # this predicate — it closes the quote's own sentence, not a new clause.
    if end < len(tail) and tail[end] in ".!?":
        end += 1
    return end


def _name_predicate(fact: Fact, allow_quote: bool = False) -> str | None:
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
        cut = punct.start() if punct else len(tail)
        quoted = False
        if _opens_quote(tail, cut):
            # A truncated "s-a rugat și a zis" is a stub, not a predicate — it
            # promises reported speech and supplies none of it. Either recover
            # the quote (when the caller wants it and it actually closes within
            # this fact) or skip the sentence rather than hand back the stub.
            extended = _extend_through_quote(tail, cut) if allow_quote else None
            if extended is None:
                continue
            cut = extended
            quoted = True
        # `_TRIM` strips quote marks along with ordinary punctuation — fine for
        # a plain clause, but it would eat the quotation's own closing mark
        # right back off a quoted predicate, so that one keeps only outer
        # whitespace trimmed.
        predicate = tail[:cut].strip() if quoted else tail[:cut].strip(_TRIM)
        words = predicate.split()
        if quoted:
            if len(predicate) > _PREDICATE_QUOTE_MAX_CHARS:
                continue
        # Reference predicates are usually short, but a plain, clean single
        # clause ("au luat chivotul lui Dumnezeu ... la Asdod") stays
        # readable well past 9 words — the punctuation truncation above
        # already guarantees it's one clause, not a run-on.
        elif not 2 <= len(words) <= 14:
            continue
        # If the name sits after its verb ("se suia Ana la Casa Domnului"),
        # what follows is the verb's own complement, not a fresh predicate
        # about the name — a real predicate opens with a verb, not another
        # preposition continuing the earlier phrase.
        #
        # A bare linker or subordinator opening it is the same defect wearing a
        # different word class. „Saul și oamenii lui erau..." names a compound
        # subject, so the clause after „Saul" starts on „și" and „Cine" lands in
        # front of it: „Cine și oamenii lui erau în fundul peșterii?". „Domnul
        # să te răsplătească" is a wish, not an assertion about the Lord, and
        # gives „Cine să pun mâna pe unsul Domnului?". Both read as broken
        # Romanian rather than as questions, and 47 of them shipped.
        #
        # The bare word only — deliberately not `_parallel_member`'s pre-hyphen
        # stem. Romanian glues the reflexive clitic on with a hyphen, so „și-a"
        # in „Saul și-a ales trei mii de bărbați" is not the conjunction „și",
        # and „Cine și-a ales trei mii de bărbați?" is perfectly good.
        if words[0].lower() in _OBLIQUE_MARKERS | _SUBORDINATE | _LINKERS:
            continue
        # The same defect at the other end. Truncating at the next comma cuts
        # „David a luat cuvântul și, ..." down to „a luat cuvântul și" — a
        # predicate that announces a second half and then stops, which shipped
        # into Section III as an association for David. A clause this program
        # hands over has to end on something, not on the promise of more.
        if not quoted and words[-1].lower().strip("-") in _OBLIQUE_MARKERS | _SUBORDINATE | _LINKERS:
            continue
        # The name must not reappear, or the association gives itself away.
        if len(predicate) < 10 or _mentions(predicate, fact.object):
            continue
        # A predicate that itself depends on context outside this sentence
        # (an unresolved "ei", a bare "ca și în celelalte dăți") reads as a
        # non sequitur once it's the only thing left of the verse.
        if not _self_contained(predicate, fact.statement):
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
    # `allow_quote=True`: unlike Section III's short attribute pairs, a
    # wh-question reads naturally with the reported speech included — "Cine a
    # zis: «Eu nu găsesc nicio vină în El»?" — so it's worth recovering here.
    predicate = _name_predicate(fact, allow_quote=True)
    if not predicate:
        return None
    return f"Cine {predicate}?", predicate


def _same_referent(one: str, other: str) -> bool:
    """True for two answer terms that name the same person.

    „Domnul"/„Dumnezeul" and „Domnul"/„Domnului" are different strings and
    different `fact.object` values, but a question answered by one is answered
    by the other — so they must not count as competing answers below.
    """
    if one.lower() == other.lower() or (one in _DEITY and other in _DEITY):
        return True
    return _inflection(one, [other])


def _distinct_referents(candidates: list[str], count: int) -> list[str] | None:
    """The first `count` candidates that all name different things, else None.

    `candidates[0]` is the correct answer and is always kept; the rest are
    tried in the caller's own order of preference. Two options that name one
    referent make an item unanswerable when one of them is correct, and make
    it a two-way guess when neither is, so the check is the same either way.
    """
    chosen: list[str] = []
    for value in candidates:
        if len(chosen) == count:
            break
        if not any(_same_referent(value, taken) for taken in chosen):
            chosen.append(value)
    return chosen if len(chosen) == count else None


def _rival_predicates(facts: list[Fact]) -> list[tuple[str, set[str]]]:
    """Everything the selection says each person did, as bags of words.

    Deliberately not built with `_name_predicate`: that function answers
    "would this make a good question?", and rejects for reasons — an
    unrecoverable quotation, a predicate too long, a name that reappears —
    that have nothing to do with whether the verse describes a rival doing the
    same thing. 1 Samuel 3's "Dar Eli l-a chemat pe Samuel și i-a zis:
    «Samuele, fiule!»" is exactly such a rejection, and it is the single verse
    that makes „Cine l-a chemat din nou pe Samuel?" ambiguous.

    What it does keep from `_name_predicate` is the clause cut. A rival has to
    be one person's own action, and everything past the next comma may already
    belong to someone else — taking the whole rest of the sentence instead
    swept unrelated clauses into the bag and made almost any question look
    ambiguous against almost any verse.
    """
    rivals = []
    for fact in facts:
        for sentence in _sentences(fact.statement):
            hit = re.search(rf"(?<!\w){re.escape(fact.object)}(?!\w)", sentence)
            if not hit:
                continue
            tail = sentence[hit.end():]
            punct = re.search(r"[,;:.!?]", tail)
            cut = punct.start() if punct else len(tail)
            if _opens_quote(tail, cut):
                cut = _extend_through_quote(tail, cut) or cut
            if words := set(re.findall(r"\w+", tail[:cut].lower())):
                rivals.append((fact.object, words))
    return rivals


def _uniquely_answered(predicate: str, subject: str, rivals: list[tuple[str, set[str]]]) -> bool:
    """False if some other person in the corpus did the same thing too.

    1 Samuel 3 has Eli call Samuel and the Lord call Samuel, in nearly the same
    words, several times over. „Cine l-a chemat din nou pe Samuel?" therefore
    has two defensible answers, and the one in the answer key is only right
    because of which verse it happened to be built from — a distinction the
    student cannot see and has no way to reason about.

    Unlike `_StemLedger`, which stops a question repeating one already issued,
    this compares against every verse in the selection, issued or not: the
    rival that makes the question ambiguous is usually a verse no section
    picked up at all, but the student has read it just the same.

    The comparison is containment of the question's own words in the rival's,
    not overlap between the two — `_StemLedger`'s symmetric measure answers
    "are these the same question?", which is a different question from this
    one. A rival that says everything the question says *and more* ("l-a
    chemat pe Samuel și i-a zis: «Samuele, fiule!»" against "l-a chemat din nou
    pe Samuel") scores barely half on overlap while still making the question
    ambiguous, because everything the student is asked about is true of that
    other person too.
    """
    words = set(re.findall(r"\w+", predicate.lower()))
    if not words:
        return False
    return not any(
        len(words & rival) / len(words) >= _STEM_OVERLAP_LIMIT
        for other, rival in rivals if not _same_referent(other, subject)
    )


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
            if not _self_contained(clause, fact.statement):
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
                # A half ending on „și"/„sau"/„dar"/„iar" dangles exactly the
                # way a comma-truncated predicate does; the seam is a different
                # mechanism reaching the same broken pair.
                if closer in _LINKERS or opener in _LINKERS:
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


# Explicit nominal forms, rather than a word-count guess. Unknown phrases
# remain available to the other question shapes; they are not assumed to be
# list members merely because a conjunction precedes them.
_ENUMERATION_FORMS = {
    "quantity": re.compile(
        r"(?:un|o|doi|două|trei|patru|cinci|șase|șapte|opt|nouă|zece|"
        r"(?:două|trei|patru|cinci) sute de|o sută de) "
        r"(?:fii|fiice|tauri|oi(?: pregătite)?|efă de făină|burduf(?:uri)? cu vin|"
        r"pâini|pâine|bucată de carne|turtă de stafide|turte de stafide|"
        r"măsuri de grâu prăjit|legături de smochine|suliță|sabie|bou|vițel gras)"
    ),
    "vessels": re.compile(r"vase de (?:argint|aur|aramă)"),
    "instruments": re.compile(r"cu (?:harfe|lăute|timpane|fluiere|chimvale)"),
    "kinship": re.compile(r"(?:os din oasele|carne din carnea) (?:tale|mele)"),
}


def _member_kind(value: str) -> str | None:
    return next((kind for kind, pattern in _ENUMERATION_FORMS.items()
                 if pattern.fullmatch(value)), None)


def _parallel_member(value: str) -> bool:
    return _member_kind(value) is not None


def _enumeration(fact: Fact) -> tuple[str, list[str]] | None:
    """Extract only whole, recognised nominal phrases in a coordinated list.

    The complete suffix must parse, and each preceding member is matched at
    its actual boundary. In particular a seven-word item does not imply that
    its neighbour also has seven words. Clauses and open quotations are never
    converted to a multiple-answer completion.
    """
    for sentence in _sentences(fact.statement):
        if not _self_contained(sentence, fact.statement):
            continue
        # Quoted lists need the surrounding speaker/context and punctuation
        # preserved; leave these to the quoted-speech and blank shapes.
        if any(mark in sentence for mark in ('"', '„', '”', '«', '»')):
            continue
        links = list(_CONJUNCTION.finditer(sentence))
        if not links:
            continue
        link = links[-1]
        tail = sentence[link.end():].strip(_TRIM)
        kind = _member_kind(tail)
        if kind is None:
            continue
        members = [tail]
        head = sentence[:link.start()].rstrip()
        while True:
            # Longest recognised suffix, preserving determiners and modifiers.
            found = None
            for match in re.finditer(r"(?<!\S)\S", head):
                value = head[match.start():]
                if _member_kind(value) == kind:
                    found = (head[:match.start()].rstrip(), value)
                    break
            if found is None:
                break
            head, value = found
            members.insert(0, value)
            if head.endswith(','):
                head = head[:-1].rstrip()
            elif (separator := re.search(r"\s+(?:și|sau)$", head)):
                head = head[:separator.start()].rstrip()
            else:
                break
        if len(members) < 2 or len(set(members)) != len(members):
            continue
        # A short named predicate such as "Ioram a adus" is sufficient once
        # the entire list (rather than an arbitrary suffix) has been removed.
        if not 12 <= len(head) <= _STEM_MAX_CHARS or not _self_contained(head, fact.statement):
            continue
        if head.split()[-1].lower() in _OBLIQUE_MARKERS | _PREPOSITIONS:
            continue
        return f"{head} {_BLANK}", members
    return None


def _section_iv(pool: list[Fact], facts: list[Fact], used: set[str], rng: random.Random, stems: _StemLedger, rivals: list[tuple[str, set[str]]], reserved: set[str]) -> list[MultiChoiceQuestion]:
    """Mirrors the reference mix: items with three, two and one correct answer."""
    candidates = [(fact, *found) for fact in pool if (found := _enumeration(fact))]
    foreign = [member for _, _, members in candidates for member in members]
    multis: list[MultiChoiceQuestion] = []

    def add(fact: Fact, stem: str, values: list[str], correct_values: list[str]) -> bool:
        # Same rule as Section II: three options that are distinct *strings* can
        # still be fewer than three distinct answers.
        if _distinct_referents(values, 3) is None:
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
    # `reserved` is Section II's candidate set; `_may_take` stops the
    # single-answer fallback below — which is literally Section II's two shapes
    # — from spending a verse Section II cannot spare. The enumeration shape
    # never collides with Section II, so this costs those items nothing.
    # `allow_reserved` is the last thing to give way. Section IV must reach three
    # items or raise, so once its own shapes are exhausted it takes a verse
    # Section II wanted rather than fail — Section II's shortfall message at
    # least tells the reader to add a chapter, while a Section IV one on a
    # selection that could have produced a test is a worse outcome.
    for allow_reserved, enforce in ((False, True), (False, False), (True, True), (True, False)):
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
                # No `_may_take` here: the enumeration shape needs a coordinated
                # „și"/„sau" list, which none of Section II's shapes can use, so
                # a fact being in `reserved` says nothing about whether spending
                # it here costs Section II anything — and Section IV has to
                # reach three items or raise.
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
                    and _member_kind(value) == _member_kind(correct_values[0])
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
            # This fallback *is* Section II's two shapes, so unlike the
            # enumeration loop above it has to respect Section II's floor —
            # until `allow_reserved` lifts it.
            if fact.id in used or not (allow_reserved or fact.id not in reserved):
                continue
            # The „Cine ...?" shape needs the same ambiguity guard Section II
            # applies to it. Without it this loop shipped the very question
            # `_uniquely_answered` was written for — „Cine l-a chemat din nou pe
            # Samuel?", which 1 Samuel 3 answers with both Eli and the Lord —
            # and „Cine s-a ascuns în câmp?", where the distractor beside the
            # keyed answer hid in that field too.
            built = None
            for name, builder in (("blank", _completion_stem), ("wh", _wh_question)):
                if not (candidate := builder(fact)):
                    continue
                if name == "wh" and not _uniquely_answered(candidate[1], fact.object, rivals):
                    continue
                built = candidate
                break
            if not built:
                continue
            stem, segment = built
            distractors = _dedup(f.object for f in facts if not _mentions(segment, f.object) and _compatible_kind(f.object, fact.object) and f.object != "Domnului")
            values = _distinct_referents([fact.object, *distractors], 3)
            if values is None:
                continue
            add(fact, stem, values, [fact.object])
        if len(multis) == 3:
            break
    if len(multis) != 3:
        raise GenerationError("Nu s-au putut construi trei intrebari verificabile pentru Sectiunea IV.")
    return multis


# What one complete test spends: 10 (I) + 10 (II) + 5 (III) + 3 (IV) distinct
# verses, of which everything but Section I's five True statements needs a
# `quality` verse — one carrying a recognised name to build an answer around.
_TOTAL_ITEMS = 28
_QUALITY_ITEMS = 23


def _may_take(fact: Fact, reserved: set[str], used: set[str]) -> bool:
    """True unless `fact` is one Section II could have used.

    Sections III-named and IV both pick before Section II and both want the
    same verses it does — III's shape *is* `_name_predicate`, which is also
    Section II's „Cine ...?" shape, and IV's single-answer fallback is literally
    Section II's two shapes. Sorting the pool to try Section II's candidates
    last was too weak (it still reached them the moment a non-reserved verse
    failed the shape, costing Section II 170 candidates across a 53-selection
    sweep); refusing them outright was too strong (it starved Section III on
    thin selections, trading one section's failures for another's).

    This was a budget for a while — spend a reserved verse while Section II has
    more than `_SECTION_II_QUOTA` left, protect it once it does not — and the
    floor had to be re-tuned upwards every time a new shape widened Section II's
    candidate set (24 after the leading blank, 40 after quoted speech), because
    a larger reserved set means a larger share of the pool that III and IV are
    free to spend before the floor bites. Every one of those sweeps plateaued at
    the value where the budget stops binding at all, i.e. where it behaves
    exactly as the plain refusal below does.

    So it is the plain refusal, which needs no tuning and cannot drift out of
    calibration with the corpus. Section II is the one section whose shortfall
    is fatal — III falls back to `_clause_halves` and to Section II's own
    leftovers afterwards, IV to its single-answer shape and then to an escape
    pass — so it simply gets its candidates.
    """
    return fact.id not in reserved


def _section_iii_named(pool: list[Fact], used: set[str], rng: random.Random, reserved: set[str]) -> list[tuple[Fact, str, str]]:
    """The scarcer of Section III's two shapes: a recognised name as the
    clause's subject, paired with its predicate. Claims facts before Section
    II runs, same as the enumeration shape in Section IV — a name needing to
    be the clause's *subject* is a tighter constraint than anything Section
    II's shapes require.

    `reserved` holds the facts Section II could use; `_may_take` keeps this
    shape from spending one Section II cannot spare. Returning fewer than five
    rows is not a failure here, which is what makes deferring cheap:
    `_section_iii_fill` tops the column up *after* Section II has run, both
    from `_clause_halves` and from whatever named verses Section II left.
    """
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
            if not _may_take(fact, reserved, used):
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
    seen_lower = {name.lower() for _, name, _ in rows}
    if len(rows) < 5:
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
    # `_section_iii_named` refuses Section II's candidates outright, which is
    # what gave Section II its verses back — but Section II has now run, so
    # whatever it did not take is free. Retrying the named shape over those
    # leftovers costs Section II nothing and recovers the column on selections
    # where `_clause_halves` alone could not fill it.
    if len(rows) < 5:
        for fact in pool:
            if len(rows) == 5:
                break
            if fact.id in used or not (predicate := _name_predicate(fact)):
                continue
            if fact.object.lower() in seen_lower or any(predicate == other[2] for other in rows):
                continue
            if _inflection(fact.object, [name for _, name, _ in rows]):
                continue
            rows.append((fact, fact.object, predicate))
            seen_lower.add(fact.object.lower())
            used.add(fact.id)
    if len(rows) != 5:
        # Section III runs last and, unlike Section II, gives every other
        # section first pick of a shared pool on the theory that it alone has
        # a fallback shape to fall back on. That holds in general, but on a
        # selection where `_clause_halves` itself is scarce, Sections I/II/IV
        # can still exhaust it before Section III gets a turn — measured on
        # real selections where Section II used every non-overlapping
        # candidate it had and still needed some of III's few clause-half
        # verses to reach its own quota. Reordering priority there would only
        # move the failure to Section II, which has no fallback of its own —
        # so, like Section II's message, this names the shortfall and points
        # at the one thing that reliably fixes it.
        raise GenerationError(
            f"Secțiunea III are nevoie de 5 asocieri, dar selecția a produs doar {len(rows)}. "
            "Adăugați încă un capitol la selecție."
        )
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


def _section_ii(pool: list[Fact], facts: list[Fact], used: set[str], rng: random.Random, stems: _StemLedger, rivals: list[tuple[str, set[str]]], avoid: set[str]) -> list[SingleChoiceQuestion]:
    singles: list[SingleChoiceQuestion] = []
    letters = _balanced_letters(10, rng)
    answers: dict[str, int] = defaultdict(int)
    shapes: dict[str, int] = defaultdict(int)

    # Both quotas below are preferences, not correctness rules, so a corpus
    # too thin to satisfy them gets progressively relaxed passes rather than a
    # GenerationError. The answer cap gives way first (tier 2) — ten questions
    # that repeat an answer up to a point still reads as a real test. Only the
    # tier that can't otherwise reach ten relaxes the stem ledger's near-
    # duplicate check too (tier 3): a visibly repeated question is a worse
    # defect than an uncapped answer, so it stays last to give way.
    # `avoid` is what sibling variants of this paper already spent. Ordering the
    # pool by it is too weak to matter here — Section II's eligible set is small
    # enough that a reshuffle reaches the same verses regardless — so the whole
    # tier ladder runs once refusing them outright and again accepting them.
    # Refusing is still only a preference: two variants of a selection that can
    # barely fill one test have to be allowed to overlap rather than fail.
    tiers = [(cap, ledger, skip)
             for skip in (True, False)
             for cap, ledger in ((_MAX_SAME_ANSWER, True), (_MAX_SAME_ANSWER_RELAXED, True), (_MAX_SAME_ANSWER_RELAXED, False))]
    for cap, enforce_ledger, skip_siblings in tiers:
        stems.strict = enforce_ledger
        for fact in pool:
            if len(singles) == 10:
                break
            if fact.id in used or (skip_siblings and fact.id in avoid):
                continue
            # A shape is usable only if its *options* can be built too, so a
            # candidate is carried all the way through the answer cap, the
            # distractor pool, the referent check and the stem ledger before it
            # is accepted, and a shape that fails any of them falls through to
            # the next. Choosing the shape first and discovering afterwards that
            # its options did not work abandoned the whole verse — a fact whose
            # „Cine ...?" has no safe distractors still makes a perfectly good
            # blank — and with four shapes competing that was the difference
            # between a test and a GenerationError.
            #
            # Least-used shape first, so ten questions do not all look alike. A
            # stable sort keeps ties in the order listed, so a given seed still
            # produces exactly the same paper.
            builders = [("blank", lambda f: _completion_stem(f, rivals)), ("wh", _wh_question),
                        ("place", _place_stem), ("numeral", _numeral_stem)]
            builders.sort(key=lambda item: shapes[item[0]])
            chosen = None
            for name, builder in builders:
                if not (candidate := builder(fact)):
                    continue
                # A „Cine ...?" whose predicate several different people also
                # satisfy has no single right answer. The other shapes are not
                # exposed to this — they quote one specific verse rather than
                # asking which person a description picks out — so the fact can
                # still become a question through one of them.
                if name == "wh" and not _uniquely_answered(candidate[1], fact.object, rivals):
                    continue
                stem, segment = candidate[0], candidate[1]
                # The place and numeral shapes answer with something other than
                # the fact's own object — a place the verse names, or the
                # quantity it states — so the answer travels with the stem
                # instead of being assumed, and each shape brings the pool its
                # distractors come from.
                answer = candidate[2] if len(candidate) > 2 else fact.object
                # „Cine?" only ever answers with a name, and the corpus leans
                # hard on a few of them (the deity terms above all), so without
                # a cap a run of verses about one subject becomes a run of
                # questions with the same answer.
                if answers[answer] >= cap:
                    continue
                # A distractor the quoted verse already offers could fill the
                # blank too. A place among the options of a „Cine ...?", or a
                # person among the completions of a blank the verse fills with a
                # place, is ruled out on category alone. And „Domnului" is a case
                # form, not a name: against a subject-position blank it reads as
                # „Domnului au început lupta".
                safe = lambda value: not _mentions(segment, value) and _compatible_kind(value, answer) and (value == answer or value != "Domnului")
                if name == "numeral":
                    available = _numeral_options(answer, facts)
                elif name == "place":
                    available = _place_options(answer, facts)
                else:
                    available = [*fact.options, *(f.object for f in facts)]
                # Each option has to name a *different* answer from every other,
                # not merely differ as a string: „Domnul", „Dumnezeu" and
                # „Dumnezeul" are one being under three spellings, so an item
                # offering two of them either has two correct answers or two
                # distractors the student eliminates at once.
                values = _distinct_referents([answer, *(value for value in available if safe(value))], 3)
                if values is None:
                    continue
                if not stems.claim(stem):
                    continue
                chosen = (name, stem, answer, values)
                break
            if not chosen:
                continue
            shape, stem, answer, values = chosen
            letter = letters[len(singles)]
            rng.shuffle(values)
            values.remove(answer)
            values.insert("ABC".index(letter), answer)
            singles.append(SingleChoiceQuestion(f"II-{len(singles) + 1}", stem, dict(zip("ABC", values)), letter, fact.evidence, fact.id))
            used.add(fact.id)
            answers[answer] += 1
            shapes[shape] += 1
        if len(singles) == 10:
            break
    if len(singles) != 10:
        # Section II is where a thin selection almost always runs out, and the
        # bare message gave the reader nothing to act on. Two-chapter selections
        # fail this way about a third of the time while three chapters fail 3%
        # of the time and four fail none — so the shortfall is worth naming
        # alongside the one thing that reliably fixes it.
        raise GenerationError(
            f"Secțiunea II are nevoie de 10 întrebări, dar selecția a produs doar {len(singles)}. "
            "Adăugați încă un capitol la selecție."
        )
    return singles


def _reserve(candidates: list[Fact], count: int, used: set[str], stems: _StemLedger, statement_of) -> list[Fact]:
    """Takes `count` facts whose statements are not near-twins of each other.

    Two passes, like every other quota in this module: near-duplicate rejection
    is a quality preference, so a selection too thin to satisfy it gets the
    relaxed pass (identical text only) rather than a failed generation.
    """
    picked: list[Fact] = []
    for strict in (True, False):
        stems.strict = strict
        for fact in candidates:
            if len(picked) == count:
                break
            if fact.id in used or not (statement := statement_of(fact)) or not stems.claim(statement):
                continue
            picked.append(fact)
            used.add(fact.id)
        if len(picked) == count:
            break
    return picked


def build_test(facts: list[Fact], source: dict[str, list[int]], contest: dict, scoring: dict[str, int], seed: int, version: int, avoid: set[str] | None = None) -> TestDefinition:
    all_facts = facts
    facts = [fact for fact in facts if fact.quality]
    # A complete test spends 28 *distinct* verses — 10 + 10 + 5 + 3 — and every
    # section but Section I's True statements needs a `quality` one (a verse
    # with a recognised name to build an answer around). The old floor of 20
    # let selections through that could not arithmetically produce a test, so
    # they failed several steps later on whichever section happened to run out
    # first: 1 Samuel 28 alone has 22 quality verses and reported "Secțiunea II
    # are nevoie de 10 întrebări, dar selecția a produs doar 2".
    if len(facts) < _QUALITY_ITEMS or len(all_facts) < _TOTAL_ITEMS:
        raise GenerationError(
            f"Un test complet folosește {_TOTAL_ITEMS} versete distincte, dintre care {_QUALITY_ITEMS} trebuie "
            f"să conțină un nume recunoscut. Selecția are {len(all_facts)} versete ({len(facts)} cu nume). "
            "Adăugați încă un capitol la selecție."
        )
    # Not `seed + version`: that makes (seed=100, version=2) and (seed=101,
    # version=1) the same draw, so an off-by-one in the seed silently reissues
    # a paper already handed out. Separating the two axes keeps every
    # (seed, version) pair its own test while staying fully deterministic.
    rng = random.Random(seed * 1_000_003 + version)
    pool = _round_robin(facts, len(facts), rng)
    # Facts already spent by sibling variants of the same paper. Two variants
    # handed to neighbouring students shared a median 6 of their 10 Section II
    # questions, because a different shuffle of the same small eligible set
    # keeps reaching the same verses. Deferring — not excluding — is deliberate:
    # a selection barely able to fill one test must still be able to fill the
    # second, so this only reorders the pool and every downstream sort is
    # stable, which carries the preference into all four sections at once.
    if avoid:
        pool.sort(key=lambda fact: fact.id in avoid)
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
    # Which facts Section II could actually use, computed once and consulted by
    # every section that picks before it. `_uniquely_answered` is applied here
    # too, so a fact whose only shape is an ambiguous „Cine ...?" counts as
    # unusable rather than as a verse worth protecting.
    rivals = _rival_predicates(facts)
    ii_eligible = {
        fact.id for fact in facts
        if _completion_stem(fact)
        or ((wh := _wh_question(fact)) and _uniquely_answered(wh[1], fact.object, rivals))
    }
    # (fact, keeps_gender) for every verse Section I can turn false at all.
    falsifiable = [(fact, found[1]) for fact in pool if (found := _falsifiable(fact, facts))]
    gender_safe = {fact.id for fact, keeps_gender in falsifiable if keeps_gender}
    false_pool = [fact for fact, _ in falsifiable]
    # The False reservation runs before every other section, so on a thin
    # corpus it is the first place a scarce Section II verse can be lost — and
    # unlike Sections III and IV, which were already taught to leave those
    # verses alone, it used to take the pool in plain order. Section I's own
    # requirement is indifferent to which of the qualifying verses it gets, so
    # deferring the II-eligible ones costs it nothing.
    # Gender-safe verses first, then the usual deference to Section II. A swap
    # that breaks agreement („o iubea pe Ana" -> „o iubea pe Elcana") is a
    # visibly broken sentence, so it outranks the pool-sharing preference.
    false_pool.sort(key=lambda fact: (fact.id not in gender_safe, fact.id in ii_eligible))
    # Section I had no near-duplicate check of any kind — `_StemLedger` guarded
    # Sections II and IV only — so two of its ten statements could be the same
    # statement: „Efraim l-a chemat din nou pe Samuel" beside „Atunci Efraim l-a
    # chemat pe Samuel", both keyed F, in one test. The ledger is shared with
    # the later sections rather than private to this one, so a Section I
    # statement and a Section II stem cannot be near-twins either; a fact
    # reserved here is already off the table for them, so sharing costs nothing
    # beyond the overlap check itself.
    #
    # Claimed on the verse as written, before the name swap: the pairs that
    # collide differ only in the words around the name, so the original catches
    # them and the swapped form would not necessarily.
    stems = _StemLedger()
    false_facts = _reserve(false_pool, 5, used, stems, lambda fact: _concise(fact, True))
    if len(false_facts) != 5:
        raise GenerationError(
            f"Secțiunea I are nevoie de 5 afirmații false, dar selecția a produs doar {len(false_facts)}. "
            "Adăugați încă un capitol la selecție."
        )

    # A True statement needs no recognised object at all — it is quoted as-is —
    # so it may use the `quality`-filtered facts every other section needs *and*
    # the ones that lack a known name, which no other section can touch. Those
    # come first for exactly that reason: spending them here is free, while
    # spending a named verse here may be the fact Section II or III needed.
    #
    # Reserved here, beside the False picks, rather than after the other
    # sections have run. Taken last it was the first thing to starve — all 13
    # Section I shortfalls in a 53-selection sweep were True statements failing
    # on a pool the other three sections had already emptied — even though its
    # own requirement is the loosest in the generator and it is indifferent to
    # which verses it gets. The False picks still go first: `_concise(fact,
    # False)` is strictly weaker than False's `_concise(fact, True)` plus
    # `_falsifiable`, so every false_pool fact is also a true_pool fact and
    # choosing True first would eat the shared verses either way.
    non_quality_true = [fact for fact in all_facts if not fact.quality and fact.id not in used and _concise(fact, False)]
    quality_true = [fact for fact in pool if fact.id not in used and _concise(fact, False)]
    quality_true.sort(key=lambda fact: fact.id in ii_eligible)
    true_facts = _reserve(non_quality_true + quality_true, 5, used, stems, lambda fact: _concise(fact, False))
    if len(true_facts) != 5:
        raise GenerationError(
            f"Secțiunea I are nevoie de 5 afirmații adevărate, dar selecția a produs doar {len(true_facts)}. "
            "Adăugați încă un capitol la selecție."
        )

    priority_pool = sorted(pool, key=lambda fact: fact.id in ii_eligible)
    multis = _section_iv(priority_pool, facts, used, rng, stems, rivals, ii_eligible)
    iii_rows = _section_iii_named(priority_pool, used, rng, ii_eligible)
    singles = _section_ii(pool, facts, used, rng, stems, rivals, avoid or set())
    matching = _section_iii_fill(pool, used, rng, iii_rows)

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
            # Whole-word match, like every other name lookup in this module.
            # `str.rpartition` matches a bare substring, so on a sentence that
            # names „Domnul" and later „Domnului" it split inside the longer
            # word — replacing the last five letters of an inflected form
            # („unsul Domnului" -> „unsul Dumnezeului") instead of swapping the
            # subject the statement is actually about. That yields a sentence
            # which is neither the verse nor a clean falsehood: the claim the
            # student is asked to judge is still true, only misspelled.
            hits = list(re.finditer(rf"(?<!\w){re.escape(fact.object)}(?!\w)", statement))
            if not hits:
                raise GenerationError(f"Statementul pentru {fact.id} nu mai conține obiectul de înlocuit.")
            hit = hits[-1]
            before, after = statement[:hit.start()], statement[hit.end():]
            replacement = _wrong_object(fact, facts, lead=before, statement=f"{before} {after}", require_gender=fact.id in gender_safe)
            statement = before + replacement + after
        tf.append(TrueFalseQuestion(f"I-{index}", statement, "A" if is_true else "F", fact.evidence, fact.id))

    return TestDefinition(source, seed, version, contest, scoring, section_i=tf, section_ii=singles, section_iii=matching, section_iv=multis)
