"""Render the complete nine-scene Context Graph presentation movie."""

from __future__ import annotations

import shutil
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

from gtts import gTTS
from PIL import Image, ImageDraw, ImageFont


W, H, FPS = 1280, 720, 24
ROOT = Path(__file__).resolve().parent
BUILD = ROOT / "context_graph_movie_build"
OUT = ROOT / "context_graph_e2e_narrated.mp4"
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
        "Ingestion Completes the Workflow",
        25,
        "The workflow starts before the question. Documents enter the knowledge graph "
        "through the ingestion pipeline. The pipeline chunks source material, extracts "
        "entities and relationships, creates embeddings, and preserves document and chunk "
        "identity so later retrieval can verify exactly where an answer came from.",
    ),
    Scene(
        "The Question Becomes a Case",
        25,
        "A normal RAG answer ends when the model returns text. In a governed "
        "environment, that is where the important part begins. We start with a case: "
        "determine whether this action is allowed under the current policy.",
    ),
    Scene(
        "The Agent Run Is Captured",
        25,
        "The system opens a durable case and starts an agent run. This gives the "
        "request an identity, a tenant boundary, and a lifecycle. The run can now be "
        "connected to the context it used, the tools it called, and the decision it "
        "eventually produced.",
    ),
    Scene(
        "Evidence Is Assembled",
        30,
        "Retrieval finds the evidence: the source document, the policy statement, and "
        "the exact document and chunk versions behind them. The Context Graph records "
        "those references explicitly. It does not merely say that the model searched; "
        "it records what was available to the model at decision time.",
    ),
    Scene(
        "The Manifest Locks the Moment",
        25,
        "This is the key difference between context and a loose conversation log. The "
        "manifest captures evidence, policy versions, retrieval configuration, model "
        "and prompt versions, temporal boundaries, and tool observations. Canonical "
        "content produces an integrity hash. Reconstruct the same context, and the hash "
        "must match. Structured rationale is stored. Hidden chain-of-thought is not.",
    ),
    Scene(
        "Tools Produce Auditable Observations",
        20,
        "When the agent uses a tool, the call and its observation become part of the "
        "trace. We preserve the auditable result, not private internal reasoning: which "
        "policy was evaluated, what rule controlled, and what constraint was returned.",
    ),
    Scene(
        "Alternatives Make the Decision Governed",
        30,
        "The agent does not write an unexplained verdict. It records the alternatives it "
        "considered. Allow is rejected because the controlling policy rule is not "
        "satisfied. Escalation remains possible, but the evidence is sufficient for a "
        "policy decision. Deny is selected, with a concise rationale and explicit reason "
        "codes for the alternatives.",
    ),
    Scene(
        "Policy Evaluation Is Linked",
        20,
        "The decision is linked to the exact policy version and its evaluation. When the "
        "policy changes, a later run can use a newer version and produce a different "
        "result without rewriting what happened here. The history remains append-only.",
    ),
    Scene(
        "Replay the Trace",
        25,
        "Now we can answer the questions an enterprise actually asks: what case was "
        "handled, which run handled it, what evidence was available, which policy applied, "
        "which alternatives were considered, why they were rejected, which observations "
        "contributed, and whether the reconstructed manifest still matches its hash.",
    ),
    Scene(
        "From Answer to Accountable Decision",
        10,
        "The Knowledge Graph helps the system find what is true. The Context Graph records "
        "what the system knew, what it considered, and why it acted. That is the difference "
        "between an answer and an accountable decision.",
    ),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
    return ImageFont.truetype(path, size)


def text(draw: ImageDraw.ImageDraw, xy, value: str, size=20, color=WHITE, bold=False, anchor=None):
    draw.text(xy, value, font=font(size, bold), fill=color, anchor=anchor)


def wrapped(draw: ImageDraw.ImageDraw, xy, value: str, width=48, size=20, color=WHITE, bold=False, spacing=7):
    draw.multiline_text(xy, textwrap.fill(value, width), font=font(size, bold), fill=color, spacing=spacing)


