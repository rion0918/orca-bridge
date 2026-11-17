"""Authenticated local bridge. Execution approval is a local CLI operation only."""
import argparse
import contextlib
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
import time
import uuid
import threading
from typing import Literal
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import JSONResponse

ROOT=Path(__file__).resolve().parent
DATA=ROOT/'state'
PROJECTS={'sandbox':ROOT/'sandbox'}
ORCA=os.environ.get('ORCA_CLI_COMMAND') or ('orca-dev' if os.environ.get('ORCA_DEV_REPO_ROOT') else shutil.which('orca'))
ACTIVE=('launching','launch_unknown','running','cancel_requested')
def autorun(): return os.environ.get('BRIDGE_AUTORUN_SANDBOX')=='1'

@contextlib.asynccontextmanager
async def lifespan(app):
    done=threading.Event()
    thread=None
    if autorun():
        def work():
            while not done.is_set():
                try: dispatch_once()
                except Exception: audit('dispatcher_error')
                done.wait(.5)
        thread=threading.Thread(target=work,daemon=True)
        thread.start()
    yield
    done.set()
    if thread: thread.join(timeout=25)

app=FastAPI(title='Muse Orca Bridge',version='0.5.3',lifespan=lifespan,docs_url=None,redoc_url=None,openapi_url=None)

class BodyLimit:
    def __init__(self,app): self.app=app
    async def __call__(self,scope,receive,send):
        if scope['type']!='http': return await self.app(scope,receive,send)
        chunks=[]
        size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect': return
            size+=len(message.get('body',b''))
            if size>32768:
                audit('http_body_too_large')
                return await JSONResponse({'detail':'Request body too large'},status_code=413)(scope,receive,send)
            chunks.append(message)
            if not message.get('more_body',False): break
        async def replay():
            if chunks: return chunks.pop(0)
            return await receive()
        async def audited_send(message):
            if message['type']=='http.response.start':
                route=scope.get('route')
                audit('http_'+getattr(route,'name','unknown')+'_'+str(message['status']))
            await send(message)
        await self.app(scope,replay,audited_send)

app.add_middleware(BodyLimit)

class CLIError(Exception):
    def __init__(self,code,unknown=False):
        self.code=code
        self.unknown=unknown
        super().__init__(code)

def redact(s):
    for key,value in os.environ.items():
        if value and len(value)>7 and any(k in key.upper() for k in ('TOKEN','SECRET','API_KEY','PASSWORD')):
            s=s.replace(value,'[REDACTED]')
    s=re.sub(r'(?i)(Bearer\s+)\S+',r'\1[REDACTED]',s)
    s=re.sub(r'\bsk-[\w-]+','[REDACTED]',s)
    s=re.sub(r'(?i)((?:api[_-]?key|password|secret|token)["\s]*[:=]["\s]*)[^\s",}]+',r'\1[REDACTED]',s)
    return s

def connection():
    DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
    c=sqlite3.connect(DATA/'bridge.sqlite3',timeout=10)
    c.row_factory=sqlite3.Row
    c.execute('''create table if not exists tasks (
        id text primary key, project text, prompt text, state text, created real,
        updated real, terminal text, error text, result text, exit_code integer,
        idem text unique, fingerprint text, timeout integer, execution text not null default '{}')''')
    if 'execution' not in [r[1] for r in c.execute('pragma table_info(tasks)')]:
        c.execute("alter table tasks add column execution text not null default '{}'")
        c.commit()
    os.chmod(DATA/'bridge.sqlite3',0o600)
    return c

@contextlib.contextmanager
def transaction():
    c=connection()
    try:
        with c: yield c
    finally: c.close()

def audit(action,id=None):
    DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd=os.open(DATA/'audit.jsonl',os.O_WRONLY|os.O_CREAT|os.O_APPEND,0o600)
    with os.fdopen(fd,'a') as f:
        f.write(json.dumps({'time':time.time(),'action':action,'task_id':id})+'\n')

