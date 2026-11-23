"""Panel detection with classical corner/edge machine vision. No AI.

Pipeline (all on a 1600 px wide working copy, boxes scaled back to full res):
ink mask -> deskew (Hough line angles) -> horizontal/vertical line masks ->
corner junctions -> rectangles from corner quadruples, verified by border
coverage -> contour candidates through the same verification -> prune merged
containers and nested rectangles -> reading order -> crop margin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import ceil, floor
from pathlib import Path

import cv2
import numpy as np

WORK_WIDTH = 1600
MIN_DESKEW_DEGREES = 0.25      # ignore scanner noise smaller than this
MAX_DESKEW_DEGREES = 12.0      # still a scan; beyond this ask the user to straighten it
CANVAS_MARGIN_FRACTION = 0.006   # white bleed kept outside the drawn border
BORDER_REACH_FRACTION = 0.05     # how far outside a box the border stroke may still run
BORDER_INK_SHARE = 0.25          # a line that far out is still the border at this ink share
BORDER_GAP_TOLERANCE = 3         # pen lifts and paper speckle inside the walk
CONTAINER_CHILD_MIN_COVERAGE = 0.60  # align with the weak-border confidence threshold
MIN_AREA, MAX_AREA = 0.015, 0.90  # fraction of the page
MIN_ASPECT, MAX_ASPECT = 0.25, 4.0


@dataclass
class Box:
    x: int
    y: int
    w: int
    h: int
    coverage: float = 1.0
    min_coverage: float = 1.0
    source: str = "corner"

    @property
    def area(self) -> int:
        return self.w * self.h

    def intersection(self, other: "Box") -> int:
        ix = max(0, min(self.x + self.w, other.x + other.w) - max(self.x, other.x))
        iy = max(0, min(self.y + self.h, other.y + other.h) - max(self.y, other.y))
        return ix * iy

    def as_dict(self) -> dict:
        return {"x": self.x, "y": self.y, "w": self.w, "h": self.h}


@dataclass
class Detection:
    boxes: list[Box]
    angle: float  # degrees the page was rotated to straighten it
    render_image: np.ndarray  # deskewed full-resolution BGR source image
    detection_image: np.ndarray  # deskewed full-resolution grayscale analysis image
    confident: bool = True
    reasons: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- loading

def load_gray(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)  # Unicode-safe on Windows
    gray = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
    if gray is None:
        raise ValueError(f"Not an image I can read: {path}")
    return normalize_levels(gray)


def _load_color(path: str | Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)  # Unicode-safe on Windows
    color = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if color is None:
        raise ValueError(f"Not an image I can read: {path}")
    return color


def normalize_levels(gray: np.ndarray) -> np.ndarray:
    """Push paper to white and ink to black. No-op for already-binary scans."""
    binary_share = np.mean((gray == 0) | (gray == 255))
    if binary_share > 0.99:
        return gray
    black, white = np.percentile(gray, [0.5, 60.0])
    if white - black < 32:
        return gray
    stretched = (gray.astype(np.float32) - black) * (255.0 / (white - black))
    return np.clip(stretched, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- masks

def _shrink(gray: np.ndarray) -> tuple[np.ndarray, float]:
    scale = WORK_WIDTH / gray.shape[1]
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    return small, scale


def ink_mask(small: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(small, (3, 3), 0)
    return cv2.adaptiveThreshold(blur, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 15)


def estimate_skew(ink: np.ndarray) -> float:
    """Length-squared-weighted tilt of long panel-border strokes."""
    w = ink.shape[1]
    lines = cv2.HoughLinesP(ink, 1, np.pi / 720, threshold=60,
                            minLineLength=int(w * 0.08), maxLineGap=int(w * 0.008))
    if lines is None:
        return 0.0
    angles, weights = [], []
    for x0, y0, x1, y1 in lines[:, 0]:
        a = np.degrees(np.arctan2(y1 - y0, x1 - x0)) % 90.0
        if a > 45.0:
            a -= 90.0
        if abs(a) <= MAX_DESKEW_DEGREES:
            angles.append(a)
            length = float(np.hypot(x1 - x0, y1 - y0))
            weights.append(length * length)
    if not angles:
        return 0.0
    order = np.argsort(angles)
    cumulative = np.cumsum(np.array(weights)[order])
    return float(np.array(angles)[order][np.searchsorted(cumulative, cumulative[-1] / 2)])


def deskew(image: np.ndarray, angle: float) -> np.ndarray:
    h, w = image.shape[:2]
    matrix = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(image, matrix, (w, h), flags=cv2.INTER_LINEAR,
                          borderMode=cv2.BORDER_CONSTANT, borderValue=(255, 255, 255) if image.ndim == 3 else 255)


def line_masks(ink: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    w = ink.shape[1]
    length = max(int(w * 0.025), 15)
    bridge = max(int(w * 0.006), 4)  # pen-lift gaps only; never as wide as a gutter
    rect = cv2.MORPH_RECT
    k = cv2.getStructuringElement
    horizontal = cv2.morphologyEx(cv2.morphologyEx(ink, cv2.MORPH_CLOSE, k(rect, (1, 3))), cv2.MORPH_OPEN, k(rect, (length, 1)))
    vertical = cv2.morphologyEx(cv2.morphologyEx(ink, cv2.MORPH_CLOSE, k(rect, (3, 1))), cv2.MORPH_OPEN, k(rect, (1, length)))
    horizontal = cv2.morphologyEx(horizontal, cv2.MORPH_CLOSE, k(rect, (bridge, 1)))
    vertical = cv2.morphologyEx(vertical, cv2.MORPH_CLOSE, k(rect, (1, bridge)))
    return horizontal, vertical


# ---------------------------------------------------------------- corners

def find_corners(horizontal: np.ndarray, vertical: np.ndarray) -> list[tuple[int, int, set[str]]]:
    """Junctions of horizontal and vertical lines, typed by which arms exist."""
    h, w = horizontal.shape
    d = max(int(w * 0.01), 4)
    reach = max(int(w * 0.03), 12)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2 * d + 1, 2 * d + 1))
    junctions = cv2.bitwise_and(cv2.dilate(horizontal, kernel), cv2.dilate(vertical, kernel))
    count, _labels, _stats, centroids = cv2.connectedComponentsWithStats(junctions)
    corners = []
    for i in range(1, count):
        x, y = (int(v) for v in centroids[i])
        right = horizontal[max(0, y - d):y + d + 1, min(w, x + d + 1):min(w, x + reach)].any()
        left = horizontal[max(0, y - d):y + d + 1, max(0, x - reach):max(0, x - d)].any()
        down = vertical[min(h, y + d + 1):min(h, y + reach), max(0, x - d):x + d + 1].any()
        up = vertical[max(0, y - reach):max(0, y - d), max(0, x - d):x + d + 1].any()
        types = {name for name, ok in (("TL", right and down), ("TR", left and down),
                                       ("BL", right and up), ("BR", left and up)) if ok}
        if types:
            corners.append((x, y, types))
    return corners


def border_coverage(mask: np.ndarray, x0: int, y0: int, x1: int, y1: int, band: int) -> list[float]:
    def share(segment: np.ndarray) -> float:
        if segment.size == 0:
            return 0.0
        along = segment.any(axis=0) if segment.shape[0] <= segment.shape[1] else segment.any(axis=1)
        return float(along.mean())

    top = share(mask[max(0, y0 - band):y0 + band + 1, x0:x1 + 1])
    right = share(mask[y0:y1 + 1, max(0, x1 - band):x1 + band + 1])
    bottom = share(mask[max(0, y1 - band):y1 + band + 1, x0:x1 + 1])
    left = share(mask[y0:y1 + 1, max(0, x0 - band):x0 + band + 1])
    return [top, right, bottom, left]


def _verified(mask: np.ndarray, x0: int, y0: int, x1: int, y1: int, band: int, source: str) -> Box | None:
    h, w = mask.shape
    bw, bh = x1 - x0, y1 - y0
    if bw <= 0 or bh <= 0:
        return None
    area = bw * bh / (w * h)
    if not (MIN_AREA <= area <= MAX_AREA) or not (MIN_ASPECT <= bw / bh <= MAX_ASPECT):
        return None
    cov = border_coverage(mask, x0, y0, x1, y1, band)
    if min(cov) < 0.5 or sum(cov) / 4 < 0.8:
        return None
    return Box(x0, y0, bw, bh, coverage=sum(cov) / 4, min_coverage=min(cov), source=source)


def corner_candidates(corners, mask: np.ndarray) -> list[Box]:
    h, w = mask.shape
    tol = max(int(w * 0.01), 6)
    band = max(int(w * 0.006), 4)
    tls = [c for c in corners if "TL" in c[2]]
    trs = [c for c in corners if "TR" in c[2]]
    bls = [c for c in corners if "BL" in c[2]]
    brs = [c for c in corners if "BR" in c[2]]
    found: list[Box] = []
    for x0, y0, _ in tls:
        for x1, y1, _ in trs:
            if x1 - x0 < w * 0.08 or abs(y1 - y0) > tol:
                continue
            for x2, y2, _ in bls:
                if y2 - y0 < h * 0.08 or abs(x2 - x0) > tol:
                    continue
                if not any(abs(x3 - x1) <= tol and abs(y3 - y2) <= tol for x3, y3, _ in brs):
                    continue
                box = _verified(mask, x0, y0, x1, y2, band, "corner")
                if box:
                    found.append(box)
    return found


def contour_candidates(ink: np.ndarray, mask: np.ndarray, partial: list[Box] | None = None) -> list[Box]:
    """Second source: verified rectangular contours from the full hierarchy."""
    w = mask.shape[1]
    band = max(int(w * 0.006), 4)
    closed = cv2.morphologyEx(ink, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    # RETR_LIST recovers an individual panel even when artwork touches another border
    # and makes the whole row one external contour. Verification and pruning remove
    # speech bubbles, nested artwork, and duplicate inner/outer border contours.
    contours, _ = cv2.findContours(closed, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    found = []
    for contour in contours:
        x, y, bw, bh = cv2.boundingRect(contour)
        if bw * bh < MIN_AREA * ink.size:
            continue
        if cv2.contourArea(cv2.convexHull(contour)) / (bw * bh) < 0.85:
            continue
        box = _verified(mask, x, y, x + bw, y + bh, band, "contour")
        if box:
            found.append(box)
        elif partial is not None and (MIN_AREA <= bw * bh / ink.size <= MAX_AREA
                                      and MIN_ASPECT <= bw / bh <= MAX_ASPECT):
            cov = border_coverage(mask, x, y, x + bw, y + bh, band)
            if sum(c >= .85 for c in cov) == 3 and min(cov) < .5:
                partial.append(Box(x, y, bw, bh, sum(cov) / 4, min(cov), "partial contour"))
    return found


def _distinct_candidates(candidates: list[Box]) -> list[Box]:
    """One exterior measurement per frame, before reasoning about child panels.

    Corner junctions and the inner/outer contours often describe the same border.
    Counting their areas as separate children used to delete the real outer frame.
    Keep the largest measurement so a stronger inner stroke cannot tighten a crop.
    """
    unique: list[Box] = []
    for box in sorted(candidates, key=lambda b: -b.area):
        if not any(box.intersection(other) /
                   (box.area + other.area - box.intersection(other)) >= .90
                   for other in unique):
            unique.append(box)
    return unique


def _independent(candidates: list[Box]) -> list[Box]:
    """Largest non-overlapping rectangles; duplicates never inflate covered area."""
    out: list[Box] = []
    for box in sorted(candidates, key=lambda b: -b.area):
        if not any(box.intersection(other) > .10 * min(box.area, other.area) for other in out):
            out.append(box)
    return out


def _separated_children(parent: Box, children: list[Box], ink: np.ndarray) -> bool:
    """Only split an enclosing rectangle at a real, open paper gutter.

    A table line/shared edge is not a gutter. Neither is white space between two
    speech bubbles: a gutter must also interrupt both opposite parent borders.
    This intentionally keeps a shared-border layout as one conservative crop
    rather than gambling that a scene line is a panel boundary.
    """
    height, width = ink.shape
    band = max(3, round(width * .006))
    for vertical in (True, False):
        start = (lambda b: b.x) if vertical else (lambda b: b.y)
        end = (lambda b: b.x + b.w) if vertical else (lambda b: b.y + b.h)
        gaps = {(end(a), start(b)) for a in children for b in children
                if a is not b and start(a) < start(b) and end(a) < end(b)
                and start(b) - end(a) >= -2 * band
                and start(parent) < end(a) < end(parent)
                and start(parent) < start(b) < end(parent)}
        cuts: set[int] = set()
        for near_left, near_right in sorted(gaps):
            # Corner centroids can overshoot a narrow gutter. Search for the
            # actual paper near those edges, including nominally overlapping
            # boxes. All three tests must pass along the SAME row/column.
            lo = max(start(parent) + band, min(near_left, near_right) - 2 * band)
            hi = min(end(parent) - band, max(near_left, near_right) + 2 * band + 1)
            if hi <= lo:
                continue
            if vertical:
                strip = ink[max(0, parent.y):min(height, parent.y + parent.h), lo:hi]
                first = ink[max(0, parent.y - band):min(height, parent.y + band + 1), lo:hi]
                last = ink[max(0, parent.y + parent.h - band):min(height, parent.y + parent.h + band + 1), lo:hi]
            else:
                strip = ink[lo:hi, max(0, parent.x):min(width, parent.x + parent.w)]
                first = ink[lo:hi, max(0, parent.x - band):min(width, parent.x + band + 1)]
                last = ink[lo:hi, max(0, parent.x + parent.w - band):min(width, parent.x + parent.w + band + 1)]
            if any(not part.size for part in (strip, first, last)):
                continue
            axis = 0 if vertical else 1
            paper = ((np.count_nonzero(strip, axis=axis) / strip.shape[axis] <= .04) &
                     (np.count_nonzero(first, axis=axis) / first.shape[axis] <= .08) &
                     (np.count_nonzero(last, axis=axis) / last.shape[axis] <= .08))
            transitions = np.diff(np.pad(paper.astype(np.int8), (1, 1)))
            for run_start, run_end in zip(np.flatnonzero(transitions == 1), np.flatnonzero(transitions == -1)):
                cuts.add(lo + (int(run_start) + int(run_end) - 1) // 2)
        for cut in sorted(cuts):
            left = [b for b in children if end(b) <= cut + 2 * band and start(b) < cut - 2 * band]
            right = [b for b in children if start(b) >= cut - 2 * band and end(b) > cut + 2 * band]
            if not left or not right:
                continue
            # A spurious rectangle can start at a scene line in the preceding
            # panel and cross a real gutter. Its supporting panel extends outside
            # the spurious rectangle; count only the part actually inside it.
            clipped = []
            for child in left + right:
                x0, y0 = max(parent.x, child.x), max(parent.y, child.y)
                x1 = min(parent.x + parent.w, child.x + child.w)
                y1 = min(parent.y + parent.h, child.y + child.h)
                if x1 > x0 and y1 > y0:
                    clipped.append(Box(x0, y0, x1 - x0, y1 - y0))
            separate = _independent(clipped)
            if len(separate) < 2 or sum(b.area for b in separate) < .50 * parent.area:
                continue
            # Inset artwork must not be allowed to discard any outer strip.
            if (min(b.x for b in separate) <= parent.x + 2 * band and
                    min(b.y for b in separate) <= parent.y + 2 * band and
                    max(b.x + b.w for b in separate) >= parent.x + parent.w - 2 * band and
                    max(b.y + b.h for b in separate) >= parent.y + parent.h - 2 * band):
                return True
    return False


def prune(candidates: list[Box], ink: np.ndarray | None = None) -> list[Box]:
    """Prefer complete outer frames unless independent panels have real gutters."""
    unique = _distinct_candidates(candidates)
    kept: list[Box] = []
    for box in unique:
        children = [o for o in unique if o is not box
                    and o.min_coverage >= CONTAINER_CHILD_MIN_COVERAGE
                    and .08 * box.area <= box.intersection(o) < .95 * box.area]
        if ink is not None and len(children) >= 2 and _separated_children(box, children, ink):
            continue
        kept.append(box)
    final: list[Box] = []
    for box in kept:  # already largest first
        if not any(box.intersection(other) >= .80 * box.area for other in final):
            final.append(box)
    return final


def recover_partial_frames(boxes: list[Box], partial: list[Box], mask: np.ndarray) -> list[Box]:
    """Recover one damaged outer edge without trusting arbitrary open artwork.

    The other three sides must coincide with an already verified frame. A
    recovered box retains its weak-edge score and therefore requires review.
    """
    out = list(boxes)
    band = max(4, round(mask.shape[1] * .006))
    for candidate in sorted(partial, key=lambda b: -b.area):
        cov = border_coverage(mask, candidate.x, candidate.y,
                              candidate.x + candidate.w, candidate.y + candidate.h, band)
        outer_edges = [candidate.y, candidate.x + candidate.w,
                       candidate.y + candidate.h, candidate.x]
        for i, box in enumerate(out):
            if not 1.05 * box.area <= candidate.area <= 2.5 * box.area:
                continue
            if candidate.intersection(box) < .98 * box.area:
                continue
            edges = [box.y, box.x + box.w, box.y + box.h, box.x]
            if any(abs(a - b) > 2 * band for a, b, strength in zip(edges, outer_edges, cov) if strength >= .85):
                continue
            if any(j != i and candidate.intersection(other) > .05 * min(candidate.area, other.area)
                   for j, other in enumerate(out)):
                continue
            out[i] = candidate
            break
    return out


# ---------------------------------------------------------------- ordering

def group_rows(boxes: list[Box]) -> list[list[Box]]:
    """Rows top-to-bottom, boxes left-to-right. Same row = vertical overlap >= 50% of the smaller box."""
    rows: list[list[Box]] = []
    for box in sorted(boxes, key=lambda b: b.y):
        for row in rows:
            ref = row[0]
            overlap = min(box.y + box.h, ref.y + ref.h) - max(box.y, ref.y)
            if overlap >= 0.5 * min(box.h, ref.h):
                row.append(box)
                break
        else:
            rows.append([box])
    for row in rows:
        row.sort(key=lambda b: b.x)
    return rows


def reading_order(boxes: list[Box]) -> list[Box]:
    return [box for row in group_rows(boxes) for box in row]


def _edge_line(ink: np.ndarray, box: Box, side: str, offset: int, samples: int = 60) -> float:
    """Share of the sampled edge that is still ink, `offset` pixels outside the box."""
    h, w = ink.shape
    if side in ("top", "bottom"):
        y = (box.y - offset) if side == "top" else (box.y + box.h - 1 + offset)
        if not 0 <= y < h:
            return 0.0
        xs = np.linspace(box.x + box.w * 0.08, box.x + box.w * 0.92, samples).astype(int).clip(0, w - 1)
        return float(ink[y, xs].mean())
    x = (box.x - offset) if side == "left" else (box.x + box.w - 1 + offset)
    if not 0 <= x < w:
        return 0.0
    ys = np.linspace(box.y + box.h * 0.08, box.y + box.h * 0.92, samples).astype(int).clip(0, h - 1)
    return float(ink[ys, x].mean())


def _growth_limits(box: Box, boxes: list[Box], want: int) -> dict[str, int]:
    """Divide the available gutter between neighbors, before either crop grows."""
    side = dict(left=want, right=want, top=want, bottom=want)
    for other in boxes:
        if other is box:
            continue
        if min(box.y + box.h, other.y + other.h) > max(box.y, other.y):
            if other.x < box.x and other.x + other.w < box.x + box.w:
                side["left"] = min(side["left"], max(0, (box.x - other.x - other.w) // 2))
            elif other.x > box.x and other.x + other.w > box.x + box.w:
                side["right"] = min(side["right"], max(0, (other.x - box.x - box.w) // 2))
        if min(box.x + box.w, other.x + other.w) > max(box.x, other.x):
            if other.y < box.y and other.y + other.h < box.y + box.h:
                side["top"] = min(side["top"], max(0, (box.y - other.y - other.h) // 2))
            elif other.y > box.y and other.y + other.h > box.y + box.h:
                side["bottom"] = min(side["bottom"], max(0, (other.y - box.y - box.h) // 2))
    return side


def snap_outward(gray: np.ndarray, box: Box, ink: np.ndarray | None = None,
                 neighbors: list[Box] | None = None) -> Box:
    """Grow each edge until the drawn border really ends.

    A rectangle assembled from corner junctions sits in the middle of the ink, and a
    hand-drawn line wobbles, so the outer part of the border would otherwise be cut off.
    Walk outward while a good share of the edge is still ink, tolerating small gaps, and
    stop at the paper. Never shrinks the box.
    """
    if ink is None:
        ink = gray < 160
    reach = max(int(min(box.w, box.h) * BORDER_REACH_FRACTION), 6)
    limits = _growth_limits(box, neighbors or [], reach)
    grown = {}
    for side in ("top", "right", "bottom", "left"):
        best, gap = 0, 0
        for offset in range(1, limits[side] + 1):
            if _edge_line(ink, box, side, offset) >= BORDER_INK_SHARE:
                best, gap = offset, 0
            else:
                gap += 1
                if gap > BORDER_GAP_TOLERANCE:
                    break
        grown[side] = best
    return Box(box.x - grown["left"], box.y - grown["top"],
               box.w + grown["left"] + grown["right"], box.h + grown["top"] + grown["bottom"],
               box.coverage, box.min_coverage, box.source)


def add_bleed(boxes: list[Box], width: int, height: int) -> list[Box]:
    """A little white around each panel, never reaching across a gutter into a neighbour."""
    out = []
    for box in boxes:
        want = int(max(box.w, box.h) * CANVAS_MARGIN_FRACTION)
        side = _growth_limits(box, boxes, want)
        x0 = max(0, box.x - max(0, side["left"]))
        y0 = max(0, box.y - max(0, side["top"]))
        x1 = min(width, box.x + box.w + max(0, side["right"]))
        y1 = min(height, box.y + box.h + max(0, side["bottom"]))
        out.append(Box(x0, y0, x1 - x0, y1 - y0, box.coverage, box.min_coverage, box.source))
    return out


# ---------------------------------------------------------------- confidence

def assess(boxes: list[Box], width: int, height: int) -> list[str]:
    reasons = []
    if not boxes:
        return ["no panels found"]
    if len(boxes) > 8:
        reasons.append(f"{len(boxes)} panels is more than I expect")
    for i, box in enumerate(boxes, 1):
        if box.coverage < 0.85 or box.min_coverage < 0.6:
            reasons.append(f"panel {i} has a weak border")
    for i, a in enumerate(boxes):
        for j, b in enumerate(boxes[i + 1:], i + 1):
            if a.intersection(b) > 0.05 * min(a.area, b.area):
                reasons.append(f"panels {i + 1} and {j + 1} overlap")
    if sum(b.area for b in boxes) < 0.10 * width * height:
        reasons.append("panels cover little of the page")
    return reasons


# ---------------------------------------------------------------- entry

def detect(detection_image: np.ndarray, render_image: np.ndarray | None = None) -> Detection:
    """Detect panels from grayscale while retaining the source pixels for rendering.

    Direct grayscale callers remain supported for tests and simple integrations. In
    that case the grayscale input is expanded to BGR only at the render boundary.
    """
    if detection_image.ndim == 3:
        if render_image is not None:
            raise ValueError("Pass either a color image or separate detection/render images, not both")
        render_image = detection_image
        detection_image = cv2.cvtColor(detection_image, cv2.COLOR_BGR2GRAY)
    elif detection_image.ndim != 2:
        raise ValueError("Detection image must be grayscale or BGR")
    if render_image is None:
        render_image = cv2.cvtColor(detection_image, cv2.COLOR_GRAY2BGR)
    if render_image.ndim != 3 or render_image.shape[:2] != detection_image.shape[:2]:
        raise ValueError("Render image must be a color image with matching dimensions")

    detection_image = normalize_levels(detection_image)
    small, scale = _shrink(detection_image)
    ink = ink_mask(small)
    angle = estimate_skew(ink)
    if abs(angle) >= MIN_DESKEW_DEGREES:
        detection_image = deskew(detection_image, angle)
        render_image = deskew(render_image, angle)
        small, scale = _shrink(detection_image)
        ink = ink_mask(small)
    else:
        angle = 0.0
    horizontal, vertical = line_masks(ink)
    mask = cv2.bitwise_or(horizontal, vertical)
    candidates = corner_candidates(find_corners(horizontal, vertical), mask)
    partial: list[Box] = []
    candidates += contour_candidates(ink, mask, partial)
    height, width = detection_image.shape[:2]
    full_boxes = []
    full_ink = detection_image < 160
    selected = recover_partial_frames(prune(candidates, ink), partial, mask)
    for box in selected:
        x0, y0 = floor(box.x / scale), floor(box.y / scale)
        x1, y1 = ceil((box.x + box.w) / scale), ceil((box.y + box.h) / scale)
        full_boxes.append(Box(x0, y0, x1 - x0, y1 - y0,
                   box.coverage, box.min_coverage, box.source)
        )
    snapped = [snap_outward(detection_image, full, full_ink, full_boxes) for full in full_boxes]
    boxes = reading_order(add_bleed(snapped, width, height))
    reasons = assess(boxes, width, height)
    return Detection(boxes=boxes, angle=angle, render_image=render_image,
                     detection_image=detection_image, confident=not reasons, reasons=reasons)


def detect_file(path: str | Path) -> Detection:
    color = _load_color(path)
    gray = normalize_levels(cv2.cvtColor(color, cv2.COLOR_BGR2GRAY))
    return detect(gray, color)
