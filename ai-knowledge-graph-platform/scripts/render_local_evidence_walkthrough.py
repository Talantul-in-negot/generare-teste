"""Render a silent, locally reproducible MP4 walkthrough from evidence reports."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


W, H, FPS = 1280, 720, 24
BG, PANEL, WHITE, CYAN, GREEN, MUTED = (5, 12, 25), (12, 30, 47), (240, 247, 252), (92, 215, 255), (77, 210, 151), (145, 174, 195)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "arialbd.ttf" if bold else "arial.ttf"
    return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)


def _frame(title: str, lines: list[str], path: Path) -> None:
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((70, 70, W - 70, H - 70), radius=22, fill=PANEL, outline=CYAN, width=3)
    draw.text((120, 125), title, font=_font(38, True), fill=WHITE)
    y = 230
    for line in lines:
        draw.text((140, y), line, font=_font(25), fill=GREEN if line.startswith("✓") else MUTED)
        y += 62
    draw.text((W // 2, H - 125), "Local evidence only — not production-scale or customer outcomes", font=_font(18), fill=MUTED, anchor="mm")
    image.save(path)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts", type=Path, default=Path("artifacts"))
    parser.add_argument("--output", type=Path, default=Path("docs/presentation/local-evidence-walkthrough.mp4"))
    args = parser.parse_args()
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise SystemExit("ffmpeg is required to render the walkthrough")
    artifacts = args.artifacts
    load = json.loads((artifacts / "mcp-graph-fact-load.json").read_text(encoding="utf-8"))
    write = json.loads((artifacts / "governed-write-evidence.json").read_text(encoding="utf-8"))
    golden = json.loads((artifacts / "graph-fact-golden-eval.json").read_text(encoding="utf-8"))
    build = args.output.parent / "local_evidence_walkthrough_build"
    build.mkdir(parents=True, exist_ok=True)
    slides = [
        ("AI Knowledge Graph Platform", ["Authenticated remote MCP", "Tenant-scoped graph retrieval", "Approval-gated operational writes"]),
        ("Fixed Local Knowledge Graph", ["✓ 10 documents", "✓ 20 entities", "✓ 14 relations / conflict evidence", "Isolated tenant: local-evidence"]),
        ("Retrieval Evaluation", [f"✓ {golden['candidate']['questions']}/{golden['candidate']['questions']} fixed graph-fact questions", "Empty-corpus baseline: 0%", "Parameterized, tenant-scoped read templates"]),
        ("MCP Load Measurement", [f"✓ {load['passed']}/{load['total']} successful calls", f"Throughput: {load['throughput_rps']:.2f} req/s", f"p95: {load['p95_latency_ms']:.2f} ms"]),
        ("Governed Operational Writes", [f"✓ Approval: {write['write_approval_requested']['outcome']}", f"✓ Execute + replay: {write['write_executed']['outcome']}", f"✓ Stale protection: {write['stale_version_refusal']['outcome']}", f"✓ Compensation: {write['compensated']['outcome']}"]),
        ("Traceable, Reproducible Evidence", ["Receipts and report artifacts are versioned", "MCP contract and ontology package are exportable", "All claims distinguish local evidence from production results"]),
    ]
    segments = []
    for index, (title, lines) in enumerate(slides, start=1):
        png, mp4 = build / f"slide-{index}.png", build / f"slide-{index}.mp4"
        _frame(title, lines, png)
        subprocess.run([ffmpeg, "-y", "-loop", "1", "-i", str(png), "-t", "6", "-r", str(FPS), "-pix_fmt", "yuv420p", "-c:v", "libx264", str(mp4)], check=True)
        segments.append(mp4)
    concat = build / "segments.txt"
    concat.write_text(
        "".join(f"file '{segment.resolve().as_posix()}'\n" for segment in segments),
        encoding="utf-8",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-c", "copy", str(args.output)], check=True)
    print(args.output)


if __name__ == "__main__":
    main()
