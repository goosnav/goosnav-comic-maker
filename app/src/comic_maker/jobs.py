"""Durable queue. A single worker bounds memory for full-resolution scans."""
from __future__ import annotations
import json
import sqlite3
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from . import pipeline, providers
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
            if job['status'] in ('running','queued'):
                for item in job['items']:
                    if item['status']=='running': item['status']='queued'
                job['status']='queued';self.save(job);self.pool.submit(self.run,job['id'])

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

    def start(self,identifiers,output,settings,overrides=None,force=False):
        if not identifiers: raise ValueError('Choose at least one comic')
        paths=[self.source(i) for i in dict.fromkeys(identifiers)]
        target=pipeline.validate_output(output,paths)
        target.mkdir(parents=True,exist_ok=True)
        job={'id':uuid.uuid4().hex[:16],'created':time.time(),'status':'queued','output':str(target),
            'settings':settings.model_dump(),'cancel_requested':False,'calls':0,'force':force,
            'items':[{'id':pipeline.source_id(p),'source':str(p),'name':p.name,'status':'queued','stage':'Waiting','boxes':(overrides or {}).get(pipeline.source_id(p))} for p in paths]}
        self.save(job);self.pool.submit(self.run,job['id'])
        return job

    def cancel(self,identifier):
        with self.lock:
            job=self.get(identifier);job['cancel_requested']=True;self.save(job)
        return job

    def resume(self,identifier):
        with self.lock:
            job=self.get(identifier)
            if job['status'] in ('running','queued'): raise ValueError('Batch is already running')
            job['cancel_requested']=False;job['status']='queued'
            for item in job['items']:
                if item['status'] in ('failed','cancelled','partial'): item['status']='queued';item['stage']='Waiting'
            self.save(job)
        self.pool.submit(self.run,identifier)
        return job

    def run(self,identifier):
        job=self.get(identifier);settings=Settings.model_validate(job['settings'])
        def check():
            if self.get(identifier)['cancel_requested']: raise Cancelled('Stopped after current operation')
        def persist():
            with self.lock:
                current=self.get(identifier)
                job['cancel_requested']=current['cancel_requested']
                self.save(job)
        def reserve():
            check()
            if job['calls']>=settings.ai_call_limit:
                raise providers.ProviderFailure('Batch AI call limit reached; local exports remain available')
            job['calls']+=1;persist()
        job['status']='running';persist()
        try:
            for item in job['items']:
                if item['status'] in ('complete','warning'): continue
                try:
                    check();item['status']='running';persist()
                    def event(stage):
                        item['stage']=stage;persist()
                    with self.image_lock:
                        result=pipeline.process(Path(item['source']),Path(job['output']),settings,self.store,reserve,event,check,item.get('boxes'),job['force'])
                    item['result']=result
                    item['status']='partial' if result['status']=='partial' else 'warning' if result['warnings'] else 'complete'
                    item['stage']='Already exported' if result.get('cached') else 'Exported'
                except Cancelled:
                    item['status']='cancelled';item['stage']='Stopped';persist();break
                except Exception as exc:
                    item['status']='failed';item['stage']='Could not export';item['error']=f'{type(exc).__name__}: {exc}'
                persist()
            for item in job['items']:
                if item['status']=='queued' and self.get(identifier)['cancel_requested']:
                    item['status']='cancelled';item['stage']='Stopped'
            job['status']='cancelled' if self.get(identifier)['cancel_requested'] else 'partial' if any(i['status'] in ('failed','partial') for i in job['items']) else 'complete'
        finally:
            if job['status']=='running': job['status']='partial'
            persist()
            # Readable batch report alongside individual manifests, with no credentials.
            try: atomic_json(Path(job['output'])/f'batch-{identifier}.json',job)
            except OSError: pass  # SQLite still retains the complete diagnostic record.

    def close(self):
        self.pool.shutdown(wait=True)
        self.db.close()
