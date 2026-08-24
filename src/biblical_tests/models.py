from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Literal


Answer = Literal["A", "B", "C"]


@dataclass(frozen=True)
class Evidence:
    book: str
    chapter: int
    verse_start: int
    verse_end: int
    text: str

    @property
    def reference(self) -> str:
        verses = str(self.verse_start) if self.verse_start == self.verse_end else f"{self.verse_start}-{self.verse_end}"
        return f"{self.book} {self.chapter}:{verses}"


@dataclass(frozen=True)
class Fact:
    id: str
    statement: str
    subject: str
    predicate: str
    object: str
    evidence: Evidence
    options: tuple[str, ...] = ()
    quality: bool = True


@dataclass(frozen=True)
class TrueFalseQuestion:
    id: str
    statement: str
    answer: Literal["A", "F"]
    evidence: Evidence
    fact_id: str


@dataclass(frozen=True)
class SingleChoiceQuestion:
    id: str
    question: str
    options: dict[Answer, str]
    correct: Answer
    evidence: Evidence
    fact_id: str


@dataclass(frozen=True)
class MatchingQuestion:
    id: str
    left: list[str]
    right: dict[str, str]
    answers: dict[str, str]
    evidence: list[Evidence]
    fact_ids: list[str]


@dataclass(frozen=True)
class MultiChoiceQuestion:
    id: str
    question: str
    options: dict[Answer, str]
    correct: list[Answer]
    evidence: Evidence
    fact_id: str
    supporting_evidence: list[Evidence] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)


@dataclass
class TestDefinition:
    source: dict[str, list[int]]
    seed: int
    version: int
    contest: dict[str, object]
    scoring: dict[str, int]
    generated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    generator: str = "deterministic-fact-generator"
    section_i: list[TrueFalseQuestion] = field(default_factory=list)
    section_ii: list[SingleChoiceQuestion] = field(default_factory=list)
    section_iii: MatchingQuestion | None = None
    section_iv: list[MultiChoiceQuestion] = field(default_factory=list)

    @property
    def total_points(self) -> int:
        return 10 * self.scoring["section_1"] + 10 * self.scoring["section_2"] + 5 * self.scoring["section_3"] + 3 * self.scoring["section_4"]

    def to_dict(self) -> dict:
        return asdict(self) | {"total_points": self.total_points}
