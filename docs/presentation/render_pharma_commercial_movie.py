"""Render a static-frame English presentation from the live pharma capture."""

from __future__ import annotations

import json
import math
import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont

from graphrag.context_graph.models import ContextManifest


W, H, FPS = 1280, 720, 24
ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "pharma_commercial_movie_build"
OUT = ROOT / "pharma_commercial_kg_context_graph_en.mp4"
CAPTURE = json.loads((ROOT / "pharma_commercial_movie_trace.json").read_text(encoding="utf-8"))
RESULT = CAPTURE["query_response"]
TRACE = CAPTURE["trace"]
COUNTS = CAPTURE["graph_counts"]
POLICY = CAPTURE["policy"]
CASE = TRACE["case"]
RUN = TRACE["run"]
MANIFEST = dict(TRACE["manifest"])
if isinstance(MANIFEST.get("retrieval_config"), str):
    MANIFEST["retrieval_config"] = json.loads(MANIFEST["retrieval_config"])
DECISION = TRACE["decision"]
DOCUMENTS = {item["id"]: item for item in TRACE.get("documents", []) if item}
HASH_VALID = ContextManifest.model_validate(MANIFEST).compute_integrity_hash() == MANIFEST["integrity_hash"]

BG = (7, 17, 30)
PANEL = (12, 32, 48)
WHITE = (242, 248, 252)
MUTED = (153, 180, 198)
CYAN = (96, 222, 255)
GOLD = (255, 201, 92)
GREEN = (79, 212, 151)
RED = (255, 111, 118)


@dataclass(frozen=True)
class Scene:
    title: str
    duration: int
    voiceover: str


SCENES = [
    Scene("The Business Question", 18, "Commercial teams need approved content for a precise interaction, not a plausible answer. This synthetic demonstration asks which CardioDemo content may be used for a cardiology specialist in Germany, for a defined synthetic indication."),
    Scene("Governed Source Corpus", 18, "The tenant starts with a small but realistic governed corpus: label, claims, HCP profile, campaign, policy, and two content versions. Each source is synthetic and versioned before it becomes retrievable evidence."),
    Scene("Commercial Ontology", 22, "The ontology makes the commercial vocabulary explicit. A product treats an indication. A professional has a specialty. Content is approved for a market. These are graph constraints, not instructions hidden in a prompt."),
    Scene("Validation and Semantic Export", 18, "The schema rejects a relationship that violates its domain and range. The same graph exports to RDF and its structure is checked by SHACL, providing an independent semantic control."),
    Scene("Live Hybrid Retrieval", 28, "The normal application path retrieves evidence through vector and lexical retrieval, cross-encoder reranking, graph expansion, and graph-aware scoring. It connects the question to product, market, specialty, policy, and content evidence."),
    Scene("Grounded Answer", 24, "The system recommends the current Germany Cardiology Detail Aid. The response is grounded in the approved content and its supporting campaign, label, HCP profile, and claims. The expired version is not silently treated as equivalent."),
    Scene("Deterministic Policy Result", 24, "Policy evaluation is deterministic. The current version is allowed because product, indication, market, specialty, validity, and evidence match. The prior revision is denied because it is expired. This is commercial content governance, not medical advice."),
    Scene("Optional Context Graph Trace", 23, "The Knowledge Graph makes the selection defensible. The optional Context Graph makes the agent run auditable: which case was handled, which evidence was available, what it selected, and the exact immutable manifest used for that retrieval."),
    Scene("Precise and Auditable", 18, "Together, the Knowledge Graph governs what is known and allowed. The Context Graph records what an AI system actually used and concluded. That makes commercial AI more precise, inspectable, and easier to govern."),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf", size)


def text(draw, xy, value: str, size: int = 20, color=WHITE, bold: bool = False, anchor=None) -> None:
    draw.text(xy, str(value), font=font(size, bold), fill=color, anchor=anchor)


def wrapped(draw, xy, value: str, width: int, size: int = 20, color=WHITE, bold: bool = False) -> None:
    draw.multiline_text(xy, textwrap.fill(str(value), width), font=font(size, bold), fill=color, spacing=7)


def panel(draw, box, outline=(45, 95, 116), fill=PANEL, radius: int = 9, width: int = 2) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def arrow(draw, start, end, color=CYAN, width: int = 3) -> None:
    draw.line((*start, *end), fill=color, width=width)
    x, y = end
    draw.polygon([(x, y), (x - 12, y - 6), (x - 12, y + 6)], fill=color)


def node(draw, xy, label: str, sub: str = "", color=CYAN, radius: int = 52) -> None:
    x, y = xy
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(9, 35, 52), outline=color, width=3)
    text(draw, (x, y - 8), label, 14, color, True, "mm")
    if sub:
        text(draw, (x, y + 15), sub, 11, MUTED, False, "mm")


