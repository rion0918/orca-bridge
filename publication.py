"""New public GitHub repositories from immutable, separately approved snapshots."""
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import shlex
import shutil
import subprocess
import time
import uuid
import bridge


def initialize():
    with bridge.transaction() as c:
        c.execute('''create table if not exists publications
            (id text primary key, state text not null, review text not null, result text)''')


def approval_key():
    configured=os.environ.get('BRIDGE_PUBLICATION_APPROVAL_TOKEN')
    if configured: return configured
    path=bridge.DATA/'publication-approval.key'
    bridge.DATA.mkdir(mode=0o700,parents=True,exist_ok=True)
    try:
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    except FileExistsError: pass
    else:
        with os.fdopen(fd,'w') as f: f.write(secrets.token_urlsafe(32)+'\n')
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise bridge.HTTPException(503,'Approval key requires private local storage')
    return path.read_text().strip()


def human(authorization):
    token=authorization[7:] if authorization.startswith('Bearer ') else ''
    # A misconfiguration must never let the agent approve its own request.
    key=approval_key()
    if key in (os.environ.get('BRIDGE_TOKEN'),os.environ.get('BRIDGE_READ_TOKEN')):
        raise bridge.HTTPException(503,'User approval credential must be independent')
    if not token or not hmac.compare_digest(key,token):
        raise bridge.HTTPException(401,'User approval key required')


def environment():
    env={k:os.environ[k] for k in ('HOME','PATH','LANG') if k in os.environ}
    env.update(GIT_CONFIG_GLOBAL='/dev/null',GIT_CONFIG_NOSYSTEM='1',GIT_TERMINAL_PROMPT='0',
               GIT_NO_REPLACE_OBJECTS='1',GIT_NO_LAZY_FETCH='1',GH_HOST='github.com',GH_PROMPT_DISABLED='1')
    return env


def run(args,*,cwd=None,input=None,timeout=30,check=True):
    try:
        r=subprocess.run(args,cwd=cwd,input=input,capture_output=True,env=environment(),timeout=timeout)
    except (OSError,subprocess.TimeoutExpired):
        raise bridge.HTTPException(502,'Publication command unavailable or timed out; inspect state before retrying')
    if check and r.returncode:
        # Never expose subprocess output: it can contain credentials or file contents.
        raise bridge.HTTPException(502,'Publication command failed; inspect local state before retrying')
    return r


def git(root,*args):
    return run(['git','-C',str(root),'-c','core.hooksPath=/dev/null','-c','core.fsmonitor=false',*args]).stdout


def source_state(root):
    if git(root,'status','--porcelain','--untracked-files=all','--ignored=no'):
        raise bridge.HTTPException(409,'Commit or remove pending changes before requesting publication')
    return git(root,'rev-parse','--verify','HEAD^{commit}').decode().strip()


def snapshot_dir(id):
    # IDs originate here, but also validate on lookup before constructing local paths.
    try:
        if str(uuid.UUID(id))!=id: raise ValueError()
    except ValueError: raise bridge.HTTPException(404,'Publication request not found')
    return bridge.DATA/'publications'/id


def secret_scan(root,commit):
    commits=git(root,'rev-list',commit).decode().splitlines()
    if len(commits)>500: raise bridge.HTTPException(409,'History exceeds publication review limit')
    files=[];history_files=set()
    for sha in commits:
        names=git(root,'ls-tree','-r','-z','--name-only',sha).decode().split('\0')
        for name in filter(None,names):
            p=Path(name);history_files.add(name)
            if '.github/workflows/' in name or name.startswith('.github/workflows/'):
                raise bridge.HTTPException(409,'GitHub Actions workflows require a separate execution review')
            if any((part.startswith('.env') and part!='.env.example') or part in ('.aws','.ssh','.codex','.agents') for part in p.parts) or p.suffix.lower() in ('.pem','.key','.p12','.pfx'):
                raise bridge.HTTPException(409,'Protected path found in Git history; remove it from history before publication')
        if sha==commit: files=sorted(filter(None,names))
    objects=git(root,'rev-list','--objects','--no-object-names',commit).decode().splitlines()
    if len(objects)>5000: raise bridge.HTTPException(409,'Object count exceeds publication review limit')
    metadata=run(['git','-C',str(root),'cat-file','--batch-check=%(objectname) %(objecttype) %(objectsize)'],input=('\n'.join(objects)+'\n').encode()).stdout.decode().splitlines()
    known=[v.encode() for k,v in os.environ.items() if len(v)>=8 and any(s in k.upper() for s in ('TOKEN','SECRET','API_KEY','PASSWORD'))]
    key_path=bridge.DATA/'publication-approval.key'
    if key_path.is_file(): known.append(key_path.read_bytes().strip())
    pattern=re.compile(rb'(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)')
    total=0
    for line in metadata:
        oid,kind,size=line.split();size=int(size);total+=size
        if size>10_000_000 or total>100_000_000: raise bridge.HTTPException(409,'Snapshot exceeds publication scan limit')
        if kind not in ('blob','commit','tag'): continue
        content=git(root,'cat-file',kind,oid)
        if pattern.search(content) or any(value and value in content for value in known):
            raise bridge.HTTPException(409,'Possible credential in Git history; publication refused')
    return files,commits,sorted(history_files)


