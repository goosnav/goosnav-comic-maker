"""Summary geometry adapted from Comic Carousel Generator. Rendering lives in imaging.py."""
from .detector_base import Box, group_rows
CANVAS_W, CANVAS_H = 1080, 1350
FILL = 0.95
GUTTER = 0.025

def row_plans(boxes: list[Box]) -> dict[str, list[list[int]]]:
    """Which panel indices share a row, per named layout."""
    n = len(boxes)
    index = {id(b): i for i, b in enumerate(boxes)}
    plans = {
        "as-drawn": [[index[id(b)] for b in row] for row in group_rows(boxes)],
        "stack": [[i] for i in range(n)],
        "grid": [list(range(i, min(i + 2, n))) for i in range(0, n, 2)],
    }
    if n == 3:
        plans["one-over-two"] = [[0], [1, 2]]
    return plans


def place(boxes: list[Box], rows: list[list[int]]) -> tuple[list[tuple[int, int, int, int]], float]:
    """Justified rows: every row spans the usable width at one height; the block is
    scaled to fit the usable height and centered. Returns (x, y, w, h) per panel and a
    score = canvas coverage x scale uniformity, so a layout that enlarges one panel at the
    expense of the others does not win on area alone."""
    usable_w, usable_h = CANVAS_W * FILL, CANVAS_H * FILL
    gutter = CANVAS_W * GUTTER
    heights = []
    for row in rows:
        aspects = sum(boxes[i].w / boxes[i].h for i in row)
        heights.append((usable_w - gutter * (len(row) - 1)) / aspects)
    total_h = sum(heights) + gutter * (len(rows) - 1)
    scale = min(1.0, usable_h / total_h)
    block_h = total_h * scale
    block_w = usable_w * scale
    y = (CANVAS_H - block_h) / 2
    placements: dict[int, tuple[int, int, int, int]] = {}
    for row, row_h in zip(rows, heights):
        rh = row_h * scale
        x = (CANVAS_W - block_w) / 2
        for i in row:
            pw = boxes[i].w / boxes[i].h * rh
            placements[i] = (int(round(x)), int(round(y)), int(round(pw)), int(round(rh)))
            x += pw + gutter * scale
        y += rh + gutter * scale
    ordered = [placements[i] for i in range(len(boxes))]
    coverage = sum(w * h for _, _, w, h in ordered) / (CANVAS_W * CANVAS_H)
    scales = [w / boxes[i].w for i, (_, _, w, _) in enumerate(ordered)]
    uniformity = min(scales) / max(scales)  # 1.0 when every panel keeps the same relative size
    return ordered, coverage * uniformity


def choose_layout(boxes: list[Box], layout: str = "auto") -> str:
    plans = row_plans(boxes)
    if layout != "auto":
        return layout if layout in plans else "as-drawn"
    return max(plans, key=lambda name: place(boxes, plans[name])[1])


