"""One application service for preview, CLI and queued production jobs."""
from __future__ import annotations
import fnmatch
import hashlib
import json
import os
import random
import re
import shutil
import tempfile
from pathlib import Path
import numpy as np
from PIL import Image
from . import imaging, detection, __version__
from .settings import Settings, Store, atomic_json, atomic_bytes
from .detector_base import Box

SUPPORTED={'.png','.jpg','.jpeg','.tif','.tiff','.bmp','.webp','.gif'}

def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''): h.update(chunk)
    return h.hexdigest()

def discover(paths, settings: Settings, export_root=None):
    files=set();warnings=[]
    excluded=Path(export_root).expanduser().resolve() if export_root else None
    def add(p):
        if p.suffix.lower() in SUPPORTED and fnmatch.fnmatch(p.name.lower(),(settings.pattern or '*').lower()):
            real=p.resolve()
            if not excluded or not real.is_relative_to(excluded): files.add(real)
    for value in paths:
        path=Path(value).expanduser().resolve()
        if path.is_file(): add(path)
        elif path.is_dir():
            for root,dirs,names in os.walk(path,followlinks=False):
                dirs[:]=sorted(d for d in dirs if not d.startswith('.') and not d.startswith(('carousel-export-','red-sun-run-')) and not (Path(root)/d/'.comic-maker-export').exists() and (not excluded or not (Path(root)/d).resolve().is_relative_to(excluded)))
                if (Path(root)/'.comic-maker-export').exists():
                    dirs[:]=[];continue
                for name in sorted(names):
                    if not name.startswith('.'): add(Path(root)/name)
                if not settings.recursive: dirs[:]=[]
        else: warnings.append(f'Not found or not readable: {path}')
    return sorted(files),warnings

def source_id(path):
    return hashlib.sha256(str(path.resolve()).encode()).hexdigest()[:20]

def validate_output(export_root, sources):
    target=Path(export_root).expanduser().resolve()
    if target.is_file(): raise ValueError('Choose an export folder, not a file')
    if any(Path(p).resolve().is_relative_to(target) for p in sources):
        raise ValueError('Choose a separate export folder that does not contain your originals')
    return target

COORDINATE_VERSION = 1
SCHEMA_VERSION = 2
SOCIAL_PHASES = ('regular-carousel', 'stylized-carousel')
PRINT_COLLECTIONS = {
    'comic-crop': ('all-book-prints', 'comic', 'book_print'),
    'full-page': ('all-processed-pages', 'full-page', 'processed_page'),
}
COLLECTION_FOLDERS = {'all-social', *(value[0] for value in PRINT_COLLECTIONS.values())}

def print_collection_paths(root):
    return {prefix+'_output': str(root/folder) for folder, _, prefix in PRINT_COLLECTIONS.values()}

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()

def correction(source, store, sha=None):
    sha = sha or digest(source)
    records = store.read('corrections.json', {})
    saved = records.get(f'{COORDINATE_VERSION}:{sha}', records.get(source_id(source), {}))
    if saved.get('source_sha256') == sha and saved.get('coordinate_version') == COORDINATE_VERSION:
        return saved
    return {}

def prepare(source, settings, overrides=None):
    loaded = imaging.load(source)
    dpi = imaging.source_dpi(loaded)
    analysis = detection.analyze(loaded)
    if overrides is not None:
        analysis.boxes = detection.validate_boxes(overrides, analysis.image.size)
        analysis.method = 'manual'
        analysis.warnings = []
    clean = imaging.ink(analysis.image, settings)
    return analysis, clean, dpi

