"""Render a live-retrieval Context Graph presentation movie."""

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
BUILD = ROOT / "context_graph_movie_build"
OUT = ROOT / "context_graph_e2e_narrated.mp4"
CAPTURE = json.loads((ROOT / "context_graph_movie_trace.json").read_text(encoding="utf-8"))
TRACE = CAPTURE["trace_api_response"]
API_RESULT = CAPTURE["query_response"]
CACHE_RESULT = CAPTURE.get("cache_response", {})
CACHE_DEMO = CAPTURE.get("cache_demo", {})
API = CAPTURE["api"]
COUNTS = CAPTURE["graph_counts"]
CASE = TRACE["case"]
RUN = TRACE["run"]
MANIFEST = dict(TRACE["manifest"])
if isinstance(MANIFEST.get("retrieval_config"), str):
    MANIFEST["retrieval_config"] = json.loads(MANIFEST["retrieval_config"])
RETRIEVAL_CONFIG = MANIFEST.get("retrieval_config", {})
DECISION = TRACE["decision"]
POLICY = TRACE["policy_versions"][0]
EVALUATION = TRACE["policy_evaluations"][0]
OPTIONS = TRACE["options"]
DOCUMENTS = {item["id"]: item for item in TRACE.get("documents", []) if item}
CHUNKS = {item["id"]: item for item in TRACE.get("chunks", []) if item}
HASH_VALID = ContextManifest.model_validate(MANIFEST).compute_integrity_hash() == MANIFEST["integrity_hash"]
ANSWER = API_RESULT["answer"]
CANONICAL_MANIFEST = json.dumps(
    ContextManifest.model_validate(MANIFEST).canonical_content(),
    sort_keys=True,
    separators=(",", ":"),
)

BG = (5, 12, 25)
PANEL = (9, 25, 40)
CYAN = (95, 221, 255)
GOLD = (255, 201, 92)
WHITE = (242, 248, 252)
MUTED = (148, 177, 197)
GREEN = (80, 211, 154)
RED = (255, 109, 116)


@dataclass(frozen=True)
class Scene:
    title: str
    duration: int
    voiceover: str


