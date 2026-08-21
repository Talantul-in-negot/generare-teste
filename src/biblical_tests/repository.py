from __future__ import annotations

import json
from pathlib import Path

from .models import Evidence, Fact


class BibleRepository:
    """A local, auditable Bible corpus. It never downloads a translation."""

    def __init__(self, corpus_path: str | Path):
        self.path = Path(corpus_path)
        if not self.path.exists():
            raise FileNotFoundError(f"Lipsește corpusul biblic local: {self.path}")
        self.data = json.loads(self.path.read_text(encoding="utf-8"))
        self.translation = self.data.get("translation", "Nespecificată")
        self.books = self.data.get("books", self.data)
        self.facts = self._read_facts(self.data.get("facts", []))

    def _read_facts(self, values: list[dict]) -> list[Fact]:
        facts = []
        for raw in values:
            ref = raw["evidence"]
            evidence = Evidence(ref["book"], int(ref["chapter"]), int(ref["verse_start"]), int(ref.get("verse_end", ref["verse_start"])), ref["text"])
            facts.append(Fact(raw["id"], raw["statement"], raw["subject"], raw["predicate"], raw["object"], evidence))
        return facts

    def get_chapter(self, book: str, chapter: int) -> dict[str, str]:
        return self.books[book][str(chapter)]

    def get_verse(self, book: str, chapter: int, verse: int) -> str:
        return self.get_chapter(book, chapter)[str(verse)]

    def get_passage(self, selection: dict[str, list[int]]) -> dict[str, dict[str, dict[str, str]]]:
        return {book: {str(ch): self.get_chapter(book, ch) for ch in chapters} for book, chapters in selection.items()}

    def facts_for(self, selection: dict[str, list[int]]) -> list[Fact]:
        return [fact for fact in self.facts if fact.evidence.book in selection and fact.evidence.chapter in selection[fact.evidence.book]]
