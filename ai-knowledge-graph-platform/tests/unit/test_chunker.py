"""graphrag.ingestion.chunker — section-aware splitting.

No test file for this module existed before this one, despite it being the
first stage of the ingestion pipeline. These cases pin down a real defect
found and fixed in the aerospace corpus: `_HEADING_RE` only recognised
markdown and numbered headings, so any document using plain un-numbered
ALL-CAPS section titles (routine in compliance/regulatory reports) fell
entirely to naive fixed-size splitting — see CON-02 in evals/golden_set.json
for the retrieval failure this caused.
"""

from __future__ import annotations

from datetime import datetime, timezone

from graphrag.core.models import Document
from graphrag.ingestion.chunker import _HEADING_RE, chunk_document


def _doc(raw_text: str) -> Document:
    return Document(
        filename="test.txt",
        source_path="test.txt",
        raw_text=raw_text,
        tenant="test",
        ingested_at=datetime.now(timezone.utc),
    )


class TestHeadingDetection:
    def test_matches_markdown_heading(self):
        assert _HEADING_RE.search("## Overview\n")

    def test_matches_numbered_all_caps_heading(self):
        assert _HEADING_RE.search("3. BUDGET & KPI TARGETS\n")

    def test_matches_numbered_heading_with_a_decimal(self):
        """Regression: the numbered branch's char class previously excluded
        '.', so "3. CHANGES IN VERSION 2.0" (a real corpus heading) silently
        failed to match — a pre-existing gap fixed alongside the un-numbered
        branch."""
        assert _HEADING_RE.search("3. CHANGES IN VERSION 2.0\n")

    def test_matches_plain_all_caps_title(self):
        assert _HEADING_RE.search("CRITICAL FINDING\n")

    def test_matches_all_caps_title_with_trailing_colon(self):
        assert _HEADING_RE.search("REQUIRED ACTIONS:\n")

    def test_matches_all_caps_title_with_em_dash(self):
        assert _HEADING_RE.search("CRITICAL FINDING — NON-COMPLIANCE IDENTIFIED\n")

    def test_rejects_prose_sentence(self):
        assert not _HEADING_RE.match("FAA has approved an AMOC for this product.")

    def test_rejects_all_caps_metadata_line(self):
        """'KEY: value' (trailing content after the colon) must not be treated
        as a section title — only a bare title ending in ':' should match."""
        assert not _HEADING_RE.match("MSN: 44567")

    def test_rejects_bullet_list_item(self):
        assert not _HEADING_RE.match("- 737-700, 737-800, 737-900ER")


class TestSectionSoftCap:
    def test_modestly_oversized_section_stays_whole(self):
        """A section slightly over chunk_size must not be split at whatever
        paragraph boundary happens to fall nearest the budget — that boundary
        is often between two paragraphs that belong together (see CON-02:
        a compliance status line and the sentence immediately qualifying it,
        split into separate chunks that never reach the LLM together)."""
        heading = "CRITICAL FINDING\n\n"
        # ~150 chars of "status" content + ~150 chars of qualifying "note" —
        # comfortably over chunk_size=512 once combined with padding below,
        # but well under a 1.6x soft cap.
        status = "Status: IS_NON_COMPLIANT_WITH. " + ("Detail line. " * 20)
        note = "NOTE: non-compliance is anticipated; the aircraft remains airworthy until the deadline. " * 3
        section = heading + status + "\n\n" + note
        assert 512 < len(section) <= 512 * 1.6, f"fixture must land in the soft-cap gap, got {len(section)} chars"

        doc = _doc(section)
        chunks = chunk_document(doc)

        assert len(chunks) == 1
        assert "IS_NON_COMPLIANT_WITH" in chunks[0].text
        assert "remains airworthy" in chunks[0].text

    def test_genuinely_long_section_still_gets_split(self):
        """The soft cap must not disable splitting entirely — a section far
        beyond the cap still needs to be broken up."""
        heading = "APPENDIX\n\n"
        body = "This is one sentence of filler content. " * 100  # ~4200 chars
        doc = _doc(heading + body)

        chunks = chunk_document(doc)

        assert len(chunks) > 1
        assert all(len(c.text) <= 512 * 1.6 + 100 for c in chunks)  # small margin for prepended heading

    def test_heading_is_prepended_to_each_split_piece(self):
        heading = "APPENDIX\n\n"
        body = "This is one sentence of filler content. " * 100
        doc = _doc(heading + body)

        chunks = chunk_document(doc)

        assert all(c.text.startswith("APPENDIX") for c in chunks)


class TestUnheadedDocument:
    def test_document_with_no_headings_falls_back_to_fixed_size_splitting(self):
        body = "Filler prose with no section titles at all. " * 40
        doc = _doc(body)

        chunks = chunk_document(doc)

        assert len(chunks) >= 1
        assert all(len(c.text) <= 512 * 1.6 for c in chunks)
