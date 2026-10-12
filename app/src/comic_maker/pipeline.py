"""One application service for preview, CLI and queued production jobs."""
from __future__ import annotations
import fnmatch
import hashlib
import json
import os
import re
from pathlib import Path
import numpy as np
from PIL import Image
from . import imaging, detection, providers, __version__
from .settings import Settings, Store, atomic_json
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

def prepare(source, settings, overrides=None):
    analysis=detection.analyze(imaging.load(source))
    if overrides:
        analysis.boxes=detection.validate_boxes(overrides,analysis.image.size)
        analysis.method='manual';analysis.warnings=[]
    clean=imaging.ink(analysis.image,settings)
    panel_width=float(np.median([b.w for b in analysis.boxes]))
    styled=imaging.paint(clean,settings,panel_width)
    return analysis,clean,styled,panel_width

def preview(source,settings,store,overrides=None):
    fingerprint=digest(source)
    saved=store.read('corrections.json',{}).get(source_id(source),{})
    if overrides is None and saved.get('source_sha256')==fingerprint:
        overrides=saved.get('boxes')
    analysis,clean,styled,_=prepare(source,settings,overrides)
    if overrides:
        corrections=store.read('corrections.json',{})
        corrections[source_id(source)]={'source_sha256':fingerprint,'boxes':overrides}
        atomic_json(store.root/'corrections.json',corrections)
    key=hashlib.sha256((fingerprint+settings.model_dump_json()+json.dumps(overrides)).encode()).hexdigest()[:20]
    folder=store.root/'previews'/key
    folder.mkdir(parents=True,exist_ok=True)
    imaging.save(imaging.thumbnail(analysis.image,1800),folder/'source.png')
    imaging.save(imaging.thumbnail(styled,1800),folder/'ink.png')
    imaging.save(styled,folder/'pixels.png')
    _,summary,layout=imaging.render(styled,analysis.boxes,settings)
    imaging.save(summary,folder/'summary.png')
    return {'id':key,'size':list(analysis.image.size),'boxes':[b.as_dict() for b in analysis.boxes],
            'warnings':analysis.warnings,'method':analysis.method,'layout':layout}

def _export_variant(art,boxes,settings,target,color=False):
    target.mkdir(parents=True,exist_ok=True)
    bounds=imaging.crop_bounds(boxes,art.size)
    comic=art.crop(bounds)
    if comic.width*comic.height*settings.master_scale**2>imaging.MAX_PIXELS:
        raise ValueError('Requested master enlargement exceeds 85 megapixels; choose a smaller scale')
    if settings.master_scale>1:
        comic=comic.resize((comic.width*settings.master_scale,comic.height*settings.master_scale),Image.Resampling.NEAREST)
    imaging.save(comic,target/'comic.png')
    imaging.save(art,target/'source-layout.png')
    slides,summary,layout=imaging.render(art,boxes,settings,color)
    for i,slide in enumerate(slides,1): imaging.save(slide,target/f'{i:02d}-panel.png')
    imaging.save(summary,target/f'{len(slides)+1:02d}-summary.png')
    imaging.save(summary,target/'summary.png')
    return layout