def task(id):
    with transaction() as c: row=c.execute('select * from tasks where id=?',(id,)).fetchone()
    if row is None: raise HTTPException(404,'Task not found')
    return dict(row)

def update(id,**fields):
    fields['updated']=time.time()
    with transaction() as c:
        c.execute('update tasks set '+','.join(k+'=?' for k in fields)+' where id=?',(*fields.values(),id))

def project_path(name):
    p=PROJECTS.get(name)
    if p is None:
        import projects
        return projects.path(name)
    if p is None or p.is_symlink() or not p.is_dir() or p.absolute()!=p.resolve():
        raise HTTPException(422,'Project is not an allowed local directory')
    return p.resolve()

def orca(*args,timeout=20):
    if not ORCA: raise CLIError('orca_not_installed')
    env={k:v for k,v in os.environ.items() if k not in ('ORCA_ENVIRONMENT','ORCA_PAIRING_CODE') and not k.startswith('BRIDGE_')}
    try:
        p=subprocess.run([ORCA,*args,'--environment','local','--json'],capture_output=True,text=True,timeout=timeout,env=env)
        r=json.loads(p.stdout)
    except subprocess.TimeoutExpired: raise CLIError('cli_timeout',unknown=True)
    except (OSError,ValueError): raise CLIError('cli_unavailable',unknown=True)
    if not r.get('ok',False) or p.returncode:
        code=r.get('error',{}).get('code','cli_failed') if isinstance(r.get('error'),dict) else 'cli_failed'
        raise CLIError(code,unknown=True)
    return r['result']

def permission(scope):
    def check(authorization:str=Header(default='')):
        token=authorization.removeprefix('Bearer ') if authorization.startswith('Bearer ') else ''
        full=os.environ.get('BRIDGE_TOKEN','')
        read=os.environ.get('BRIDGE_READ_TOKEN','')
        if full and hmac.compare_digest(token,full): return
        if read and hmac.compare_digest(token,read):
            if scope=='read': return
            raise HTTPException(403,'Read-only token')
        raise HTTPException(401,'Unauthorized',headers={'WWW-Authenticate':'Bearer'})
    return check

class Task(BaseModel):
    model_config=ConfigDict(extra='forbid')
    prompt:str=Field(min_length=1,max_length=8000)
    project:str='sandbox'
    timeout_seconds:int=Field(default=300,ge=10,le=900)
    operation:Literal['development','deployment','spending']='development'
    execution_profile:Literal['sol_low','sol_high','sol_max','hydrogen_high','argon_high']='sol_low'
    execution_reason:str=Field(default='',max_length=500)

@app.get('/status',dependencies=[Depends(permission('read'))])
def status():
    for attempt in range(2):
        try:
            r=orca('status')
            return {'running':r['app']['running'],'runtime_state':r['runtime']['state']}
        except CLIError as e:
            if attempt: raise HTTPException(503,e.code)
    raise HTTPException(503,'Unavailable')

def codex_authentication():
    executable=shutil.which('codex')
    if not executable: return False,'cli_missing'
    env={k:os.environ[k] for k in ('HOME','PATH','USER','LANG','TMPDIR') if k in os.environ}
    try:
        checked=subprocess.run([executable,'login','status'],capture_output=True,text=True,timeout=5,env=env)
        logged_in=checked.returncode==0
        return logged_in,'logged_in' if logged_in else 'not_logged_in'
    except (OSError,subprocess.TimeoutExpired):
        return False,'unknown'

def available_project_ids():
    import projects
    return list(PROJECTS)+[p['id'] for p in projects.listing() if p['state'] in ('available','ready')]

def checked_execution(profile,reason=''):
    import model_policy
    status=model_policy.availability(profile)
    if status['status']!='available':
        raise HTTPException(409,{'code':'model_quota_'+status['status'],'profile':profile,'availability':status})
    if status.get('execution_ready') is False:
        raise HTTPException(409,{'code':'model_execution_not_ready','profile':profile,'availability':status})
    return model_policy.selection(profile,reason)

