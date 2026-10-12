"""Black-and-white grid processing adapted from Goosnav Red Sun core.py.

Reduce once with Lanczos, snap to black or white, and leave enlargement to
the carousel compositor. No dithering, sharpening, or median filtering.
"""
from PIL import Image

def otsu_threshold(gray):
    hist = gray.histogram()
    total = sum(hist)
    sum_total = sum(i * hist[i] for i in range(256))
    sum_bg = weight_bg = 0
    best_variance, threshold = -1., 128
    for i in range(256):
        weight_bg += hist[i]
        if weight_bg == 0:
            continue
        weight_fg = total - weight_bg
        if weight_fg == 0:
            break
        sum_bg += i * hist[i]
        variance = weight_bg * weight_fg * (sum_bg / weight_bg - (sum_total-sum_bg) / weight_fg) ** 2
        if variance > best_variance:
            best_variance, threshold = variance, i
    return threshold

def process(image, grid_width=None):
    gray = image.convert('L')
    if grid_width and gray.width > grid_width:
        gray = gray.resize((grid_width, max(1, round(gray.height * grid_width / gray.width))), Image.Resampling.LANCZOS)
    threshold = otsu_threshold(gray)
    return gray.point(lambda p: 255 if p > threshold else 0, mode='1')
