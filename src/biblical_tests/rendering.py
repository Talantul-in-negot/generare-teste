from __future__ import annotations

import html
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from .models import Evidence, TestDefinition


RED = colors.HexColor("#ff0000")


def _register_fonts() -> tuple[str, str, str]:
    candidates = [
        Path("C:/Windows/Fonts/calibri.ttf"), Path("C:/Windows/Fonts/arial.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ]
    bold = [Path("C:/Windows/Fonts/calibrib.ttf"), Path("C:/Windows/Fonts/arialbd.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")]
    italic = [Path("C:/Windows/Fonts/calibrii.ttf"), Path("C:/Windows/Fonts/ariali.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf")]
    regular = next((item for item in candidates if item.exists()), None)
    bold_path = next((item for item in bold if item.exists()), regular)
    italic_path = next((item for item in italic if item.exists()), regular)
    if regular is None:
        return "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
    for name, path in [("Talant", regular), ("Talant-Bold", bold_path), ("Talant-Italic", italic_path)]:
        if name not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont(name, str(path)))
    return "Talant", "Talant-Bold", "Talant-Italic"


def _styles() -> dict[str, ParagraphStyle]:
    regular, bold, italic = _register_fonts()
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle("body", parent=base["Normal"], fontName=regular, fontSize=10.5, leading=13, spaceAfter=0),
        "small": ParagraphStyle("small", parent=base["Normal"], fontName=regular, fontSize=9.8, leading=12),
        "header": ParagraphStyle("header", parent=base["Normal"], fontName=bold, fontSize=10.5, leading=13),
        "section": ParagraphStyle("section", parent=base["Normal"], fontName=bold, fontSize=10.5, leading=13, spaceBefore=8, spaceAfter=3),
        "red": ParagraphStyle("red", parent=base["Normal"], fontName=italic, textColor=RED, fontSize=9.7, leading=12, alignment=TA_RIGHT),
        "correct": ParagraphStyle("correct", parent=base["Normal"], fontName=italic, textColor=RED, fontSize=10.5, leading=13),
    }


def _p(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(html.escape(text).replace("\n", "<br/>"), style)


def _reference(evidence: Evidence, styles: dict) -> Paragraph:
    return _p(evidence.reference, styles["red"])


def _header(test: TestDefinition, styles: dict) -> list:
    c = test.contest
    category = str(c.get("category", ""))
    header = Table([
        [_p(f"Categoria {category}", styles["header"]),
         _p(f"{c.get('title', '')}\n{'Ediția ' + str(c.get('edition', ''))}\n\n{c.get('stage', '')}\n{c.get('date', '')}", ParagraphStyle("center", parent=styles["header"], alignment=TA_CENTER)),
         _p(f"Versiune Test:     {test.version}", ParagraphStyle("right", parent=styles["header"], alignment=TA_RIGHT))]
    ], colWidths=[62*mm, 66*mm, 62*mm])
    header.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP")]))
    return [header, Spacer(1, 9*mm)]


def _line() -> HRFlowable:
    return HRFlowable(width="100%", thickness=0.75, color=colors.black, spaceBefore=1, spaceAfter=1)


def _tf_item(index: int, q, answer_key: bool, styles: dict):
    answer = q.answer if answer_key else ""
    answer_style = styles["correct"] if answer_key else styles["body"]
    table = Table([[_p(f"{index}.", styles["body"]), _p(answer, answer_style), _p(q.statement, styles["body"]), _reference(q.evidence, styles)]], colWidths=[7*mm, 8*mm, 133*mm, 42*mm])
    table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"), ("ALIGN", (0, 0), (1, 0), "CENTER"),
        ("BOX", (1, 0), (1, 0), 0.8, colors.black), ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1),
        ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return KeepTogether([table, _line()])