@app.get('/models',dependencies=[Depends(permission('read'))])
def models():
    import model_policy
    return {'profiles':[{'profile':key,**value,'availability':model_policy.availability(key)} for key,value in model_policy.PROFILES.items()],
            'default':'sol_low','extra_credit_fallback':False,'quota_cache_seconds':60}

@app.get('/agents',dependencies=[Depends(permission('read'))])
def agents():
    verified,state=codex_authentication()
    return {'agents':[{'id':'codex','cli_installed':bool(shutil.which('codex')),'enabled':True},
                      {'id':'antigravity','cli_installed':bool(shutil.which('agy')),'enabled':False,
                       'disabled_reason':'quota exhausted at verification; execution safety audit pending'}],
            'authentication_verified':verified,'authentication_status':state,
            'authentication_check':'local_cli_login_status',
            'authentication_note':'Checks local sign-in only; does not send a model request.',
            'model_catalog':'GET /models','projects':available_project_ids(),'project_catalog':'GET /projects','approval':'automatic for isolated development; critical actions disabled pending human review' if autorun() else 'local CLI for every execution'}

@app.get('/openapi.json',dependencies=[Depends(permission('read'))],include_in_schema=False)
def schema():
    spec=app.openapi()
    spec.setdefault('components',{}).setdefault('securitySchemes',{})['BearerAuth']={'type':'http','scheme':'bearer'}
    spec['security']=[{'BearerAuth':[]}]
    return spec

@app.post('/tasks',dependencies=[Depends(permission('write'))])
def create(t:Task,idempotency_key:str|None=Header(default=None,max_length=128)):
    project_path(t.project)
    if t.operation!='development':
        raise HTTPException(409,{'code':'human_approval_required','operation':t.operation,'message':'Prepare a proposal for the user. Deployment and spending are not supported by the isolated development runner; a separately reviewed execution path is required.'})
    fingerprint=hashlib.sha256(t.model_dump_json().encode()).hexdigest()
    if idempotency_key:
        with transaction() as c:
            existing=c.execute('select * from tasks where idem=?',(idempotency_key,)).fetchone()
        if existing:
            if existing['fingerprint']!=fingerprint: raise HTTPException(409,'Idempotency key reused with different request')
            return public(dict(existing))
    execution=checked_execution(t.execution_profile,t.execution_reason)
    if not t.prompt.strip(): raise HTTPException(422,'Empty prompt')
    fingerprint=hashlib.sha256(t.model_dump_json().encode()).hexdigest()
    id=str(uuid.uuid4())
    with transaction() as c:
        c.execute('BEGIN IMMEDIATE')
        if idempotency_key:
            row=c.execute('select * from tasks where idem=?',(idempotency_key,)).fetchone()
            if row:
                if row['fingerprint']!=fingerprint: raise HTTPException(409,'Idempotency key reused with different request')
                return public(dict(row))
        if c.execute('select count(*) from tasks where state in ("pending_approval","queued")').fetchone()[0]>=100:
            raise HTTPException(429,'Approval queue full')
        now=time.time()
        c.execute('insert into tasks(id,project,prompt,state,created,updated,idem,fingerprint,timeout,execution) values(?,?,?,?,?,?,?,?,?,?)',
                  (id,t.project,t.prompt,'queued' if autorun() else 'pending_approval',now,now,idempotency_key,fingerprint,t.timeout_seconds,json.dumps(execution)))
    audit('submitted',id)
    return public(task(id))

def public(t):
    return {**{k:t[k] for k in ('id','project','state','created','updated','error','exit_code','timeout')},'execution':json.loads(t.get('execution') or '{}')}

@app.get('/tasks/{id}',dependencies=[Depends(permission('read'))])
def get(id:str): return public(task(id))

