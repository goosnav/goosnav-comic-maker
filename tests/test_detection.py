"""Synthetic scan regressions: a line inside a panel must not become its crop.

The fixtures are generated here so the public tests need no private comic scans.
Coordinates describe the outermost source ink, not a detector implementation detail.
"""
from __future__ import annotations

import numpy as np
import pytest
from PIL import Image, ImageDraw

from comic_maker import detection
from comic_maker import detector_base as detector


FRAME = (140, 100, 1060, 790)


def competing_lines(side="bottom", *, border=0, broken=False, wobbly=False):
    """A framed scene with a table/wall line that reaches both outer borders."""
    image = Image.new("L", (1200, 900), 255)
    draw = ImageDraw.Draw(image)
    x0, y0, x1, y1 = FRAME
    if wobbly:
        draw.line([(x0, y0), (370, y0 + 3), (720, y0 - 2), (x1, y0),
                   (x1 - 3, 350), (x1 + 2, 610), (x1, y1),
                   (720, y1 - 3), (380, y1 + 2), (x0, y1),
                   (x0 + 3, 510), (x0 - 2, 300), (x0, y0)],
                  fill=border, width=5)
    else:
        draw.rectangle(FRAME, outline=border, width=5)
    if broken:
        # Pen lifts are wider than a stroke, but the frame still has strong support.
        draw.rectangle((570, y1 - 5, 650, y1 + 5), fill=255)
        draw.rectangle((x0 - 5, 410, x0 + 5, 455), fill=255)
    if side == "bottom":
        draw.line((x0, 650, x1, 650), fill=0, width=9)
    elif side == "top":
        draw.line((x0, 250, x1, 250), fill=0, width=9)
    elif side == "left":
        draw.line((330, y0, 330, y1), fill=0, width=9)
    elif side == "right":
        draw.line((890, y0, 890, y1), fill=0, width=9)
    elif side == "both":
        draw.line((x0, 650, x1, 650), fill=0, width=9)
        draw.line((890, y0, 890, y1), fill=0, width=9)
    elif side == "speech":
        draw.rounded_rectangle((185, 135, 995, 380), radius=25, outline=0, width=6)
        draw.line((550, 380, 510, 460, 610, 380), fill=0, width=5)
    else:
        raise ValueError(side)
    # Art touches the bottom frame. These marks must survive even below a table line.
    draw.ellipse((430, 470, 590, 630), outline=0, width=5)
    draw.line((510, 630, 485, y1, 625, y1), fill=0, width=7)
    draw.rectangle((950, 715, 1010, 765), outline=0, width=4)
    return image


def assert_encloses(box, bounds, tolerance=1):
    x0, y0, x1, y1 = bounds
    assert box.x <= x0 + tolerance, (box, bounds)
    assert box.y <= y0 + tolerance, (box, bounds)
    assert box.x + box.w > x1 - tolerance, (box, bounds)
    assert box.y + box.h > y1 - tolerance, (box, bounds)


@pytest.mark.parametrize("side", ["top", "right", "bottom", "left", "both", "speech"])
def test_inner_scene_lines_do_not_replace_outer_frame(side):
    result = detector.detect(np.asarray(competing_lines(side)))
    assert len(result.boxes) == 1, result.boxes
    assert_encloses(result.boxes[0], FRAME)
    # Keeping the whole source page is not a successful frame detection.
    assert result.boxes[0].area < 0.75 * 1200 * 900


@pytest.mark.parametrize("border,broken,wobbly", [
    (0, True, False), (0, False, True), (185, False, False),
])
def test_imperfect_outer_frame_beats_strong_internal_line(border, broken, wobbly):
    image = competing_lines("bottom", border=border, broken=broken, wobbly=wobbly)
    result = detection.analyze(image.convert("RGB"))
    assert len(result.boxes) == 1, result.boxes
    # Allow a few pixels for the wobbly frame's tiny automatic deskew.
    assert_encloses(result.boxes[0], FRAME, tolerance=5 if wobbly else 1)
    assert result.boxes[0].area < 0.75 * image.width * image.height