def _choice_item(index: int, q, answer_key: bool, styles: dict):
    rows = [[_p(f"{index}.", styles["body"]), _p(q.question, styles["body"]), _reference(q.evidence, styles)]]
    for letter in "ABC":
        style = styles["correct"] if answer_key and letter in q.correct else styles["body"]
        content = f"{letter}    {q.options[letter]}"
        rows.append(["", _p(content, style), ""])
    table = Table(rows, colWidths=[10*mm, 138*mm, 42*mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return KeepTogether([table, _line()])


def _matching(q, answer_key: bool, styles: dict):
    rows = []
    refs = "; ".join(item.reference for item in q.evidence)
    for i, left in enumerate(q.left, 1):
        answer = q.answers[str(i)] if answer_key else ""
        answer_style = styles["correct"] if answer_key else styles["body"]
        letter = chr(64 + i)
        rows.append([_p(f"{i}.", styles["body"]), _p(answer, answer_style), _p(left, styles["body"]), _p(f"{letter}.    {q.right[letter]}", styles["body"]), _p(q.evidence[i-1].reference, styles["red"])])
    # Keep the answer boxes as individual cells and move the A-E column
    # farther right, matching the reference form's wider central text area.
    table = Table(rows, colWidths=[7*mm, 8*mm, 70*mm, 63*mm, 42*mm])
    table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("GRID", (1, 0), (1, -1), 0.8, colors.black), ("ALIGN", (0, 0), (1, -1), "CENTER"), ("LEFTPADDING", (0, 0), (-1, -1), 1), ("RIGHTPADDING", (0, 0), (-1, -1), 1), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]))
    return table


def render_pdf(test: TestDefinition, path: str | Path, answer_key: bool = False) -> Path:
    styles = _styles()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=10*mm, rightMargin=10*mm, topMargin=11*mm, bottomMargin=10*mm, title="Talantul în Negoț")
    story = _header(test, styles)
    s = test.scoring
    story += [_p(f"I  Marcați răspunsul corect pe foaia cu răspunsuri, A (adevărat) sau F (fals): (câte {s['section_1']} puncte fiecare)", styles["section"]), _line()]
    story += [_tf_item(i, q, answer_key, styles) for i, q in enumerate(test.section_i, 1)]
    story += [_p(f"II  Marcați litera corespunzătoare răspunsului corect pe foaia cu răspunsuri: (câte {s['section_2']} puncte fiecare)\n(doar un răspuns corect)", styles["section"]), _line()]
    story += [_choice_item(i, q, answer_key, styles) for i, q in enumerate(test.section_ii, 1)]
    story += [_p(f"III  Faceți asocierea și marcați litera corespunzătoare pe foaia cu răspunsuri: (câte {s['section_3']} puncte fiecare)", styles["section"]), _line(), _matching(test.section_iii, answer_key, styles)]
    iv_items = [_choice_item(i, q, answer_key, styles) for i, q in enumerate(test.section_iv, 1)]
    story += [KeepTogether([_p(f"IV  Marcați litera corespunzătoare răspunsului corect pe foaia cu răspunsuri: (câte {s['section_4']} puncte fiecare)\n(poate fi unul, două, trei sau nici un răspuns corect)", styles["section"]), _line(), iv_items[0]])]
    story += iv_items[1:]
    doc.build(story)
    return path


def render_pair(test: TestDefinition, directory: str | Path) -> tuple[Path, Path]:
    folder = Path(directory)
    labels = []
    for book, chapters in test.source.items():
        ranges = []
        start = previous = chapters[0]
        for chapter in chapters[1:]:
            if chapter == previous + 1:
                previous = chapter
                continue
            ranges.append(str(start) if start == previous else f"{start}-{previous}")
            start = previous = chapter
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        labels.append(f"{book} {','.join(ranges)}")
    label = "; ".join(labels)
    competitor = render_pdf(test, folder / f"{label}.pdf")
    key = render_pdf(test, folder / f"{label} barem.pdf", answer_key=True)
    return competitor, key
