"""Scan cleanup and hard-edge rendering. No network or application state."""
from __future__ import annotations
import io
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps, ImageCms
from .settings import Settings, atomic_bytes
from .detector_base import Box
from . import layout_base, red_sun

MAX_PIXELS = 85_000_000
MAX_PRINT_PIXELS = 200_000_000

def load(path: Path) -> Image.Image:
    with Image.open(path) as source:
        if source.width * source.height > MAX_PIXELS:
            raise ValueError('Image exceeds the 85 megapixel working limit')
        if getattr(source, 'n_frames', 1) != 1:
            raise ValueError('Use one image per comic; split multi-page or animated files first')
        source.load()
        img = ImageOps.exif_transpose(source)
        rgba = img.convert('RGBA')
        result = Image.alpha_composite(Image.new('RGBA', img.size, 'white'), rgba).convert('RGB')
        if source.info.get('icc_profile'):
            try:
                profile = ImageCms.ImageCmsProfile(io.BytesIO(source.info['icc_profile']))
                result = ImageCms.profileToProfile(result, profile, ImageCms.createProfile('sRGB'), outputMode='RGB')
            except (ValueError, OSError, ImageCms.PyCMSError):
                # Malformed profiles do not prevent processing the decoded pixels.
                pass
        result.info["dpi"] = source.info.get("dpi", (600, 600))
        return result

def image_bytes(img, fmt='PNG', dpi=None):
    buf = io.BytesIO()
    options = {'dpi': dpi} if dpi else {}
    if fmt == 'TIFF':
        options['compression'] = 'group4' if img.mode == '1' else 'tiff_lzw'
    img.save(buf, fmt, **options)
    return buf.getvalue()

def save(img: Image.Image, path: Path, dpi=None):
    fmt = 'TIFF' if path.suffix.lower() in ('.tif', '.tiff') else 'PNG'
    atomic_bytes(path, image_bytes(img, fmt, dpi))

def source_dpi(image):
    value = image.info.get('dpi', (600, 600))
    try:
        x, y = map(float, value)
        if not (0 < x < 100000 and 0 < y < 100000):
            raise ValueError
        return x, y
    except (ValueError, TypeError):
        return 600., 600.

def validate_enlargement(source, scale):
    with Image.open(source) as image:
        if image.width * image.height > MAX_PIXELS:
            raise ValueError('Image exceeds the 85 megapixel working limit')
        if image.width * image.height * scale ** 2 > MAX_PRINT_PIXELS:
            raise ValueError('Print enlargement exceeds 200 megapixels; choose a smaller scale')

def normalize(gray: np.ndarray) -> np.ndarray:
    """Estimate slow paper variation independently of narrow pencil strokes."""
    h, w = gray.shape
    scale = min(1, 1600 / max(h, w))
    small = cv2.resize(gray, (max(1, round(w*scale)), max(1, round(h*scale))), interpolation=cv2.INTER_AREA)
    paper = cv2.morphologyEx(small, cv2.MORPH_CLOSE, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (31,31)))
    paper = cv2.GaussianBlur(paper, (0,0), 12)
    paper = cv2.resize(paper, (w,h), interpolation=cv2.INTER_LINEAR)
    return cv2.divide(gray, np.maximum(paper, 80), scale=255)

def ink(rgb: Image.Image, settings: Settings) -> Image.Image:
    gray = np.asarray(rgb.convert('L'))
    if np.all((gray == 0) | (gray == 255)):
        return Image.fromarray(gray).convert('1', dither=Image.Dither.NONE)
    norm = normalize(gray)
    small = norm[::max(1, norm.shape[0]//1200), ::max(1, norm.shape[1]//1600)]
    threshold, _ = cv2.threshold(small, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    threshold = np.clip(threshold + (settings.ink_strength-50)*0.65 + 8, 110, 247)
    binary = (norm <= threshold).astype(np.uint8)
    # Remove only isolated tiny components; broad median filters erase punctuation.
    if settings.cleanup:
        count, labels, stats, _ = cv2.connectedComponentsWithStats(binary, 8)
        cutoff = max(1, round(settings.cleanup / 20 * (rgb.width/6600)**2))
        keep = stats[:, cv2.CC_STAT_AREA] > cutoff
        keep[0] = False
        binary = keep[labels].astype(np.uint8)
    return Image.fromarray((1-binary)*255).convert('1', dither=Image.Dither.NONE)

def crop_bounds(boxes: list[Box], size):
    w, h = size
    margin = max(4, round(min(w, h) * .006))
    return (max(0, min(b.x for b in boxes) - margin), max(0, min(b.y for b in boxes) - margin),
            min(w, max(b.x+b.w for b in boxes) + margin), min(h, max(b.y+b.h for b in boxes) + margin))

def crop_panel(art, box):
    return art.crop((box.x, box.y, box.x + box.w, box.y + box.h))

def resize_piece(piece, size, stylized=False):
    size = tuple(max(1, round(v)) for v in size)
    return piece.resize(size, Image.Resampling.NEAREST if stylized else Image.Resampling.LANCZOS)

def panel_canvas(piece, settings, stylized=False):
    if stylized:
        piece = red_sun.process(piece, settings.panel_grid_width)
    else:
        piece = piece.convert('L')
    width, height = settings.canvas_width, settings.canvas_width * 5 // 4
    frame = Image.new('1' if stylized else 'L', (width, height), 255)
    scale = min(width * .95 / piece.width, height * .95 / piece.height)
    piece = resize_piece(piece, (piece.width * scale, piece.height * scale), stylized)
    frame.paste(piece, ((width-piece.width)//2, (height-piece.height)//2))
    return frame

def summary_canvas(art, boxes, settings, stylized=False):
    width, height = settings.canvas_width, settings.canvas_width * 5 // 4
    frame = Image.new('1' if stylized else 'L', (width, height), 255)
    layout = layout_base.choose_layout(boxes, settings.layout)
    placements, _ = layout_base.place(boxes, layout_base.row_plans(boxes)[layout])
    factor = width / 1080
    for box, (x,y,w,h) in zip(boxes, placements):
        piece = crop_panel(art, box)
        piece = red_sun.process(piece, settings.summary_grid_width) if stylized else piece.convert('L')
        piece = resize_piece(piece, (w * factor, h * factor), stylized)
        frame.paste(piece, (round(x*factor), round(y*factor)))
    return frame, layout

def thumbnail(img, edge=1400, pixelated=False):
    thumb = img.copy().convert('RGB')
    thumb.thumbnail((edge,edge), Image.Resampling.NEAREST if pixelated else Image.Resampling.LANCZOS)
    return thumb