def short(value: object, length: int = 34) -> str:
    value = str(value or "n/a")
    return value if len(value) <= length else value[: length - 3] + "..."


def result_value(*keys: str, default: str = "captured live") -> str:
    for key in keys:
        value = RESULT.get(key)
        if value not in (None, ""):
            return str(value)
    return default


def base(index: int):
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    for x in range(0, W, 64):
        draw.line((x, 0, x, H), fill=(12, 34, 51), width=1)
    for y in range(0, H, 48):
        draw.line((0, y, W, y), fill=(12, 34, 51), width=1)
    text(draw, (54, 35), "SYNTHETIC COMMERCIAL-PHARMA / LIVE KNOWLEDGE GRAPH", 15, CYAN, True)
    text(draw, (54, 68), f"{index + 1:02d}", 16, GOLD, True)
    text(draw, (88, 63), SCENES[index].title, 30, WHITE, True)
    text(draw, (1225, 43), f"TENANT: {CASE['tenant'].upper()}", 13, MUTED, True, "ra")
    for item in range(len(SCENES)):
        x = 54 + item * 122
        draw.line((x, 112, x + 106, 112), fill=GOLD if item <= index else (37, 70, 86), width=4)
    return image, draw


def scene_0(draw) -> None:
    panel(draw, (70, 160, 1210, 590), outline=GOLD)
    text(draw, (105, 198), "SYNTHETIC DEMO DATA / NOT MEDICAL ADVICE", 14, GOLD, True)
    wrapped(draw, (105, 252), RESULT["question"], 72, 28, WHITE, True)
    panel(draw, (105, 420, 1175, 525), outline=(55, 116, 140))
    text(draw, (140, 450), "Case", 14, MUTED, True)
    text(draw, (260, 450), short(CASE["id"], 44), 19, CYAN, True)
    text(draw, (140, 490), "Scope", 14, MUTED, True)
    text(draw, (260, 490), "commercial content approval", 19, GREEN, True)


def scene_1(draw) -> None:
    panel(draw, (80, 165, 520, 570), outline=CYAN)
    text(draw, (115, 202), "LIVE NEO4J TENANT SNAPSHOT", 14, GOLD, True)
    for index, (label, value) in enumerate((("Documents", COUNTS["documents"]), ("Chunks", COUNTS["chunks"]), ("Entities", COUNTS["entities"]), ("Edges", COUNTS["edges"]))):
        y = 265 + index * 58
        text(draw, (115, y), label, 18, MUTED)
        text(draw, (470, y), str(value), 26, WHITE, True, "ra")
    panel(draw, (600, 165, 1200, 570), outline=(54, 112, 134))
    text(draw, (635, 202), "GOVERNED SOURCE SET", 14, GOLD, True)
    source_labels = ["Product label", "Medical claims", "HCP profile", "Campaign brief", "Commercial policy", "Content revision v1", "Content revision v2"]
    for index, label in enumerate(source_labels):
        y = 255 + index * 40
        draw.ellipse((640, y + 5, 650, y + 15), fill=GREEN)
        text(draw, (675, y), label, 17, WHITE, index == 6)


def scene_2(draw) -> None:
    coords = {"PRODUCT": (170, 310), "INDICATION": (445, 230), "HCP": (445, 455), "SPECIALTY": (710, 455), "CONTENT": (710, 230), "MARKET": (1035, 230)}
    for source, target, label in (("PRODUCT", "INDICATION", "TREATS"), ("HCP", "SPECIALTY", "SPECIALIZES_IN"), ("CONTENT", "MARKET", "APPROVED_FOR")):
        arrow(draw, coords[source], coords[target], CYAN, 4)
        x = (coords[source][0] + coords[target][0]) // 2
        y = (coords[source][1] + coords[target][1]) // 2 - 28
        text(draw, (x, y), label, 12, GOLD, True, "mm")
    for label, xy in coords.items():
        node(draw, xy, label, "ontology type", GOLD if label == "CONTENT" else CYAN, 56)
    panel(draw, (260, 570, 1020, 640), outline=GREEN)
    text(draw, (640, 605), "Constraints are graph semantics, not hidden prompt instructions.", 18, GREEN, True, "mm")


