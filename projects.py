"""Orca project IDs, isolated existing checkouts, and bounded new-project creation."""
from pathlib import Path
import hashlib
import sqlite3
import subprocess
import bridge

NEW_ROOT=Path.home()/'MuseProjects'

def initialize():
    with bridge.transaction() as c:
        c.execute('''create table if not exists project_catalog(
            id text primary key,name text,source text,kind text,repo_id text,
            workspace text,state text)''')

def overlaps_bridge(path):
    return path.is_relative_to(bridge.ROOT) or bridge.ROOT.is_relative_to(path)

def safe(path):
    p=Path(path).absolute()
    return (p.is_dir() and p.resolve()==p and p!=Path.home() and
            not overlaps_bridge(p) and
            not any(part.startswith('.') for part in p.parts))

def record(id):
    initialize()
    with bridge.transaction() as c: r=c.execute('select * from project_catalog where id=?',(id,)).fetchone()
    if r is None: raise bridge.HTTPException(422,'Select a project ID from GET /projects')
    return dict(r)

def public(r):
    path=Path(r['workspace'] or r['source'])
    state='blocked' if overlaps_bridge(path) else r['state']
    return {'id':r['id'],'name':r['name'],'kind':r['kind'],'state':state,
            'execution':'dedicated Orca worktree' if r['kind']=='git' else 'project folder',
            'has_workspace':bool(r['workspace']),'source_path':r['source'],'workspace_path':r['workspace']}

def refresh():
    initialize()
    repos=bridge.orca('repo','list')['repos']
    ids=[]
    with bridge.transaction() as c:
        for repo in repos:
            source=repo['path']
            if repo.get('hiddenAt') and not Path(source).is_dir():
                continue
            id='orca-'+repo['id']; ids.append(id)
            state='available' if safe(source) else 'blocked'
            # The existing sandbox has its original stable alias.
            if Path(source)==bridge.ROOT/'sandbox': continue
            if c.execute("select 1 from project_catalog where source=? and kind='managed'",(source,)).fetchone(): continue
            c.execute('''insert into project_catalog values(?,?,?,?,?,?,?)
                on conflict(id) do update set name=excluded.name,
                state=case when project_catalog.source!=excluded.source or excluded.state='blocked'
                then 'blocked' else project_catalog.state end''',
                (id,repo.get('displayName',repo['id']),source,repo.get('kind','folder'),repo['id'],None,state))
        rows=c.execute("select * from project_catalog where id like 'orca-%'").fetchall()
        for row in rows:
            if row['id'] not in ids: c.execute("update project_catalog set state='blocked' where id=?",(row['id'],))
    return listing()

def listing():
    initialize()
    with bridge.transaction() as c: rows=c.execute('select * from project_catalog order by name,id').fetchall()
    return [public(dict(r)) for r in rows]

def path(id):
    r=record(id)
    if r['state'] not in ('available','ready'): raise bridge.HTTPException(422,'Project is blocked or needs local reconciliation')
    p=Path(r['workspace'] or r['source'])
    if not safe(p): raise bridge.HTTPException(422,'Unsafe or unavailable project path')
    return p

def git_clean(p):
    try:
        r=subprocess.run(['git','-c','core.fsmonitor=false','-C',str(p),'status','--porcelain','--untracked-files=normal'],capture_output=True,timeout=10)
    except (OSError,subprocess.TimeoutExpired): raise bridge.HTTPException(409,'Cannot verify source Git state')
    if r.returncode or r.stdout: raise bridge.HTTPException(409,'Source has uncommitted changes; review them before starting an isolated checkout')
    hooks=subprocess.run(['git','-C',str(p),'rev-parse','--git-path','hooks/post-checkout'],capture_output=True,text=True,timeout=10)
    hook=Path(hooks.stdout.strip())
    if not hook.is_absolute(): hook=p/hook
    if hooks.returncode or (hook.is_file() and __import__('os').access(hook,__import__('os').X_OK)):
        raise bridge.HTTPException(409,'Checkout hook requires local review')

def prepare(id):
    r=record(id)
    p=path(id)
    if r['workspace'] or r['kind']=='managed': return p
    if r['kind']!='git':
        # Ordinary registered folders are bounded to exactly that folder.
        return p
    git_clean(p)
    with bridge.transaction() as c:
        c.execute('BEGIN IMMEDIATE')
        current=c.execute('select state,workspace from project_catalog where id=?',(id,)).fetchone()
        if current['workspace']: return Path(current['workspace'])
        if current['state']!='available': raise bridge.HTTPException(409,'Workspace creation requires reconciliation')
        c.execute("update project_catalog set state='preparing' where id=?",(id,))
    try:
        result=bridge.orca('worktree','create','--repo','id:'+r['repo_id'],'--name','muse-'+hashlib.sha256(id.encode()).hexdigest()[:10],
                           '--no-parent','--setup','skip','--base-branch','HEAD',timeout=30)
        p=Path(result['worktree']['path'])
        if not safe(p): raise ValueError('Unsafe checkout')
    except Exception:
        with bridge.transaction() as c: c.execute("update project_catalog set state='workspace_unknown' where id=?",(id,))
        raise bridge.HTTPException(409,'Workspace outcome unknown; do not repeat creation')
    with bridge.transaction() as c: c.execute("update project_catalog set workspace=?,state='ready' where id=?",(str(p),id))
    bridge.audit('isolated_workspace_created',id)
    return p

def create(name):
    initialize()
    if not NEW_ROOT.exists(): NEW_ROOT.mkdir(mode=0o700)
    if NEW_ROOT.is_symlink() or NEW_ROOT.resolve()!=NEW_ROOT.absolute(): raise bridge.HTTPException(409,'Unsafe new-project root')
    id='new-'+name
    p=NEW_ROOT/name
    with bridge.transaction() as c:
        c.execute('BEGIN IMMEDIATE')
        old=c.execute('select * from project_catalog where id=?',(id,)).fetchone()
        if old: return public(dict(old))
        if p.exists() or p.is_symlink(): raise bridge.HTTPException(409,'Destination already exists; refusing to overwrite')
        c.execute('insert into project_catalog values(?,?,?,?,?,?,?)',(id,name,str(p),'managed',None,str(p),'creating'))
    try:
        p.mkdir(mode=0o700)
        subprocess.run(['git','-c','core.hooksPath=/dev/null','init','--template=',str(p)],check=True,capture_output=True,timeout=10)
        result=bridge.orca('repo','add','--path',str(p),'--kind','git')
        repo_id=result['repo']['id']
    except Exception:
        with bridge.transaction() as c: c.execute("update project_catalog set state='registration_unknown' where id=?",(id,))
        bridge.audit('project_registration_unknown',id)
        return public(record(id))
    with bridge.transaction() as c: c.execute("update project_catalog set repo_id=?,state='ready' where id=?",(repo_id,id))
    bridge.audit('project_created',id)
    return public(record(id))