def grid_page(columns, rows, *, connected=False):
    image = Image.new("L", (1200, 1000), 255)
    draw = ImageDraw.Draw(image)
    margin, gutter = 65, 55
    pw = (1200 - 2 * margin - (columns - 1) * gutter) // columns
    ph = (1000 - 2 * margin - (rows - 1) * gutter) // rows
    expected = []
    for row in range(rows):
        for column in range(columns):
            x0, y0 = margin + column * (pw + gutter), margin + row * (ph + gutter)
            bounds = (x0, y0, x0 + pw, y0 + ph)
            expected.append(bounds)
            draw.rectangle(bounds, outline=0, width=5)
            draw.ellipse((x0 + 25, y0 + 40, x0 + 100, y0 + 120), outline=0, width=4)
            # Strong scene line in every panel exercises pruning as well as grouping.
            draw.line((x0, y0 + ph * 0.72, x0 + pw, y0 + ph * 0.72), fill=0, width=6)
    if connected and columns > 1:
        # A prop/stroke across the gutter joins otherwise distinct outer contours.
        draw.line((expected[0][2] - 20, 220, expected[1][0] + 20, 220), fill=0, width=5)
    return image, expected


@pytest.mark.parametrize("columns,rows", [(2, 1), (2, 2), (3, 2)])
def test_real_gutters_preserve_separate_panels_and_reading_order(columns, rows):
    image, expected = grid_page(columns, rows)
    result = detector.detect(np.asarray(image))
    assert len(result.boxes) == len(expected), result.boxes
    for box, bounds in zip(result.boxes, expected):
        assert_encloses(box, bounds)
        assert box.area < (bounds[2] - bounds[0]) * (bounds[3] - bounds[1]) * 1.10


def test_art_bridging_one_gutter_does_not_merge_two_panels():
    image, expected = grid_page(2, 1, connected=True)
    result = detector.detect(np.asarray(image))
    assert len(result.boxes) == 2, result.boxes
    for box, bounds in zip(result.boxes, expected):
        assert_encloses(box, bounds)
        assert box.w < 570