@app.get('/tasks/{id}/result',dependencies=[Depends(permission('read'))])
def result(id:str):
    t=task(id)
    return {**public(t),'result':redact(t['result'] or '')}

@app.get('/tasks/{id}/logs',dependencies=[Depends(permission('read'))])
def logs(id:str):
    task(id)
    p=DATA/(id+'.jsonl')
    return {'id':id,'text':redact(p.read_text()[-65536:]) if p.exists() else '', 'max_characters':65536}

@app.post('/tasks/{id}/cancel',dependencies=[Depends(permission('write'))])
def cancel(id:str):
    with transaction() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('select state from tasks where id=?',(id,)).fetchone()
        if row is None: raise HTTPException(404,'Task not found')
        state=row['state']
        if state in ('pending_approval','queued'): state='cancelled'
        elif state in ACTIVE: state='cancel_requested'
        c.execute('update tasks set state=?,updated=? where id=?',(state,time.time(),id))
    audit('cancel_requested',id)
    return public(task(id))

@app.post('/tasks/{id}/retry',dependencies=[Depends(permission('write'))])
def retry(id:str):
    t=task(id)
    if t['state'] not in ('failed','timed_out','cancelled'):
        raise HTTPException(409,'Retry requires a confirmed settled attempt')
    return create(Task(prompt=t['prompt'],project=t['project'],timeout_seconds=t['timeout'],execution_profile=json.loads(t['execution'] or '{}').get('profile','sol_low')),None)

def approve(id,automatic=False):
    t=task(id)
    selected=json.loads(t['execution'] or '{}')
    checked_execution(selected.get('profile','sol_low'),selected.get('reason',''))
    if automatic and not autorun(): raise HTTPException(403,'Automatic execution is disabled')
    with transaction() as c:
        if c.execute('select count(*) from tasks where state in (?,?,?,?)',ACTIVE).fetchone()[0]:
            raise HTTPException(409,'Concurrency limit is one; reconcile unknown executions first')
    if t['project'] not in PROJECTS:
        import projects
        p=projects.prepare(t['project'])
    else: p=project_path(t['project'])
    # Config/plugins supplied by a generated project must never broaden Codex permissions.
    if (p/'.codex').exists(): raise HTTPException(409,'Remove/review project .codex configuration first')
    with transaction() as c:
        c.execute('BEGIN IMMEDIATE')
        row=c.execute('select state from tasks where id=?',(id,)).fetchone()
        if row['state']!=('queued' if automatic else 'pending_approval'): raise HTTPException(409,'Task is not eligible for this execution mode')
        if c.execute('select count(*) from tasks where state in (?,?,?,?)',ACTIVE).fetchone()[0]:
            raise HTTPException(409,'Concurrency limit is one; reconcile unknown executions first')
        c.execute('update tasks set state="launching",error=null,updated=? where id=?',(time.time(),id))
    audit('authorized_by_local_development_policy' if automatic else 'approved_locally',id)
    command=shlex.join([sys.executable,str(ROOT/'runner.py'),id])
    try:
        r=orca('terminal','create','--worktree','path:'+str(p),'--title','Bridge '+id[:8],'--command',command)
        handle=r.get('terminalHandle') or r.get('handle') or (r.get('terminal') or {}).get('handle')
        update(id,terminal=handle)
        audit('launch_receipt',id)
    except CLIError as e:
        with transaction() as c:
            c.execute('update tasks set state="launch_unknown",error=?,updated=? where id=? and state="launching"',(e.code,time.time(),id))
        audit('launch_unknown',id)
    return public(task(id))


@app.get('/policy',dependencies=[Depends(permission('read'))])
def policy():
    return {'automatic_development':autorun(),'model_catalog':'GET /models','projects':available_project_ids(),'project_catalog':'GET /projects','new_projects':'POST /projects','concurrency':1,
            'critical_operations':{'deployment':'human approval and separate executor required','spending':'human approval and separate executor required'},
            'generated_code_network':False,'unknown_launch':'blocks subsequent executions'}