def preview(source, settings, store, overrides=None):
    sha = digest(source)
    key = fingerprint([sha, str(source), settings.ink_strength, settings.cleanup, COORDINATE_VERSION, __version__])[:24]
    folder = store.root / 'previews' / key
    meta_path = folder / 'meta.json'
    meta = store.read(str(meta_path.relative_to(store.root)), {})
    if not meta or not (folder/'page.png').exists():
        analysis, clean, dpi = prepare(source, settings)
        folder.mkdir(parents=True, exist_ok=True)
        imaging.save(analysis.image, folder/'source.png')
        imaging.save(clean, folder/'page.png', dpi)
        meta = {'id': key, 'source': str(source), 'source_sha256': sha, 'coordinate_version': COORDINATE_VERSION,
                'size': list(clean.size), 'auto_boxes': [b.as_dict() for b in analysis.boxes],
                'warnings': analysis.warnings, 'method': analysis.method, 'angle': analysis.angle}
        atomic_json(meta_path, meta)
    saved = correction(source, store, sha)
    boxes = overrides if overrides is not None else saved.get('boxes', meta['auto_boxes'])
    detection.validate_boxes(boxes, meta['size'])
    return {**meta, 'boxes': boxes, 'warnings': [] if saved or overrides is not None else meta['warnings'], 'settings': settings.model_dump()}

def preview_meta(store, identifier):
    if not identifier.isalnum():
        raise ValueError('Invalid preview')
    meta = store.read(f'previews/{identifier}/meta.json', {})
    if not meta:
        raise ValueError('Reopen the panel editor')
    return meta

def save_correction(source, store, identifier, values):
    meta = preview_meta(store, identifier)
    if meta['source'] != str(source) or meta['source_sha256'] != digest(source):
        raise ValueError('The scan changed. Reopen the editor before saving.')
    boxes = detection.validate_boxes(values, meta['size'])
    saved = {'source_sha256': meta['source_sha256'], 'coordinate_version': COORDINATE_VERSION,
             'boxes': [b.as_dict() for b in boxes]}
    corrections = store.read('corrections.json', {})
    corrections[f'{COORDINATE_VERSION}:{meta["source_sha256"]}'] = saved
    atomic_json(store.root/'corrections.json', corrections)
    return saved

def render_preview(store, identifier, values, settings, selected=0):
    meta = preview_meta(store, identifier)
    boxes = detection.validate_boxes(values, meta['size'])
    selected = max(0, min(int(selected), len(boxes)-1))
    key = fingerprint([identifier, values, settings.model_dump(), selected, __version__])[:24]
    folder = store.root/'previews'/key
    if not (folder/'summary.png').exists():
        folder.mkdir(parents=True, exist_ok=True)
        with Image.open(store.root/'previews'/identifier/'page.png') as image:
            piece = imaging.crop_panel(image, boxes[selected])
            imaging.save(imaging.thumbnail(piece, 900), folder/'crop.png')
            imaging.save(imaging.thumbnail(imaging.panel_canvas(piece, settings), 700), folder/'regular.png')
            imaging.save(imaging.thumbnail(imaging.panel_canvas(piece, settings, True), 700, pixelated=True), folder/'stylized.png')
            summary, _ = imaging.summary_canvas(image, boxes, settings)
            imaging.save(imaging.thumbnail(summary, 700), folder/'summary.png')
    return {'id': key}

def folder_name(name):
    value = re.sub(r'[^\w .-]', '_', name).strip(' .')[:80] or 'comic'
    if value.upper().split('.')[0] in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1,10)), *(f'LPT{i}' for i in range(1,10))}:
        value = '_' + value
    return value

def allocate_names(sources):
    names = []
    used = set(COLLECTION_FOLDERS)
    for source in sources:
        base = folder_name(source.stem)
        name, n = base, 2
        while name.casefold() in used:
            name = f'{base}-{n}'
            n += 1
        used.add(name.casefold())
        names.append(name)
    return names

def create_run(parent):
    parent = Path(parent).expanduser().resolve()
    if parent.is_file():
        raise ValueError('Choose a destination folder')
    parent.mkdir(parents=True, exist_ok=True)
    n = 1
    while True:
        root = parent/f'comic-maker-run-{n:03d}'
        try:
            root.mkdir()
            (root/'.comic-maker-export').touch()
            for folder in COLLECTION_FOLDERS:
                (root/folder).mkdir()
            return root
        except FileExistsError:
            n += 1

def read_lines(path):
    """Non-blank lines of the caption source; a chosen file must be usable."""
    try:
        lines = [v.strip() for v in Path(path).expanduser().read_text(encoding='utf-8-sig', errors='replace').splitlines()]
    except OSError as exc:
        raise ValueError(f'Could not read the caption file: {exc}')
    lines = [v for v in lines if v]
    if not lines:
        raise ValueError('The caption file has no text lines')
    return lines

