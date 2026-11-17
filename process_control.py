"""Stop only a recorded local process whose PID/start time/command still match."""
import json
import os
from pathlib import Path
import signal
import subprocess
ROOT=Path(__file__).resolve().parent

def identity(pid):
    p=subprocess.run(['ps','-p',str(pid),'-o','lstart=','-o','command='],capture_output=True,text=True)
    return p.stdout.strip() if p.returncode==0 else None

def record(name,pid):
    path=ROOT/'state'/(name+'-process.json')
    path.parent.mkdir(mode=0o700,exist_ok=True)
    value={'pid':pid,'identity':identity(pid)}
    if not value['identity']: raise RuntimeError('Process not found')
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f: json.dump(value,f)

def stop(name):
    path=ROOT/'state'/(name+'-process.json')
    if not path.exists(): return name+': no recorded process'
    value=json.loads(path.read_text())
    actual=identity(value['pid'])
    if actual is None: return name+': already stopped'
    if actual!=value['identity']: raise RuntimeError(name+': process identity changed; refusing to stop')
    os.kill(value['pid'],signal.SIGTERM)
    return name+': stop requested'

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--tunnel-only',action='store_true')
    args=parser.parse_args()
    print(stop('tunnel'))
    if not args.tunnel_only: print(stop('bridge'))