def test_shared_border_ambiguity_never_discards_an_outer_strip():
    image = Image.new("L", (1200, 900), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle(FRAME, outline=0, width=5)
    draw.line((600, FRAME[1], 600, FRAME[3]), fill=0, width=5)
    result = detection.analyze(image.convert("RGB"))
    # A central shared line is inherently ambiguous: one scene or two panels.
    # Both interpretations must keep the complete outer bounds and all four corners.
    for x, y in [(FRAME[0], FRAME[1]), (FRAME[2], FRAME[1]),
                 (FRAME[0], FRAME[3]), (FRAME[2], FRAME[3])]:
        assert any(box.x <= x < box.x + box.w and box.y <= y < box.y + box.h
                   for box in result.boxes), result.boxes


def test_duplicate_border_candidates_do_not_discard_the_outer_box():
    outer = detector.Box(100, 100, 900, 650)
    copies = [detector.Box(102, 102, 896, 646, source="contour"),
              detector.Box(104, 104, 892, 642, source="contour")]
    # These are repeated measurements of one border, not two child panels.
    result = detector.prune([outer, *copies])
    assert len(result) == 1
    assert_encloses(result[0], (100, 100, 999, 749))


def test_inset_scene_rectangles_are_not_panels_just_because_they_have_a_gap():
    image = Image.new("L", (1200, 900), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle(FRAME, outline=0, width=5)
    for bounds in [(185, 145, 565, 745), (625, 145, 1015, 745)]:
        draw.rectangle(bounds, outline=0, width=5)
    result = detector.detect(np.asarray(image))
    assert len(result.boxes) == 1, result.boxes
    assert_encloses(result.boxes[0], FRAME)


def test_high_resolution_scan_keeps_the_complete_frame():
    image = competing_lines("both").resize((6600, 5100), Image.Resampling.NEAREST)
    result = detector.detect(np.asarray(image))
    assert len(result.boxes) == 1, result.boxes
    scaled = (FRAME[0] * 5.5, FRAME[1] * 5100 / 900,
              FRAME[2] * 5.5, FRAME[3] * 5100 / 900)
    assert_encloses(result.boxes[0], scaled)


@pytest.mark.parametrize("angle", [-3.0, 3.0])
def test_deskew_does_not_make_inner_lines_the_crop(angle):
    image = competing_lines("bottom").rotate(angle, resample=Image.Resampling.BICUBIC, fillcolor=255)
    result = detector.detect(np.asarray(image))
    assert len(result.boxes) == 1, result.boxes
    assert abs(result.angle + angle) < 0.5
    assert_encloses(result.boxes[0], FRAME, tolerance=5)


def test_large_missing_bottom_section_keeps_art_below_competing_line():
    image = Image.new("L", (1200, 900), 255)
    draw = ImageDraw.Draw(image)
    bounds = (100, 100, 1100, 800)
    draw.rectangle(bounds, outline=0, width=5)
    # Most of the bottom border is absent; the side endpoints still show its extent.
    draw.rectangle((300, 795, 900, 805), fill=255)
    draw.line((100, 600, 1100, 600), fill=0, width=8)
    draw.line((430, 550, 460, 780, 520, 780), fill=0, width=6)
    draw.ellipse((750, 680, 850, 770), outline=0, width=5)
    result = detection.analyze(image.convert("RGB"))
    assert len(result.boxes) == 1, result.boxes
    assert_encloses(result.boxes[0], bounds)
    assert result.warnings, "Recovery of a mostly missing edge must be identified for review"


@pytest.mark.parametrize("gutter", [2, 4])
def test_narrow_paper_gutters_survive_wobbly_borders_and_merged_corner_junctions(gutter):
    image = Image.new("L", (1024, 768), 255)
    draw = ImageDraw.Draw(image)
    expected = []
    for column in range(3):
        x0 = 80 + column * (261 + gutter)
        y0, x1, y1 = 150, x0 + 260, 620
        expected.append((x0, y0, x1, y1))
        # Stroke centers stay two pixels inside the bounds. The narrowest paper
        # corridor remains exactly 2/4 pixels wide, despite hand-drawn wobble.
        draw.line([(x0 + 2, y0 + 2), (x0 + 100, y0 + 4), (x1 - 2, y0 + 2),
                   (x1 - 4, y0 + 120), (x1 - 2, y0 + 300), (x1 - 2, y1 - 2),
                   (x0 + 130, y1 - 4), (x0 + 2, y1 - 2),
                   (x0 + 4, y0 + 300), (x0 + 2, y0 + 100), (x0 + 2, y0 + 2)],
                  fill=0, width=5)
        draw.ellipse((x0 + 50, 280, x0 + 140, 390), outline=0, width=4)
        draw.line((x0 + 95, 390, x0 + 80, 565), fill=0, width=5)
        # A prominent internal line still must not shorten any individual panel.
        draw.line((x0 + 3, 535, x1 - 3, 535), fill=0, width=5)
    result = detector.detect(np.asarray(image))
    assert len(result.boxes) == 3, result.boxes
    for index, (box, bounds) in enumerate(zip(result.boxes, expected)):
        assert_encloses(box, bounds, tolerance=2)
        allowed_bleed = max(3, gutter // 2)
        if index:
            assert box.x >= bounds[0] - allowed_bleed, "The preceding panel must not leak into the crop"
        if index + 1 < len(expected):
            assert box.x + box.w <= bounds[2] + allowed_bleed, "The next panel must not leak into the crop"