def _color_input(clean,boxes):
    bounds=imaging.crop_bounds(boxes,clean.size)
    crop=clean.crop(bounds).convert('RGB')
    # Pad extreme strips instead of stretching or losing panels to model size limits.
    w,h=crop.size
    pad_w=max(w,round(h/2.8));pad_h=max(h,round(w/2.8))
    canvas=Image.new('RGB',(pad_w,pad_h),'white')
    offset=((pad_w-w)//2,(pad_h-h)//2)
    canvas.paste(crop,offset)
    canvas.thumbnail((3840,3840),Image.Resampling.NEAREST)
    return canvas,bounds,offset,(pad_w,pad_h)

def process(source: Path, export_root: Path, settings: Settings, store: Store,
            reserve=lambda:None, event=lambda stage:None, check=lambda:None,
            overrides=None, force=False):
    export_root=validate_output(export_root,[source])
    check();event('Reading scan')
    fingerprint=digest(source)
    saved=store.read('corrections.json',{}).get(source_id(source),{})
    if overrides is None and saved.get('source_sha256')==fingerprint:
        overrides=saved.get('boxes')
    recipe=hashlib.sha256((fingerprint+str(source.resolve())+settings.model_dump_json()+json.dumps(overrides)+__version__).encode()).hexdigest()
    slug=re.sub(r'[^\w .-]','_',source.stem).strip(' .')[:80] or 'comic'
    export_root.mkdir(parents=True,exist_ok=True)
    (export_root/'.comic-maker-export').touch()
    target=export_root/f'{slug}--{recipe[:10]}'
    if force and target.exists():
        n=2
        while target.with_name(target.name+f'-{n}').exists(): n+=1
        target=target.with_name(target.name+f'-{n}')
    manifest_path=target/'manifest.json'
    previous={}
    if manifest_path.exists():
        try: previous=json.loads(manifest_path.read_text())
        except (OSError,ValueError): previous={}
    if previous.get('status')=='complete' and previous.get('files'):
        if all((target/name).is_file() and digest(target/name)==sha for name,sha in previous['files'].items()):
            return {**previous,'output':str(target),'cached':True}
    target.mkdir(parents=True,exist_ok=True)
    event('Finding panels');check()
    analysis,clean,art,panel_width=prepare(source,settings,overrides)
    if analysis.warnings and settings.vision_fallback and not overrides:
        event('Recovering panels with vision')
        for attempt in range(settings.ai_attempts):
            try:
                check()
                values=providers.vision_boxes(analysis.image,settings,store.key('openai'),reserve)
                analysis.boxes=detection.validate_boxes(values,analysis.image.size)
                analysis.method='vision estimate'
                analysis.warnings=['Panel boundaries were estimated by AI; check crops if needed.']
                panel_width=float(np.median([b.w for b in analysis.boxes]))
                art=imaging.paint(clean,settings,panel_width)
                break
            except providers.ProviderFailure as exc:
                if providers.transient(exc) and attempt+1<settings.ai_attempts:
                    providers.backoff(attempt);continue
                analysis.warnings.append(str(exc));break
            except ValueError:
                if attempt+1==settings.ai_attempts:
                    analysis.warnings.append('Vision estimates were invalid; used local estimates.')
    check();event('Exporting black & white')
    layout=_export_variant(art,analysis.boxes,settings,target/'bw')
    result={'schema_version':1,'app_version':__version__,'source':str(source),'source_sha256':fingerprint,
            'recipe':recipe,'settings':settings.model_dump(),'size':list(art.size), 'boxes':[b.as_dict() for b in analysis.boxes],
            'angle':analysis.angle,'method':analysis.method,'layout':layout,'panel_count':len(analysis.boxes),
            'warnings':analysis.warnings,'status':'bw_ready','fixture':settings.provider=='fixture' and settings.colorize,
            'color_status':'off','output':str(target)}
    atomic_json(manifest_path,result)
    input_color=settings.input_mode=='color' or (settings.input_mode=='auto' and imaging.has_color(analysis.image.crop(imaging.crop_bounds(analysis.boxes,analysis.image.size))))
    if input_color:
        check();event('Cleaning original colors')
        original_color=imaging.flat_color(analysis.image,settings.palette_colors)
        styled=imaging.paint(original_color,settings,panel_width,True)
        _export_variant(styled,analysis.boxes,settings,target/'color',True)
        result['color_status']='original';result['color_folder']='color'
    elif settings.colorize:
        check();event('Colorizing · beta' if settings.provider!='fixture' else 'Colorizing · FIXTURE')
        try:
            model_input,bounds,offset,pad_size=_color_input(clean,analysis.boxes)
            raw_path=target/'color'/'provider-output.png'
            best=None;best_score=-1
            # A saved response is a checkpoint: restart does not purchase it again.
            checkpoint=raw_path if raw_path.exists() else target/'color'/'best-attempt.png'
            had_checkpoint=checkpoint.exists()
            attempts=1 if had_checkpoint else settings.ai_attempts
            for attempt in range(attempts):
                check()
                try:
                    generated=imaging.load(checkpoint) if had_checkpoint else providers.request_color(model_input,settings,store.key(settings.provider),reserve,correction=attempt>0)
                    report=providers.fidelity(model_input,generated)
                    if report['line_recall']>best_score:
                        best,best_score=generated,report['line_recall'];best_report=report
                        imaging.save(generated,target/'color'/'best-attempt.png')
                    if not report['suspect'] or settings.provider=='fixture': break
                except providers.ProviderFailure as exc:
                    if providers.transient(exc) and attempt+1<attempts:
                        providers.backoff(attempt);continue
                    if best is None: raise
                    result['warnings'].append(str(exc));break
            if best is None: raise providers.ProviderFailure('No usable color image returned')
            imaging.save(best,raw_path)
            result['color_check']=best_report
            if best_report['suspect']:
                result['warnings'].append('Color beta: possible drawing changes detected. Black-and-white exports are unaffected.')
            check();event('Flattening color & exporting')
            # Normalize provider dimensions once, then quantize; never multiply in original ink.
            paletted=imaging.flat_color(best,settings.palette_colors)
            scaled=imaging.hard_resize(paletted,pad_size,True,paletted)
            x0,y0,x1,y1=bounds;ox,oy=offset
            cropped=scaled.crop((ox,oy,ox+x1-x0,oy+y1-y0))
            full=Image.new('RGB',clean.size,'white');full.paste(cropped,(x0,y0))
            full=full.quantize(palette=paletted,dither=Image.Dither.NONE)
            styled=imaging.paint(full,settings,panel_width,True)
            color_folder=target/('color-draft' if best_report['suspect'] else 'color')
            _export_variant(styled,analysis.boxes,settings,color_folder,True)
            result['color_status']='draft' if best_report['suspect'] else 'fixture' if settings.provider=='fixture' else 'beta'
            result['color_folder']=color_folder.name
        except (providers.ProviderFailure,ValueError,OSError) as exc:
            result['color_status']='failed';result['warnings'].append(f'Colorization: {exc}')
    result['status']='complete' if result['color_status']!='failed' else 'partial'
    result['files']={str(p.relative_to(target)):digest(p) for p in sorted(target.rglob('*.png'))}
    atomic_json(manifest_path,result)
    return result