def prepare(project,owner,repository,visibility):
    initialize();approval_key()
    root=bridge.project_path(project)
    commit=source_state(root)
    id=str(uuid.uuid4());directory=snapshot_dir(id);directory.mkdir(mode=0o700,parents=True)
    bundle=directory/'source.bundle'
    # Bundle HEAD only: no other branches, tags, hooks, repository config or working files.
    git(root,'bundle','create',str(bundle),'HEAD')
    os.chmod(bundle,0o600)
    isolated=directory/'snapshot.git'
    run(['git','init','--bare','--template=',str(isolated)])
    git(isolated,'fetch','--no-tags',str(bundle),'HEAD:refs/heads/main')
    if git(isolated,'rev-parse','refs/heads/main').decode().strip()!=commit:
        raise bridge.HTTPException(409,'Project changed while preparing publication')
    files,commits,history_files=secret_scan(isolated,commit)
    changes=git(isolated,'log','--no-ext-diff','--no-textconv','--format=commit %H %an <%ae> %s','--patch',commit).decode(errors='replace')
    if len(changes)>500_000: raise bridge.HTTPException(409,'Diff exceeds mobile approval review limit')
    review={'id':id,'project':project,'owner':owner,'repository':repository,'visibility':visibility,
            'commit':commit,'branch':'main','files':files,'history_files':history_files,'history_commit_count':len(commits),
            'commits':git(isolated,'log','--format=%H %an <%ae> %s',commit).decode().splitlines(),
            'changes':changes,
            'diffstat':git(isolated,'diff-tree','--root','--stat',commit).decode(),
            'bundle_sha256':hashlib.sha256(bundle.read_bytes()).hexdigest(),
            'created_at':time.time(),'expires_at':time.time()+900,
            'warning':'The repository will include the full listed Git history and author metadata. Secret scan is heuristic. No deployment or paid service is authorized.'}
    review['fingerprint']=hashlib.sha256(json.dumps(review,sort_keys=True).encode()).hexdigest()
    with bridge.transaction() as c:
        c.execute('insert into publications values(?,?,?,NULL)',(id,'pending_approval',json.dumps(review)))
    bridge.audit('publication_requested',id)
    return get(id)


def get(id):
    snapshot_dir(id);initialize()
    with bridge.transaction() as c:
        r=c.execute('select * from publications where id=?',(id,)).fetchone()
    if r is None: raise bridge.HTTPException(404,'Publication request not found')
    return {**json.loads(r['review']),'state':r['state'],'result':json.loads(r['result']) if r['result'] else None}


def approve(id,fingerprint):
    initialize()
    with bridge.transaction() as c:
        c.execute('BEGIN IMMEDIATE')
        r=c.execute('select * from publications where id=?',(id,)).fetchone()
        if r is None: raise bridge.HTTPException(404,'Publication request not found')
        review=json.loads(r['review'])
        if r['state']!='pending_approval' or review['expires_at']<time.time() or not hmac.compare_digest(review['fingerprint'],fingerprint):
            raise bridge.HTTPException(409,'Request is expired, changed or already approved; prepare a new review')
        c.execute("update publications set state='approved' where id=?",(id,))
    bridge.audit('publication_human_approved',id)
    return get(id)


