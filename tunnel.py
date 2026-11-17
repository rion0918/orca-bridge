"""Provider-specific transport around the unchanged authenticated local Bridge API."""
import argparse
import json
import logging
from pathlib import Path
import queue
import re
import shutil
import subprocess
import threading
import time
from urllib.parse import urlsplit
import httpx
from process_control import record, stop
from serve import load
import os

ROOT=Path(__file__).resolve().parent

def command(provider):
    if provider=='ngrok':
        local=ROOT/'.tools/ngrok'
        binary=str(local) if local.exists() else shutil.which('ngrok')
        if not binary: raise RuntimeError('ngrok CLI not installed')
        return [binary,'http','http://127.0.0.1:8765','--inspect=false','--log=stdout','--log-format=json']
    if provider=='cloudflare':
        return [str(ROOT/'.tools/cloudflared'),'tunnel','--url','http://127.0.0.1:8765','--no-autoupdate']
    raise ValueError('Unknown tunnel provider')

def save_endpoint(provider,url,pid):
    parsed=urlsplit(url)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.path or parsed.query:
        raise ValueError('Expected a plain HTTPS origin')
    folder=ROOT/'connector'
    descriptor=json.loads((folder/'connection.example.json').read_text())
    descriptor.update(base_url=url,openapi_url=url+"/openapi.json")
    headers={"User-Agent":"Muse-Orca-Bridge/0.2"}
    if provider=="ngrok": headers["ngrok-skip-browser-warning"]="1"
    descriptor["default_headers"]=headers
    # Distribution examples and optional prompts remain unchanged.
    if not (folder/"muse-prompt.txt").exists():
        logging.getLogger(__name__).info("Optional connector/muse-prompt.txt absent; skipping prompt update")
    state=ROOT/"state"
    state.mkdir(mode=0o700,exist_ok=True)
    (state/"connector-endpoint.json").write_text(json.dumps(descriptor,ensure_ascii=False,indent=2)+"\n")
    (state/"tunnel.json").write_text(json.dumps({"url":url,"provider":provider,"pid":pid,"approved":True,"updated":time.time()},indent=2))

def verify(url,provider):
    load()
    headers={'User-Agent':'Muse-Orca-Bridge/0.2'}
    if provider=='ngrok': headers['ngrok-skip-browser-warning']='1'
    with httpx.Client(base_url=url,headers=headers,timeout=15,follow_redirects=False) as c:
        if c.get('/status').status_code!=401: raise RuntimeError('Unauthenticated request did not return 401')
        for path in ['/status','/agents','/openapi.json']:
            r=c.get(path,headers={'Authorization':'Bearer '+os.environ['BRIDGE_TOKEN']})
            if r.status_code!=200: raise RuntimeError('Authenticated check failed: '+path+' '+str(r.status_code))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--publish-approved',action='store_true',required=True)
    p.add_argument('--provider',choices=['ngrok','cloudflare'],default='ngrok')
    a=p.parse_args()
    proc=subprocess.Popen(command(a.provider),stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    record(a.provider,proc.pid)
    lines=queue.Queue()
    def read():
        for line in proc.stdout: lines.put(line)
        lines.put(None)
    threading.Thread(target=read,daemon=True).start()
    url=None
    codes=set()
    deadline=time.monotonic()+45
    try:
        while time.monotonic()<deadline:
            try: line=lines.get(timeout=1)
            except queue.Empty: continue
            if line is None: break
            codes.update(re.findall(r'ERR_NGROK_\d+',line))
            if a.provider=='ngrok':
                try: candidate=json.loads(line).get('url','')
                except ValueError: continue
                if candidate.startswith('https://'): url=candidate
            else:
                match=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',line)
                if match: url=match.group(0)
            if url: break
        if not url: raise RuntimeError('Tunnel did not start. '+','.join(sorted(codes)))
        verify(url,a.provider)
        # Only retire the exact previously recorded route after the new one is verified.
        print(stop('tunnel'),flush=True)
        record('tunnel',proc.pid)
        save_endpoint(a.provider,url,proc.pid)
        print('Verified HTTPS endpoint: '+url,flush=True)
        print('HTTP inspection disabled; local approval required for every task.',flush=True)
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        if proc.poll() is None:
            proc.terminate()
            proc.wait(timeout=10)
        raise

if __name__=='__main__': main()