def stylized_folder(target, number):
    """Numbered per comic; an existing folder (retry, older run) keeps its name."""
    found = sorted(p.name for p in target.glob('stylized-carousel*') if p.is_dir())
    return found[0] if found else f'stylized-carousel-{number:02d}'

def _collect_files(target, expected, owned_pattern, check):
    """Copies are derived outputs; a retry repairs them without processing images."""
    target.mkdir(exist_ok=True)
    for name, path in expected.items():
        check()
        dest = target/name
        if not dest.exists() or digest(dest) != digest(path):
            atomic_bytes(dest, path.read_bytes())
    for path in target.iterdir():
        owned = re.fullmatch(owned_pattern, path.name)
        if path.is_file() and owned and path.name not in expected:
            path.unlink()
    return {name: digest(target/name) for name in expected}

def collect_social(root, comic_name, check=lambda: None):
    prefix = comic_name + '--'
    expected = {prefix+'stylized-carousel--'+path.name: path
                for path in sorted((root/comic_name).glob('stylized-carousel*/*.png'))}
    return _collect_files(root/'all-social', expected,
                          re.escape(prefix) + r'(regular-carousel|stylized-carousel)--\d+-(panel|summary)\.png', check)

def collect_prints(root, comic_name, check=lambda: None, phase=None):
    result = print_collection_paths(root)
    for name, (folder, stem, prefix) in PRINT_COLLECTIONS.items():
        if phase is not None and phase != name:
            continue
        expected = {}
        for ext in ('png', 'tif'):
            source = root/comic_name/'print'/f'{stem}.{ext}'
            if source.is_file():
                expected[f'{comic_name}--{stem}.{ext}'] = source
        result[prefix+'_files'] = _collect_files(root/folder, expected,
            re.escape(comic_name+'--'+stem) + r'\.(png|tif)', check)
    return result

def _write_print(image, folder, stem, settings, dpi):
    if settings.master_scale > 1:
        image = image.resize((image.width*settings.master_scale, image.height*settings.master_scale), Image.Resampling.NEAREST)
    scaled_dpi = tuple(v*settings.master_scale for v in dpi)
    for ext in ('png', 'tif'):
        imaging.save(image, folder/f'{stem}.{ext}', scaled_dpi)

