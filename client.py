"""Local operator client. Token is read from environment, never passed in argv."""
import argparse
import json
import os
import httpx
from serve import load

if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['status','agents','submit','get','result','logs','cancel','retry'])
    p.add_argument('id',nargs='?')
    p.add_argument('--prompt-file')
    p.add_argument('--project',default='sandbox')
    a=p.parse_args()
    load()
    with httpx.Client(base_url='http://127.0.0.1:8765',headers={'Authorization':'Bearer '+os.environ['BRIDGE_TOKEN']},timeout=30) as c:
        if a.action in ('status','agents'): r=c.get('/'+a.action)
        elif a.action=='submit':
            if not a.prompt_file: p.error('--prompt-file required')
            from pathlib import Path
            r=c.post('/tasks',json={'prompt':Path(a.prompt_file).read_text(),'project':a.project})
        else:
            if not a.id: p.error('task ID required')
            path='/tasks/'+a.id
            if a.action in ('result','logs'): path+='/'+a.action
            r=c.post(path+'/'+a.action) if a.action in ('cancel','retry') else c.get(path)
        print(json.dumps(r.json(),ensure_ascii=False,indent=2))
        r.raise_for_status()
