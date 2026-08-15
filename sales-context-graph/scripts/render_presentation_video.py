"""Render the presentation_script.md into a self-contained demo film.

The renderer intentionally uses only local, deterministic assets: Pillow for
the UI-style frames, Windows Speech for the voice-over, and ffmpeg for the
final MP4. It is a presentation artifact, not a product runtime path.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "artifacts" / "presentation_video_work"
OUTPUT = ROOT / "artifacts" / "sales_context_graph_presentation.mp4"
W, H = 1920, 1080

NAVY = "#0d1b35"
NAVY_2 = "#15254e"
BLUE = "#0d5189"
CYAN = "#539dc4"
BRICK = "#dd7159"
PLUM = "#8c3fcc"
SAND = "#e8ded4"
CREAM = "#f0ece8"
WHITE = "#f7f9fc"
MUTED = "#a8b5ca"
GREEN = "#45c486"


def executable(name: str) -> str:
    """Resolve only a locally installed, named rendering executable."""
    path = shutil.which(name)
    if path is None:
        raise RuntimeError(f"required executable is not available: {name}")
    return path


def run_checked(command: list[str], *, cwd: Path) -> None:
    """Run an internally constructed renderer command with checked failures."""
    subprocess.run(command, cwd=str(cwd), check=True)  # noqa: S603 -- no user-controlled command fragments


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "segoeuib.ttf" if bold else "segoeui.ttf"
    return ImageFont.truetype(Path("C:/Windows/Fonts") / name, size)


def wrap(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if draw.textlength(candidate, font=fnt) <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def base(index: int, title: str, kicker: str, *, light: bool = False) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (W, H), CREAM if light else NAVY)
    draw = ImageDraw.Draw(image)
    for y in range(H):
        t = y / H
        if light:
            start, end = "f0ece8", "dfeaf5"
            color = tuple(int(int(start[i : i + 2], 16) * (1 - t) + int(end[i : i + 2], 16) * t) for i in (0, 2, 4))
            draw.line((0, y, W, y), fill="#%02x%02x%02x" % color)
        else:
            draw.line((0, y, W, y), fill=(12 + int(10 * t), 24 + int(16 * t), 48 + int(28 * t)))
    draw.ellipse((1450, -250, 2050, 350), fill=(26, 80, 124), outline=None)
    draw.ellipse((-280, 850, 420, 1450), fill=(44, 42, 85), outline=None)
    text_color = NAVY if light else WHITE
    draw.text((110, 70), f"{index:02d}  /  SALES CONTEXT GRAPH", font=font(24, True), fill=BLUE if light else CYAN)
    draw.text((110, 130), title, font=font(62, True), fill=text_color)
    draw.text((110, 205), kicker, font=font(28), fill="#5c687a" if light else MUTED)
    draw.line((110, 270, 1810, 270), fill=BRICK, width=3)
    draw.text((110, 1018), "Evidence first  ·  tenant-isolated  ·  Showpad-shaped companion", font=font(20), fill="#657187" if light else MUTED)
    return image, draw


def panel(draw: ImageDraw.ImageDraw, xy: tuple[int, int, int, int], title: str, body: list[str], accent: str = BLUE) -> None:
    x1, y1, x2, y2 = xy
    draw.rounded_rectangle(xy, radius=22, fill="#ffffff" if y1 > 280 else "#162b4d", outline="#385273", width=2)
    draw.rectangle((x1, y1, x1 + 8, y2), fill=accent)
    draw.text((x1 + 30, y1 + 26), title, font=font(28, True), fill=WHITE if y1 <= 280 else NAVY)
    y = y1 + 82
    for line in body:
        draw.text((x1 + 30, y), line, font=font(22), fill="#d9e5f4" if y1 <= 280 else "#304057")
        y += 42


def graph_nodes(draw: ImageDraw.ImageDraw, points: list[tuple[int, int, str, str]], edges: list[tuple[int, int, str]]) -> None:
    for a, b, label in edges:
        x1, y1, *_ = points[a]
        x2, y2, *_ = points[b]
        draw.line((x1, y1, x2, y2), fill=CYAN, width=5)
        draw.text(((x1 + x2) // 2, (y1 + y2) // 2 - 25), label, font=font(18, True), fill=SAND)
    for x, y, label, color in points:
        draw.ellipse((x - 24, y - 24, x + 24, y + 24), fill=color, outline=WHITE, width=3)
        draw.text((x + 38, y - 15), label, font=font(22, True), fill=WHITE)


SCENES = [
    ("Click a question. Or press 1-4.", "Quick questions 1-4  |  text-first  |  optional TTS", "The answer appears immediately. Audio is a convenience, never a dependency.", "tts"),
    ("The evidence layer for confident selling.", "A grounded companion for every seller question.", "When the answer matters, confidence is not enough. Evidence is.", "hero"),
    ("Commercial memory is fragmented.", "CRM records  ·  conversations  ·  content engagement", "Sales teams have the data. They do not have the connective tissue.", "sources"),
    ("One graph. One evidence model.", "Ingest → resolve → extract → govern → retrieve", "Every claim carries its source, time, polarity, confidence and tenant boundary.", "architecture"),
    ("Context Graph", "A bounded graph, not an unbounded prompt", "The seller sees claims, budgets and provenance before the answer.", "graph"),
    ("Ask the question that moves the deal.", "Natural language → intent → grounded action", "Explicit context makes fuzzy names safe, reviewable and repeatable.", "ask"),
    ("From one answer to pipeline intelligence.", "Browse Intents  ·  Alerts  ·  temporal views", "Missing stakeholders, objections, conflicts and change become visible signals.", "insights"),
    ("Trust is a product feature.", "Tenant isolation  ·  ACLs  ·  redaction  ·  injection guardrails", "The system can refuse to guess—and that is a successful outcome.", "trust"),
    ("Engineered for the next gate.", "Readiness  ·  queue health  ·  load tests  ·  backups", "Local evidence: Context Graph 57–82 ms HTTP; cloud numbers require a real deploy.", "ops"),
    ("A companion layer for Showpad.", "Content taxonomy in  ·  evidence-backed recommendation out", "The boundary is honest: this is Showpad-shaped, not a packaged OAuth connector.", "showpad"),
    ("Make every seller question answerable.", "Sales Context Graph", "One opportunity. One piece of evidence. One better next step.", "close"),
]


NARRATION = [
    "Sellers can click a frequent question or press its number from one to four. The text answer arrives first; optional voice starts separately, and a slow provider leaves the text untouched.",
    "The future of sales AI is not the loudest answer. It is the answer a seller can trust in front of a buyer, a manager and a customer record.",
    "Commercial context lives in structured CRM fields, messy transcripts and content engagement. The graph connects them without pretending they are equally reliable.",
    "The pipeline is deliberately evidence-first. Names are resolved with bounded candidates, claims retain polarity and time, and every tenant boundary is enforced before retrieval.",
    "This is a real running surface. Context Graph returns the relevant claims, evidence budget, token budget and truncation state before a seller acts.",
    "Ask turns a natural-language question into an intent and a grounded recommendation. Explicit opportunity and buyer context keeps approximate names from silently linking to the wrong deal.",
    "The same evidence model powers cross-deal questions, point-in-time views and proactive alerts. The seller can see what changed and what deserves attention next.",
    "Security is visible in the product: workspace scoping, deny-before-handler checks, redaction, audit events and prompt-injection guardrails. Refusal is part of reliability.",
    "The project has a deploy path and reproducible load tests. The local baseline is measured; cloud latency and capacity are intentionally left to the target Fly, Aura and Redis environment.",
    "For Showpad, this is the connective tissue between readiness and action: content taxonomy in, evidence-backed next step out. The remaining external connector boundary is explicit.",
    "Sales Context Graph turns fragmented commercial memory into grounded action—one opportunity, one piece of evidence, one better next step.",
]


def draw_scene(image: Image.Image, draw: ImageDraw.ImageDraw, kind: str) -> None:
    if kind == "hero":
        draw.text((110, 390), "Grounded answers for", font=font(42), fill=MUTED)
        draw.text((110, 445), "sales teams", font=font(92, True), fill=WHITE)
        draw.rounded_rectangle((1110, 390, 1700, 790), radius=40, fill="#142b4c", outline=CYAN, width=3)
        graph_nodes(draw, [(1210, 535, "buyer", BRICK), (1450, 470, "claim", GREEN), (1480, 660, "asset", PLUM), (1280, 710, "deal", BLUE)], [(0, 1, "said"), (1, 2, "maps"), (3, 1, "has")])
    elif kind == "sources":
        panel(draw, (110, 350, 650, 820), "CRM", ["Account: Volkswagen Group", "Stage: Negotiation", "Owner: Sam Seller", "IDs: deterministic"], BLUE)
        panel(draw, (710, 350, 1250, 820), "Transcript", ["“Volks Wagen”", "pricing objection", "speaker: buyer", "timestamp preserved"], BRICK)
        panel(draw, (1310, 350, 1810, 820), "Content", ["pricing guide", "ROI calculator", "view history", "approval + expiry"], PLUM)
    elif kind == "architecture":
        labels = [(160, 500, "CRM", BLUE), (470, 500, "Transcript", BRICK), (780, 500, "Resolver", CYAN), (1090, 500, "Claims", GREEN), (1400, 500, "Graph", PLUM), (1670, 500, "Action", BRICK)]
        for x, y, label, color in labels:
            draw.rounded_rectangle((x - 110, y - 70, x + 110, y + 70), radius=20, fill=color)
            draw.text((x - 70, y - 16), label, font=font(26, True), fill=WHITE)
        for i in range(len(labels) - 1):
            draw.line((labels[i][0] + 115, 500, labels[i + 1][0] - 115, 500), fill=SAND, width=7)
    elif kind == "graph":
        panel(draw, (110, 330, 550, 860), "Context Graph", ["Workspace: ws-demo", "Conversation: seeded VW", "Build", "nodes_used: 2 / 50", "tokens_used: 6 / 4000", "truncated: false"], BLUE)
        graph_nodes(draw, [(850, 510, "spk_1", BLUE), (1250, 420, "volkswagen", CYAN), (1250, 650, "pricing", BRICK), (1590, 540, "evidence", GREEN)], [(0, 1, "MENTIONS_ORG"), (0, 2, "RAISED_OBJECTION"), (2, 3, "cited")])
    elif kind == "ask":
        panel(draw, (110, 350, 760, 820), "Ask", ["What content should I send?", "Optional context expanded", "opportunity_id: provided", "buyer_contact_id: provided", "Ask"], BLUE)
        panel(draw, (850, 350, 1810, 820), "Answer · confidence 0.90", ["intent: recommend-content", "objection: pricing", "recommended: Enterprise Pricing ROI Calculator", "excluded: already viewed asset", "disclaimer: verify evidence", "requires_human_review: true"], GREEN)
    elif kind == "tts":
        panel(draw, (110, 350, 900, 820), "Click or press a number", ["[1] What objections are open?", "[2] Who have we not engaged?", "[3] What content should I send?", "[4] What changed since last call?"], BLUE)
        panel(draw, (980, 350, 1810, 820), "Voice output", ["Answer text: immediate", "TTS: optional", "Timeout: 2 seconds", "Fallback: text remains", "No fabricated audio"], GREEN)
    elif kind == "insights":
        panel(draw, (110, 350, 880, 820), "Browse Intents", ["Who haven't we talked to?", "Top objections", "Open conflicts", "What's new / As-of", "12 live intents"], BLUE)
        panel(draw, (950, 350, 1810, 820), "Alerts digest", ["single_threaded_deal", "objection_without_follow_up", "unresolved conflict", "review before action"], BRICK)
    elif kind == "trust":
        panel(draw, (110, 350, 850, 820), "Guardrails", ["workspace_id on every query", "deny before handler", "division policy", "PII redaction", "injection guardrail"], PLUM)
        panel(draw, (920, 350, 1810, 820), "Refusal states", ["ambiguous name", "no citable evidence", "review queue", "audit event", "safe failure"], BRICK)
    elif kind == "ops":
        panel(draw, (110, 350, 700, 820), "Readiness", ["/health 200", "/ready 200", "Redis worker heartbeat", "schema indexes online"], GREEN)
        panel(draw, (760, 350, 1240, 820), "Local baseline", ["57–82 ms HTTP", "300 Claims", "single machine", "not cloud result"], BLUE)
        panel(draw, (1300, 350, 1810, 820), "Load gate", ["p95 / p99", "throughput", "error rate", "queue lag", "cost / request"], BRICK)
    elif kind == "showpad":
        draw.rounded_rectangle((180, 390, 780, 760), radius=30, fill="#182e50", outline=CYAN, width=3)
        draw.text((260, 510), "Showpad", font=font(64, True), fill=WHITE)
        draw.text((260, 600), "content + engagement", font=font(28), fill=MUTED)
        draw.line((820, 575, 1100, 575), fill=SAND, width=8)
        draw.polygon((1100, 575, 1050, 545, 1050, 605), fill=SAND)
        draw.rounded_rectangle((1140, 390, 1740, 760), radius=30, fill="#ffffff", outline=GREEN, width=3)
        draw.text((1210, 490), "Grounded next step", font=font(38, True), fill=NAVY)
        draw.text((1210, 590), "evidence → recommendation", font=font(28), fill=BLUE)
    elif kind == "close":
        draw.text((110, 405), "One better next step.", font=font(78, True), fill=WHITE)
        draw.text((110, 530), "Built from evidence.", font=font(54), fill=CYAN)
        draw.rounded_rectangle((1130, 390, 1710, 750), radius=50, fill="#162d51", outline=BRICK, width=4)
        graph_nodes(draw, [(1270, 560, "claim", GREEN), (1530, 470, "asset", PLUM), (1530, 650, "seller", BRICK)], [(0, 1, "supports"), (0, 2, "guides")])


def synthesize(work: Path) -> None:
    ps = work / "synthesize.ps1"
    ps.write_text(
        "Add-Type -AssemblyName System.Speech\n"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer\n"
        "$s.Rate = -1\n"
        "Get-ChildItem -Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) -Filter '*.txt' | Sort-Object Name | ForEach-Object {\n"
        "  $out = Join-Path $_.DirectoryName ($_.BaseName + '.wav')\n"
        "  $s.SetOutputToWaveFile($out)\n"
        "  $s.Speak((Get-Content -Raw $_.FullName))\n"
        "  $s.SetOutputToNull()\n"
        "}\n$s.Dispose()\n",
        encoding="utf-8",
    )
    run_checked([executable("powershell.exe"), "-NoProfile", "-File", str(ps)], cwd=work)


def run() -> None:
    if WORK.exists():
        shutil.rmtree(WORK)
    WORK.mkdir(parents=True)
    frames = []
    for i, (title, kicker, _, kind) in enumerate(SCENES, 1):
        image, draw = base(i, title, kicker, light=kind in {"sources", "architecture", "ask", "insights"})
        draw_scene(image, draw, kind)
        frame = WORK / f"scene_{i:02d}.png"
        image.save(frame, optimize=True)
        frames.append(frame)
        (WORK / f"scene_{i:02d}.txt").write_text(NARRATION[i - 1], encoding="utf-8")

    synthesize(WORK)
    clips = []
    for i, frame in enumerate(frames, 1):
        wav = WORK / f"scene_{i:02d}.wav"
        clip = WORK / f"clip_{i:02d}.mp4"
        run_checked([
            executable("ffmpeg"), "-y", "-loglevel", "error", "-loop", "1", "-i", str(frame),
            "-i", str(wav), "-c:v", "libx264", "-tune", "stillimage", "-c:a", "aac",
            "-b:a", "160k", "-pix_fmt", "yuv420p", "-r", "30", "-shortest", str(clip),
        ], cwd=WORK)
        clips.append(clip)
    manifest = WORK / "concat.txt"
    manifest.write_text("\n".join(f"file '{p.as_posix()}'" for p in clips), encoding="utf-8")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    run_checked([
        executable("ffmpeg"), "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
        "-i", str(manifest), "-c", "copy", str(OUTPUT),
    ], cwd=WORK)
    metadata = {"output": str(OUTPUT), "scenes": len(SCENES), "resolution": f"{W}x{H}", "voiceover": "Windows SpeechSynthesizer", "source": "docs/presentation_script.md"}
    (WORK / "render_metadata.json").write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(json.dumps(metadata))


if __name__ == "__main__":
    run()
