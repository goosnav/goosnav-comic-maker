from pathlib import Path
import json
import time
import threading
import pytest
import numpy as np
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from comic_maker import imaging, pipeline, detection, red_sun
from comic_maker.settings import Settings, Store
from comic_maker.detector_base import Box
from comic_maker.jobs import Jobs, Cancelled
from comic_maker.server import create_app


def drawing(path, gray=105):
    image=Image.new('RGB',(1200,800),(246,246,246));d=ImageDraw.Draw(image)
    for x in (100,650):
        d.rectangle((x,120,x+430,680),outline=(gray,)*3,width=5)
        d.ellipse((x+70,260,x+240,440),outline=(gray,)*3,width=5)
        d.text((x+40,180),'HELLO, COMIC!',fill=(gray,)*3)
    image.save(path,dpi=(600,600))
    return image


def wait_job(jobs,identifier):
    until=time.monotonic()+30
    while time.monotonic()<until:
        job=jobs.get(identifier)
        if job['status'] not in ('queued','running'):return job
        time.sleep(.02)
    raise AssertionError('Job did not finish')


def test_exports_resolution_dpi_modes_source_and_collection(tmp_path):
    source=tmp_path/'scan.png';drawing(source);before=pipeline.digest(source)
    store=Store(tmp_path/'state');root=pipeline.create_run(tmp_path/'exports')
    result=pipeline.process(source,root,Settings(),store)
    assert result['schema_version']==2 and result['recipe_version']==2 and result['status']=='complete'
    assert result['panel_count']==2 and len(result['files'])==10
    assert set(result['stages'])=={'full-page','comic-crop','regular-carousel','stylized-carousel'}
    for ext in ('png','tif'):
        with Image.open(root/'scan'/'print'/f'full-page.{ext}') as image:
            assert image.mode=='1' and image.size==(1200,800)
            assert image.info['dpi']==pytest.approx((600,600),abs=.03)
        with Image.open(root/'scan'/'print'/f'comic.{ext}') as image:
            assert image.width<1200 and image.height<800
    for phase in pipeline.SOCIAL_PHASES:
        names=sorted((root/'scan'/(phase+'-01' if phase=='stylized-carousel' else phase)).glob('*.png'))
        assert [p.name for p in names]==['01-panel.png','02-panel.png','03-summary.png']
        for p in names:
            with Image.open(p) as image:
                assert image.size==(2160,2700)
                if phase=='stylized-carousel':assert image.mode=='1'
    with Image.open(root/'scan'/'regular-carousel'/'01-panel.png') as image:
        assert image.mode=='L' and len(image.getcolors(256))>2
    assert len(result['social_files'])==3
    for name in result['social_files']:
        assert '--stylized-carousel--' in name
        assert pipeline.digest(root/'all-social'/name)==result['social_files'][name]
    for phase, (folder, stem, prefix) in pipeline.PRINT_COLLECTIONS.items():
        assert result[prefix+'_output']==str(root/folder)
        assert len(result[prefix+'_files'])==2
        for ext in ('png','tif'):
            name=f'scan--{stem}.{ext}'
            assert pipeline.digest(root/folder/name)==pipeline.digest(root/'scan/print'/f'{stem}.{ext}')
            assert result[prefix+'_files'][name]==pipeline.digest(root/folder/name)
    assert pipeline.digest(source)==before


def test_enlargement_preserves_physical_size_and_has_separate_limit(tmp_path):
    source=tmp_path/'scan.png';drawing(source)
    result=pipeline.process(source,pipeline.create_run(tmp_path/'out'),Settings(master_scale=2),Store(tmp_path/'state'))
    with Image.open(Path(result['output'])/'print/full-page.tif') as image:
        assert image.size==(2400,1600)
        assert image.info['dpi']==pytest.approx((1200,1200),abs=.03)
    huge=Image.new('1',(6602,5088),1);huge.save(tmp_path/'large.png')
    imaging.validate_enlargement(tmp_path/'large.png',2)
    with pytest.raises(ValueError,match='200 megapixels'):imaging.validate_enlargement(tmp_path/'large.png',3)
    jobs=Jobs(Store(tmp_path/'limit-state'))
    try:
        row=jobs.register([tmp_path/'large.png'])[0]
        with pytest.raises(ValueError,match='200 megapixels'):jobs.start([row['id']],tmp_path/'oversized',Settings(master_scale=3))
        assert not (tmp_path/'oversized').exists() and jobs.list()==[]
    finally:jobs.close()