def dispatch_once():
    if not autorun(): return
    with transaction() as c:
        row=c.execute('select id from tasks where state="queued" order by created,id limit 1').fetchone()
    if row is None: return
    try: approve(row['id'],automatic=True)
    except HTTPException as e:
        if isinstance(e.detail,dict) and str(e.detail.get('code','')).startswith('model_quota_'):
            with transaction() as c:
                c.execute("update tasks set error=?,updated=? where id=? and state='queued'",(e.detail['code'],time.time(),row['id']))
            return
        if e.status_code==409 and ('Concurrency limit' in str(e.detail) or 'eligible' in str(e.detail)): return
        with transaction() as c:
            c.execute("update tasks set state='pending_approval',error='project_review_required',updated=? where id=? and state='queued'",(time.time(),row['id']))

class NewProject(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name:str=Field(pattern=r'^[a-z][a-z0-9-]{0,47}$')

@app.get('/projects',dependencies=[Depends(permission('read'))])
def list_projects():
    import projects
    try: catalog=projects.refresh()
    except CLIError as e: raise HTTPException(503,e.code)
    defaults=[]
    for k in PROJECTS:
        try:
            project_path(k)
            state="ready"
        except HTTPException:
            state="blocked"
        defaults.append({"id":k,"name":k,"kind":"sandbox","state":state})
    return {"projects":defaults+catalog}

@app.post('/projects',dependencies=[Depends(permission('write'))])
def new_project(request:NewProject):
    import projects
    return projects.create(request.name)


class CommitRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    paths:list[str]=Field(min_length=1)
    message:str=Field(min_length=1)

class EnvironmentExampleRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    content:str

class CleanupRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    paths:list[str]=Field(min_length=1)
    recursive:bool=False
    dry_run:bool=False
    empty_tree:bool=False

class EnvironmentExampleResponse(BaseModel):
    project:str
    content:str

class RenameExampleResponse(BaseModel):
    project:str
    renamed_to:Literal['.env.example']

class CommitResponse(BaseModel):
    project:str
    commit:str
    files:int
    published:Literal[False]

class CleanupResponse(BaseModel):
    project:str
    paths:list[str]
    dry_run:bool

class PythonEnvironmentResponse(BaseModel):
    project:str
    python:str
    offline:Literal[True]
    tests_executed:Literal[False]

@contextlib.contextmanager
def project_io():
    try: yield
    except FileNotFoundError: raise HTTPException(404,'Project file not found')
    except (OSError,UnicodeError): raise HTTPException(409,'Project filesystem operation failed')

def project_file(root,name,template=False):
    relative=Path(name)
    if (not name or '\x00' in name or relative.is_absolute() or
            relative==Path('.') or '..' in relative.parts):
        raise HTTPException(422,'Path must be inside the project')
    for part in relative.parts:
        if (part in ('.git','.aws','.codex') or part.endswith('.pem') or
                (part.startswith('.env') and part!='.env.example')):
            raise HTTPException(422,'Protected project path')
    target=root
    for part in relative.parts:
        target=target/part
        if target.is_symlink(): raise HTTPException(422,'Symlink paths are not allowed')
    if not target.resolve().is_relative_to(root):
        raise HTTPException(422,'Path must be inside the project')
    return target

def example_content(content):
    pattern=r'''(?ms)^([ \t]*(?:export[ \t]+)?(?:BRIDGE_TOKEN|BRIDGE_READ_TOKEN)[ \t]*=)[ \t]*(?:"(?:\\.|[^"\\])*(?:"|\Z)|'(?:\\.|[^'\\])*(?:'|\Z)|[^\r\n]*)[^\r\n]*'''
    return re.sub(pattern,r'\1<redacted>',content)

@app.post('/projects/{project}/environment-example',dependencies=[Depends(permission('write'))],response_model=RenameExampleResponse)
def rename_environment_example(project:str):
    root=project_path(project)
    with project_io():
        source=project_file(root,'ENVIRONMENT.example')
        target=project_file(root,'.env.example',template=True)
        if not source.is_file(): raise HTTPException(404,'Project file not found')
        if target.exists(): raise HTTPException(409,'Environment example already exists')
        source.rename(target)
    return {'project':project,'renamed_to':'.env.example'}

@app.get('/projects/{project}/environment-example',dependencies=[Depends(permission('read'))],response_model=EnvironmentExampleResponse)
def get_environment_example(project:str):
    root=project_path(project)
    with project_io():
        content=project_file(root,'.env.example',template=True).read_bytes().decode('utf-8')
    return {'project':project,'content':example_content(content)}

@app.put('/projects/{project}/environment-example',dependencies=[Depends(permission('write'))],response_model=EnvironmentExampleResponse)
def put_environment_example(project:str,request:EnvironmentExampleRequest):
    root=project_path(project)
    with project_io():
        project_file(root,'.env.example',template=True).write_bytes(request.content.encode('utf-8'))
    return {'project':project,'content':example_content(request.content)}

def local_git(root,*args):
    # Hooks and signing can launch arbitrary programs; this endpoint is local-only.
    command=['git','--literal-pathspecs','-c','core.hooksPath=/dev/null',
             '-c','commit.gpgsign=false','-c','core.fsmonitor=false','-C',str(root),*args]
    env={k:os.environ[k] for k in ('HOME','PATH','USER','LANG','TMPDIR') if k in os.environ}
    try:
        return subprocess.run(command,capture_output=True,text=True,timeout=30,env=env)
    except (OSError,subprocess.TimeoutExpired):
        raise HTTPException(409,'Local Git operation unavailable')

@app.post('/projects/{project}/commit',dependencies=[Depends(permission('write'))],response_model=CommitResponse)
def commit_project(project:str,request:CommitRequest):
    root=project_path(project)
    if not request.message.strip(): raise HTTPException(422,'Empty commit message')
    paths=[]
    with project_io():
        for name in request.paths:
            target=project_file(root,name)
            if not target.is_file(): raise HTTPException(404,'Existing project file required')
            relative=target.relative_to(root).as_posix()
            if relative not in paths: paths.append(relative)
    repository=local_git(root,'rev-parse','--show-toplevel')
    if repository.returncode or repository.stdout.strip()!=str(root):
        raise HTTPException(409,'Project must be a Git working tree root')
    for key in ('user.name','user.email'):
        identity=local_git(root,'config','--get',key)
        if identity.returncode or not identity.stdout.strip():
            raise HTTPException(409,'Configure Git user.name and user.email locally before committing')
    staged=local_git(root,'diff','--cached','--quiet','--exit-code')
    if staged.returncode:
        raise HTTPException(409,'Index must be empty and readable before committing')
    if local_git(root,'add','--',*paths).returncode:
        raise HTTPException(409,'Cannot stage requested files')
    if local_git(root,'commit','--only','-m',request.message,'--',*paths).returncode:
        raise HTTPException(409,'Local commit failed; review the index before retrying')
    head=local_git(root,'rev-parse','--verify','HEAD')
    if head.returncode or not re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}',head.stdout.strip()):
        raise HTTPException(409,'Cannot verify local commit')
    return {'project':project,'commit':head.stdout.strip(),'files':len(paths),'published':False}

