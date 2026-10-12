"""Canonical verifier: tests, real loopback HTTP, CLI, output pixels and shutdown."""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
import httpx
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]

def main():
    for path in (ROOT/'app/src').rglob('*.py'):
        ast.parse(path.read_text(),filename=str(path))
    subprocess.run([sys.executable,'-m','pytest','-q','-p','no:cacheprovider'],cwd=ROOT,check=True)
    with tempfile.TemporaryDirectory(prefix='comic-maker-verify-') as temporary:
        work=Path(temporary);source=work/'fixture.png'
        image=Image.new('RGB',(1000,650),(247,247,247));draw=ImageDraw.Draw(image)
        for x in (70,540):
            draw.rectangle((x,80,x+390,570),outline=(100,100,100),width=6)
            draw.ellipse((x+100,220,x+280,400),outline=(90,90,90),width=5)
            draw.text((x+60,140),'FIXTURE: NOT REAL ART',fill=(80,80,80))
        image.save(source)
        env={**os.environ,'PYTHONPATH':str(ROOT/'app/src'),'COMIC_MAKER_DATA':str(work/'state'),'PYTHONDONTWRITEBYTECODE':'1'}
        env.pop('OPENAI_API_KEY',None);env.pop('GEMINI_API_KEY',None)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        with (work/'server.log').open('w') as log:
            process=subprocess.Popen([sys.executable,'-m','comic_maker','gui','--no-browser','--port',str(port)],cwd=ROOT,env=env,stdout=log,stderr=log)
            try:
                with httpx.Client(base_url=f'http://127.0.0.1:{port}',timeout=60) as client:
                    for _ in range(100):
                        try:
                            if client.get('/health/ready').status_code==200: break
                        except httpx.TransportError: pass
                        time.sleep(.1)
                    else: raise AssertionError('Server did not become ready')
                    html=client.get('/').text
                    token=re.search(r'name="session" content="([^"]+)"',html).group(1)
                    headers={'x-session':token}
                    assert client.get('/static/app.js').status_code==200
                    assert client.post('/api/settings',json={}).status_code==403
                    values={}
                    imported=client.post('/api/scan',headers=headers,json={'paths':[str(source)],'output':str(work/'exports')}).json()
                    identifier=imported['sources'][0]['id']
                    job=client.post('/api/jobs',headers=headers,json={'ids':[identifier],'output':str(work/'exports'),'settings':values}).json()
                    for _ in range(200):
                        result=client.get('/api/jobs/'+job['id']).json()
                        if result['status'] not in ('queued','running'): break
                        time.sleep(.1)
                    assert result['status']=='complete',result
                    item=result['items'][0]['result']
                    assert item['schema_version']==2 and item['panel_count']==2
                    assert len(item['social_files'])==6
                    for name in item['files']:
                        with Image.open(Path(item['output'])/name) as im:
                            im.verify()
                    url=f'/api/artifact/{job["id"]}/{identifier}/regular-carousel/03-summary.png'
                    assert client.get(url).status_code==200
                    client.post('/api/quit',headers=headers,json={}).raise_for_status()
                process.wait(timeout=10)
                assert process.returncode==0
            finally:
                if process.poll() is None: process.terminate();process.wait(timeout=10)
        cli=subprocess.run([sys.executable,'-m','comic_maker','batch',str(source),'--out',str(work/'cli'),'--json'],env=env,cwd=ROOT,capture_output=True,text=True,check=True)
        assert json.loads(cli.stdout)['results'][0]['schema_version']==2
        negative=subprocess.run([sys.executable,'-m','comic_maker','batch',str(work/'missing.png'),'--out',str(work/'negative'),'--json'],env=env,cwd=ROOT,capture_output=True,text=True)
        assert negative.returncode==2
    print('PASS: processing and recovery tests; real HTTP import/export; collected social copies; CLI; negative path; clean shutdown.')
    print('Browser interactions and scan inspection are recorded separately in VERIFICATION.md.')

if __name__=='__main__': main()