def test_missing_dpi_defaults_to_600(tmp_path):
    Image.new('RGB',(20,20),'white').save(tmp_path/'scan.png')
    assert imaging.source_dpi(imaging.load(tmp_path/'scan.png'))==(600,600)


def test_red_sun_is_reused_exactly_and_has_no_gray(tmp_path):
    image=drawing(tmp_path/'scan.png');clean=imaging.ink(image,Settings())
    for grid in (512,1024,None):
        out=red_sun.process(clean,grid)
        assert out.width==(grid or clean.width)
        assert out.mode=='1'
    image=Image.new('L',(1200,800),255);d=ImageDraw.Draw(image);d.line((0,0,1199,799),fill=0,width=8)
    from importlib.util import spec_from_file_location,module_from_spec
    import sys
    source=Path(__file__).resolve().parents[2]/'red-sun/app/src/red_sun/core.py'
    if source.exists():
        spec=spec_from_file_location('red_sun_reference',source);reference=module_from_spec(spec);sys.modules[spec.name]=reference;spec.loader.exec_module(reference)
        for grid in (512,1024,None):
            expected=reference.process(image,reference.Settings(mode='bw',grid_width=grid,min_output_width=0)).image
            actual=red_sun.process(image,grid)
            assert actual.size==expected.size and actual.tobytes()==expected.tobytes()


def test_panel_and_summary_grids_are_independent(monkeypatch):
    image=Image.new('1',(1600,1200),1);calls=[]
    original=red_sun.process
    def record(image,grid):calls.append((image.size,grid));return original(image,grid)
    monkeypatch.setattr(red_sun,'process',record)
    boxes=[Box(0,0,700,900),Box(800,0,700,900)]
    imaging.panel_canvas(imaging.crop_panel(image,boxes[0]),Settings(),True)
    imaging.summary_canvas(image,boxes,Settings(),True)
    assert [grid for _,grid in calls]==[512,1024,1024]
    assert all(size==(700,900) for size,_ in calls)


def test_saved_corrections_are_explicit_drafts_invalidate_and_preserve_order(tmp_path):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state')
    meta=pipeline.preview(source,Settings(),store)
    boxes=[{'x':640,'y':110,'w':450,'h':580},{'x':90,'y':110,'w':450,'h':580}]
    assert pipeline.preview(source,Settings(),store,boxes)['boxes']==boxes
    assert not pipeline.correction(source,store)
    pipeline.save_correction(source,store,meta['id'],boxes)
    assert pipeline.preview(source,Settings(),Store(tmp_path/'state'))['boxes']==boxes
    drawing(source,gray=70)
    assert not pipeline.correction(source,store)
    with pytest.raises(ValueError,match='changed'):pipeline.save_correction(source,store,meta['id'],boxes)
    assert pipeline.preview(source,Settings(),store)['boxes']!=boxes


def test_corrections_follow_fingerprint_and_coordinate_version(tmp_path):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state')
    meta=pipeline.preview(source,Settings(),store);boxes=[meta['boxes'][0]]
    pipeline.save_correction(source,store,meta['id'],boxes)
    moved=tmp_path/'renamed.png';moved.write_bytes(source.read_bytes())
    assert pipeline.correction(moved,store)['boxes']==boxes
    records=store.read('corrections.json',{});key=f'{pipeline.COORDINATE_VERSION}:{pipeline.digest(source)}'
    records[key]['coordinate_version']=0
    from comic_maker.settings import atomic_json
    atomic_json(store.root/'corrections.json',records)
    assert pipeline.correction(source,store)=={}