GENERATED_CACHES={'__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'}

def cleanup_tree(root,target,empty_only):
    directories=[target]
    for directory in directories:
        for child in directory.iterdir():
            project_file(root,child.relative_to(root).as_posix())
            if child.is_dir(): directories.append(child)
            elif empty_only: raise HTTPException(409,'Empty tree contains a file')
            elif not child.is_file(): raise HTTPException(422,'Unsupported cleanup target')
    return directories

@app.post('/projects/{project}/cleanup',dependencies=[Depends(permission('write'))],response_model=CleanupResponse)
def cleanup_project(project:str,request:CleanupRequest):
    root=project_path(project)
    targets=[]
    paths=[]
    # Preflight every path, including descendants, before deleting anything.
    with project_io():
        for name in request.paths:
            target=project_file(root,name)
            if not target.exists(): raise HTTPException(404,'Project file not found')
            relative=target.relative_to(root).as_posix()
            if relative in paths: continue
            directories=[]
            if target.is_dir():
                if request.recursive and target.name not in GENERATED_CACHES:
                    raise HTTPException(422,'Recursive cleanup is limited to generated caches')
                if request.empty_tree:
                    directories=cleanup_tree(root,target,empty_only=True)
                elif request.recursive:
                    cleanup_tree(root,target,empty_only=False)
                elif any(target.iterdir()):
                    raise HTTPException(409,'Directory is not empty')
            elif not target.is_file():
                raise HTTPException(422,'Unsupported cleanup target')
            elif request.empty_tree:
                raise HTTPException(409,'Empty tree requires a directory')
            targets.append((target,directories))
            paths.append(relative)
        if not request.dry_run:
            # Descendants first also handles overlapping requested paths.
            for target,directories in sorted(targets,key=lambda item:len(item[0].parts),reverse=True):
                if not target.exists(): continue
                if directories:
                    for directory in reversed(directories):
                        if directory.exists(): directory.rmdir()
                elif target.is_dir() and request.recursive: shutil.rmtree(target)
                elif target.is_dir(): target.rmdir()
                else: target.unlink()
    return {'project':project,'paths':paths,'dry_run':request.dry_run}

