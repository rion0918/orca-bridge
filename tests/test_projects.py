import subprocess
import pytest
import bridge
import projects
from test_bridge import client

@pytest.fixture
def catalog(client,monkeypatch,tmp_path):
    root=tmp_path/'new-projects'; root.mkdir()
    monkeypatch.setattr(projects,'NEW_ROOT',root)
    monkeypatch.setattr(bridge,'orca',lambda *a,**k:{'repos':[]})
    return client

def test_create_and_replay_project(catalog,monkeypatch):
    calls=[]
    def cli(*a,**k):
        calls.append(a); return {'repo':{'id':'fixture-repo'}}
    monkeypatch.setattr(bridge,'orca',cli)
    r=catalog.post('/projects',json={'name':'sample-app'})
    assert r.status_code==200
    p=r.json(); assert p['state']=='ready'
    assert catalog.post('/projects',json={'name':'sample-app'}).json()['id']==p['id']
    assert len(calls)==1
    assert p['id'] in catalog.get('/policy').json()['projects']
    t=catalog.post('/tasks',json={'project':p['id'],'prompt':'Create a file'})
    assert t.status_code==200

def test_no_arbitrary_project_paths(catalog):
    assert catalog.post('/projects',json={'name':'../escape'}).status_code==422
    assert catalog.post('/projects',json={'name':'valid','path':'/tmp/escape'}).status_code==422

def test_read_token_cannot_create(catalog,monkeypatch):
    monkeypatch.setenv('BRIDGE_READ_TOKEN','reader')
    assert catalog.post('/projects',json={'name':'sample'},headers={'Authorization':'Bearer reader'}).status_code==403

def test_registration_unknown_is_not_repeated(catalog,monkeypatch):
    calls=[]
    def unknown(*a,**k): calls.append(a); raise bridge.CLIError('timeout',unknown=True)
    monkeypatch.setattr(bridge,'orca',unknown)
    first=catalog.post('/projects',json={'name':'unknown'}).json()
    second=catalog.post('/projects',json={'name':'unknown'}).json()
    assert first['state']==second['state']=='registration_unknown'
    assert len(calls)==1

def test_home_catalog_is_blocked(catalog,monkeypatch):
    monkeypatch.setattr(bridge,'orca',lambda *a,**k:{'repos':[{'id':'home','path':str(projects.Path.home()),'displayName':'Home','kind':'folder'}]})
    data=catalog.get('/projects').json()['projects']
    home=next(p for p in data if p['id']=='orca-home')
    assert home['state']=='blocked'
    assert catalog.post('/tasks',json={'project':home['id'],'prompt':'edit'}).status_code==422

def test_existing_git_uses_orca_checkout(catalog,monkeypatch,tmp_path):
    source=tmp_path/'existing'; source.mkdir()
    subprocess.run(['git','init','--template=',str(source)],check=True,capture_output=True)
    subprocess.run(['git','-C',str(source),'-c','user.name=Test','-c','user.email=test@example.invalid','commit','--allow-empty','-m','initial'],check=True,capture_output=True)
    workspace=tmp_path/'isolated'; workspace.mkdir()
    calls=[]
    def cli(*a,**k):
        calls.append(a)
        if a[:2]==('repo','list'): return {'repos':[{'id':'repo','path':str(source),'displayName':'App','kind':'git'}]}
        return {'worktree':{'path':str(workspace)}}
    monkeypatch.setattr(bridge,'orca',cli)
    catalog.get('/projects')
    assert projects.prepare('orca-repo')==workspace
    assert projects.prepare('orca-repo')==workspace
    mutations=[a for a in calls if a[:2]==('worktree','create')]
    assert len(mutations)==1 and '--setup' in mutations[0] and 'skip' in mutations[0]

def test_dirty_existing_source_is_not_modified(catalog,monkeypatch,tmp_path):
    source=tmp_path/'dirty'; source.mkdir()
    subprocess.run(['git','init','--template=',str(source)],check=True,capture_output=True)
    (source/'personal.txt').write_text('unchanged')
    monkeypatch.setattr(bridge,'orca',lambda *a,**k:{'repos':[{'id':'dirty','path':str(source),'displayName':'Dirty','kind':'git'}]})
    catalog.get('/projects')
    with pytest.raises(bridge.HTTPException): projects.prepare('orca-dirty')
    assert (source/'personal.txt').read_text()=='unchanged'

def test_existing_checkout_hooks_require_review(catalog,monkeypatch,tmp_path):
    source=tmp_path/'hooked'; source.mkdir()
    subprocess.run(['git','init','--template=',str(source)],check=True,capture_output=True)
    hook=source/'.git/hooks/post-checkout'; hook.parent.mkdir(exist_ok=True)
    hook.write_text('#!/bin/sh\nexit 0\n'); hook.chmod(0o700)
    with pytest.raises(bridge.HTTPException,match='hook'): projects.git_clean(source)

@pytest.mark.parametrize("kind",["missing","directory","symlink"])
def test_default_sandbox_state(catalog,monkeypatch,tmp_path,kind):
    p=tmp_path/"sandbox"
    if kind=="directory": p.mkdir()
    if kind=="symlink": p.symlink_to(tmp_path,target_is_directory=True)
    monkeypatch.setattr(bridge,"PROJECTS",{"sandbox":p})
    item=catalog.get("/projects").json()["projects"][0]
    assert item["state"]==("ready" if kind=="directory" else "blocked")