def test_preview_drag_does_not_repeat_cleanup(tmp_path,monkeypatch):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state')
    meta=pipeline.preview(source,Settings(),store)
    monkeypatch.setattr(imaging,'ink',lambda *_:pytest.fail('Cleanup must be cached'))
    assert pipeline.preview(source,Settings(),store)['id']==meta['id']
    rendered=pipeline.render_preview(store,meta['id'],meta['boxes'],Settings(),1)
    assert (store.root/'previews'/rendered['id']/'crop.png').exists()
    with Image.open(store.root/'previews'/rendered['id']/'stylized.png') as image:
        assert len(image.getcolors(256))==2


def test_regeneration_reuses_page_updates_collected_files_and_removes_old_slides(tmp_path):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state');jobs=Jobs(store)
    try:
        row=jobs.register([source])[0];job=jobs.start([row['id']],tmp_path/'out',Settings())
        job=wait_job(jobs,job['id']);result=job['items'][0]['result'];target=Path(result['output'])
        page=target/'print/full-page.png';before=(pipeline.digest(page),page.stat().st_mtime_ns)
        collected_page=Path(job['processed_page_output'])/'scan--full-page.png'
        page_copy_before=(pipeline.digest(collected_page),collected_page.stat().st_mtime_ns)
        crop_copy=Path(job['book_print_output'])/'scan--comic.png';crop_before=pipeline.digest(crop_copy)
        meta=pipeline.preview(source,Settings(),store)
        pipeline.save_correction(source,store,meta['id'],[{'x':90,'y':110,'w':450,'h':580}])
        jobs.regenerate(job['id'],row['id'],'stack');updated=wait_job(jobs,job['id'])
        assert updated['status']=='complete' and updated['output']==job['output']
        assert updated['items'][0]['result']['panel_count']==1
        assert before==(pipeline.digest(page),page.stat().st_mtime_ns)
        assert page_copy_before==(pipeline.digest(collected_page),collected_page.stat().st_mtime_ns)
        assert pipeline.digest(crop_copy)!=crop_before
        assert pipeline.digest(crop_copy)==pipeline.digest(target/'print/comic.png')
        assert not (target/'regular-carousel/03-summary.png').exists()
        assert len(list(Path(job['social_output']).glob('*.png')))==2
        for path in Path(job['social_output']).glob('*.png'):
            assert pipeline.digest(path)==updated['items'][0]['result']['social_files'][path.name]
    finally:jobs.close()


def test_stage_hashes_repair_only_damaged_files(tmp_path):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state');root=pipeline.create_run(tmp_path/'out')
    first=pipeline.process(source,root,Settings(),store)
    assert pipeline.process(source,root,Settings(),store)['cached']
    page=root/'scan/print/full-page.png';mtime=page.stat().st_mtime_ns
    damaged=root/'scan/stylized-carousel-01/01-panel.png';damaged.write_bytes(b'broken')
    repaired=pipeline.process(source,root,Settings(),store)
    assert not repaired['cached'] and page.stat().st_mtime_ns==mtime
    with Image.open(damaged) as image:assert image.size==(2160,2700)
    social=root/'all-social'/next(iter(repaired['social_files']));social.write_bytes(b'broken')
    old_copy=root/'all-social/scan--regular-carousel--01-panel.png'
    old_copy.write_bytes((root/'scan/regular-carousel/01-panel.png').read_bytes())
    assert pipeline.process(source,root,Settings(),store)['cached']
    assert pipeline.digest(social)==repaired['social_files'][social.name]
    assert not old_copy.exists()
    assert pipeline.process(source,root,Settings(),store)['layout']==first['layout']
    for folder, stem, prefix in pipeline.PRINT_COLLECTIONS.values():
        copied=root/folder/f'scan--{stem}.tif';copied.write_bytes(b'broken')
    assert pipeline.process(source,root,Settings(),store)['cached']
    for folder, stem, prefix in pipeline.PRINT_COLLECTIONS.values():
        assert pipeline.digest(root/folder/f'scan--{stem}.tif')==pipeline.digest(root/'scan/print'/f'{stem}.tif')


def test_uncertain_comic_exports_and_is_flagged(tmp_path):
    source=tmp_path/'uncertain.png';Image.new('RGB',(320,200),'white').save(source)
    result=pipeline.process(source,pipeline.create_run(tmp_path/'out'),Settings(),Store(tmp_path/'state'))
    assert result['status']=='complete' and result['needs_review'] and result['warnings']
    assert result['panel_count']==1 and len(result['social_files'])==2


