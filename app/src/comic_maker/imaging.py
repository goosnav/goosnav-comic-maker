"""Scan cleanup and hard-edge rendering. No network or application state."""
from __future__ import annotations
import io
from pathlib import Path
import cv2
import numpy as np
from PIL import Image, ImageOps, ImageCms
from .settings import Settings, atomic_bytes
from .detector_base import Box
from . import layout_base

MAX_PIXELS = 85_000_000

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
        return result

def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()

def has_color(img: Image.Image) -> bool:
    thumb=img.copy();thumb.thumbnail((400,400))
    arr=np.asarray(thumb.convert('RGB')).astype(np.int16)
    chroma=arr.max(axis=2)-arr.min(axis=2)
    return float(np.mean(chroma>35))>.005

def save(img: Image.Image, path: Path):
    atomic_bytes(path, png_bytes(img))

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

def hard_resize(img: Image.Image, size: tuple[int,int], color=False, palette=None) -> Image.Image:
    size = tuple(max(1,int(v)) for v in size)
    if size == img.size:
        return img.copy()
    if size[0] >= img.width and size[1] >= img.height:
        return img.resize(size, Image.Resampling.NEAREST)
    # Area sampling keeps fine marks represented; snap AFTER sampling to forbid fringes.
    small = img.convert('RGB' if color else 'L').resize(size, Image.Resampling.BOX)
    if color:
        return small.quantize(palette=palette or img, dither=Image.Dither.NONE)
    return small.point(lambda x: 255 if x > 175 else 0).convert('1', dither=Image.Dither.NONE)

def paint(img: Image.Image, settings: Settings, panel_width: float, color=False) -> Image.Image:
    if settings.style != 'paint' or not settings.crunch:
        return img
    cell = max(1.0, panel_width / 1080) * (1 + settings.crunch/40)
    size = (max(1,round(img.width/cell)), max(1,round(img.height/cell)))
    grid = hard_resize(img, size, color, img if color else None)
    return grid.resize(img.size, Image.Resampling.NEAREST)

def flat_color(rgb: Image.Image, count: int) -> Image.Image:
    """Build the palette from region interiors; edge blends do not earn colors."""
    thumb = rgb.copy()
    thumb.thumbnail((1400,1400), Image.Resampling.NEAREST)
    arr = np.asarray(thumb)
    high = cv2.dilate(arr, np.ones((3,3), np.uint8))
    low = cv2.erode(arr, np.ones((3,3), np.uint8))
    flat = np.max(high.astype(np.int16)-low,axis=2) < 22
    samples = arr[flat]
    if len(samples) < 100:
        samples = arr.reshape(-1,3)
    sample_image = Image.fromarray(samples[::max(1,len(samples)//300000)].reshape(-1,1,3))
    q = sample_image.quantize(colors=count-2, dither=Image.Dither.NONE)
    raw = q.getpalette()
    colors = [(0,0,0),(255,255,255)]
    for _,i in sorted(q.getcolors(), reverse=True):
        c=tuple(raw[i*3:i*3+3])
        if max(c)<40 or min(c)>238:
            continue
        if all(sum((a-b)**2 for a,b in zip(c,other))>28**2 for other in colors):
            colors.append(c)
    pal = Image.new('P',(1,1))
    pal.putpalette([v for c in colors for v in c]+list(colors[-1])*(256-len(colors)))
    result=rgb.quantize(palette=pal, dither=Image.Dither.NONE)
    # Gray edge blends must not borrow a colored fill elsewhere in the drawing.
    # Work in strips so a large scan does not allocate several full RGB arrays.
    for y in range(0,rgb.height,256):
        region=(0,y,rgb.width,min(rgb.height,y+256))
        source=np.asarray(rgb.crop(region)).astype(np.int16)
        labels=np.asarray(result.crop(region)).copy()
        nearest=np.asarray(result.crop(region).convert('RGB')).astype(np.int16)
        grayish=source.max(axis=2)-source.min(axis=2)<28
        colored=nearest.max(axis=2)-nearest.min(axis=2)>=28
        fringe=grayish & colored
        labels[fringe]=(source.mean(axis=2)[fringe]>160).astype(np.uint8)
        result.paste(Image.fromarray(labels).convert('P'),(0,y))
    return result

def crop_bounds(boxes: list[Box], size):
    w,h=size
    margin=max(4, round(min(w,h)*.006))
    return (max(0,min(b.x for b in boxes)-margin), max(0,min(b.y for b in boxes)-margin),
            min(w,max(b.x+b.w for b in boxes)+margin), min(h,max(b.y+b.h for b in boxes)+margin))

def fit(art, width, height, color=False, palette=None):
    scale=min(width/art.width,height/art.height)
    return hard_resize(art,(round(art.width*scale),round(art.height*scale)),color,palette)

def render(art: Image.Image, boxes: list[Box], settings: Settings, color=False):
    """One canvas per panel plus a reflowed summary, snapped at actual export size."""
    width=settings.canvas_width
    height=width*5//4
    palette=art if color else None
    crops=[art.crop((b.x,b.y,b.x+b.w,b.y+b.h)) for b in boxes]
    def canvas():
        return Image.new('RGB' if color else '1',(width,height),'white')
    slides=[]
    for piece in crops:
        frame=canvas()
        piece=fit(piece,width*.95,height*.95,color,palette)
        frame.paste(piece,((width-piece.width)//2,(height-piece.height)//2))
        slides.append(frame)
    layout=layout_base.choose_layout(boxes, settings.layout)
    placements,_=layout_base.place(boxes,layout_base.row_plans(boxes)[layout])
    summary=canvas()
    factor=width/1080
    for crop,(x,y,w,h) in zip(crops,placements):
        piece=hard_resize(crop,(round(w*factor),round(h*factor)),color,palette)
        summary.paste(piece,(round(x*factor),round(y*factor)))
    return slides,summary,layout

def thumbnail(img, edge=1400):
    thumb=img.copy().convert('RGB')
    thumb.thumbnail((edge,edge),Image.Resampling.NEAREST)
    return thumb