def execute(id,terms=None):
    request=get(id)
    if request['state']!='approved' or request['expires_at']<time.time():
        raise bridge.HTTPException(409,'Current user approval required')
    if terms is not None and any(request[key]!=value for key,value in terms.items()):
        raise bridge.HTTPException(409,'Execution terms do not match the approved publication')
    root=bridge.project_path(request['project'])
    if source_state(root)!=request['commit']:
        raise bridge.HTTPException(409,'Project changed; prepare a new publication request')
    bundle=snapshot_dir(id)/'source.bundle'
    if bundle.is_symlink() or hashlib.sha256(bundle.read_bytes()).hexdigest()!=request['bundle_sha256']:
        raise bridge.HTTPException(409,'Snapshot changed; publication refused')
    with bridge.transaction() as c:
        c.execute('BEGIN IMMEDIATE')
        if c.execute("update publications set state='executing' where id=? and state='approved'",(id,)).rowcount!=1:
            raise bridge.HTTPException(409,'Approval already consumed')
    bridge.audit('publication_execution_started',id)
    try:
        result=publish_snapshot(request)
    except Exception:
        with bridge.transaction() as c: c.execute("update publications set state='needs_reconciliation' where id=?",(id,))
        bridge.audit('publication_needs_reconciliation',id)
        raise
    with bridge.transaction() as c:
        c.execute("update publications set state='succeeded',result=? where id=?",(json.dumps(result),id))
    bridge.audit('publication_succeeded',id)
    return get(id)


def publish_snapshot(request):
    gh=shutil.which('gh')
    if not gh: raise bridge.HTTPException(503,'GitHub CLI unavailable')
    def api(method,endpoint,body=None):
        args=[gh,'api','--hostname','github.com','--include','--method',method,endpoint]
        if body is not None: args+=['--input','-']
        r=run(args,input=json.dumps(body).encode() if body is not None else None,check=False)
        headers,sep,payload=r.stdout.partition(b'\r\n\r\n')
        if not sep: headers,sep,payload=r.stdout.partition(b'\n\n')
        status=re.search(rb'^HTTP/\S+ (\d+)',headers)
        if not status: raise bridge.HTTPException(502,'Cannot verify GitHub response')
        try: data=json.loads(payload)
        except ValueError: raise bridge.HTTPException(502,'Cannot parse GitHub response')
        return int(status[1]),data
    status,user=api('GET','user')
    if status!=200 or user.get('login','').lower()!=request['owner'].lower():
        raise bridge.HTTPException(409,'Approved owner differs from the authenticated GitHub user')
    target=request['owner']+'/'+request['repository']
    status,_=api('GET','repos/'+target)
    if status!=404: raise bridge.HTTPException(409,'Target must be a new repository; existing or inaccessible target refused')
    # Build a fresh bare export from the sealed bundle, ignoring project config/hooks/remotes.
    export=snapshot_dir(request['id'])/'export.git'
    run(['git','init','--bare','--template=',str(export)])
    git(export,'fetch','--no-tags',str(snapshot_dir(request['id'])/'source.bundle'),'HEAD:refs/heads/main')
    if git(export,'rev-parse','refs/heads/main').decode().strip()!=request['commit']:
        raise bridge.HTTPException(409,'Snapshot commit mismatch')
    visibility=request['visibility']
    if visibility not in ('public','private'):
        raise bridge.HTTPException(409,'Invalid approved visibility')
    private=visibility=='private'
    status,repo=api('POST','user/repos',{'name':request['repository'],'private':private,'auto_init':False,'has_wiki':False,'has_projects':False})
    if status!=201 or repo.get('full_name','').lower()!=target.lower() or repo.get('private') is not private:
        raise bridge.HTTPException(502,'Repository creation not verified; inspect GitHub before retrying')
    url='https://github.com/'+target+'.git'
    authenticated_git=['git','-C',str(export),'-c','core.hooksPath=/dev/null','-c','credential.helper=',
                       '-c','credential.helper=!'+shlex.quote(gh)+' auth git-credential']
    run([*authenticated_git,'push','--no-verify','--porcelain',url,'refs/heads/main:refs/heads/main'],timeout=120)
    remote=run([*authenticated_git,'ls-remote',url,'refs/heads/main']).stdout.decode().split()
    if not remote or remote[0]!=request['commit']:
        raise bridge.HTTPException(502,'Remote commit verification failed; inspect GitHub before retrying')
    return {'url':'https://github.com/'+target,'commit':request['commit'],'branch':'main',
            'visibility':visibility,'published':not private}


def summary(request):
    # Keep full source diffs in the human review, not every Muse polling response.
    return {key:value for key,value in request.items() if key!='changes'}