def panel(draw: ImageDraw.ImageDraw, box, outline=(32, 72, 94), fill=PANEL, radius=10, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def arrow(draw: ImageDraw.ImageDraw, start, end, color=CYAN, width=3):
    draw.line((*start, *end), fill=color, width=width)
    x2, y2 = end
    draw.polygon([(x2, y2), (x2 - 12, y2 - 6), (x2 - 12, y2 + 6)], fill=color)


def node(draw: ImageDraw.ImageDraw, xy, label, sub="", color=CYAN, radius=50):
    x, y = xy
    draw.ellipse((x-radius, y-radius, x+radius, y+radius), fill=(7, 25, 42), outline=color, width=3)
    text(draw, (x, y-8), label, 14, color, True, "mm")
    if sub:
        text(draw, (x, y+16), sub, 11, (197, 220, 233), False, "mm")


def base(scene_index: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    for x in range(0, W, 64):
        draw.line((x, 0, x, H), fill=(10, 29, 47), width=1)
    for y in range(0, H, 48):
        draw.line((0, y, W, y), fill=(10, 29, 47), width=1)
    text(draw, (54, 35), "CONTEXT GRAPH  /  END-TO-END DECISION TRACE", 16, CYAN, True)
    text(draw, (54, 67), f"{scene_index:02d}", 16, GOLD, True)
    text(draw, (86, 63), SCENES[scene_index].title, 30, WHITE, True)
    text(draw, (1222, 43), "TENANT: MARKETING", 13, MUTED, True, "ra")
    for i in range(len(SCENES)):
        x1 = 54 + i * 115
        draw.line((x1, 112, x1 + 102, 112), fill=GOLD if i <= scene_index else (35, 66, 84), width=4)
    return img, draw


def scene_0(draw):
    panel(draw, (70, 150, 1210, 620))
    text(draw, (104, 184), "PRE-RECORDED TERMINAL  /  KNOWLEDGE GRAPH INGESTION", 13, GOLD, True)
    panel(draw, (104, 225, 1176, 535), fill=(3, 10, 18), outline=(47, 103, 126))
    text(draw, (132, 252), "PS C:\\ai-knowledge-graph-platform>", 17, MUTED)
    text(draw, (132, 286), "python scripts/ingest_corpus.py --tenant marketing --commit", 19, CYAN, True)
    text(draw, (132, 340), "[ingestion] reading source documents...", 16, MUTED)
    text(draw, (132, 378), "[ingestion] chunks created: 48", 16, WHITE)
    text(draw, (132, 416), "[ingestion] entities extracted: 51", 16, WHITE)
    text(draw, (132, 454), "[ingestion] embeddings written: 48", 16, WHITE)
    text(draw, (132, 492), "[ingestion] graph commit complete", 16, GREEN, True)
    panel(draw, (835, 328, 1140, 505), outline=GOLD)
    text(draw, (865, 358), "FINAL SUMMARY", 12, GOLD, True)
    text(draw, (865, 401), "documents     4", 17, WHITE, True)
    text(draw, (865, 438), "chunks       48", 17, WHITE, True)
    text(draw, (865, 475), "status       COMPLETE", 17, GREEN, True)


def scene_1(draw):
    panel(draw, (78, 150, 1202, 625))
    text(draw, (112, 184), "GOVERNED ACTION", 14, GOLD, True)
    text(draw, (112, 222), "Campaign placement review", 30, WHITE, True)
    panel(draw, (112, 284, 1168, 400), fill=(12, 35, 55))
    text(draw, (142, 310), "QUESTION", 12, CYAN, True)
    wrapped(draw, (142, 340), "Is this campaign placement allowed under the current Data Privacy Policy?", 72, 23, WHITE, True)
    panel(draw, (112, 438, 520, 565), outline=(48, 104, 129))
    text(draw, (140, 460), "TENANT", 12, MUTED, True)
    text(draw, (140, 491), "marketing", 23, WHITE, True)
    panel(draw, (550, 438, 1168, 565), outline=(48, 104, 129))
    text(draw, (578, 460), "CASE CREATED", 12, MUTED, True)
    text(draw, (578, 491), "case_campaign_placement_0142", 23, CYAN, True)
    text(draw, (578, 531), "status: open", 14, GOLD, True)


def scene_2(draw):
    node(draw, (250, 355), "CGCase", "case_0142", GOLD, 68)
    node(draw, (685, 355), "CGAgentRun", "run_0142", CYAN, 76)
    arrow(draw, (605, 355), (325, 355), CYAN, 4)
    text(draw, (465, 322), "ADDRESSES", 15, CYAN, True, "mm")
    panel(draw, (880, 215, 1175, 500))
    text(draw, (910, 244), "RUN LIFECYCLE", 13, MUTED, True)
    text(draw, (910, 286), "RUNNING", 22, GOLD, True)
    draw.line((910, 330, 1130, 330), fill=(41, 85, 106), width=3)
    text(draw, (910, 361), "tenant", 13, MUTED)
    text(draw, (1130, 361), "marketing", 15, WHITE, True, "ra")
    text(draw, (910, 399), "started", 13, MUTED)
    text(draw, (1130, 399), "10:32:14Z", 15, WHITE, True, "ra")
    text(draw, (910, 437), "schema", 13, MUTED)
    text(draw, (1130, 437), "cg:1.0", 15, WHITE, True, "ra")
    text(draw, (250, 548), "The request now has identity, tenancy, and lifecycle.", 22, WHITE, True)


def scene_3(draw):
    docs = [(185, 250, "Campaign Brief", "doc:brief-v3"), (185, 440, "Privacy Policy", "doc:privacy-v4.2")]
    for x, y, title, version in docs:
        panel(draw, (80, y-70, 390, y+70), outline=(49, 99, 120))
        text(draw, (108, y-42), "DOCUMENT", 11, MUTED, True)
        text(draw, (108, y-8), title, 20, WHITE, True)
        text(draw, (108, y+28), version, 14, CYAN)
    node(draw, (690, 350), "MANIFEST", "ctx_0142", GOLD, 78)
    arrow(draw, (390, 250), (605, 322))
    arrow(draw, (390, 440), (605, 378))
    panel(draw, (850, 175, 1190, 535))
    text(draw, (880, 202), "EXACT INFERENCE CONTEXT", 12, GOLD, True)
    fields = ["Statement IDs + versions", "Chunk + document refs", "Valid / transaction time", "Ontology: marketing-v7", "Retrieval: hybrid + GAT", "Model + prompt version"]
    for i, item in enumerate(fields):
        y = 248 + i * 44
        draw.ellipse((882, y+4, 892, y+14), fill=GREEN)
        text(draw, (908, y), item, 16, WHITE)
    text(draw, (80, 590), "What was available to the model is now explicit and reconstructable.", 21, WHITE, True)


def scene_4(draw):
    panel(draw, (62, 155, 482, 582))
    text(draw, (92, 180), "CONTEXT MANIFEST", 13, GOLD, True)
    rows = [("tenant", '"marketing"'), ("model", '"deepseek-v4-pro"'), ("prompt", '"decision-v3"'), ("policy", '"privacy-v4.2"'), ("valid_at", '"2026-08-01"'), ("tool_obs", '["obs_0142"]')]
    for i, (k, v) in enumerate(rows):
        y = 226 + i * 48
        text(draw, (92, y), k, 15, MUTED)
        text(draw, (228, y), v, 15, CYAN, True)
    arrow(draw, (482, 370), (590, 370), GOLD, 4)
    panel(draw, (590, 270, 825, 470), outline=(64, 119, 140))
    text(draw, (708, 320), "CANONICAL JSON", 15, WHITE, True, "mm")
    text(draw, (708, 365), "sorted keys", 14, MUTED, False, "mm")
    text(draw, (708, 397), "stable serialization", 14, MUTED, False, "mm")
    arrow(draw, (825, 370), (900, 370), GOLD, 4)
    panel(draw, (900, 245, 1200, 495), outline=GOLD, width=3)
    text(draw, (1050, 290), "SHA-256", 20, GOLD, True, "mm")
    text(draw, (1050, 345), "9f2a7d0c...e184", 22, WHITE, True, "mm")
    text(draw, (1050, 395), "INTEGRITY VALID", 15, GREEN, True, "mm")
    text(draw, (1050, 445), "same context = same hash", 13, MUTED, False, "mm")
    text(draw, (640, 610), "Structured rationale stored  •  hidden chain-of-thought excluded", 18, CYAN, True, "mm")


def scene_5(draw):
    node(draw, (200, 355), "AGENT RUN", "run_0142", CYAN, 70)
    panel(draw, (410, 205, 760, 505), outline=(55, 111, 135))
    text(draw, (440, 235), "TOOL CALL", 13, GOLD, True)
    text(draw, (440, 279), "evaluate_policy", 24, WHITE, True)
    text(draw, (440, 326), "policy: privacy-v4.2", 15, CYAN)
    text(draw, (440, 365), "action: campaign_placement", 15, CYAN)
    text(draw, (440, 430), "status: completed", 14, GREEN, True)
    panel(draw, (900, 205, 1190, 505), outline=GREEN)
    text(draw, (930, 235), "OBSERVATION", 13, GREEN, True)
    text(draw, (930, 278), "rule: DP-17", 18, WHITE, True)
    text(draw, (930, 320), "result: DENY", 22, RED, True)
    wrapped(draw, (930, 370), "Consent condition is not satisfied.", 25, 16, MUTED)
    arrow(draw, (270, 355), (410, 355))
    arrow(draw, (760, 355), (900, 355), GREEN)
    text(draw, (585, 555), "MADE_TOOL_CALL", 13, CYAN, True, "mm")
    text(draw, (1045, 555), "PRODUCED", 13, GREEN, True, "mm")


def scene_6(draw):
    text(draw, (640, 156), "THREE OPTIONS. ONE EXPLAINABLE SELECTION.", 18, MUTED, True, "mm")
    options = [
        (80, "ALLOW", "REJECTED", "POLICY_CONDITION_UNMET", RED),
        (460, "DENY", "SELECTED", "CONTROLLING_RULE_DP_17", GOLD),
        (840, "ESCALATE", "REJECTED", "EVIDENCE_SUFFICIENT", RED),
    ]
    for x, title, state, reason, color in options:
        panel(draw, (x, 215, x+340, 520), outline=color, width=3)
        text(draw, (x+28, 245), "OPTION", 12, MUTED, True)
        text(draw, (x+170, 309), title, 32, color, True, "mm")
        text(draw, (x+170, 365), state, 16, color, True, "mm")
        draw.line((x+35, 404, x+305, 404), fill=(43, 78, 96), width=2)
        text(draw, (x+28, 429), "REASON CODE", 11, MUTED, True)
        wrapped(draw, (x+28, 460), reason, 30, 14, WHITE, True)
    text(draw, (640, 590), "Every alternative remains visible in the durable decision trace.", 21, WHITE, True, "mm")


def scene_7(draw):
    panel(draw, (80, 205, 440, 505), outline=CYAN)
    text(draw, (260, 248), "POLICY VERSION", 13, MUTED, True, "mm")
    text(draw, (260, 315), "privacy-v4.2", 28, CYAN, True, "mm")
    text(draw, (260, 367), "rule DP-17", 16, WHITE, True, "mm")
    text(draw, (260, 412), "effective: 2026-07-01", 14, MUTED, False, "mm")
    node(draw, (700, 355), "DECISION", "DENY", GOLD, 78)
    arrow(draw, (440, 355), (618, 355), CYAN, 4)
    text(draw, (528, 323), "APPLIED_POLICY", 13, CYAN, True, "mm")
    panel(draw, (900, 205, 1200, 505), outline=(64, 102, 120))
    text(draw, (1050, 250), "LATER VERSION", 13, MUTED, True, "mm")
    text(draw, (1050, 315), "privacy-v4.3", 25, WHITE, True, "mm")
    text(draw, (1050, 370), "new run", 16, GREEN, True, "mm")
    text(draw, (1050, 415), "old trace unchanged", 14, MUTED, False, "mm")
    text(draw, (640, 585), "APPEND-ONLY HISTORY  •  NO IN-PLACE REWRITE", 18, GOLD, True, "mm")


def scene_8(draw):
    panel(draw, (54, 145, 1226, 610))
    text(draw, (84, 171), "GET /context-graph/traces/decision_0142", 15, CYAN, True)
    draw.line((84, 207, 1194, 207), fill=(38, 79, 99), width=2)
    path = [(145, 298, "CASE"), (330, 298, "RUN"), (515, 298, "MANIFEST"), (700, 298, "EVIDENCE"), (885, 298, "POLICY"), (1070, 298, "DECISION")]
    for i, (x, y, label) in enumerate(path):
        if i:
            arrow(draw, (path[i-1][0]+45, y), (x-45, y), (52, 139, 166), 3)
        node(draw, (x, y), label, "", GOLD if label == "DECISION" else CYAN, 43)
    fields = [
        (84, "selected_option", '"deny"', GOLD),
        (390, "policy_version", '"privacy-v4.2"', CYAN),
        (725, "reason_codes", '["DP_17"]', WHITE),
        (1010, "integrity_hash_valid", "true", GREEN),
    ]
    for x, key, value, color in fields:
        text(draw, (x, 405), key, 12, MUTED, True)
        text(draw, (x, 441), value, 16, color, True)
    text(draw, (84, 525), "tenant_scope", 12, MUTED, True)
    text(draw, (220, 525), '"marketing"', 15, WHITE, True)
    text(draw, (390, 525), "replay_status", 12, MUTED, True)
    text(draw, (525, 525), '"reconstructed"', 15, GREEN, True)


def scene_9(draw):
    coords = {"CASE": (160, 370), "RUN": (355, 285), "MANIFEST": (585, 370), "EVIDENCE": (805, 260), "POLICY": (805, 485), "DECISION": (1080, 370)}
    links = [("CASE", "RUN"), ("RUN", "MANIFEST"), ("MANIFEST", "EVIDENCE"), ("MANIFEST", "POLICY"), ("EVIDENCE", "DECISION"), ("POLICY", "DECISION")]
    for a, b in links:
        arrow(draw, coords[a], coords[b], (40, 115, 143), 3)
    for label, xy in coords.items():
        node(draw, xy, label, "", GOLD if label == "DECISION" else CYAN, 48)
    text(draw, (640, 580), "KNOW WHAT HAPPENED.  RECONSTRUCT WHY.", 24, WHITE, True, "mm")
    text(draw, (640, 628), "From an answer to an accountable decision.", 18, GOLD, True, "mm")


DRAWERS = [scene_0, scene_1, scene_2, scene_3, scene_4, scene_5, scene_6, scene_7, scene_8, scene_9]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def main() -> None:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required")
    BUILD.mkdir(exist_ok=True)
    segments: list[Path] = []
    for i, scene in enumerate(SCENES):
        image, draw = base(i)
        DRAWERS[i](draw)
        png = BUILD / f"scene_{i+1:02d}.png"
        mp3 = BUILD / f"scene_{i+1:02d}.mp3"
        segment = BUILD / f"scene_{i+1:02d}.mp4"
        image.save(png)
        gTTS(scene.voiceover, lang="en", slow=False).save(mp3)
        frames = scene.duration * FPS
        vf = (
            f"zoompan=z='min(zoom+0.00012,1.035)':x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':d={frames}:s={W}x{H}:fps={FPS},"
            "fade=t=in:st=0:d=0.6,fade=t=out:st="
            f"{scene.duration-0.6}:d=0.6,format=yuv420p"
        )
        run([
            ffmpeg, "-y", "-loop", "1", "-i", str(png), "-i", str(mp3),
            "-filter_complex", f"[0:v]{vf}[v];[1:a]adelay=500|500,apad[a]",
            "-map", "[v]", "-map", "[a]", "-t", str(scene.duration),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-c:a", "aac", "-b:a", "128k", str(segment),
        ])
        segments.append(segment)
    concat = BUILD / "segments.txt"
    concat.write_text("".join(f"file '{p.as_posix()}'\n" for p in segments), encoding="utf-8")
    run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(OUT)])
    print(OUT)


if __name__ == "__main__":
    main()
