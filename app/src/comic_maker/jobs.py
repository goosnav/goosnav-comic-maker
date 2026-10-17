"""Durable queue. A single worker bounds memory for full-resolution scans."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from . import pipeline, imaging
from .settings import Settings, atomic_json

class Cancelled(Exception):
    pass

class Jobs:
    def __init__(self,store):
        self.store=store
        self.lock=threading.RLock()
        self.image_lock=threading.Lock()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='comic')
        self.db=sqlite3.connect(store.root/'jobs.sqlite',check_same_thread=False)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)')
        self.db.execute('CREATE TABLE IF NOT EXISTS sources (id TEXT PRIMARY KEY, path TEXT NOT NULL)')
        self.db.commit()
        for job in reversed(self.list()):
            if job.get('schema_version') == 2 and job['status'] not in ('running','queued'):
                root = Path(job['output'])
                if root.is_dir() and (job.get('collection_version') != 1 or
                    any(not (root/folder).is_dir() for folder in pipeline.COLLECTION_FOLDERS)):
                    try:
                        self.refresh_collections(job)
                    except OSError as exc:
                        job['collection_error'] = f'Could not collect exports: {exc}'
                        self.save(job)
            if job['status'] in ('running','queued'):
                if job.get('schema_version') != 2:
                    job['status'] = 'partial'
                    for item in job['items']:
                        if item['status'] in ('running','queued'):
                            item['status'] = 'failed'; item['error'] = 'Import this comic for the new export workflow'
                    self.save(job)
                    continue
                for item in job['items']:
                    if item['status']=='running': item['status']='queued'
                job['status']='queued';self.save(job);self.pool.submit(self.run,job['id'])

    def refresh_collections(self, job):
        """Upgrade existing run collections from exports without reopening scans."""
        root = Path(job['output'])
        for folder in pipeline.COLLECTION_FOLDERS:
            (root/folder).mkdir(exist_ok=True)
        job.update(pipeline.print_collection_paths(root))
        job['social_output'] = str(root/'all-social')
        job['carousel_output'] = str(root/'all-social-carousels')
        for item in job['items']:
            result = item.get('result', {})
            if result.get('schema_version') != 2:
                continue
            result.update(pipeline.collect_prints(root, item['folder']))
            if any((root/item['folder']).glob('stylized-carousel*/')):
                result['social_files'] = pipeline.collect_social(root, item['folder'])
            atomic_json(root/item['folder']/'manifest.json', result)
        job['collection_version'] = 1
        job.pop('collection_error', None)
        self.save(job)
        atomic_json(root/f'batch-{job["id"]}.json', job)

    def save(self,job):
        with self.lock:
            self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?,?)',(job['id'],json.dumps(job)))
            self.db.commit()

    def get(self,identifier):
        with self.lock:
            row=self.db.execute('SELECT data FROM jobs WHERE id=?',(identifier,)).fetchone()
        if not row: raise ValueError('Batch not found')
        return json.loads(row[0])

    def list(self):
        with self.lock:
            rows=self.db.execute('SELECT data FROM jobs ORDER BY rowid DESC LIMIT 100').fetchall()
        return sorted([json.loads(r[0]) for r in rows],key=lambda j:j['created'],reverse=True)

    def register(self,paths):
        result=[]
        with self.lock:
            for path in paths:
                identifier=pipeline.source_id(path)
                self.db.execute('INSERT OR REPLACE INTO sources VALUES (?,?)',(identifier,str(path)))
                result.append({'id':identifier,'path':str(path),'name':path.name,'bytes':path.stat().st_size})
            self.db.commit()
        return result

    def source(self,identifier):
        with self.lock:
            row=self.db.execute('SELECT path FROM sources WHERE id=?',(identifier,)).fetchone()
        if not row: raise ValueError('Import this source first')
        return Path(row[0])

    def start(self, identifiers, output, settings):
        if not identifiers:
            raise ValueError('Choose at least one comic')
        paths = [self.source(i) for i in dict.fromkeys(identifiers)]
        for path in paths:
            try:
                imaging.validate_enlargement(path, settings.master_scale)
            except OSError:
                pass  # An unreadable comic fails in the queue without blocking the others.
        if settings.lines_file:
            pipeline.read_lines(settings.lines_file)
        if settings.ad_enabled:
            pipeline.ad_images(settings.ad_folder)
        target = pipeline.create_run(output)
        names = pipeline.allocate_names(paths)
        job = {'id': uuid.uuid4().hex[:16], 'schema_version': 2, 'created': time.time(), 'stamp': time.strftime('%Y-%m-%d_%H%M'), 'status': 'queued',
               'output': str(target), 'social_output': str(target/'all-social'), 'carousel_output': str(target/'all-social-carousels'), **pipeline.print_collection_paths(target), 'settings': settings.model_dump(),
               'cancel_requested': False, 'items': [
                   {'id': pipeline.source_id(p), 'source': str(p), 'name': p.name, 'folder': name,
                    'status': 'queued', 'stage': 'Waiting'} for p,name in zip(paths,names)]}
        self.save(job)
        self.pool.submit(self.run, job['id'])
        return job

    def regenerate(self, identifier, source_identifier, layout=None):
        with self.lock:
            job = self.get(identifier)
            if job.get('schema_version') != 2:
                raise ValueError('Import this comic to create a new run; older exports remain unchanged')
            if job['status'] in ('queued','running'):
                raise ValueError('Wait for this batch to finish before updating its exports')
            item = next((i for i in job['items'] if i['id'] == source_identifier), None)
            if not item:
                raise ValueError('Comic not found in this batch')
            if layout is not None:
                item['layout'] = Settings(layout=layout).layout
            item['status'] = 'queued'
            item['stage'] = 'Waiting'
            item.pop('boxes', None)
            job['cancel_requested'] = False
            job['status'] = 'queued'
            job.update(pipeline.print_collection_paths(Path(job['output'])))
            self.save(job)
        self.pool.submit(self.run, identifier)
        return job

    def cancel(self,identifier):
        with self.lock:
            job=self.get(identifier);job['cancel_requested']=True;self.save(job)
        return job

    def resume(self,identifier):
        with self.lock:
            job=self.get(identifier)
            if job.get('schema_version') != 2: raise ValueError('Import the originals for the new export workflow')
            if job['status'] in ('running','queued'): raise ValueError('Batch is already running')
            job['cancel_requested']=False;job['status']='queued'
            job.update(pipeline.print_collection_paths(Path(job['output'])))
            for item in job['items']:
                if item['status'] in ('failed','cancelled','partial'): item['status']='queued';item['stage']='Waiting'
            self.save(job)
        self.pool.submit(self.run,identifier)
        return job

    def run(self,identifier):
        job=self.get(identifier);settings=Settings.model_validate(job['settings'])
        job.update(pipeline.print_collection_paths(Path(job['output'])))
        def check():
            if self.get(identifier)['cancel_requested']: raise Cancelled('Stopped after current operation')
        def persist():
            with self.lock:
                current=self.get(identifier)
                job['cancel_requested']=current['cancel_requested']
                self.save(job)
        job['status']='running';persist()
        try:
            for number,item in enumerate(job['items'],1):
                try:
                    if item['status'] in ('complete','warning'):
                        result = item.get('result', {})
                        if result.get('schema_version') == 2:
                            result.update(pipeline.collect_prints(Path(job['output']), item['folder'], check))
                            result['social_files'] = pipeline.collect_social(Path(job['output']), item['folder'], check)
                            atomic_json(Path(result['output'])/'manifest.json', result)
                        persist()
                        continue
                    check();item['status']='running';persist()
                    def event(stage):
                        item['stage']=stage;persist()
                    with self.image_lock:
                        item_settings = settings.model_copy(update={'layout':item.get('layout', settings.layout)})
                        result=pipeline.process(Path(item['source']),Path(job['output']),item_settings,self.store,event,check,comic_name=item.get('folder'),number=number,stamp=job.get('stamp'))
                    item['result']=result
                    item.pop('error', None)
                    item['status']='partial' if result['status']=='partial' else 'warning' if result['warnings'] else 'complete'
                    item['stage']='Already exported' if result.get('cached') else 'Exported'
                except Cancelled:
                    if item['status'] not in ('complete','warning'):
                        item['status']='cancelled';item['stage']='Stopped'
                    persist();break
                except Exception as exc:
                    item['status']='failed';item['stage']='Could not export';item['error']=f'{type(exc).__name__}: {exc}'
                    manifest = Path(job['output'])/item.get('folder', pipeline.folder_name(Path(item['source']).stem))/'manifest.json'
                    if manifest.exists():
                        try:
                            item['result'] = json.loads(manifest.read_text())
                            item['status'] = 'partial'
                        except (OSError,ValueError):
                            pass
                persist()
            for item in job['items']:
                if item['status']=='queued' and self.get(identifier)['cancel_requested']:
                    item['status']='cancelled';item['stage']='Stopped'
            job['status']='cancelled' if self.get(identifier)['cancel_requested'] else 'partial' if any(i['status'] in ('failed','partial') for i in job['items']) else 'complete'
            if job['status'] == 'complete':
                job['collection_version'] = 1
                job.pop('collection_error', None)
        finally:
            if job['status']=='running': job['status']='partial'
            persist()
            # Readable batch report alongside individual manifests, with no credentials.
            try: atomic_json(Path(job['output'])/f'batch-{identifier}.json',job)
            except OSError: pass  # SQLite still retains the complete diagnostic record.

    def close(self):
        self.pool.shutdown(wait=True)
        self.db.close()
