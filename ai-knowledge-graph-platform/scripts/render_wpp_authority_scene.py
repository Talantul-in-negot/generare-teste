"""Render a static WPP/AdTech knowledge-graph presentation scene."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


W, H = 1600, 900
OUT = Path("docs/presentation/wpp_authority_graph.png")

BG = "#06121f"
PANEL = "#0b1d2e"
CYAN = "#5fddff"
GOLD = "#ffc95c"
WHITE = "#f2f8fc"
MUTED = "#94b1c5"
RED = "#ff6d74"
GREEN = "#50d39a"


def font(size: int, bold: bool = False):
    name = "arialbd.ttf" if bold else "arial.ttf"
    return ImageFont.truetype(f"C:/Windows/Fonts/{name}", size)


def text(draw, xy, value, size, color=WHITE, bold=False, anchor=None):
    draw.text(xy, value, fill=color, font=font(size, bold), anchor=anchor)


def node(draw, box, title, subtitle, authority, color):
    x1, y1, x2, y2 = box
    draw.rounded_rectangle(box, 24, fill=PANEL, outline=color, width=4)
    draw.rounded_rectangle((x1 + 18, y1 + 18, x2 - 18, y1 + 68), 12, fill=color)
    text(draw, ((x1 + x2) // 2, y1 + 43), title, 22, BG, True, "mm")
    y = y1 + 105
    for line in subtitle:
        text(draw, ((x1 + x2) // 2, y), line, 18, WHITE, False, "mm")
        y += 29
    draw.rounded_rectangle((x1 + 30, y2 - 58, x2 - 30, y2 - 25), 8, fill=GOLD)
    text(draw, ((x1 + x2) // 2, y2 - 42), authority, 14, BG, True, "mm")


def arrow(draw, start, end, label, color=CYAN):
    draw.line((*start, *end), fill=color, width=5)
    x2, y2 = end
    draw.polygon([(x2, y2), (x2 - 18, y2 - 10), (x2 - 18, y2 + 10)], fill=color)
    text(draw, ((start[0] + end[0]) // 2, start[1] - 22), label, 13, color, True, "mm")


def main():
    image = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(image)
    for x in range(0, W, 64):
        draw.line((x, 0, x, H), fill="#0b2134", width=1)
    for y in range(0, H, 48):
        draw.line((0, y, W, y), fill="#0b2134", width=1)

    text(draw, (70, 52), "WPP / ADTECH COMPLIANCE", 20, CYAN, True)
    text(draw, (70, 92), "Nova Beverages — Authority & Contradiction Graph", 38, WHITE, True)
    text(draw, (70, 143), "Marketing tenant · EU Q3 SummerRush · information nodes from the WPP scenario", 18, MUTED)

    nodes = [
        ((70, 245, 410, 560), "SOW", ["Binding contract", "Excludes gambling /", "sports-betting placements"], "AUTHORITY 1", "#4e79a7"),
        ((455, 245, 795, 560), "Data Privacy Policy", ["Legally binding", "Prohibits gambling-adjacent", "behavioral inference"], "AUTHORITY 2", "#2f8f9d"),
        ((840, 245, 1180, 560), "Brand Guideline", ["Global creative standards", "Allows local flexibility", "Defers to the SOW"], "AUTHORITY 3", "#687887"),
        ((1225, 245, 1565, 560), "Campaign Brief", ["EU Q3 SummerRush", "Approves sports-betting", "companion-app adjacency"], "AUTHORITY 4  / WARNING", RED),
    ]
    for args in nodes:
        node(draw, *args)
    arrow(draw, (410, 400), (455, 400), "GOVERNS")
    arrow(draw, (795, 400), (840, 400), "SUPERSEDES")
    arrow(draw, (1180, 400), (1225, 400), "CONSTRAINS")

    draw.rounded_rectangle((70, 640, 1530, 805), 22, fill="#2a1b1d", outline=RED, width=3)
    text(draw, (105, 682), "WARNING  /  TWO INDEPENDENT CONFLICTS DETECTED", 21, GOLD, True)
    text(draw, (105, 727), "C01 Contract breach: SOW §2 excludes sports-betting placements; the Campaign Brief permits them.", 19, WHITE)
    text(draw, (105, 765), "C02 Privacy violation: DPP §3 prohibits gambling-adjacent inference implied by that same placement.", 19, WHITE)
    text(draw, (1495, 850), "Static presentation frame · source: docs/presentation/wpp_pitch.js", 14, MUTED, False, "rs")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
