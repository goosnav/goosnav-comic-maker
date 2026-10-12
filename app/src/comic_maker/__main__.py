"""GUI launcher and CLI adapter to the same application service."""
from __future__ import annotations
import argparse
import json
import socket
import threading
import time
import webbrowser
import os
from pathlib import Path
from .settings import Store, Settings, atomic_json
from . import pipeline, providers

def main():
    parser=argparse.ArgumentParser(description='Goosnav Comic Maker')
    sub=parser.add_subparsers(dest='command')
    gui=sub.add_parser('gui');gui.add_argument('--port',type=int,default=0);gui.add_argument('--no-browser',action='store_true')
    batch=sub.add_parser('batch');batch.add_argument('paths',nargs='+');batch.add_argument('--out',required=True)
    batch.add_argument('--settings',type=Path);batch.add_argument('--style',choices=['ink','paint']);batch.add_argument('--pattern')
    batch.add_argument('--colorize',action='store_true');batch.add_argument('--fixture',action='store_true');batch.add_argument('--json',action='store_true');batch.add_argument('--force',action='store_true')
    args=parser.parse_args();store=Store()
    if args.command=='batch':
        values=json.loads(args.settings.read_text()) if args.settings else store.settings().model_dump()
        if args.style: values['style']=args.style
        if args.pattern: values['pattern']=args.pattern
        if args.colorize: values['colorize']=True
        if args.fixture: values.update(colorize=True,provider='fixture')
        settings=Settings.model_validate(values)
        files,warnings=pipeline.discover(args.paths,settings,args.out)
        calls=0
        def reserve():
            nonlocal calls
            if calls>=settings.ai_call_limit: raise providers.ProviderFailure('Batch AI call limit reached')
            calls+=1
        results=[]
        for path in files:
            try: results.append(pipeline.process(path,Path(args.out).expanduser().resolve(),settings,store,reserve=reserve,force=args.force))
            except Exception as exc: results.append({'source':str(path),'status':'failed','error':str(exc)})
        report={'results':results,'warnings':warnings,'ai_calls':calls}
        if args.json: print(json.dumps(report))
        else:
            for item in results: print(f'{Path(item["source"]).name}: {item["status"]} — {item.get("output",item.get("error"))}')
            for warning in warnings: print(warning)
        return 0 if files and not warnings and all(r['status']=='complete' for r in results) else 2
    import uvicorn
    from .server import create_app
    # Only one GUI worker may own this data directory. Reopening the launcher
    # returns to the running app instead of replaying its unfinished paid jobs.
    lease=(store.root/'instance.lock').open('a+b')
    try:
        if os.name=='nt':
            import msvcrt
            lease.seek(0);lease.write(b'0');lease.flush();lease.seek(0)
            msvcrt.locking(lease.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lease.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError:
        instance=store.read('instance.json',{})
        port=instance.get('port')
        if not isinstance(port,int) or not 1<=port<=65535:
            print('Comic Maker is starting. Reopen the launcher in a moment.');return 0
        url=f'http://127.0.0.1:{port}'
        print(f'Goosnav Comic Maker is already running · {url}',flush=True)
        if not getattr(args,'no_browser',False): webbrowser.open(url)
        return 0
    sock=socket.socket();sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    sock.bind(('127.0.0.1',getattr(args,'port',0)));port=sock.getsockname()[1]
    atomic_json(store.root/'instance.json',{'port':port})
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