def test_duplicate_names_get_distinct_collected_copies(tmp_path):
    sources=[]
    for directory in ('first','second'):
        folder=tmp_path/directory;folder.mkdir();source=folder/'Comic A.png';drawing(source);sources.append(source)
    jobs=Jobs(Store(tmp_path/'state'))
    try:
        rows=jobs.register(sources);job=jobs.start([r['id'] for r in rows],tmp_path/'out',Settings())
        completed=wait_job(jobs,job['id'])
        assert completed['status']=='complete'
        assert [i['folder'] for i in completed['items']]==['Comic A','Comic A-2']
        social=Path(job['social_output']);assert len(list(social.glob('*.png')))==6
        for item in completed['items']:
            for name,value in item['result']['social_files'].items():assert pipeline.digest(social/name)==value
        for folder, stem, prefix in pipeline.PRINT_COLLECTIONS.values():
            assert len(list((Path(job['output'])/folder).iterdir()))==4
        # Regenerating an older run backfills every comic's print collections.
        import shutil
        untouched=Path(completed['items'][1]['result']['output'])/'print/full-page.png'
        untouched_mtime=untouched.stat().st_mtime_ns
        for folder, stem, prefix in pipeline.PRINT_COLLECTIONS.values():
            shutil.rmtree(Path(job['output'])/folder)
            completed.pop(prefix+'_output',None)
        completed.pop('collection_version', None)
        old_copy=social/'Comic A--regular-carousel--01-panel.png'
        old_copy.write_bytes((Path(completed['items'][0]['result']['output'])/'regular-carousel/01-panel.png').read_bytes())
        jobs.save(completed);jobs.close()
        jobs=Jobs(Store(tmp_path/'state'))
        reopened=jobs.get(job['id'])
        assert reopened['collection_version']==1 and not old_copy.exists()
        assert untouched.stat().st_mtime_ns==untouched_mtime
        for folder, stem, prefix in pipeline.PRINT_COLLECTIONS.values():
            assert len(list((Path(job['output'])/folder).iterdir()))==4
            assert reopened[prefix+'_output']==str(Path(job['output'])/folder)
        jobs.regenerate(job['id'],rows[0]['id']);updated=wait_job(jobs,job['id'])
        assert updated['status']=='complete' and untouched.stat().st_mtime_ns==untouched_mtime
        for folder, stem, prefix in pipeline.PRINT_COLLECTIONS.values():
            assert len(list((Path(job['output'])/folder).iterdir()))==4
            for item in updated['items']:
                for name,value in item['result'][prefix+'_files'].items():
                    assert pipeline.digest(Path(updated[prefix+'_output'])/name)==value
    finally:jobs.close()


def test_failed_regeneration_keeps_previous_phase(tmp_path,monkeypatch):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state');root=pipeline.create_run(tmp_path/'out')
    first=pipeline.process(source,root,Settings(),store)
    original=imaging.panel_canvas
    def fail(piece,settings,styled=False):
        if styled:raise OSError('disk failure')
        return original(piece,settings,styled)
    monkeypatch.setattr(imaging,'panel_canvas',fail)
    with pytest.raises(OSError):pipeline.process(source,root,Settings(panel_grid_width=256),store)
    for name,value in first['stages']['stylized-carousel']['files'].items():assert pipeline.digest(root/'scan'/name)==value
    new_root=pipeline.create_run(tmp_path/'out')
    with pytest.raises(OSError):pipeline.process(source,new_root,Settings(),store)
    partial=json.loads((new_root/'scan/manifest.json').read_text())
    for folder, stem, prefix in pipeline.PRINT_COLLECTIONS.values():
        assert len(partial[prefix+'_files'])==2
        assert len(list((new_root/folder).iterdir()))==2


