from pathlib import Path
import io
import json
import time
import base64
import cv2
import httpx
import numpy as np
import pytest
from PIL import Image, ImageDraw
from fastapi.testclient import TestClient
from comic_maker import imaging, pipeline, detection, providers
from comic_maker.settings import Settings, Store
from comic_maker.detector_base import Box
from comic_maker.jobs import Jobs
from comic_maker.server import create_app

@pytest.fixture(autouse=True)
def no_live_credentials(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.delenv('GEMINI_API_KEY',raising=False)

def drawing(path,gray=105):
    img=Image.new('RGB',(1200,800),(246,246,246));d=ImageDraw.Draw(img)
    for x in (100,650):
        d.rectangle((x,120,x+430,680),outline=(gray,)*3,width=5)
        d.ellipse((x+70,260,x+240,440),outline=(gray,)*3,width=5)
        d.line((x+155,440,x+155,580),fill=(gray,)*3,width=5)
        d.text((x+40,180),'HELLO, COMIC!',fill=(gray,)*3)
    img.save(path);return img

def wait_job(jobs,identifier):
    until=time.monotonic()+30
    while time.monotonic()<until:
        job=jobs.get(identifier)
        if job['status'] not in ('queued','running'): return job
        time.sleep(.03)
    raise AssertionError('Job did not finish')

def test_cleanup_and_final_resize_remain_binary(tmp_path):
    img=drawing(tmp_path/'scan.png')
    clean=imaging.ink(img,Settings())
    assert set(np.unique(np.asarray(clean.convert('L'))))=={0,255}
    slides,summary,_=imaging.render(clean,[Box(95,115,440,570),Box(645,115,440,570)],Settings())
    for image in [*slides,summary]:
        assert image.size==(1080,1350)
        assert set(np.unique(np.asarray(image.convert('L'))))=={0,255}
    assert np.mean(np.asarray(clean.convert('L'))<128)<.10

def test_paint_changes_detail_without_adding_gray(tmp_path):
    clean=imaging.ink(drawing(tmp_path/'scan.png'),Settings())
    painted=imaging.paint(clean,Settings(style='paint',crunch=75),600)
    assert painted.size==clean.size
    assert painted.tobytes()!=clean.tobytes()
    assert set(np.unique(np.asarray(painted.convert('L'))))=={0,255}

def test_flat_color_export_contains_only_shared_palette():
    arr=np.full((700,1100,3),255,np.uint8)
    arr[100:600,100:1000]=(180,60,40)
    cv2.line(arr,(120,120),(900,580),(0,0,0),7,cv2.LINE_AA)
    q=imaging.flat_color(Image.fromarray(arr),16)
    allowed=set(q.convert('RGB').getdata())|{(255,255,255)}
    slides,summary,_=imaging.render(q,[Box(80,80,940,540)],Settings(),True)
    assert len(allowed)<=16
    assert all(set(im.convert('RGB').getdata())<=allowed for im in [*slides,summary])

def test_neutral_edges_cannot_become_colored_fringes():
    arr=np.full((300,400,3),255,np.uint8)
    arr[:,200:]=(80,150,140)
    arr[:,20:25]=0
    arr[:,25]=120
    q=imaging.flat_color(Image.fromarray(arr),16).convert('RGB')
    for y in (20,100,200):
        pixel=q.getpixel((25,y))
        assert max(pixel)==min(pixel)

def test_punctuation_survives_default_cleanup():
    image=Image.new('L',(660,500),245);draw=ImageDraw.Draw(image)
    draw.rectangle((100,100,105,105),fill=90)
    clean=imaging.ink(image.convert('RGB'),Settings())
    assert clean.getpixel((102,102))==0

def test_input_is_unchanged_and_exports_cache(tmp_path):
    source=tmp_path/'scan.png';drawing(source)
    before=pipeline.digest(source);store=Store(tmp_path/'state')
    result=pipeline.process(source,tmp_path/'exports',Settings(),store)
    assert result['panel_count']==2
    assert result['status']=='complete'
    assert pipeline.digest(source)==before
    assert len(result['files'])==6
    assert pipeline.process(source,tmp_path/'exports',Settings(),store)['cached']
    damaged=Path(result['output'])/'bw/01-panel.png';damaged.write_bytes(b'broken')
    repaired=pipeline.process(source,tmp_path/'exports',Settings(),store)
    assert not repaired.get('cached')
    with Image.open(damaged) as im: assert im.size==(1080,1350)

def test_recursive_scan_filter_duplicates_and_export_exclusion(tmp_path):
    source=tmp_path/'in';source.mkdir();nested=source/'nested';nested.mkdir()
    drawing(source/'one raw.png');drawing(nested/'two raw.png');drawing(source/'finished.png')
    out=source/'out';out.mkdir();(out/'.comic-maker-export').touch();drawing(out/'generated raw.png')
    paths,warnings=pipeline.discover([str(source),str(nested)],Settings(pattern='*raw*'))
    assert len(paths)==2 and not warnings

def test_fixture_color_has_real_files_and_label(tmp_path):
    source=tmp_path/'scan.png';drawing(source)
    result=pipeline.process(source,tmp_path/'exports',Settings(colorize=True,provider='fixture'),Store(tmp_path/'state'))
    assert result['fixture'] and result['color_status']=='fixture'
    assert 'color/01-panel.png' in result['files']
    assert result['color_check']['line_recall']>.95

def test_missing_key_preserves_bw_and_reports_partial(tmp_path,monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    source=tmp_path/'scan.png';drawing(source)
    result=pipeline.process(source,tmp_path/'exports',Settings(colorize=True),Store(tmp_path/'state'))
    assert result['status']=='partial'
    assert result['color_status']=='failed' and (Path(result['output'])/'bw/summary.png').is_file()

def test_batch_survives_bad_file_and_persists(tmp_path):
    good=tmp_path/'good.png';drawing(good);bad=tmp_path/'bad.png';bad.write_text('not an image')
    store=Store(tmp_path/'state');jobs=Jobs(store)
    items=jobs.register([bad,good]);job=jobs.start([i['id'] for i in items],tmp_path/'out',Settings())
    result=wait_job(jobs,job['id']);jobs.close()
    assert result['status']=='partial'
    assert [i['status'] for i in result['items']]==['failed','complete']
    reopened=Jobs(store)
    assert reopened.get(job['id'])['items'][1]['result']['panel_count']==2
    reopened.close()

def test_http_import_preview_export_and_guards(tmp_path):
    drawing(tmp_path/'scan.png')
    app=create_app(Store(tmp_path/'state'),'test-session')
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.post('/api/settings',json={}).status_code==403
        headers={'x-session':'test-session'}
        assert client.post('/api/settings',headers={**headers,'origin':'https://evil.test'},json={}).status_code==403
        assert client.get('/api/state',headers={'host':'evil.test'}).status_code==403
        imported=client.post('/api/scan',headers=headers,json={'paths':[str(tmp_path/'scan.png')]}).json()
        identifier=imported['sources'][0]['id']
        preview=client.post('/api/preview',headers=headers,json={'id':identifier,'settings':{}}).json()
        assert len(preview['boxes'])==2
        assert client.get(f'/api/preview/{preview["id"]}/summary.png').status_code==200
        response=client.post('/api/jobs',headers=headers,json={'ids':[identifier],'output':str(tmp_path/'out')}).json()
        result=wait_job(app.state.jobs,response['id'])
        assert result['status']=='complete'
        assert client.get(f'/api/artifact/{response["id"]}/{identifier}/bw/summary.png').status_code==200
        assert client.get(f'/api/artifact/{response["id"]}/{identifier}/manifest.json').status_code==404

def test_openai_provider_request_shape_and_fidelity():
    image=Image.new('RGB',(800,600),'white');ImageDraw.Draw(image).rectangle((100,100,650,500),outline='black',width=8)
    calls=[]
    def handle(request):
        assert request.url.path=='/v1/images/edits'
        assert b'gpt-image-2.5-sunburst' in request.content
        assert b'input_fidelity' not in request.content
        assert b'name="image"' in request.content
        return httpx.Response(200,json={'data':[{'b64_json':base64.b64encode(imaging.png_bytes(image)).decode()}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        output=providers.request_color(image,Settings(),'test-key',lambda:calls.append(1),client)
    assert len(calls)==1
    assert not providers.fidelity(image,output)['suspect']
    assert providers.fidelity(image,Image.new('RGB',image.size,'white'))['suspect']

def test_gemini_provider_request_shape():
    image=Image.new('RGB',(800,600),'white')
    def handle(request):
        body=json.loads(request.content)
        assert body['generationConfig']['imageConfig']['imageSize']=='4K'
        assert request.headers['x-goog-api-key']=='test-key'
        return httpx.Response(200,json={'candidates':[{'content':{'parts':[{'inlineData':{'data':base64.b64encode(imaging.png_bytes(image)).decode()}}]}}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result=providers.request_color(image,Settings(provider='gemini',image_model='gemini-3-pro-image'),'test-key',lambda:None,client)
    assert result.size==image.size

def test_invalid_panel_boxes_rejected():
    with pytest.raises(ValueError): detection.validate_boxes([{'x':-1,'y':0,'w':10,'h':10}],(100,100))
    with pytest.raises(ValueError): detection.validate_boxes([], (100,100))

def test_blank_prompt_restores_default_and_private_keys(tmp_path):
    store=Store(tmp_path)
    assert store.save({'prompt':' '}).prompt.startswith('Add color')
    store.set_key('openai','secret-for-test')
    assert store.key_status()['openai']
    assert 'secret-for-test' not in json.dumps(store.key_status())
    assert (tmp_path/'keys.json').stat().st_mode & 0o777==0o600

def test_existing_color_is_processed_without_ai(tmp_path,monkeypatch):
    source=tmp_path/'colored.png';img=drawing(source);ImageDraw.Draw(img).rectangle((180,310,290,390),fill=(240,80,50));img.save(source)
    def forbidden(*args,**kwargs): raise AssertionError('Existing color must not call AI')
    monkeypatch.setattr(providers,'request_color',forbidden)
    result=pipeline.process(source,tmp_path/'out',Settings(colorize=True),Store(tmp_path/'state'))
    assert result['color_status']=='original'
    assert 'color/summary.png' in result['files']

def test_fidelity_retry_is_bounded_and_uses_new_response(tmp_path,monkeypatch):
    source=tmp_path/'scan.png';drawing(source);calls=[]
    def request(image,settings,key,reserve,**kwargs):
        reserve();calls.append(kwargs.get('correction'))
        return Image.new('RGB',image.size,'white') if len(calls)==1 else image.convert('RGB')
    monkeypatch.setattr(providers,'request_color',request)
    result=pipeline.process(source,tmp_path/'out',Settings(colorize=True),Store(tmp_path/'state'))
    assert calls==[False,True]
    assert result['color_status']=='beta'

def test_transient_retry_and_quota_no_retry(tmp_path,monkeypatch):
    source=tmp_path/'scan.png';drawing(source);calls=[]
    def request(image,settings,key,reserve,**kwargs):
        reserve();calls.append(1)
        if len(calls)==1: raise providers.ProviderFailure('Provider temporarily unavailable')
        return image.convert('RGB')
    monkeypatch.setattr(providers,'request_color',request);monkeypatch.setattr(providers,'backoff',lambda _:None)
    result=pipeline.process(source,tmp_path/'out',Settings(colorize=True),Store(tmp_path/'state'))
    assert len(calls)==2 and result['color_status']=='beta'
    with httpx.Client(transport=httpx.MockTransport(lambda _:httpx.Response(429,json={'error':{'code':'insufficient_quota'}}))) as client:
        with pytest.raises(providers.ProviderFailure) as exc: providers.post(client,'https://api.openai.com/test',lambda:None)
    assert not providers.transient(exc.value)

def test_request_budget_keeps_local_export(tmp_path,monkeypatch):
    source=tmp_path/'scan.png';drawing(source)
    monkeypatch.setattr(providers,'request_color',lambda image,settings,key,reserve,**kw:reserve())
    store=Store(tmp_path/'state');jobs=Jobs(store)
    registered=jobs.register([source]);job=jobs.start([registered[0]['id']],tmp_path/'out',Settings(colorize=True,ai_call_limit=0))
    result=wait_job(jobs,job['id']);jobs.close()
    assert result['calls']==0 and result['status']=='partial'
    assert result['items'][0]['result']['color_status']=='failed'
    assert (Path(result['items'][0]['result']['output'])/'bw/summary.png').exists()

def test_cancel_then_resume_does_not_lose_completed_items(tmp_path,monkeypatch):
    import threading
    source=tmp_path/'scan.png';drawing(source);entered=threading.Event();release=threading.Event()
    original=pipeline.process
    def slow(*args,**kwargs):
        entered.set();release.wait(5);return original(*args,**kwargs)
    monkeypatch.setattr(pipeline,'process',slow)
    store=Store(tmp_path/'state');jobs=Jobs(store);registered=jobs.register([source]);job=jobs.start([registered[0]['id']],tmp_path/'out',Settings())
    assert entered.wait(5);jobs.cancel(job['id']);release.set()
    stopped=wait_job(jobs,job['id']);assert stopped['status']=='cancelled'
    monkeypatch.setattr(pipeline,'process',original);jobs.resume(job['id'])
    assert wait_job(jobs,job['id'])['status']=='complete';jobs.close()

def test_restart_recovers_incomplete_job(tmp_path):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state');jobs=Jobs(store)
    registered=jobs.register([source]);identifier=registered[0]['id']
    job={'id':'interrupted','created':1,'status':'running','output':str(tmp_path/'out'),'settings':Settings().model_dump(),'cancel_requested':False,'calls':0,'force':False,'items':[{'id':identifier,'source':str(source),'name':source.name,'status':'running','stage':'Reading scan','boxes':None}]}
    jobs.save(job);jobs.close();jobs=Jobs(store)
    assert wait_job(jobs,'interrupted')['status']=='complete';jobs.close()

def test_output_cannot_be_ancestor_of_original(tmp_path):
    source=tmp_path/'inputs'/'scan.png';source.parent.mkdir();drawing(source)
    with pytest.raises(ValueError): pipeline.validate_output(tmp_path,[source])

def test_duplicate_names_and_changed_settings_have_separate_exports(tmp_path):
    one=tmp_path/'a';two=tmp_path/'b';one.mkdir();two.mkdir()
    drawing(one/'scan.png');drawing(two/'scan.png');store=Store(tmp_path/'state')
    a=pipeline.process(one/'scan.png',tmp_path/'out',Settings(),store)
    b=pipeline.process(two/'scan.png',tmp_path/'out',Settings(),store)
    c=pipeline.process(one/'scan.png',tmp_path/'out',Settings(style='paint'),store)
    assert len({a['output'],b['output'],c['output']})==3

def test_manual_correction_survives_reopen_and_is_invalidated_on_source_change(tmp_path):
    source=tmp_path/'scan.png';drawing(source);store=Store(tmp_path/'state')
    boxes=[{'x':640,'y':110,'w':450,'h':580},{'x':90,'y':110,'w':450,'h':580}]
    preview=pipeline.preview(source,Settings(),store,boxes)
    assert preview['method']=='manual'
    again=pipeline.preview(source,Settings(),Store(tmp_path/'state'))
    assert again['boxes']==boxes
    result=pipeline.process(source,tmp_path/'out',Settings(),store)
    assert result['boxes']==boxes
    drawing(source,gray=70)
    changed=pipeline.preview(source,Settings(),store)
    assert changed['method']!='manual'

def test_vision_coordinates_are_validated_and_scaled():
    image=Image.new('RGB',(1200,800),'white');calls=[]
    def handle(request):
        body=json.loads(request.content)
        assert body['model']=='gpt-6-astra'
        return httpx.Response(200,json={'output':[{'content':[{'type':'output_text','text':json.dumps({'boxes':[{'x':100,'y':100,'w':300,'h':600}]})}]}]})
    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        boxes=providers.vision_boxes(image,Settings(),'test-key',lambda:calls.append(1),client)
    assert boxes==[{'x':120,'y':80,'w':360,'h':480}] and len(calls)==1
    detection.validate_boxes(boxes,image.size)

@pytest.mark.parametrize('name,count', [('09.08.2026 COMICS',4),('09.15.2026 COMICS 1',1),('09.15.2026 COMICS 2',3),('09.15.2026 COMICS',2),('09.20.2026 COMICS 1',4),('09.20.2026 COMICS 2',4),('09.20.2026 COMICS',3)])
def test_supplied_raw_scan_panel_counts(name,count):
    path=Path(__file__).resolve().parents[1]/'examples/raw_comics'/f'{name}.tiff'
    if not path.exists(): pytest.skip('Private artist fixtures not present')
    result=detection.analyze(imaging.load(path))
    assert len(result.boxes)==count
    assert not result.warnings