@app.post('/projects/{project}/python-environment',dependencies=[Depends(permission('write'))],response_model=PythonEnvironmentResponse)
def python_environment(project:str):
    root=project_path(project)
    # Share the execution-state lock with task approval while preparing the environment.
    with transaction() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute('select count(*) from tasks where state in (?,?,?,?)',ACTIVE).fetchone()[0]:
            raise HTTPException(409,'Wait for active tasks to finish before preparing Python')
        with project_io():
            environment=project_file(root,'.venv')
            project_file(root,'.venv/bin')
            project_file(root,'.venv/pyvenv.cfg')
            python=environment/'bin/python'
            if environment.exists() and not environment.is_dir():
                raise HTTPException(409,'Python environment path is not a directory')
            if not (python.is_file() and (environment/'pyvenv.cfg').is_file()):
                # venv uses the interpreter's bundled ensurepip wheels, with no downloads.
                env={k:os.environ[k] for k in ('PATH','LANG','TMPDIR') if k in os.environ}
                env.update(PIP_NO_INDEX='1',PIP_DISABLE_PIP_VERSION_CHECK='1')
                try:
                    prepared=subprocess.run([sys.executable,'-m','venv',str(environment)],
                                            cwd=root,capture_output=True,text=True,timeout=60,env=env)
                except (OSError,subprocess.TimeoutExpired):
                    raise HTTPException(409,'Offline Python environment preparation failed')
                if prepared.returncode or not (python.is_file() and (environment/'pyvenv.cfg').is_file()):
                    raise HTTPException(409,'Offline Python environment preparation failed')
    return {'project':project,'python':str(python),'offline':True,'tests_executed':False}

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['list','show','approve'])
    parser.add_argument('id',nargs='?')
    a=parser.parse_args()
    if a.action=='list':
        with transaction() as c:
            print(json.dumps([public(dict(r)) for r in c.execute('select * from tasks order by created desc limit 100')],indent=2))
    elif a.action=='show': print(json.dumps(task(a.id),ensure_ascii=False,indent=2))
    else: print(json.dumps(approve(a.id),indent=2))