def test_run_names_discovery_and_duplicate_names(tmp_path):
    for directory in ['one','two']:
        (tmp_path/directory).mkdir();drawing(tmp_path/directory/'scan.png')
    paths=[tmp_path/'one/scan.png',tmp_path/'two/scan.png']
    assert pipeline.allocate_names(paths)==['scan','scan-2']
    assert pipeline.allocate_names([Path('all-social.png'),Path('ALL-SOCIAL.png')])==['all-social-2','ALL-SOCIAL-3']
    assert pipeline.allocate_names([Path('all-book-prints.png'),Path('all-processed-pages.png')])==['all-book-prints-2','all-processed-pages-2']
    root=pipeline.create_run(tmp_path);drawing(root/'export.png')
    assert pipeline.create_run(tmp_path).name=='comic-maker-run-002'
    discovered,_=pipeline.discover([str(tmp_path)],Settings())
    assert discovered==paths
    assert pipeline.discover([str(tmp_path)],Settings(recursive=False))[0]==[]


def test_bad_file_does_not_block_batch_and_restart(tmp_path):
    good=tmp_path/'good.png';drawing(good);bad=tmp_path/'bad.png';bad.write_text('bad')
    store=Store(tmp_path/'state');jobs=Jobs(store)
    rows=jobs.register([bad,good]);job=jobs.start([r['id'] for r in rows],tmp_path/'out',Settings())
    result=wait_job(jobs,job['id']);jobs.close()
    assert result['status']=='partial' and [i['status'] for i in result['items']]==['failed','complete']
    jobs=Jobs(store);assert jobs.get(job['id'])['items'][1]['result']['panel_count']==2;jobs.close()


def test_cancel_resume_preserves_completed_stages(tmp_path,monkeypatch):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state');jobs=Jobs(store)
    entered=threading.Event();release=threading.Event();original=imaging.panel_canvas
    def block(*args):entered.set();release.wait(10);return original(*args)
    monkeypatch.setattr(imaging,'panel_canvas',block)
    row=jobs.register([source])[0];job=jobs.start([row['id']],tmp_path/'out',Settings())
    assert entered.wait(10);jobs.cancel(job['id']);release.set()
    assert wait_job(jobs,job['id'])['status']=='cancelled'
    page=Path(job['output'])/'scan/print/full-page.png';mtime=page.stat().st_mtime_ns
    jobs.resume(job['id']);assert wait_job(jobs,job['id'])['status']=='complete'
    assert page.stat().st_mtime_ns==mtime;jobs.close()


def test_interrupted_queue_recovers_without_creating_another_run(tmp_path):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state');jobs=Jobs(store)
    row=jobs.register([source])[0];root=pipeline.create_run(tmp_path/'out')
    job={'id':'interrupted','schema_version':2,'created':1,'status':'running','output':str(root),'social_output':str(root/'all-social'),'settings':Settings().model_dump(),'cancel_requested':False,'items':[{'id':row['id'],'source':str(source),'name':source.name,'folder':'scan','status':'running','stage':'Reading scan'}]}
    jobs.save(job);jobs.close();jobs=Jobs(store)
    assert wait_job(jobs,'interrupted')['status']=='complete'
    assert len(list((tmp_path/'out').glob('comic-maker-run-*')))==1;jobs.close()


def test_http_correction_preview_regeneration_and_guards(tmp_path,monkeypatch):
    source=tmp_path/'scan.png';drawing(source);app=create_app(Store(tmp_path/'state'),'test-session')
    with TestClient(app) as client:
        headers={'x-session':'test-session'}
        assert client.get('/').status_code==200
        assert client.post('/api/settings',json={}).status_code==403
        assert client.post('/api/settings',headers={**headers,'origin':'https://evil.test'},json={}).status_code==403
        assert client.get('/api/state',headers={'host':'evil.test'}).status_code==403
        assert client.post('/api/settings',headers=headers,json={'panel_grid_width':1}).status_code==400
        imported=client.post('/api/scan',headers=headers,json={'paths':[str(source)]}).json()['sources'][0]
        meta=client.post('/api/preview',headers=headers,json={'id':imported['id']}).json()
        assert client.get('/api/corrections/'+imported['id']).json()=={}
        boxes=[meta['boxes'][0]]
        assert client.post('/api/corrections/'+imported['id'],headers=headers,json={'preview':meta['id'],'boxes':boxes}).status_code==200
        job=client.post('/api/jobs',headers=headers,json={'ids':[imported['id']],'output':str(tmp_path/'out')}).json()
        result=wait_job(app.state.jobs,job['id']);assert result['items'][0]['result']['panel_count']==1
        opened=[]
        monkeypatch.setattr('comic_maker.server.subprocess.Popen',lambda command,**kwargs:opened.append(command[-1]))
        for collection,key in [('book-prints','book_print_output'),('processed-pages','processed_page_output')]:
            assert client.post('/api/open-output',headers=headers,json={'job':job['id'],'collection':collection}).status_code==200
            assert opened[-1]==job[key]
        artifact=f'/api/artifact/{job["id"]}/{imported["id"]}/print/full-page.tif'
        assert client.get(artifact).status_code==200
        assert client.get(f'/api/artifact/{job["id"]}/{imported["id"]}/../../secret').status_code==404
        assert client.post('/api/jobs/'+job['id']+'/regenerate',headers=headers,json={'source':imported['id'],'layout':'stack'}).status_code==200
        assert wait_job(app.state.jobs,job['id'])['status']=='complete'
        assert client.get('/api/state').json()['session']['output']==str(tmp_path/'out')


