"""GUI launcher and CLI adapter to the same application service."""
from __future__ import annotations
import argparse
import json
import socket
import threading
import time
import webbrowser
import os
import hashlib
import re
import urllib.request
from pathlib import Path
from .settings import Store, Settings, atomic_json
from . import pipeline

def code_revision():
    root = Path(__file__).resolve().parents[2]
    files = sorted((root/'src/comic_maker').glob('*.py')) + sorted((root/'static').glob('*'))
    digest = hashlib.sha256()
    for path in files:
        if path.is_file():
            digest.update(str(path.relative_to(root)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()

def lock_instance(lease):
    if os.name == 'nt':
        import msvcrt
        lease.seek(0);lease.write(b'0');lease.flush();lease.seek(0)
        msvcrt.locking(lease.fileno(),msvcrt.LK_NBLCK,1)
    else:
        import fcntl
        fcntl.flock(lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)

def stop_idle_instance(url):
    with urllib.request.urlopen(url+'/api/state', timeout=5) as response:
        state = json.load(response)
    if any(job['status'] in ('running','queued') for job in state['jobs']):
        return False
    with urllib.request.urlopen(url, timeout=5) as response:
        html = response.read().decode()
    token = re.search(r'name="session" content="([^"]+)"', html).group(1)
    request = urllib.request.Request(url+'/api/quit', data=b'{}',
        headers={'Content-Type':'application/json', 'x-session':token}, method='POST')
    with urllib.request.urlopen(request, timeout=5):
        pass
    return True

def main():
    parser=argparse.ArgumentParser(description='Goosnav Comic Maker')
    sub=parser.add_subparsers(dest='command')
    gui=sub.add_parser('gui');gui.add_argument('--port',type=int,default=0);gui.add_argument('--no-browser',action='store_true')
    batch=sub.add_parser('batch');batch.add_argument('paths',nargs='+');batch.add_argument('--out',required=True)
    batch.add_argument('--settings',type=Path);batch.add_argument('--pattern')
    batch.add_argument('--json',action='store_true')
    args=parser.parse_args();store=Store()
    if args.command=='batch':
        values=json.loads(args.settings.read_text()) if args.settings else store.settings().model_dump()
        if args.pattern: values['pattern']=args.pattern
        settings=Settings.model_validate(values)
        files,warnings=pipeline.discover(args.paths,settings)
        for path in files:
            try:
                from .imaging import validate_enlargement
                validate_enlargement(path, settings.master_scale)
            except OSError:
                pass
        root = pipeline.create_run(args.out) if files else None
        results=[]
        for path,name in zip(files,pipeline.allocate_names(files)):
            try: results.append(pipeline.process(path,root,settings,store,comic_name=name))
            except Exception as exc: results.append({'source':str(path),'status':'failed','error':str(exc)})
        report={'results':results,'warnings':warnings,'output':str(root) if root else None}
        if args.json: print(json.dumps(report))
        else:
            for item in results: print(f'{Path(item["source"]).name}: {item["status"]} — {item.get("output",item.get("error"))}')
            for warning in warnings: print(warning)
        return 0 if files and not warnings and all(r['status']=='complete' for r in results) else 2
    import uvicorn
    from .server import create_app
    # Only one GUI worker may own this data directory.
    lease=(store.root/'instance.lock').open('a+b')
    revision = code_revision()
    try:
        lock_instance(lease)
    except OSError:
        instance=store.read('instance.json',{})
        port=instance.get('port')
        if not isinstance(port,int) or not 1<=port<=65535:
            print('Comic Maker is starting. Reopen the launcher in a moment.');return 0
        url=f'http://127.0.0.1:{port}'
        restarting = False
        if instance.get('revision') != revision:
            try:
                restarting = stop_idle_instance(url)
            except (OSError, ValueError, AttributeError):
                pass
            if restarting:
                print('Loading the updated Comic Maker…', flush=True)
                for _ in range(100):
                    try:
                        lock_instance(lease)
                        break
                    except OSError:
                        time.sleep(.1)
                else:
                    print('Comic Maker is still stopping. Reopen the launcher in a moment.')
                    return 0
            else:
                print('Finish the current batch, then reopen this launcher to load the update.', flush=True)
        if not restarting:
            print(f'Goosnav Comic Maker is already running · {url}',flush=True)
            if not getattr(args,'no_browser',False): webbrowser.open(url)
            return 0
    sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind(('127.0.0.1',getattr(args,'port',0)));port=sock.getsockname()[1]
    atomic_json(store.root/'instance.json',{'port':port, 'revision':revision})
    app=create_app(store)
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='warning'))
    app.state.stop=lambda:setattr(server,'should_exit',True)
    url=f'http://127.0.0.1:{port}'
    print(f'Goosnav Comic Maker · {url}',flush=True)
    if not getattr(args,'no_browser',False):
        def open_when_ready():
            for _ in range(100):
                if server.started: webbrowser.open(url);return
                time.sleep(.1)
        threading.Thread(target=open_when_ready,daemon=True).start()
    try: server.run(sockets=[sock])
    finally: sock.close();lease.close()
    return 0

if __name__=='__main__':
    raise SystemExit(main())