def scene_3(draw) -> None:
    panel(draw, (90, 180, 560, 535), outline=GREEN)
    text(draw, (125, 215), "DOMAIN / RANGE CHECK", 14, GOLD, True)
    text(draw, (125, 275), "PHARMA_PRODUCT", 20, WHITE, True)
    arrow(draw, (325, 285), (420, 285), GREEN, 4)
    text(draw, (372, 248), "TREATS", 12, MUTED, True, "mm")
    text(draw, (435, 275), "INDICATION", 20, WHITE, True)
    text(draw, (125, 410), "VALID", 18, GREEN, True)
    panel(draw, (700, 180, 1190, 535), outline=RED)
    text(draw, (735, 215), "INVALID EXTRACTION", 14, GOLD, True)
    text(draw, (735, 275), "PERSON", 20, WHITE, True)
    arrow(draw, (825, 285), (950, 285), RED, 4)
    text(draw, (887, 248), "TREATS", 12, MUTED, True, "mm")
    text(draw, (975, 275), "INDICATION", 20, WHITE, True)
    text(draw, (735, 410), "REJECTED BY SCHEMA", 18, RED, True)
    panel(draw, (245, 580, 1035, 645), outline=CYAN)
    text(draw, (640, 612), "Live RDF export: 391 triples  /  SHACL: conforms", 19, CYAN, True, "mm")


def scene_4(draw) -> None:
    panel(draw, (75, 165, 420, 570), outline=CYAN)
    text(draw, (110, 200), "NORMAL LIVE PATH", 14, GOLD, True)
    for index, stage in enumerate(("1  Vector ANN", "2  BM25 / RRF fusion", "3  Cross-encoder rerank", "4  Two-hop traversal", "5  GAT scoring", "6  Answer synthesis")):
        y = 255 + index * 44
        text(draw, (115, y), stage, 18, WHITE, index in (0, 5))
    arrow(draw, (440, 365), (570, 365), GOLD, 4)
    node(draw, (680, 365), "KG", "evidence graph", GOLD, 82)
    arrow(draw, (770, 365), (895, 365), GOLD, 4)
    panel(draw, (895, 240, 1185, 490), outline=GREEN)
    text(draw, (930, 275), "LIVE RETRIEVAL", 14, GREEN, True)
    text(draw, (930, 320), "mode", 14, MUTED)
    text(draw, (1145, 320), result_value("retrieval_mode", "mode"), 17, WHITE, True, "ra")
    text(draw, (930, 365), "citations", 14, MUTED)
    text(draw, (1145, 365), str(len(RESULT.get("citations", []))), 17, WHITE, True, "ra")
    text(draw, (930, 410), "latency", 14, MUTED)
    text(draw, (1145, 410), f"{float(RESULT['latency_ms']):,.0f} ms", 17, WHITE, True, "ra")


def scene_5(draw) -> None:
    panel(draw, (65, 155, 595, 585), outline=GREEN)
    text(draw, (98, 190), "CAPTURED ANSWER", 14, GOLD, True)
    wrapped(draw, (98, 230), RESULT["answer"], 48, 18, WHITE)
    panel(draw, (660, 155, 1215, 585), outline=CYAN)
    text(draw, (695, 190), "LIVE CITATIONS", 14, GOLD, True)
    citations = RESULT.get("citations", [])
    for index, citation in enumerate(citations[:5]):
        value = citation.get("source_id", citation.get("id", "source")) if isinstance(citation, dict) else str(citation)
        y = 245 + index * 54
        draw.ellipse((700, y + 5, 710, y + 15), fill=GREEN)
        text(draw, (730, y), short(value, 44), 15, WHITE, index == 0)