def process(source: Path, export_root: Path, settings: Settings, store: Store,
            event=lambda stage: None, check=lambda: None, overrides=None, comic_name=None, number=1):
    export_root = validate_output(export_root, [source])
    imaging.validate_enlargement(source, settings.master_scale)
    sha = digest(source)
    if overrides is None:
        overrides = correction(source, store, sha).get('boxes')
    target = export_root/(comic_name or folder_name(source.stem))
    target.mkdir(parents=True, exist_ok=True)
    manifest_path = target/'manifest.json'
    try:
        previous = json.loads(manifest_path.read_text())
    except (OSError, ValueError):
        previous = {}
    check(); event('Cleaning scan')
    analysis, clean, dpi = prepare(source, settings, overrides)
    if digest(source) != sha:
        raise ValueError('The scan changed during processing. Retry with the current scan.')
    boxes = [b.as_dict() for b in analysis.boxes]
    page_recipe = fingerprint([sha, settings.ink_strength, settings.cleanup, settings.master_scale, COORDINATE_VERSION, __version__])
    recipes = {'full-page': page_recipe,
               'comic-crop': fingerprint([page_recipe, boxes]),
               'regular-carousel': fingerprint([sha, settings.ink_strength, settings.cleanup, boxes, settings.canvas_width, settings.layout, __version__]),
               'stylized-carousel': fingerprint([sha, settings.ink_strength, settings.cleanup, boxes, settings.canvas_width, settings.layout, settings.panel_grid_width, settings.summary_grid_width, __version__])}
    result = {'schema_version': SCHEMA_VERSION, 'recipe_version': SCHEMA_VERSION, 'app_version': __version__, 'source': str(source), 'source_sha256': sha,
              'coordinate_version': COORDINATE_VERSION, 'settings': settings.model_dump(), 'size': list(clean.size), 'dpi': dpi,
              'boxes': boxes, 'angle': analysis.angle, 'method': analysis.method, 'panel_count': len(boxes),
              'warnings': analysis.warnings, 'needs_review': bool(analysis.warnings), 'status': 'partial',
              'output': str(target), 'stages': {phase:{**previous.get('stages',{}).get(phase,{}), 'status': 'complete' if previous.get('stages',{}).get(phase,{}).get('recipe') == recipe else 'pending'} for phase,recipe in recipes.items()}, 'files': previous.get('files', {}),
              'social_output': str(export_root/'all-social'), **print_collection_paths(export_root),
              'layout': imaging.layout_base.choose_layout(analysis.boxes, settings.layout)}
    def checkpoint():
        result['files'] = {str(p.relative_to(target)): digest(p) for p in sorted(target.rglob('*'))
                           if p.is_file() and not any(part.startswith('.') for part in p.relative_to(target).parts) and p.suffix in ('.png','.tif')}
        atomic_json(manifest_path, result)
    skipped = 0
    try:
        for phase, recipe in recipes.items():
            check()
            folder = target/('print' if phase in ('full-page','comic-crop') else stylized_folder(target, number) if phase == 'stylized-carousel' else phase)
            backup = target/('.previous-'+phase)
            if backup.exists():
                if not folder.exists():
                    backup.rename(folder)
                else:
                    shutil.rmtree(backup)
            old = result['stages'].get(phase, {})
            if old.get('recipe') == recipe and old.get('files') and all((target/name).is_file() and digest(target/name) == value for name,value in old['files'].items()):
                skipped += 1
                if phase in PRINT_COLLECTIONS:
                    result.update(collect_prints(export_root, target.name, check, phase))
                    checkpoint()
                continue
            event({'full-page':'Exporting print page','comic-crop':'Exporting print crop','regular-carousel':'Making regular carousel','stylized-carousel':'Making stylized carousel'}[phase])
            temporary = Path(tempfile.mkdtemp(prefix='.working-', dir=target))
            try:
                if phase == 'full-page':
                    _write_print(clean, temporary, 'full-page', settings, dpi)
                elif phase == 'comic-crop':
                    _write_print(clean.crop(imaging.crop_bounds(analysis.boxes, clean.size)), temporary, 'comic', settings, dpi)
                else:
                    styled = phase == 'stylized-carousel'
                    for i, box in enumerate(analysis.boxes, 1):
                        check()
                        imaging.save(imaging.panel_canvas(imaging.crop_panel(clean, box), settings, styled), temporary/f'{i:02d}-panel.png')
                    summary, layout = imaging.summary_canvas(clean, analysis.boxes, settings, styled)
                    result['layout'] = layout
                    imaging.save(summary, temporary/f'{len(boxes)+1:02d}-summary.png')
                    if styled and settings.lines_file:
                        (temporary/'caption.txt').write_text(random.choice(read_lines(settings.lines_file))+'\n', encoding='utf-8')
                check()
                if phase in ('full-page','comic-crop'):
                    folder.mkdir(exist_ok=True)
                    names = [p.name for p in temporary.iterdir()]
                    for p in temporary.iterdir():
                        os.replace(p, folder/p.name)
                else:
                    names = [p.name for p in temporary.iterdir()]
                    if folder.exists():
                        folder.rename(backup)
                    temporary.rename(folder)
                    if backup.exists():
                        shutil.rmtree(backup)
                result['stages'][phase] = {'status':'complete', 'recipe':recipe, 'files': {str((folder/name).relative_to(target)):digest(folder/name) for name in names}}
                if phase in PRINT_COLLECTIONS:
                    result.update(collect_prints(export_root, target.name, check, phase))
                checkpoint()
            finally:
                if temporary.exists():
                    shutil.rmtree(temporary)
        check(); event('Collecting social slides')
        result['social_files'] = collect_social(export_root, target.name, check)
        result['status'] = 'complete'
        result['cached'] = skipped == len(recipes)
        result.pop('error', None)
        checkpoint()
        return result
    except Exception as exc:
        result['error'] = str(exc)
        checkpoint()
        raise
