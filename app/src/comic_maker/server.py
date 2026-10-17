"""Loopback GUI adapter. Image and job behavior live in shared services."""
from __future__ import annotations
import json
import secrets
import shutil
import subprocess
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from . import pipeline, imaging, __version__
from .settings import Store, Settings, atomic_json
from .jobs import Jobs

STATIC=Path(__file__).resolve().parents[2]/'static'

def dialog(kind):
    if kind not in ('folder','files','output'): raise ValueError('Unknown picker')
    if sys.platform=='darwin':
        command='choose file with multiple selections allowed' if kind=='files' else 'choose folder' + (' with multiple selections allowed' if kind=='folder' else '')
        script=f'''set chosen to {command}
if class of chosen is not list then set chosen to {{chosen}}
set paths to ""
repeat with itemPath in chosen
set paths to paths & POSIX path of itemPath & linefeed
end repeat
return paths'''
        result=subprocess.run(['osascript','-e',script],capture_output=True,text=True,timeout=180)
    elif sys.platform=='win32':
        script='Add-Type -AssemblyName System.Windows.Forms; '
        if kind=='files':
            script+='$d=New-Object System.Windows.Forms.OpenFileDialog; $d.Multiselect=$true; if($d.ShowDialog() -eq "OK"){ $d.FileNames }'
        else:
            script+='$d=New-Object System.Windows.Forms.FolderBrowserDialog; if($d.ShowDialog() -eq "OK"){ $d.SelectedPath }'
        result=subprocess.run(['powershell','-NoProfile','-STA','-Command',script],capture_output=True,text=True,timeout=180)
    elif shutil.which('zenity'):
        result=subprocess.run(['zenity','--file-selection',*(['--multiple','--separator=\n'] if kind=='files' else ['--directory'])],capture_output=True,text=True,timeout=180)
    else:
        raise ValueError('No native picker is installed. Paste a folder path in the source field.')
    return [p for p in result.stdout.strip().splitlines() if p]

def derived_output(paths):
    """Exports go inside the chosen folder (or beside-the-file's own folder)."""
    first=Path(paths[0]).expanduser() if paths and isinstance(paths[0],str) else None
    return str(first if first.is_dir() else first.parent) if first and first.exists() else None

