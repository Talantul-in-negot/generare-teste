"""Reconstruct the WPP Context Graph movie frame shown around 1:48."""

from pathlib import Path
import textwrap

from PIL import Image, ImageDraw, ImageFont


W, H = 1280, 720
OUT = Path("docs/presentation/context_graph_movie_build/scene_05.png")
BG, PANEL = (5, 12, 25), (9, 25, 40)
CYAN, GOLD = (95, 221, 255), (255, 201, 92)
WHITE, MUTED, GREEN = (242, 248, 252), (148, 177, 197), (80, 211, 154)


def font(size: int, bold: bool = False):
    return ImageFont.truetype(
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf", size
    )


def text(draw, xy, value, size=20, color=WHITE, bold=False, anchor=None):
    draw.text(xy, value, font=font(size, bold), fill=color, anchor=anchor)


def panel(draw, box, outline=(32, 72, 94), fill=PANEL, radius=10, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def main():
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    for x in range(0, W, 64):
        draw.line((x, 0, x, H), fill=(10, 29, 47), width=1)
    for y in range(0, H, 48):
        draw.line((0, y, W, y), fill=(10, 29, 47), width=1)

    text(draw, (54, 35), "CONTEXT GRAPH  /  LIVE RETRIEVAL TRACE", 16, CYAN, True)
    text(draw, (54, 67), "05", 16, GOLD, True)
    text(draw, (86, 63), "The API Returns a Grounded Answer", 30, WHITE, True)
    text(draw, (1222, 43), "TENANT: MARKETING", 13, MUTED, True, "ra")
    for i in range(10):
        x1 = 54 + i * 115
        draw.line((x1, 112, x1 + 102, 112), fill=GOLD if i <= 4 else (35, 66, 84), width=4)

    answer = (
        "The campaign placement is not permitted under the governing Statement of Work: "
        "it strictly excludes gambling and sports-betting placements. The Campaign Brief "
        "conflicts by approving companion-app adjacency. The Data Privacy Policy also "
        "prohibits gambling-adjacent behavioural inference."
    )
    panel(draw, (65, 155, 520, 590))
    text(draw, (95, 185), "LIVE API RESPONSE", 13, GOLD, True)
    draw.multiline_text((95, 225), textwrap.fill(answer, 48), font=font(17), fill=WHITE, spacing=6)

    panel(draw, (635, 205, 1195, 535), outline=GREEN)
    text(draw, (670, 240), "RESPONSE METADATA", 13, GREEN, True)
    fields = [
        ("retrieval_mode", "local"),
        ("model_version", "live capture required"),
        ("latency_ms", "n/a"),
        ("citations", "4"),
        ("decision", "do not overstate"),
    ]
    for i, (key, value) in enumerate(fields):
        y = 295 + i * 44
        text(draw, (670, y), key, 14, MUTED)
        text(draw, (1135, y), value, 16, GREEN if key == "decision" else WHITE, True, "ra")

    text(draw, (54, 665), "Reconstructed from the WPP scenario; live trace metadata is unavailable locally.", 13, MUTED)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