def scene_6(draw) -> None:
    approved = POLICY["approved"]
    expired = POLICY["expired"]
    panel(draw, (70, 185, 580, 555), outline=GREEN)
    text(draw, (105, 222), "CURRENT CONTENT / POLICY RESULT", 14, GOLD, True)
    text(draw, (105, 275), approved["decision"].upper(), 25, GREEN, True)
    wrapped(draw, (105, 325), approved["rationale"], 48, 18, WHITE)
    text(draw, (105, 485), approved["reason_code"], 14, CYAN, True)
    panel(draw, (700, 185, 1210, 555), outline=RED)
    text(draw, (735, 222), "PRIOR REVISION / POLICY RESULT", 14, GOLD, True)
    text(draw, (735, 275), expired["decision"].upper(), 25, RED, True)
    wrapped(draw, (735, 325), expired["rationale"], 48, 18, WHITE)
    text(draw, (735, 485), expired["reason_code"], 14, CYAN, True)


def scene_7(draw) -> None:
    node(draw, (150, 355), "CGCase", short(CASE["id"], 14), GOLD, 65)
    node(draw, (415, 355), "CGRun", short(RUN["id"], 14), CYAN, 65)
    node(draw, (685, 250), "Manifest", short(MANIFEST["id"], 14), CYAN, 65)
    node(draw, (685, 470), "Evidence", f"{len(MANIFEST.get('chunk_ids', []))} chunks", CYAN, 65)
    node(draw, (1010, 355), "Decision", "answer selected", GOLD, 72)
    for source, target in (((215, 355), (345, 355)), ((480, 325), (610, 270)), ((480, 385), (610, 455)), ((750, 355), (925, 355))):
        arrow(draw, source, target, GOLD, 3)
    panel(draw, (210, 565, 1070, 640), outline=GREEN)
    text(draw, (640, 590), "MANIFEST SHA-256", 13, MUTED, True, "mm")
    text(draw, (640, 620), f"{short(MANIFEST['integrity_hash'], 42)}  /  {'VALID' if HASH_VALID else 'MISMATCH'}", 17, GREEN if HASH_VALID else RED, True, "mm")


def scene_8(draw) -> None:
    coords = {"SOURCES": (170, 350), "KNOWLEDGE\nGRAPH": (440, 350), "ANSWER": (710, 260), "CONTEXT\nGRAPH": (710, 465), "AUDIT": (1040, 350)}
    for source, target in (("SOURCES", "KNOWLEDGE\nGRAPH"), ("KNOWLEDGE\nGRAPH", "ANSWER"), ("ANSWER", "CONTEXT\nGRAPH"), ("CONTEXT\nGRAPH", "AUDIT")):
        arrow(draw, coords[source], coords[target], GOLD, 4)
    for label, xy in coords.items():
        node(draw, xy, label, "", GOLD if label in ("ANSWER", "AUDIT") else CYAN, 67)
    text(draw, (640, 595), "Govern what is known. Record what the AI used and concluded.", 22, WHITE, True, "mm")


DRAWERS = [scene_0, scene_1, scene_2, scene_3, scene_4, scene_5, scene_6, scene_7, scene_8]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def audio_duration(ffprobe: str, path: Path) -> float:
    output = subprocess.check_output([ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)], text=True)
    return float(output.strip())


def main() -> None:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required")
    BUILD.mkdir(exist_ok=True)
    segments: list[Path] = []
    for index, scene in enumerate(SCENES):
        image, draw = base(index)
        DRAWERS[index](draw)
        png = BUILD / f"scene_{index + 1:02d}.png"
        mp3 = BUILD / f"scene_{index + 1:02d}.mp3"
        stamp = BUILD / f"scene_{index + 1:02d}.voice.txt"
        segment = BUILD / f"scene_{index + 1:02d}.mp4"
        image.save(png)
        if not mp3.exists() or not stamp.exists() or stamp.read_text(encoding="utf-8") != scene.voiceover:
            gTTS(scene.voiceover, lang="en", slow=False).save(mp3)
            stamp.write_text(scene.voiceover, encoding="utf-8")
        duration = max(scene.duration, math.ceil(audio_duration(ffprobe, mp3) + 1))
        run([ffmpeg, "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(png), "-i", str(mp3), "-filter_complex", "[0:v]format=yuv420p[v];[1:a]adelay=500|500,apad[a]", "-map", "[v]", "-map", "[a]", "-t", str(duration), "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k", str(segment)])
        segments.append(segment)
    concat = BUILD / "segments.txt"
    concat.write_text("".join(f"file '{item.as_posix()}'\n" for item in segments), encoding="utf-8")
    run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-fflags", "+genpts", "-avoid_negative_ts", "make_zero", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-c:a", "aac", "-b:a", "128k", str(OUT)])
    print(OUT)


if __name__ == "__main__":
    main()
