from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Evidence, Fact
from .selection import SelectionError


# A verse opens with its number followed by the first word of the verse. The
# lookahead lists every character the corpus actually starts a verse with — a
# letter, or one of the marks that open reported speech. „«" was missing, so
# 1 Samuel 24:13 („«Răul de la cei răi vine», zice vechea zicală.") was never
# recognised as a verse at all and its text was silently absorbed into verse
# 12, which then carried a reference covering words it did not contain.
_VERSE_MARKER = r'(?<![\w-])(\d{1,3})\s+(?=[A-Za-zĂÂÎȘȚăâîșț„"«])'


class BibleRepository:
    """A local, auditable Bible corpus. It never downloads a translation."""

    PEOPLE = {"Ana", "Penina", "Elcana", "Eli", "Hofni", "Fineas", "Samuel", "David", "Saul", "Ionatan", "Abner", "Ioab", "Natan", "Absalom", "Mical", "Batșeba", "Goliat", "Isai", "Chis", "Ahimelec", "Dagon", "Iosua"}
    PLACES = {"Silo", "Rama", "Efraim", "Israel", "Filisteni", "Filistenii", "Ghilboa", "Ierusalim", "Hebron", "Betleem", "Gat", "Mițpa", "Iordan", "Iuda", "Ecron", "Asdod", "Gaza", "Ascalon"}
    DEITY = {"Domnul", "Domnului", "Dumnezeu", "Dumnezeul"}
    QUALITY_TERMS = PEOPLE | PLACES | DEITY

    def __init__(self, corpus_path: str | Path):
        self.path = Path(corpus_path)
        if not self.path.exists():
            raise FileNotFoundError(f"Lipsește corpusul biblic local: {self.path}")
        if self.path.is_dir():
            self.data = self._read_markdown_directory()
        elif self.path.suffix.lower() == ".md":
            self.data = self._read_markdown_files([self.path])
        elif self.path.suffix.lower() == ".js":
            self.data = self._read_js_corpus()
        else:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.translation = self.data.get("translation", "Nespecificată")
        self.books = self.data.get("books", self.data)
        self.facts = self._read_facts(self.data.get("facts", []))

    def _read_markdown_directory(self) -> dict:
        files = sorted(self.path.glob("*samuel-reference-text.md"))
        if not files:
            raise FileNotFoundError(f"Nu există corpusuri Markdown în: {self.path}")
        return self._read_markdown_files(files)

    @staticmethod
    def _strip_headings(body: list[str]) -> list[str]:
        """Drops the source's section headings, keeping only verse text.

        A heading is identified structurally — it is the first line of a
        paragraph — rather than by what it looks like, because the two are not
        distinguishable by shape. The previous rule popped any trailing line
        made only of letters, spaces and hyphens, which had both failure
        directions at once: a heading carrying a comma („Saul, ales împărat
        prin sorți") survived and was glued onto the end of the preceding
        verse, while a *verse* that happened to contain no other punctuation
        („17 Samuel a chemat poporul înaintea Domnului la Mițpa") matched the
        pattern and was deleted outright. 1 Samuel 10:17 and 2 Samuel 1:17
        were lost that way, and 34 verses carried a heading they never
        contained.

        The structural rule is exact for this corpus: the source breaks a
        paragraph only at a heading, and no heading begins with a verse
        number, so „first line after a blank one" selects all 116 of them and
        nothing else. Note a heading can land *inside* a verse (1 Samuel 1:19
        is split across „Nașterea lui Samuel"), so this runs before verse
        markers are located rather than per-verse afterwards.
        """
        return [
            line for index, line in enumerate(body)
            if line.strip() and index and body[index - 1].strip()
        ]

    def _verify_structure(self, books: dict, folder: Path) -> None:
        """Checks the parse against an external verse-count table.

        `validate_evidence` compares each question's evidence against
        `get_verse`, but both sides come out of this same parser — so a
        parsing defect validates itself as correct and the audit trail the
        whole generator rests on proves nothing. This is the one check with a
        source of truth outside the parse: a hand-written table of how many
        verses each chapter has. It is what makes a silently dropped or
        merged verse fail loudly instead of quietly shipping on a test paper.
        """
        manifest = folder / "verse-counts.json"
        if not manifest.exists():
            return
        expected = json.loads(manifest.read_text(encoding="utf-8"))["chapters"]
        problems = []
        for book, counts in expected.items():
            if book not in books:
                continue
            for index, count in enumerate(counts, 1):
                found = books[book].get(str(index), {})
                missing = [n for n in range(1, count + 1) if str(n) not in found]
                if missing:
                    problems.append(f"{book} {index}: lipsesc versetele {missing}")
                extra = sorted(int(n) for n in found if int(n) > count)
                if extra:
                    problems.append(f"{book} {index}: versete în plus {extra}")
            if len(books[book]) > len(counts):
                problems.append(f"{book}: {len(books[book])} capitole, se așteptau {len(counts)}")
        if problems:
            raise ValueError(
                "Corpusul parsat nu corespunde structurii din verse-counts.json:\n  "
                + "\n  ".join(problems)
            )

    def _read_markdown_files(self, files: list[Path]) -> dict:
        books: dict[str, dict[str, dict[str, str]]] = {}
        records: list[tuple[str, int, int, str]] = []
        for file in files:
            book_match = re.match(r"([12])samuel-reference-text\.md", file.name, re.IGNORECASE)
            if not book_match:
                continue
            book = f"{book_match.group(1)} Samuel"
            raw = "\n".join(self._strip_headings(file.read_text(encoding="utf-8").splitlines()[1:]))
            markers = list(re.finditer(_VERSE_MARKER, raw))
            if not markers:
                raise ValueError(f"Nu am putut identifica versete în: {file}")
            chapter, last_verse = 1, 0
            for position, marker in enumerate(markers):
                verse = int(marker.group(1))
                if verse == 1 and last_verse:
                    chapter += 1
                end = markers[position + 1].start() if position + 1 < len(markers) else len(raw)
                text = " ".join(raw[marker.end():end].split())
                if not text:
                    continue
                books.setdefault(book, {}).setdefault(str(chapter), {})[str(verse)] = text
                records.append((book, chapter, verse, text))
                last_verse = verse
        self._verify_structure(books, files[0].parent)
        preferred = self._preferred_terms([record[3] for record in records])
        raw_facts = [{"id": f"{book}-{chapter}-{verse}", "statement": text, "subject": f"versetul {book} {chapter}:{verse}", "predicate": "completează", "object": self._extract_target(text, preferred), "evidence": {"book": book, "chapter": chapter, "verse_start": verse, "verse_end": verse, "text": text}} for book, chapter, verse, text in records]
        for fact in raw_facts:
            fact["quality"] = fact["object"] in self.QUALITY_TERMS
        for fact in raw_facts:
            options = [fact["object"]]
            group = self._term_group(fact["object"])
            for target in (item["object"] for item in raw_facts if item["quality"] and self._term_group(item["object"]) == group):
                if target != fact["object"] and target not in options:
                    options.append(target)
                if len(options) == 3:
                    break
            fact["options"] = options
        return {"translation": "Cornilescu, primit de la user, 2026-08-31", "books": books, "facts": raw_facts}

    @classmethod
    def _term_group(cls, value: str) -> str:
        if value in cls.PEOPLE:
            return "person"
        if value in cls.PLACES:
            return "place"
        if value in cls.DEITY:
            return "deity"
        return "other"

    @staticmethod
    def _preferred_terms(texts: list[str]) -> set[str]:
        ignored = {"Era", "El", "Dar", "Și", "Nu", "Când", "Atunci", "De", "Iată", "Acum", "În", "Oare", "Apoi", "După", "Pentru", "Cum", "Unde", "Ce", "Să", "Omul", "Ori", "Potrivnica", "Chiar", "Fiindcă", "Acolo", "Așa", "Cei", "Fiii", "Tinerii", "Femeia", "Femeile", "Poporul", "Israelul", "Vrăjmașii"}
        counts: dict[str, int] = {}
        for text in texts:
            for match in re.finditer(r"\b[A-ZĂÂÎȘȚ][a-zăâîșț]+\b", text):
                if match.start() and match.group(0) not in ignored:
                    counts[match.group(0)] = counts.get(match.group(0), 0) + 1
        return {value for value, count in counts.items() if count >= 2}

    @staticmethod
    def _extract_target(text: str, preferred: set[str]) -> str:
        ignored = {"Era", "El", "Dar", "Și", "Nu", "Când", "Atunci", "De", "Iată", "Acum", "În", "Oare", "Apoi", "După", "Pentru", "Cum", "Unde", "Ce", "Să", "Omul", "Ori", "Potrivnica", "Chiar", "Fiindcă", "Acolo", "Așa", "Cei", "Fiii", "Tinerii", "Femeia", "Femeile", "Poporul", "Israelul", "Vrăjmașii"}
        terms = re.findall(r"\b[A-ZĂÂÎȘȚ][a-zăâîșț]+\b", text)
        quality = [value for value in terms if value in BibleRepository.QUALITY_TERMS]
        if quality:
            # "Domnului" is the genitive/dative case of "Domnul" — it can never
            # be a sentence's subject or a name safely swapped in elsewhere, so
            # a fact built around it is unusable for most question shapes. In
            # narratives where "chivotul Domnului" repeats constantly it's also
            # almost always the *first* capitalised word, so picking quality[0]
            # unconditionally locks a large share of verses out of every shape
            # that needs the object to be a plain, swappable name — even when
            # the same verse names someone else too. Prefer any other quality
            # term the verse offers; fall back to "Domnului" only when it's the
            # sole one present.
            non_oblique = [value for value in quality if value != "Domnului"]
            return non_oblique[0] if non_oblique else quality[0]
        proper = [value for value in terms if value in preferred and value not in ignored]
        if proper:
            return proper[0]
        words = re.findall(r"\b[a-zăâîșțA-ZĂÂÎȘȚ]{5,}\b", text)
        if not words:
            raise ValueError(f"Nu pot extrage un răspuns verificabil din verset: {text!r}")
        return max(words, key=len)

    def _read_js_corpus(self) -> dict:
        raw = self.path.read_text(encoding="utf-8")
        entries = []
        pattern = re.compile(r'\{\s*ref:\s*"((?:\\.|[^"\\])*)",\s*text:\s*"((?:\\.|[^"\\])*)",\s*blanks:\s*\[\{\s*answer:\s*"((?:\\.|[^"\\])*)",\s*options:\s*\[((?:.|\n)*?)\]\s*\}\],\s*\}', re.DOTALL)
        for match in pattern.finditer(raw):
            ref, template, answer, options_raw = match.groups()
            decode = lambda value: bytes(value, "utf-8").decode("unicode_escape").encode("latin1", "backslashreplace").decode("utf-8", "replace") if "\\" in value else value
            ref, template, answer = decode(ref), decode(template), decode(answer)
            options = tuple(decode(item) for item in re.findall(r'"((?:\\.|[^"\\])*)"', options_raw))
            book_chapter, verse = ref.rsplit(":", 1)
            book, chapter = book_chapter.rsplit(" ", 1)
            filled = template.replace("{0}", answer)
            entries.append((book, int(chapter), int(verse), filled, answer, options))
        if not entries:
            raise ValueError(f"Nu am putut citi corpusul JS: {self.path}")
        books: dict[str, dict[str, dict[str, str]]] = {}
        facts = []
        for book, chapter, verse, text, answer, options in entries:
            books.setdefault(book, {}).setdefault(str(chapter), {})[str(verse)] = text
            facts.append({"id": f"{book}-{chapter}-{verse}", "statement": text, "subject": f"versetul {book} {chapter}:{verse}", "predicate": "conține răspunsul", "object": answer, "options": list(options), "evidence": {"book": book, "chapter": chapter, "verse_start": verse, "verse_end": verse, "text": text}})
        return {"translation": f"Corpus JS local: {self.path.name}", "books": books, "facts": facts}

    def _read_facts(self, values: list[dict]) -> list[Fact]:
        facts = []
        for raw in values:
            ref = raw["evidence"]
            evidence = Evidence(ref["book"], int(ref["chapter"]), int(ref["verse_start"]), int(ref.get("verse_end", ref["verse_start"])), ref["text"])
            facts.append(Fact(raw["id"], raw["statement"], raw["subject"], raw["predicate"], raw["object"], evidence, tuple(raw.get("options", [])), bool(raw.get("quality", True))))
        return facts

    def get_chapter(self, book: str, chapter: int) -> dict[str, str]:
        return self.books[book][str(chapter)]

    def get_verse(self, book: str, chapter: int, verse: int) -> str:
        return self.get_chapter(book, chapter)[str(verse)]

    def get_passage(self, selection: dict[str, list[int]]) -> dict[str, dict[str, dict[str, str]]]:
        return {book: {str(ch): self.get_chapter(book, ch) for ch in chapters} for book, chapters in selection.items()}

    def facts_for(self, selection: dict[str, list[int]]) -> list[Fact]:
        # `parse_selection` accepts every book the aliases know, but the local
        # corpus holds only what was actually loaded. Without this the request
        # simply yields no facts and surfaces several layers later as "the
        # selection needs at least 20 verified facts", which points the reader
        # at the chapter range rather than at the book that isn't there.
        self._require_in_corpus(selection)
        return [fact for fact in self.facts if fact.evidence.book in selection and fact.evidence.chapter in selection[fact.evidence.book]]

    def _require_in_corpus(self, selection: dict[str, list[int]]) -> None:
        available = ", ".join(sorted(self.books))
        for book, chapters in selection.items():
            if book not in self.books:
                raise SelectionError(f"Cartea „{book}” nu există în corpusul local. Disponibile: {available}.")
            missing = [str(chapter) for chapter in chapters if str(chapter) not in self.books[book]]
            if missing:
                last = max(int(number) for number in self.books[book])
                raise SelectionError(f"„{book}” nu are capitolul {', '.join(missing)} (are 1-{last}).")
