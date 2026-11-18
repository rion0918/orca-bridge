import subprocess
import pytest
import bridge
from publication import publish_snapshot as real_publish_snapshot
from test_bridge import client

@pytest.fixture
def publication(client, monkeypatch):
    import publication
    monkeypatch.setenv('BRIDGE_PUBLICATION_APPROVAL_TOKEN','human-only-test-key')
    root=bridge.PROJECTS['test']
    def git(*args):
        return subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,check=True).stdout.strip()
    git('init','--template=');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
    (root/'hello.txt').write_text('hello\n');git('add','hello.txt');git('commit','-m','Fixture')
    calls=[]
    monkeypatch.setattr(publication,'publish_snapshot',lambda request: calls.append(request) or {'url':'https://github.com/fixture/demo'})
    return root,git,calls

def prepare(client, **options):
    r=client.post('/publication-requests',json={'project':'test','owner':'fixture','repository':'demo',**options})
    assert r.status_code==200,r.text
    return r.json()

def approve(client,request):
    return client.post('/publication-approvals/'+request['id'],json={'fingerprint':request['fingerprint']},headers={'Authorization':'Bearer human-only-test-key'})

def test_publication_requires_separate_human_approval(client,publication):
    r=prepare(client);url='/publication-requests/'+r['id']+'/execute'
    assert client.post(url).status_code==409
    assert client.post('/publication-approvals/'+r['id'],json={'fingerprint':r['fingerprint']}).status_code==401
    assert approve(client,r).status_code==200
    assert client.post(url).status_code==200
    assert len(publication[2])==1
    assert client.post(url).status_code==409
    assert approve(client,r).status_code==409

def test_publication_execute_checks_confirmation_terms(client,publication):
    request=prepare(client,visibility='private')
    url='/publication-requests/'+request['id']+'/execute'
    terms={key:request[key] for key in ('project','owner','repository','visibility','branch','commit','fingerprint')}
    assert client.post(url,json=terms).status_code==409
    assert approve(client,request).status_code==200
    for key,value in {'project':'other','owner':'other','repository':'other','visibility':'public',
                      'commit':'0'*40,'fingerprint':'0'*64}.items():
        assert client.post(url,json={**terms,key:value}).status_code==409
    assert publication[2]==[]
    assert client.post(url,json=terms).status_code==200
    assert len(publication[2])==1

def test_publication_binds_commit_and_dirty_state(client,publication):
    root,git,calls=publication
    r=prepare(client);assert approve(client,r).status_code==200
    (root/'hello.txt').write_text('changed');git('add','hello.txt');git('commit','-m','Changed')
    assert client.post('/publication-requests/'+r['id']+'/execute').status_code==409
    assert calls==[]

def test_publication_blocks_dirty_project(client,publication):
    (publication[0]/'untracked.txt').write_text('pending')
    assert client.post('/publication-requests',json={'project':'test','owner':'fixture','repository':'demo'}).status_code==409

def test_publication_blocks_secret_in_history(client,publication):
    root,git,calls=publication
    (root/'.env').write_text('DUMMY=private');git('add','.env');git('commit','-m','Old secret')
    git('rm','.env');git('commit','-m','Remove secret')
    assert client.post('/publication-requests',json={'project':'test','owner':'fixture','repository':'demo'}).status_code==409

def test_publication_review_and_fingerprint(client,publication):
    r=prepare(client)
    assert r['state']=='pending_approval' and r['visibility']=='public'
    assert r['files']==['hello.txt'] and r['commit']==publication[1]('rev-parse','HEAD')
    assert 'diffstat' in r and 'commits' in r
    assert client.post('/publication-approvals/'+r['id'],json={'fingerprint':'wrong'},headers={'Authorization':'Bearer human-only-test-key'}).status_code==409
    assert client.get('/publication-approvals/'+r['id'],headers={'Authorization':'Bearer human-only-test-key'}).status_code==200
    assert client.get('/publication-requests/'+r['id'],headers={'Authorization':'Bearer human-only-test-key'}).status_code==401

