"""Fixed model choices plus read-only subscription quota checks; no credit purchases."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import time

# Disjoint globs deny every .env* basename except exactly .env.example.
ENV_DENY_GLOBS=(
 '**/.env', '**/.env[!.]*',
 '**/.env.', '**/.env.[!e]*',
 '**/.env.e', '**/.env.e[!x]*',
 '**/.env.ex', '**/.env.ex[!a]*',
 '**/.env.exa', '**/.env.exa[!m]*',
 '**/.env.exam', '**/.env.exam[!p]*',
 '**/.env.examp', '**/.env.examp[!l]*',
 '**/.env.exampl', '**/.env.exampl[!e]*',
 '**/.env.example?*',
)

PROFILES={
 'sol_low':{'agent':'codex','model':'gpt-6.1-sol','effort':'low','purpose':'routine implementation'},
 'sol_high':{'agent':'codex','model':'gpt-6.1-sol','effort':'high','purpose':'specifications and design'},
 'sol_max':{'agent':'codex','model':'gpt-6.1-sol','effort':'max','purpose':'difficult repeated failures'},
 'hydrogen_high':{'agent':'antigravity','model':'gemini-4-hydrogen-high','effort':'high','purpose':'implementation from a clear plan'},
 'argon_high':{'agent':'antigravity','model':'gemini-4-argon-high','effort':'high','purpose':'difficult repeated failures or alternate analysis'},
}
_cache={}
_lock=threading.Lock()

def clean_env():
 return {k:os.environ[k] for k in ('HOME','PATH','USER','LANG','TMPDIR') if k in os.environ}

def codex_raw():
 p=subprocess.Popen([shutil.which('codex') or 'codex','app-server','-c','features.apps=false','-c','mcp_servers={}'],
     stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,env=clean_env())
 q=queue.Queue()
 def read():
  for line in p.stdout:
   try:q.put(json.loads(line))
   except ValueError:pass
 threading.Thread(target=read,daemon=True).start()
 deadline=time.monotonic()+12
 def send(r):p.stdin.write(json.dumps(r)+'\n');p.stdin.flush()
 def receive(id):
  while time.monotonic()<deadline:
   r=q.get(timeout=max(.1,deadline-time.monotonic()))
   if r.get('id')==id:
    if 'error' in r:raise RuntimeError('quota_rpc_failed')
    return r['result']
  raise TimeoutError('quota_timeout')
 try:
  send({'method':'initialize','id':1,'params':{'clientInfo':{'name':'muse_orca_bridge','version':'0.5.0'}}});receive(1)
  send({'method':'initialized','params':{}})
  send({'method':'account/rateLimits/read','id':2})
  return receive(2)
 finally:
  p.terminate()
  try:p.wait(timeout=3)
  except subprocess.TimeoutExpired:p.kill();p.wait()

def summarize_codex(r):
 windows=[]
 bucket=r.get('rateLimits') or {}
 for key in ['primary','secondary','individualLimit']:
  v=bucket.get(key)
  if v and v.get('usedPercent') is not None:
   windows.append({'name':key,'remaining_percent':max(0,100-v['usedPercent']),
                   'resets_at':v.get('resetsAt'),'window_minutes':v.get('windowDurationMins')})
 exhausted=(r.get('ordinaryUsageAllowed') is False or bucket.get('spendControlReached') is True or
            bool(bucket.get('rateLimitReachedType')) or any(w['remaining_percent']<=0 for w in windows))
 status='exhausted' if exhausted else ('available' if windows or r.get('ordinaryUsageAllowed') is True else 'unknown')
 return {'status':status,'windows':windows,'scope':'Codex subscription bucket; model-specific quota may not be reported',
         'extra_credits_not_used':True}

def agy_raw():
 p=subprocess.run([shutil.which('agy') or 'agy','-p','/usage','--output-format','json'],capture_output=True,text=True,timeout=15,env=clean_env())
 if p.returncode:raise RuntimeError('quota_cli_failed')
 return json.loads(p.stdout)['command']['data']['groups']

def summarize_agy(groups,model):
 match='Gemini 4 Argon' if 'argon' in model else 'Gemini Models'
 group=next((g for g in groups if g['name']==match),None)
 if group is None:return {'status':'unknown','windows':[]}
 windows=[{'name':b.get('id',b.get('name')),'remaining_percent':100*b['remaining_fraction'],'resets_at':b.get('reset_time')}
          for b in group['buckets'] if not b.get('disabled') and b.get('remaining_fraction') is not None]
 return {'status':'exhausted' if any(w['remaining_percent']<=0 for w in windows) else ('available' if windows else 'unknown'),
         'windows':windows,'scope':match,'extra_credits_not_used':True}

def availability(profile):
 config=PROFILES[profile]
 provider=config['agent']
 now=time.monotonic()
 with _lock:
  saved=_cache.get(provider)
  if saved is None or now-saved[0]>60:
   try:
    raw=codex_raw() if provider=='codex' else agy_raw()
    saved=(now,raw,time.time(),None)
   except Exception:
    saved=(now,None,time.time(),'quota_check_failed')
   _cache[provider]=saved
 if saved[1] is None:return {'status':'unknown','windows':[],'checked_at':saved[2],'error':saved[3]}
 r=summarize_codex(saved[1]) if provider=='codex' else summarize_agy(saved[1],config['model'])
 r['checked_at']=saved[2]
 if provider=='antigravity':
  # A separate end-to-end isolation/cancellation audit must complete before enabling this adapter.
  r['execution_ready']=False
  r['execution_blocker']='antigravity_execution_safety_not_yet_verified'
 return r

def selection(profile,reason=''):
 return {'profile':profile,**PROFILES[profile],'reason':reason or 'selected execution profile'}

def antigravity_command(project,profile):
 config=PROFILES[profile]
 settings={'toolPermission':'proceed-in-sandbox','useG1Credits':False,
           'permissions':{'allow':[],'ask':[],'deny':['read_url(*)','execute_url(*)','mcp(*)',
             'write_file(.git/)','read_file(.env)','read_file(**/*.pem)']+['read_file('+pattern+')' for pattern in ENV_DENY_GLOBS]}}
 return [shutil.which('agy') or 'agy','--model',config['model'],'--effort',config['effort'],
         '--mode','accept-edits','--sandbox','--disable-slash-commands','--settings',json.dumps(settings),
         '--input-format','stream-json','--output-format','stream-json']
