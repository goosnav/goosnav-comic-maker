"""Panel detection with bounded, independent local recovery strategies."""
from __future__ import annotations
from dataclasses import dataclass
import cv2
import numpy as np
from PIL import Image
from . import detector_base as base
from .imaging import normalize

@dataclass
class Analysis:
    image: Image.Image
    boxes: list[base.Box]
    angle: float
    warnings: list[str]
    method: str

def score(det):
    if not det.boxes:
        return -100
    area=sum(b.area for b in det.boxes)/det.detection_image.size
    return sum(b.coverage for b in det.boxes)/len(det.boxes)+min(area, .5)-len(det.reasons)*.25

def gutter_candidates(gray):
    h,w=gray.shape
    scale=min(1,1600/w)
    small=cv2.resize(gray,(round(w*scale),round(h*scale)))
    ink=(small<195).astype(np.uint8)*255
    ink=cv2.morphologyEx(ink,cv2.MORPH_CLOSE,np.ones((9,9),np.uint8))
    ink=cv2.dilate(ink,np.ones((7,7),np.uint8))
    contours,_=cv2.findContours(ink,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
    boxes=[]
    for c in contours:
        x,y,bw,bh=cv2.boundingRect(c)
        if bw*bh/ink.size>.025 and .2<bw/bh<6:
            x0,y0=max(0,round((x-4)/scale)),max(0,round((y-4)/scale))
            x1,y1=min(w,round((x+bw+4)/scale)),min(h,round((y+bh+4)/scale))
            boxes.append(base.Box(x0,y0,x1-x0,y1-y0,.65,.4,'gutter'))
    return base.reading_order(base.prune(boxes))

def analyze(image: Image.Image) -> Analysis:
    rgb=np.asarray(image)
    gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
    first=base.detect(gray,rgb)
    if first.confident:
        return Analysis(Image.fromarray(first.render_image),first.boxes,first.angle,[], 'border')
    # Stronger normalization exposes faint pencil borders to the same geometric tests.
    normalized=normalize(gray)
    second=base.detect(normalized,rgb)
    chosen=max((first,second),key=score)
    if chosen.confident:
        return Analysis(Image.fromarray(chosen.render_image),chosen.boxes,chosen.angle,[], 'normalized border')
    boxes=chosen.boxes or gutter_candidates(chosen.detection_image)
    method='best border estimate' if chosen.boxes else 'gutter estimate'
    warnings=['Panel boundaries are estimated. Check the preview if a crop looks wrong.']
    if not boxes:
        # Last resort preserves all art rather than emitting a blank summary.
        boxes=[base.Box(0,0,image.width,image.height,0,0,'whole image')]
        method='whole image'
        warnings=['No reliable panels found; exported the complete image as one panel.']
    return Analysis(Image.fromarray(chosen.render_image),boxes,chosen.angle,warnings,method)

def validate_boxes(values, size):
    w,h=size
    if not 1<=len(values)<=40:
        raise ValueError('Provide between 1 and 40 panel boxes')
    out=[]
    for value in values:
        x,y,bw,bh=(int(value[k]) for k in ('x','y','w','h'))
        if x<0 or y<0 or bw<8 or bh<8 or x+bw>w or y+bh>h:
            raise ValueError('Panel boxes must be inside the image and at least 8 pixels wide and tall')
        out.append(base.Box(x,y,bw,bh))
    return out