def test_publication_expiration(client,publication,monkeypatch):
    import publication as module
    r=prepare(client)
    monkeypatch.setattr(module.time,'time',lambda:r['expires_at']+1)
    assert approve(client,r).status_code==409
    assert publication[2]==[]

def test_publication_failure_is_not_retried(client,publication,monkeypatch):
    import publication as module
    def fail(request): raise bridge.HTTPException(502,'test failure')
    monkeypatch.setattr(module,'publish_snapshot',fail)
    r=prepare(client);approve(client,r)
    assert client.post('/publication-requests/'+r['id']+'/execute').status_code==502
    assert client.get('/publication-requests/'+r['id']).json()['state']=='needs_reconciliation'
    assert client.post('/publication-requests/'+r['id']+'/execute').status_code==409

def test_publication_snapshot_tamper(client,publication):
    r=prepare(client);approve(client,r)
    (bridge.DATA/'publications'/r['id']/'source.bundle').write_bytes(b'changed')
    assert client.post('/publication-requests/'+r['id']+'/execute').status_code==409
    assert publication[2]==[]

def test_publication_rejects_arguments_and_read_token(client,publication,monkeypatch):
    monkeypatch.setenv('BRIDGE_READ_TOKEN','reader')
    body={'project':'test','owner':'fixture','repository':'demo'}
    assert client.post('/publication-requests',json=body,headers={'Authorization':'Bearer reader'}).status_code==403
    for extra in [{'repository':'--public'},{'command':'echo hi'},{'owner':'fixture/other'}]:
        assert client.post('/publication-requests',json={**body,**extra}).status_code==422

def test_publication_blocks_actions_and_secret_content(client,publication):
    root,git,calls=publication
    (root/'example.txt').write_text('ghp_'+'a'*36)
    git('add','example.txt');git('commit','-m','Credential')
    assert client.post('/publication-requests',json={'project':'test','owner':'fixture','repository':'demo'}).status_code==409

def test_publication_blocks_workflow_execution(client,publication):
    root,git,calls=publication
    (root/'.github/workflows').mkdir(parents=True)
    (root/'.github/workflows/build.yml').write_text('name: fixture')
    git('add','.github');git('commit','-m','Workflow')
    assert client.post('/publication-requests',json={'project':'test','owner':'fixture','repository':'demo'}).status_code==409

def test_publication_approval_page_has_no_secret_or_agent_approval_route(client,publication):
    response=client.get('/publish-approval')
    assert response.status_code==200
    assert 'human-only-test-key' not in response.text
    assert "script-src 'nonce-" in response.headers['content-security-policy']
    assert 'NONCE_PLACEHOLDER' not in response.text
    assert response.headers['cache-control']=='no-store'
    schema=client.get('/openapi.json').json()
    assert '/publication-requests' in schema['paths']
    assert '/publication-approvals/{id}' not in schema['paths']

def test_same_credential_cannot_approve(client,publication,monkeypatch):
    r=prepare(client)
    monkeypatch.setenv('BRIDGE_PUBLICATION_APPROVAL_TOKEN','test-token')
    assert client.post('/publication-approvals/'+r['id'],json={'fingerprint':r['fingerprint']}).status_code==503