def test_old_settings_and_exports_remain_compatible(tmp_path):
    store=Store(tmp_path/'state');store.settings_path.write_text(json.dumps({'style':'paint','ink_strength':60,'cleanup':10,'canvas_width':1080,'colorize':True,'provider':'gemini'}))
    assert store.settings().canvas_width==2160 and store.settings().ink_strength==60
    saved=store.save({}).model_dump();assert 'colorize' not in saved and 'style' not in saved
    jobs=Jobs(store);jobs.save({'id':'old','created':1,'status':'complete','settings':{'style':'paint'},'output':str(tmp_path/'legacy'),'items':[]})
    with pytest.raises(ValueError,match='older exports'):jobs.regenerate('old','scan')
    assert jobs.get('old')['status']=='complete';jobs.close()


def test_invalid_boxes_and_source_safety(tmp_path):
    for boxes in ([],[{'x':-1,'y':0,'w':10,'h':10}],[{'x':0,'y':0,'w':7,'h':10}]):
        with pytest.raises(ValueError):detection.validate_boxes(boxes,(100,100))
    source=tmp_path/'scan.png';drawing(source)
    with pytest.raises(ValueError):pipeline.validate_output(tmp_path,[source])


@pytest.mark.parametrize('name,count',[('09.08.2026 COMICS',4),('09.15.2026 COMICS 1',1),('09.15.2026 COMICS 2',3),('09.15.2026 COMICS',2),('09.20.2026 COMICS 1',4),('09.20.2026 COMICS 2',4),('09.20.2026 COMICS',3)])
def test_supplied_raw_scan_panel_counts(name,count):
    source=Path(__file__).resolve().parents[1]/'examples/raw_comics'/f'{name}.tiff'
    if not source.exists():pytest.skip('Private artist fixtures not present')
    result=detection.analyze(imaging.load(source))
    assert len(result.boxes)==count and not result.warnings


def test_stylized_folders_are_numbered_and_get_a_random_caption(tmp_path):
    lines=tmp_path/'lines.txt';lines.write_text('\nfirst line\n  second line  \n\nthird line\n')
    store=Store(tmp_path/'state');root=pipeline.create_run(tmp_path/'out');settings=Settings(lines_file=str(lines))
    for number in (1,2):
        source=tmp_path/f'scan{number}.png';drawing(source)
        result=pipeline.process(source,root,settings,store,number=number)
        folder=root/f'scan{number}'/f'stylized-carousel-{number:02d}'
        assert (folder/'01-panel.png').is_file() and not (root/f'scan{number}/stylized-carousel').exists()
        assert (folder/'caption.txt').read_text().strip() in ('first line','second line','third line')
        assert not list(folder.glob('*/'))
        assert pipeline.process(source,root,settings,store,number=number)['cached']
    assert len(list((root/'all-social').glob('*.png')))==6
    plain=pipeline.process(tmp_path/'scan1.png',pipeline.create_run(tmp_path/'out'),Settings(),store)
    assert not list(Path(plain['output']).glob('stylized-carousel*/caption.txt'))
    with pytest.raises(ValueError):pipeline.read_lines(tmp_path/'missing.txt')
