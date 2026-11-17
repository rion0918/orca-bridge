"""Local-only launcher; tokens live in a private environment file."""
import os
from pathlib import Path
import secrets
import uvicorn
ROOT=Path(__file__).resolve().parent

def load():
    p=ROOT/'.env'
    if not p.exists():
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:
            f.write('BRIDGE_TOKEN='+secrets.token_urlsafe(32)+'\nBRIDGE_READ_TOKEN='+secrets.token_urlsafe(32)+'\n')
    if p.stat().st_mode & 0o077: raise RuntimeError('.env must be mode 600')
    for line in p.read_text().splitlines():
        if line and not line.startswith('#'):
            key,value=line.split('=',1)
            os.environ.setdefault(key,value)

if __name__=='__main__':
    load()
    from process_control import record
    record('bridge',os.getpid())
    uvicorn.run('bridge:app',host='127.0.0.1',port=8765,access_log=False,log_level='warning')