@pytest.mark.parametrize("private, returned_private", [(False, False), (True, True), (True, False), (False, True)])
def test_publication_backend_real_git_transfer_and_fixed_github_requests(client,publication,monkeypatch,tmp_path,private,returned_private):
    import publication as module
    r=prepare(client, visibility='private' if private else 'public')
    assert r['visibility'] == ('private' if private else 'public')
    remote=tmp_path/'remote.git'
    subprocess.run(['git','init','--bare','--template=',str(remote)],check=True,capture_output=True)
    original=module.run;commands=[]
    def transport(args,**kwargs):
        commands.append(args)
        if len(args)>1 and args[1]=='api':
            endpoint=args[args.index('--method')+2]
            method=args[args.index('--method')+1]
            if endpoint=='user': status,payload=200,{'login':'fixture'}
            elif method=='GET': status,payload=404,{'message':'Not Found'}
            else:
                assert endpoint=='user/repos'
                assert __import__('json').loads(kwargs['input'])=={'name':'demo','private':private,'auto_init':False,'has_wiki':False,'has_projects':False}
                status,payload=201,{'full_name':'fixture/demo','private':returned_private}
            return subprocess.CompletedProcess(args,0,('HTTP/2.0 '+str(status)+' OK\r\n\r\n'+__import__('json').dumps(payload)).encode(),b'')
        args=[str(remote) if a=='https://github.com/fixture/demo.git' else a for a in args]
        return original(args,**kwargs)
    monkeypatch.setattr(module,'run',transport)
    monkeypatch.setattr(module.shutil,'which',lambda name:'/fixture/gh')
    if private != returned_private:
        with pytest.raises(bridge.HTTPException, match="Repository creation not verified"):
            real_publish_snapshot(r)
        assert not any("push" in command for command in commands)
        return
    result=real_publish_snapshot(r)
    assert result["visibility"] == r["visibility"]
    assert result['published'] is (not private)
    assert subprocess.run(['git','--git-dir',str(remote),'rev-parse','main'],check=True,capture_output=True,text=True).stdout.strip()==r['commit']
    push=next(c for c in commands if 'push' in c)
    assert '--force' not in push and 'refs/heads/main:refs/heads/main' in push
    assert '-c' in push and 'core.hooksPath=/dev/null' in push
    assert 'credential.helper=' in push
    lookup=next(c for c in commands if 'ls-remote' in c)
    assert any(arg.endswith('auth git-credential') for arg in lookup)
    assert not (publication[0]/'.git/hooks/pre-push').exists()

def test_publication_expired_approval_and_dirty_execution(client,publication,monkeypatch):
    import publication as module
    r=prepare(client);approve(client,r)
    (publication[0]/'untracked').write_text('new')
    assert client.post('/publication-requests/'+r['id']+'/execute').status_code==409
    (publication[0]/'untracked').unlink()
    monkeypatch.setattr(module.time,'time',lambda:r['expires_at']+1)
    assert client.post('/publication-requests/'+r['id']+'/execute').status_code==409
    assert publication[2]==[]

def test_publication_key_private_and_review_contains_patch(client,publication,monkeypatch):
    import publication as module
    monkeypatch.delenv('BRIDGE_PUBLICATION_APPROVAL_TOKEN')
    key=module.approval_key()
    assert len(key)>=40
    assert (bridge.DATA/'publication-approval.key').stat().st_mode & 0o777==0o600
    r=prepare(client)
    review=client.get('/publication-approvals/'+r['id'],headers={'Authorization':'Bearer '+key}).json()
    assert '+hello' in review['changes']
    assert 'changes' not in r
    assert key not in __import__('json').dumps(r)

@pytest.mark.parametrize('user_status,login,target_status',[(200,'other',404),(403,'fixture',404),(200,'fixture',200),(200,'fixture',403)])
def test_backend_refuses_owner_or_existing_target_before_creation(client,publication,monkeypatch,user_status,login,target_status):
    import publication as module,json
    r=prepare(client);calls=[]
    def transport(args,**kwargs):
        assert args[1]=='api'
        calls.append(args)
        assert args[args.index('--method')+1]=='GET'
        endpoint=args[args.index('--method')+2]
        status,payload=(user_status,{'login':login}) if endpoint=='user' else (target_status,{})
        return subprocess.CompletedProcess(args,0,('HTTP/2.0 '+str(status)+' OK\r\n\r\n'+json.dumps(payload)).encode(),b'')
    monkeypatch.setattr(module,'run',transport);monkeypatch.setattr(module.shutil,'which',lambda _:'/fixture/gh')
    with pytest.raises(bridge.HTTPException):real_publish_snapshot(r)
    assert calls

def test_public_approval_cannot_authorize_private_creation(client, publication):
    public = prepare(client, visibility='public')
    private = prepare(client, visibility='private')
    assert approve(client, public).status_code == 200
    assert client.post('/publication-approvals/'+private['id'],
                       json={'fingerprint': public['fingerprint']},
                       headers={'Authorization': 'Bearer human-only-test-key'}).status_code == 409
    assert client.post('/publication-requests/'+private['id']+'/execute').status_code == 409
    assert publication[2] == []
