"""Panel detection with classical corner/edge machine vision. No AI.

Pipeline (all on a 1600 px wide working copy, boxes scaled back to full res):
ink mask -> deskew (Hough line angles) -> horizontal/vertical line masks ->
corner junctions -> rectangles from corner quadruples, verified by border
coverage -> contour candidates through the same verification -> prune merged
containers and nested rectangles -> reading order -> crop margin.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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


def contour_candidates(ink: np.ndarray, mask: np.ndarray) -> list[Box]:
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
    return found


def prune(candidates: list[Box]) -> list[Box]:
    """Drop merged containers (mostly filled by other panels), then nested/duplicate boxes."""
    kept = []
    for box in candidates:
        inner = sum(o.area for o in candidates
                    if o is not box and o.area < box.area
                    and o.min_coverage >= CONTAINER_CHILD_MIN_COVERAGE
                    and box.intersection(o) >= 0.8 * o.area)
        if inner < 0.5 * box.area:
            kept.append(box)
    kept.sort(key=lambda b: -b.area)
    final: list[Box] = []
    for box in kept:
        if not any(box.intersection(k) >= 0.8 * box.area for k in final):
            final.append(box)
    return final


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


def snap_outward(gray: np.ndarray, box: Box) -> Box:
    """Grow each edge until the drawn border really ends.

    A rectangle assembled from corner junctions sits in the middle of the ink, and a
    hand-drawn line wobbles, so the outer part of the border would otherwise be cut off.
    Walk outward while a good share of the edge is still ink, tolerating small gaps, and
    stop at the paper. Never shrinks the box.
    """
    ink = gray < 160
    reach = max(int(min(box.w, box.h) * BORDER_REACH_FRACTION), 6)
    grown = {}
    for side in ("top", "right", "bottom", "left"):
        best, gap = 0, 0
        for offset in range(1, reach + 1):
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
        side = dict(left=want, right=want, top=want, bottom=want)
        for other in boxes:
            if other is box:
                continue
            if min(box.y + box.h, other.y + other.h) > max(box.y, other.y):  # same row
                if other.x + other.w <= box.x:
                    side["left"] = min(side["left"], (box.x - other.x - other.w) // 2)
                elif other.x >= box.x + box.w:
                    side["right"] = min(side["right"], (other.x - box.x - box.w) // 2)
            if min(box.x + box.w, other.x + other.w) > max(box.x, other.x):  # same column
                if other.y + other.h <= box.y:
                    side["top"] = min(side["top"], (box.y - other.y - other.h) // 2)
                elif other.y >= box.y + box.h:
                    side["bottom"] = min(side["bottom"], (other.y - box.y - box.h) // 2)
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
    candidates += contour_candidates(ink, mask)
    height, width = detection_image.shape[:2]
    snapped = []
    for box in prune(candidates):
        full = Box(int(box.x / scale), int(box.y / scale), int(box.w / scale), int(box.h / scale),
                   box.coverage, box.min_coverage, box.source)
        snapped.append(snap_outward(detection_image, full))
    boxes = reading_order(add_bleed(snapped, width, height))
    reasons = assess(boxes, width, height)
    return Detection(boxes=boxes, angle=angle, render_image=render_image,
                     detection_image=detection_image, confident=not reasons, reasons=reasons)


def detect_file(path: str | Path) -> Detection:
    color = _load_color(path)
    gray = normalize_levels(cv2.cvtColor(color, cv2.COLOR_BGR2GRAY))
    return detect(gray, color)
