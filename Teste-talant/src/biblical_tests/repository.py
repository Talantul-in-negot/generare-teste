from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Evidence, Fact


class BibleRepository:
    """A local, auditable Bible corpus. It never downloads a translation."""

    PEOPLE = {"Ana", "Penina", "Elcana", "Eli", "Hofni", "Fineas", "Samuel", "David", "Saul", "Ionatan", "Abner", "Ioab", "Natan", "Absalom", "Mical", "Batșeba", "Goliat", "Isai", "Chis", "Ahimelec", "Dagon", "Iosua"}
    PLACES = {"Silo", "Rama", "Efraim", "Israel", "Filisteni", "Filistenii", "Ghilboa", "Ierusalim", "Hebron", "Betleem", "Gat", "Rama", "Mițpa", "Iordan", "Iuda", "Ecron", "Asdod", "Gaza", "Ascalon"}
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

    def _read_markdown_files(self, files: list[Path]) -> dict:
        books: dict[str, dict[str, dict[str, str]]] = {}
        records: list[tuple[str, int, int, str]] = []
        for file in files:
            book_match = re.match(r"([12])samuel-reference-text\.md", file.name, re.IGNORECASE)
            if not book_match:
                continue
            book = f"{book_match.group(1)} Samuel"
            raw = "\n".join(file.read_text(encoding="utf-8").splitlines()[1:])
            markers = list(re.finditer(r'(?<![\w-])(\d{1,3})\s+(?=[A-Za-zĂÂÎȘȚăâîșț„"])', raw))
            if not markers:
                raise ValueError(f"Nu am putut identifica versete în: {file}")
            chapter, last_verse = 1, 0
            for position, marker in enumerate(markers):
                verse = int(marker.group(1))
                if verse == 1 and last_verse:
                    chapter += 1
                end = markers[position + 1].start() if position + 1 < len(markers) else len(raw)
                excerpt = raw[marker.end():end].strip()
                lines = excerpt.splitlines()
                while lines and re.fullmatch(r"[A-ZĂÂÎȘȚ][A-Za-zĂÂÎȘȚăâîșț -]{2,}", lines[-1].strip()):
                    lines.pop()
                text = " ".join(" ".join(lines).split())
                if not text:
                    continue
                books.setdefault(book, {}).setdefault(str(chapter), {})[str(verse)] = text
                records.append((book, chapter, verse, text))
                last_verse = verse
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
        return [fact for fact in self.facts if fact.evidence.book in selection and fact.evidence.chapter in selection[fact.evidence.book]]