SCENES = [
    Scene(
        "Live Retrieval Starts with Indexed Evidence", 20,
        "The live request starts from an already indexed tenant. Marketing contains "
        "four documents and twenty-four chunks. The query enters the same retrieval "
        "path used by the application, with no hand-selected evidence in the movie.",
    ),
    Scene(
        "The Question Enters the API", 20,
        "The question asks whether a Nova Beverages EU Q3 placement beside sports-betting "
        "promotional content is allowed. The application assigns a stable query identity "
        "so the answer and its decision trace can be found again.",
    ),
    Scene(
        "Retrieval Captures Its Evidence", 25,
        "The live retriever returns the exact chunks behind the answer. They include the "
        "Campaign Brief, the Statement of Work, the Data Privacy Policy, and the global "
        "Brand Guideline. Their document lineage is captured in the Context Graph manifest.",
    ),
    Scene(
        "The Graph Expands and Reranks Context", 25,
        "The planner selects local retrieval for this fact question. The path then applies "
        "vector and lexical search, two-hop graph expansion, GNN scoring, and reranking. "
        "The manifest records the retrieval mode and configuration used for this run.",
    ),
    Scene(
        "The API Returns a Grounded Answer", 30,
        "The answer does not hide the conflict. The Statement of Work excludes gambling "
        "and sports-betting placements, while the Campaign Brief lists a sports-betting "
        "companion-app adjacency. Because the privacy provisions are not present in the "
        "retrieved context, the system says that permissibility cannot be determined.",
    ),
    Scene(
        "The Manifest Locks the Inference Moment", 25,
        "The manifest captures the question, chunks, documents, retrieval configuration, "
        "model, prompt version, ontology, and temporal boundaries. Canonical content is "
        "hashed with SHA-256. Reconstructing the same context produces the same hash.",
    ),
    Scene(
        "A Repeat Question Hits Governed Cache", 24,
        "The second identical request does not rerun retrieval or the language model. "
        "Redis returns the completed answer only because the tenant, normalized question, "
        "model route, retrieval settings, ontology, prompt version, and corpus revision "
        "match the governed cache key. The new query keeps a fresh identity and points "
        "back to the original trace.",
    ),
    Scene(
        "The Answer Becomes a Decision Trace", 25,
        "The live retrieval path records the answer as the selected option of a governed "
        "decision. The trace keeps the case, agent run, evidence, policy evaluation, and "
        "structured rationale together. It stores the answer, not hidden chain-of-thought.",
    ),
    Scene(
        "Replay Through the Tenant-Scoped API", 25,
        "A later request can load the same trace through the Context Graph API. The tenant "
        "is explicit, the evidence references are visible, and the reconstructed manifest "
        "still matches its integrity hash. The decision can be audited without rerunning "
        "the model.",
    ),
    Scene(
        "From Answer to Accountable Decision", 15,
        "The Knowledge Graph finds the evidence. The Context Graph records what the system "
        "retrieved, what it answered, and why it refused to overstate the result. That is "
        "the difference between a fast answer and an accountable decision.",
    ),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
    return ImageFont.truetype(path, size)


def text(draw, xy, value: str, size=20, color=WHITE, bold=False, anchor=None):
    draw.text(xy, value, font=font(size, bold), fill=color, anchor=anchor)


def wrapped(draw, xy, value: str, width=60, size=20, color=WHITE, bold=False, spacing=7):
    draw.multiline_text(xy, textwrap.fill(value, width), font=font(size, bold), fill=color, spacing=spacing)


def panel(draw, box, outline=(32, 72, 94), fill=PANEL, radius=10, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def arrow(draw, start, end, color=CYAN, width=3):
    draw.line((*start, *end), fill=color, width=width)
    x2, y2 = end
    draw.polygon([(x2, y2), (x2 - 12, y2 - 6), (x2 - 12, y2 + 6)], fill=color)


def node(draw, xy, label, sub="", color=CYAN, radius=50):
    x, y = xy
    draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(7, 25, 42), outline=color, width=3)
    text(draw, (x, y - 8), label, 14, color, True, "mm")
    if sub:
        text(draw, (x, y + 16), sub, 11, (197, 220, 233), False, "mm")


def short(value: str, length: int = 22) -> str:
    return value if len(value) <= length else value[:length] + "..."


def tail(value: str, length: int = 16) -> str:
    return value[-length:] if value else ""


def ms(value) -> str:
    try:
        numeric = float(value)
        if numeric <= 0:
            return "<1 ms"
        return f"{numeric:,.0f} ms"
    except (TypeError, ValueError):
        return "n/a"


def speedup_text() -> str:
    speedup = CACHE_DEMO.get("speedup")
    if speedup:
        return f"{speedup}x"
    cold = CACHE_DEMO.get("cold_latency_ms", API_RESULT.get("latency_ms"))
    warm = CACHE_DEMO.get("warm_latency_ms", CACHE_RESULT.get("latency_ms"))
    try:
        cold_f = float(cold)
        warm_f = float(warm)
    except (TypeError, ValueError):
        return "n/a"
    if warm_f <= 0:
        return f">{cold_f:,.0f}x"
    return f"{cold_f / warm_f:,.1f}x"


def base(index: int):
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    for x in range(0, W, 64):
        draw.line((x, 0, x, H), fill=(10, 29, 47), width=1)
    for y in range(0, H, 48):
        draw.line((0, y, W, y), fill=(10, 29, 47), width=1)
    text(draw, (54, 35), "CONTEXT GRAPH  /  LIVE RETRIEVAL TRACE", 16, CYAN, True)
    text(draw, (54, 67), f"{index:02d}", 16, GOLD, True)
    text(draw, (86, 63), SCENES[index].title, 30, WHITE, True)
    text(draw, (1222, 43), f"TENANT: {CASE['tenant'].upper()}", 13, MUTED, True, "ra")
    for i in range(len(SCENES)):
        x1 = 54 + i * 115
        draw.line((x1, 112, x1 + 102, 112), fill=GOLD if i <= index else (35, 66, 84), width=4)
    return img, draw


def scene_0(draw):
    panel(draw, (70, 150, 1210, 620))
    text(draw, (104, 184), "LIVE TENANT SNAPSHOT / BEFORE THE QUESTION", 13, GOLD, True)
    panel(draw, (105, 235, 610, 535), outline=(47, 103, 126))
    text(draw, (137, 270), "POST /query", 22, CYAN, True)
    text(draw, (137, 315), f'tenant: "{CASE["tenant"]}"', 17, WHITE)
    text(draw, (137, 350), f'mode: "{API_RESULT["retrieval_mode"]}"', 17, WHITE)
    text(draw, (137, 385), f'query_id: "{short(API_RESULT["query_id"], 27)}"', 16, MUTED)
    text(draw, (137, 445), "retrieval path: LIVE", 18, GREEN, True)
    panel(draw, (690, 235, 1175, 535), outline=GOLD)
    text(draw, (725, 270), "MARKETING KG / NEO4J", 13, GOLD, True)
    values = [
        ("Documents", COUNTS.get("documents", 0)),
        ("Chunks", COUNTS.get("chunks", 0)),
        ("Entities", COUNTS.get("entities", 0)),
        ("Edges", COUNTS.get("edges", 0)),
        ("Open conflicts", COUNTS.get("open_conflicts", 0)),
    ]
    for i, (label, value) in enumerate(values):
        y = 320 + i * 39
        text(draw, (725, y), label, 16, MUTED)
        text(draw, (1135, y), str(value), 18, GREEN if label == "Open conflicts" else WHITE, True, "ra")


def scene_1(draw):
    panel(draw, (78, 150, 1202, 625))
    text(draw, (112, 184), "QUESTION / STABLE QUERY IDENTITY", 14, GOLD, True)
    wrapped(draw, (112, 225), API_RESULT["question"], 75, 25, WHITE, True)
    panel(draw, (112, 365, 1168, 540), outline=(48, 104, 129))
    text(draw, (140, 395), "QUERY RESPONSE ID", 12, MUTED, True)
    text(draw, (140, 430), API_RESULT["query_id"], 22, CYAN, True)
    text(draw, (140, 480), "tenant", 13, MUTED)
    text(draw, (280, 480), CASE["tenant"], 17, WHITE, True)
    text(draw, (580, 480), "status", 13, MUTED)
    text(draw, (700, 480), "completed", 17, GREEN, True)


def scene_2(draw):
    doc_items = list(DOCUMENTS.values())
    for i, item in enumerate(doc_items[:4]):
        y = 195 + i * 96
        panel(draw, (75, y, 545, y + 75), outline=(49, 99, 120))
        text(draw, (102, y + 22), "DOCUMENT", 11, MUTED, True)
        text(draw, (102, y + 51), short(item.get("filename", item["id"]), 42), 16, WHITE, True)
        arrow(draw, (545, y + 37), (680, 355), CYAN, 2)
    node(draw, (770, 355), "MANIFEST", short(MANIFEST["id"]), GOLD, 78)
    panel(draw, (920, 190, 1195, 525))
    text(draw, (950, 220), "CAPTURED EVIDENCE", 13, GOLD, True)
    text(draw, (950, 275), f"chunks: {len(MANIFEST['chunk_ids'])}", 17, WHITE)
    text(draw, (950, 315), f"documents: {len(MANIFEST['document_ids'])}", 17, WHITE)
    text(draw, (950, 355), f"citations: {len(API_RESULT['citations'])}", 17, WHITE)
    text(draw, (950, 395), "lineage: Chunk -> Document", 16, GREEN, True)
    text(draw, (82, 595), "Evidence is selected by the live retriever and persisted for replay.", 21, WHITE, True)


def scene_3(draw):
    panel(draw, (75, 180, 510, 540), outline=CYAN)
    text(draw, (105, 215), "RETRIEVAL PIPELINE", 13, GOLD, True)
    steps = ["planner: local", "lexical + vector search", "2-hop graph expansion", "GNN scoring", "cross-encoder reranking"]
    for i, item in enumerate(steps):
        y = 270 + i * 48
        draw.ellipse((107, y + 4, 117, y + 14), fill=GREEN)
        text(draw, (137, y), item, 17, WHITE)
    node(draw, (800, 355), "CONTEXT", f"{len(MANIFEST['chunk_ids'])} chunks", GOLD, 82)
    node(draw, (1060, 275), "GNN", "2 hops", CYAN, 52)
    node(draw, (1060, 445), "RERANK", "top context", CYAN, 52)
    arrow(draw, (585, 355), (715, 355), CYAN, 3)
    arrow(draw, (880, 320), (1005, 285), CYAN, 3)
    arrow(draw, (880, 390), (1005, 435), CYAN, 3)
    text(draw, (640, 595), f"mode: {API_RESULT['retrieval_mode']}   /   model: {API_RESULT['model_version']}", 19, WHITE, True, "mm")


def scene_4(draw):
    panel(draw, (65, 155, 520, 590))
    text(draw, (95, 185), "LIVE API RESPONSE", 13, GOLD, True)
    wrapped(draw, (95, 225), ANSWER, 48, 17, WHITE, False, spacing=6)
    panel(draw, (635, 205, 1195, 535), outline=GREEN)
    text(draw, (670, 240), "RESPONSE METADATA", 13, GREEN, True)
    fields = [
        ("retrieval_mode", API_RESULT["retrieval_mode"]),
        ("model_version", API_RESULT["model_version"]),
        ("latency_ms", f"{API_RESULT['latency_ms']:.0f}"),
        ("citations", str(len(API_RESULT["citations"]))),
        ("decision", "do not overstate"),
    ]
    for i, (key, value) in enumerate(fields):
        y = 295 + i * 44
        text(draw, (670, y), key, 14, MUTED)
        text(draw, (1135, y), value, 16, GREEN if key == "decision" else WHITE, True, "ra")


def scene_5(draw):
    panel(draw, (65, 155, 475, 575))
    text(draw, (95, 185), "CONTEXT MANIFEST", 13, GOLD, True)
    rows = [
        ("tenant", MANIFEST["tenant"]),
        ("model", MANIFEST["model_version"]),
        ("prompt", MANIFEST["prompt_version"]),
        ("retrieval", MANIFEST["retrieval_mode"]),
        ("ontology", MANIFEST["ontology_version"]),
        ("corpus rev", str(RETRIEVAL_CONFIG.get("corpus_revision", "n/a"))),
        ("cache schema", str(RETRIEVAL_CONFIG.get("cache_schema_version", "n/a"))),
        ("evidence", f"{len(MANIFEST['chunk_ids'])} chunks / {len(MANIFEST['document_ids'])} docs"),
    ]
    for i, (key, value) in enumerate(rows):
        y = 224 + i * 39
        text(draw, (95, y), key, 14, MUTED)
        text(draw, (245, y), str(value), 15, CYAN, True)
    arrow(draw, (475, 365), (590, 365), GOLD, 4)
    panel(draw, (590, 270, 825, 460), outline=(64, 119, 140))
    text(draw, (708, 320), "CANONICAL JSON", 15, WHITE, True, "mm")
    text(draw, (708, 360), f"{len(CANONICAL_MANIFEST):,} bytes", 14, MUTED, False, "mm")
    text(draw, (708, 390), short(CANONICAL_MANIFEST[:46], 42), 12, MUTED, False, "mm")
    arrow(draw, (825, 365), (900, 365), GOLD, 4)
    panel(draw, (900, 245, 1200, 495), outline=GOLD, width=3)
    text(draw, (1050, 290), "SHA-256", 20, GOLD, True, "mm")
    text(draw, (1050, 345), short(MANIFEST["integrity_hash"], 20), 18, WHITE, True, "mm")
    text(draw, (1050, 395), "INTEGRITY VALID", 15, GREEN, True, "mm")
    text(draw, (1050, 445), "same context = same hash", 13, MUTED, False, "mm")


def scene_6(draw):
    panel(draw, (65, 165, 1205, 585), outline=GOLD)
    text(draw, (95, 200), "GOVERNED ANSWER CACHE / SECOND IDENTICAL REQUEST", 13, GOLD, True)
    panel(draw, (100, 250, 430, 505), outline=(48, 104, 129))
    text(draw, (130, 285), "COLD RUN", 13, CYAN, True)
    text(draw, (130, 325), short(CACHE_DEMO.get("cold_query_id", API_RESULT["query_id"]), 31), 16, WHITE, True)
    text(draw, (130, 375), "retrieval + LLM + trace", 15, MUTED)
    text(draw, (130, 425), ms(CACHE_DEMO.get("cold_latency_ms", API_RESULT.get("latency_ms"))), 26, WHITE, True)
    text(draw, (130, 470), "cache written after trace", 14, GREEN, True)
    arrow(draw, (445, 380), (585, 380), GOLD, 4)
    panel(draw, (585, 250, 900, 505), outline=GREEN)
    text(draw, (615, 285), "REDIS HASH KEY", 13, GREEN, True)
    text(draw, (615, 325), "tenant + normalized query", 15, WHITE)
    text(draw, (615, 360), "model + prompt + retrieval config", 15, WHITE)
    text(draw, (615, 395), f"corpus_revision = {RETRIEVAL_CONFIG.get('corpus_revision', 'n/a')}", 15, WHITE)
    text(draw, (615, 440), f"...{tail(CACHE_DEMO.get('cache_key', API_RESULT.get('cache_key', '')), 18)}", 18, GOLD, True)
    arrow(draw, (915, 380), (1040, 380), GOLD, 4)
    panel(draw, (1040, 250, 1190, 505), outline=GREEN)
    text(draw, (1115, 285), "REPEAT", 13, GREEN, True, "mm")
    text(draw, (1115, 335), "cache_hit", 14, MUTED, False, "mm")
    text(draw, (1115, 370), str(CACHE_DEMO.get("cache_hit", CACHE_RESULT.get("cache_hit", False))).lower(), 21, GREEN, True, "mm")
    text(draw, (1115, 420), ms(CACHE_DEMO.get("warm_latency_ms", CACHE_RESULT.get("latency_ms"))), 18, WHITE, True, "mm")
    text(draw, (1115, 465), speedup_text(), 18, GOLD, True, "mm")
    text(draw, (640, 545), f"source trace: {short(CACHE_DEMO.get('source_trace_id', CACHE_RESULT.get('source_trace_id', '')), 46)}", 17, WHITE, True, "mm")


def scene_7(draw):
    node(draw, (210, 355), "CGCase", short(CASE["id"]), GOLD, 68)
    node(draw, (560, 355), "CGAgentRun", short(RUN["id"]), CYAN, 74)
    node(draw, (910, 355), "CGDecision", short(DECISION["id"]), GOLD, 78)
    arrow(draw, (280, 355), (485, 355), CYAN, 4)
    arrow(draw, (635, 355), (830, 355), GOLD, 4)
    text(draw, (382, 320), "ADDRESSES", 13, CYAN, True, "mm")
    text(draw, (732, 320), "PRODUCED_DECISION", 13, GOLD, True, "mm")
    panel(draw, (315, 500, 970, 590), outline=GREEN)
    text(draw, (640, 545), "selected option: answer   /   reason: retrieved_evidence", 18, GREEN, True, "mm")


def scene_8(draw):
    panel(draw, (65, 175, 1215, 580))
    text(draw, (95, 210), API["method"] + " " + API["path"], 14, CYAN, True)
    draw.line((95, 245, 1190, 245), fill=(38, 79, 99), width=2)
    coords = {"CASE": (150, 340), "RUN": (390, 340), "MANIFEST": (650, 275), "EVIDENCE": (930, 275), "DECISION": (650, 445)}
    for a, b in (("CASE", "RUN"), ("RUN", "MANIFEST"), ("MANIFEST", "EVIDENCE"), ("RUN", "DECISION")):
        arrow(draw, coords[a], coords[b], GOLD if b == "DECISION" else (52, 139, 166), 3)
    for label, xy in coords.items():
        node(draw, xy, label, "", GOLD if label == "DECISION" else CYAN, 43)
    fields = [
        (95, "selected_option", '"answer"', GOLD),
        (365, "policy_version", POLICY["version"], CYAN),
        (630, "tenant", CASE["tenant"], WHITE),
        (895, "integrity_hash_valid", str(HASH_VALID).lower(), GREEN),
    ]
    for x, key, value, color in fields:
        text(draw, (x, 505), key, 12, MUTED, True)
        text(draw, (x, 540), value, 16, color, True)


def scene_9(draw):
    coords = {"CASE": (160, 350), "RUN": (370, 270), "MANIFEST": (610, 350), "EVIDENCE": (840, 270), "DECISION": (1080, 350)}
    links = [("CASE", "RUN"), ("RUN", "MANIFEST"), ("MANIFEST", "EVIDENCE"), ("RUN", "DECISION")]
    for a, b in links:
        arrow(draw, coords[a], coords[b], (40, 115, 143), 3)
    for label, xy in coords.items():
        node(draw, xy, label, "", GOLD if label == "DECISION" else CYAN, 48)
    text(draw, (640, 560), "WHAT WAS RETRIEVED.  WHAT WAS ANSWERED.", 24, WHITE, True, "mm")
    text(draw, (640, 610), "From an answer to an accountable decision.", 18, GOLD, True, "mm")


DRAWERS = [scene_0, scene_1, scene_2, scene_3, scene_4, scene_5, scene_6, scene_7, scene_8, scene_9]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def audio_duration(ffprobe: str, path: Path) -> float:
    output = subprocess.check_output(
        [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        text=True,
    )
    return float(output.strip())


def main() -> None:
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise RuntimeError("ffmpeg and ffprobe are required")
    BUILD.mkdir(exist_ok=True)
    segments: list[Path] = []
    for i, scene in enumerate(SCENES):
        image, draw = base(i)
        DRAWERS[i](draw)
        png = BUILD / f"scene_{i + 1:02d}.png"
        mp3 = BUILD / f"scene_{i + 1:02d}.mp3"
        voice_stamp = BUILD / f"scene_{i + 1:02d}.voice.txt"
        segment = BUILD / f"scene_{i + 1:02d}.mp4"
        image.save(png)
        if not mp3.exists() or not voice_stamp.exists() or voice_stamp.read_text(encoding="utf-8") != scene.voiceover:
            gTTS(scene.voiceover, lang="en", slow=False).save(mp3)
            voice_stamp.write_text(scene.voiceover, encoding="utf-8")
        duration = max(scene.duration, math.ceil(audio_duration(ffprobe, mp3) + 1.0))
        vf = "format=yuv420p"
        run([
            ffmpeg, "-y", "-loop", "1", "-framerate", str(FPS), "-i", str(png), "-i", str(mp3),
            "-filter_complex", f"[0:v]{vf}[v];[1:a]adelay=500|500,apad[a]",
            "-map", "[v]", "-map", "[a]", "-t", str(duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", str(segment),
        ])
        segments.append(segment)
    concat = BUILD / "segments.txt"
    concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments), encoding="utf-8")
    run([
        ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat),
        "-fflags", "+genpts", "-avoid_negative_ts", "make_zero",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", str(OUT),
    ])
    print(OUT)


if __name__ == "__main__":
    main()