def create_app(store=None,token=None):
    store=store or Store();token=token or secrets.token_urlsafe(32)
    jobs=Jobs(store)
    @asynccontextmanager
    async def lifespan(app):
        yield
        jobs.close()
    app=FastAPI(title='Goosnav Comic Maker',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store;app.state.jobs=jobs;app.state.token=token;app.state.stop=None

    @app.middleware('http')
    async def guard(request: Request, call_next):
        if request.url.hostname not in ('127.0.0.1','localhost','::1','testserver'):
            return JSONResponse({'error':'Local access only'},status_code=403)
        origin=request.headers.get('origin')
        expected=f'{request.url.scheme}://{request.url.netloc}'
        if origin and origin!=expected:
            return JSONResponse({'error':'Foreign origin refused'},status_code=403)
        if request.method not in ('GET','HEAD') and not secrets.compare_digest(request.headers.get('x-session',''),token):
            return JSONResponse({'error':'Reopen the app to refresh your session'},status_code=403)
        response=await call_next(request)
        response.headers['Cache-Control']='no-store'
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['Referrer-Policy']='no-referrer'
        response.headers['Content-Security-Policy']="default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request,exc): return JSONResponse({'error':str(exc)},status_code=400)

    @app.exception_handler(ValidationError)
    async def invalid_settings(request,exc):
        return JSONResponse({'error':'Invalid settings: '+str(exc)},status_code=400)

    @app.exception_handler(KeyError)
    async def missing_field(request,exc): return JSONResponse({'error':f'Missing request field: {exc}'},status_code=400)

    @app.exception_handler(OSError)
    async def file_error(request,exc): return JSONResponse({'error':f'Could not access the file or folder: {exc}'},status_code=400)

    @app.get('/',response_class=HTMLResponse)
    def index(): return (STATIC/'index.html').read_text().replace('__SESSION__',token)

    @app.get('/health/ready')
    def health(): return {'ok':True,'version':__version__}

    @app.get('/api/state')
    def state():
        session=store.read('session.json',{})
        return {'version':__version__,'settings':store.settings().model_dump(),
                'session':session,'jobs':jobs.list(),'default_output':derived_output(session.get('paths',[])) or str(Path.home()/'Pictures/Goosnav Exports')}

    @app.post('/api/settings')
    def settings(body:dict): return store.save(body).model_dump()

    @app.post('/api/dialog')
    def choose(body:dict): return {'paths':dialog(body.get('kind','folder'))}

    @app.post('/api/scan')
    def scan(body:dict):
        values=Settings.model_validate(body.get('settings',store.settings().model_dump()))
        paths=body.get('paths',[])
        if not isinstance(paths,list) or not all(isinstance(p,str) for p in paths): raise ValueError('Supply a list of folder or file paths')
        files,warnings=pipeline.discover(paths,values,body.get('output'))
        sources=jobs.register(files)
        previous_session=store.read('session.json',{})
        atomic_json(store.root/'session.json',{'paths':paths,'sources':sources,'output':body.get('destination',body.get('output',previous_session.get('output',''))),'output_manual':bool(body.get('manual'))})
        return {'sources':sources,'warnings':warnings,'default_output':derived_output(paths)}

    @app.get('/api/source/{identifier}')
    def source_image(identifier:str):
        path=jobs.source(identifier)
        cache=store.root/'thumbnails'/f'{identifier}-{path.stat().st_mtime_ns}.png'
        if not cache.exists():
            with jobs.image_lock: imaging.save(imaging.thumbnail(imaging.load(path),1600),cache)
        return FileResponse(cache)

    @app.post('/api/preview')
    def preview(body:dict):
        settings=Settings.model_validate(body.get('settings',{}))
        with jobs.image_lock:
            return pipeline.preview(jobs.source(body['id']),settings,store,body.get('boxes'))

    @app.get('/api/preview/{identifier}/{name}')
    def preview_file(identifier:str,name:str):
        if not identifier.isalnum() or name not in ('source.png','page.png','summary.png','crop.png','regular.png','stylized.png'): raise HTTPException(404)
        path=store.root/'previews'/identifier/name
        if not path.is_file(): raise HTTPException(404)
        return FileResponse(path)

    @app.post('/api/jobs')
    def start(body:dict):
        settings=Settings.model_validate(body.get('settings',{}));store.save(settings.model_dump())
        if not str(body.get('output','')).strip(): raise ValueError('Choose an export folder')
        job=jobs.start(body.get('ids',[]),body['output'],settings)
        session=store.read('session.json',{});session['output']=str(Path(body['output']).expanduser().resolve());session['output_manual']=bool(body.get('manual'))
        atomic_json(store.root/'session.json',session)
        return job

    @app.get('/api/corrections/{identifier}')
    def read_correction(identifier:str):
        return pipeline.correction(jobs.source(identifier), store)

    @app.post('/api/corrections/{identifier}')
    def save_correction(identifier:str, body:dict):
        with jobs.image_lock:
            return pipeline.save_correction(jobs.source(identifier), store, body['preview'], body['boxes'])

    @app.post('/api/preview/render')
    def render_preview(body:dict):
        with jobs.image_lock:
            return pipeline.render_preview(store, body['preview'], body['boxes'], Settings.model_validate(body.get('settings',{})), body.get('selected',0))

    @app.post('/api/jobs/{identifier}/regenerate')
    def regenerate(identifier:str, body:dict):
        return jobs.regenerate(identifier, body['source'], body.get('layout'))

    @app.get('/api/jobs')
    def list_jobs(): return jobs.list()

    @app.get('/api/jobs/{identifier}')
    def get_job(identifier:str): return jobs.get(identifier)

    @app.post('/api/jobs/{identifier}/{action}')
    def job_action(identifier:str,action:str):
        if action=='cancel': return jobs.cancel(identifier)
        if action=='resume': return jobs.resume(identifier)
        raise ValueError('Unknown batch action')

    @app.get('/api/artifact/{job_id}/{source_id}/{name:path}')
    def artifact(job_id:str,source_id:str,name:str):
        job=jobs.get(job_id)
        item=next((i for i in job['items'] if i['id']==source_id),None)
        result=(item or {}).get('result',{})
        if name not in result.get('files',{}): raise HTTPException(404)
        root=Path(result['output']).resolve();path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file(): raise HTTPException(404)
        return FileResponse(path)

    @app.post('/api/open-output')
    def open_output(body:dict):
        job=jobs.get(body['job'])
        path=job.get('social_output', job['output']) if body.get('social') else job['output']
        if body.get('collection'):
            key = {'book-prints':'book_print_output', 'processed-pages':'processed_page_output', 'social-carousels':'carousel_output'}.get(body['collection'])
            if not key or key not in job:
                raise ValueError('Collection not found in this batch')
            path = job[key]
        if body.get('source'):
            item=next((i for i in job['items'] if i['id']==body['source']),None)
            path=(item or {}).get('result',{}).get('output',path)
        command=['open',path] if sys.platform=='darwin' else ['explorer',path] if sys.platform=='win32' else ['xdg-open',path]
        subprocess.Popen(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        return {'ok':True}

    @app.post('/api/quit')
    def quit_app():
        for job in jobs.list():
            if job['status'] in ('running','queued'): jobs.cancel(job['id'])
        if app.state.stop: app.state.stop()
        return {'ok':True}

    app.mount('/static',StaticFiles(directory=STATIC),name='static')
    return app
