import subprocess
import json
from fnmatch import fnmatchcase
from pathlib import Path
import re
import model_policy
import runner
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


@pytest.fixture
def env_policies(monkeypatch):
    # Inspect generated settings only; no CLI or environment-file IO.
    monkeypatch.setattr(model_policy.shutil, 'which', lambda _: '/fixture/codex')
    command = runner.command(Path('/fixture/project'))
    filesystem = next(arg for arg in command if arg.startswith('permissions.bridge.filesystem='))
    patterns = re.findall(r'"([^"]+)"="deny"', filesystem)
    command = model_policy.antigravity_command(Path('/fixture/project'), 'hydrogen_high')
    settings = json.loads(command[command.index('--settings')+1])
    deny = settings['permissions']['deny']
    return (patterns, [rule[len('read_file('):-1] for rule in deny if rule.startswith('read_file(')])


def env_matches(name, pattern):
    return fnmatchcase(name, pattern) or (pattern.startswith('**/') and fnmatchcase(name, pattern[3:]))


@pytest.mark.parametrize('name', ['.env.example', 'nested/.env.example'])
def test_env_example_allowed_by_both_runner_policies(env_policies, name):
    for patterns in env_policies:
        assert not any(env_matches(name, pattern) for pattern in patterns)


@pytest.mark.parametrize('name', [
    '.env', '.env.local', '.env.production', '.envrc', '.environment',
    '.env.', '.env.e', '.env.ex', '.env.exa', '.env.exam', '.env.examp', '.env.exampl',
    '.env.example.bak', '.env.examples', '.env.Example',
    *['.env.'+'example'[:i]+'x' for i in range(len('example'))],
])
@pytest.mark.parametrize('prefix', ['', 'nested/'])
def test_other_env_names_denied_by_both_runner_policies(env_policies, name, prefix):
    for patterns in env_policies:
        assert any(env_matches(prefix+name, pattern) for pattern in patterns)


@pytest.mark.parametrize('name', ['.env.example', 'nested/.env.example'])
@pytest.mark.parametrize('template', [False, True])
def test_project_file_allows_exact_env_example_without_io(monkeypatch, name, template):
    root = Path('/fixture/project')
    monkeypatch.setattr(Path, 'is_symlink', lambda _: False)
    monkeypatch.setattr(Path, 'resolve', lambda path: path)
    assert bridge.project_file(root, name, template=template) == root/name


@pytest.mark.parametrize('name', [
    '.env', '.env.local', '.env.example.bak', '.env.examples', '.envrc', '.env.e',
    'nested/.env.local', '.env.local/.env.example', '.git/.env.example',
    '../.env.example', '/absolute/.env.example',
])
@pytest.mark.parametrize('template', [False, True])
def test_project_file_rejects_protected_paths_before_metadata(monkeypatch, name, template):
    def unexpected_metadata(*args, **kwargs):
        pytest.fail('Protected path reached filesystem metadata')
    monkeypatch.setattr(Path, 'is_symlink', unexpected_metadata)
    monkeypatch.setattr(Path, 'resolve', unexpected_metadata)
    with pytest.raises(bridge.HTTPException) as caught:
        bridge.project_file(Path('/fixture/project'), name, template=template)
    assert caught.value.status_code == 422


def test_env_example_still_rejects_symlinks(monkeypatch):
    monkeypatch.setattr(Path, 'is_symlink', lambda _: True)
    with pytest.raises(bridge.HTTPException) as caught:
        bridge.project_file(Path('/fixture/project'), '.env.example')
    assert caught.value.status_code == 422
