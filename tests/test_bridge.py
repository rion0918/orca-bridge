import json
import pytest
from fastapi.testclient import TestClient
import bridge

@pytest.fixture
def client(tmp_path,monkeypatch):
    monkeypatch.setenv('BRIDGE_TOKEN','test-token')
    import model_policy
    monkeypatch.setattr(model_policy,'availability',lambda _: {'status':'available','windows':[]})
    monkeypatch.setattr(bridge,'DATA',tmp_path/'state')
    monkeypatch.setattr(bridge,'PROJECTS',{'test':tmp_path/'project'})
    (tmp_path/'project').mkdir()
    with TestClient(bridge.app) as c:
        c.headers['Authorization']='Bearer test-token'
        yield c

def test_auth(client):
    assert client.get('/status',headers={'Authorization':''}).status_code==401

def test_task_approval_and_cancel(client):
    t=client.post('/tasks',json={'prompt':'Create hello.txt','project':'test'}).json()
    assert t['state']=='pending_approval'
    assert client.post('/tasks/'+t['id']+'/cancel').json()['state']=='cancelled'

def test_extra_command_rejected(client):
    assert client.post('/tasks',json={'prompt':'test','command':'rm'}).status_code==422

def test_outside_project(client):
    assert client.post('/tasks',json={'prompt':'test','project':'../../'}).status_code==422

def test_no_remote_approval(client):
    assert client.post('/tasks/x/approve').status_code==404

def test_idempotency(client):
    body={'prompt':'hello','project':'test'}
    a=client.post('/tasks',json=body,headers={'Idempotency-Key':'same'}).json()
    b=client.post('/tasks',json=body,headers={'Idempotency-Key':'same'}).json()
    assert a['id']==b['id']
    assert client.post('/tasks',json={**body,'prompt':'changed'},headers={'Idempotency-Key':'same'}).status_code==409

def test_read_token_cannot_submit(client,monkeypatch):
    monkeypatch.setenv('BRIDGE_READ_TOKEN','reader')
    assert client.post('/tasks',json={'prompt':'a','project':'test'},headers={'Authorization':'Bearer reader'}).status_code==403

def test_not_found(client):
    assert client.get('/tasks/not-a-task').status_code==404

def test_status_retry(client,monkeypatch):
    calls=[]
    def fake(*args,**kwargs):
        calls.append(args)
        if len(calls)==1: raise bridge.CLIError('unavailable')
        return {'app':{'running':True},'runtime':{'state':'ready'}}
    monkeypatch.setattr(bridge,'orca',fake)
    assert client.get('/status').json()['running'] is True
    assert len(calls)==2

def test_launch_unknown_not_retried(client,monkeypatch):
    t=client.post('/tasks',json={'prompt':'a','project':'test'}).json()
    calls=[]
    def fake(*args,**kwargs):
        calls.append(args)
        raise bridge.CLIError('timeout',unknown=True)
    monkeypatch.setattr(bridge,'orca',fake)
    bridge.approve(t['id'])
    assert bridge.task(t['id'])['state']=='launch_unknown'
    assert len(calls)==1

def test_redaction():
    assert 'sk-secret' not in bridge.redact('Bearer sk-secret')

def test_symlink_project(client,tmp_path):
    bridge.PROJECTS['test'].rmdir()
    bridge.PROJECTS['test'].symlink_to(tmp_path,target_is_directory=True)
    assert client.post('/tasks',json={'prompt':'a','project':'test'}).status_code==422

def test_runner_restricts_filesystem_and_network():
    import runner
    from pathlib import Path
    cmd=runner.command(Path('/tmp/project'))
    assert 'default_permissions="bridge"' in cmd
    assert any('":root"="deny"' in arg for arg in cmd)
    assert 'permissions.bridge.network.enabled=false' in cmd
    assert '--ignore-user-config' in cmd
    assert '--ignore-rules' in cmd

def test_runner_success_and_logs(client,monkeypatch):
    import runner,sys
    t=client.post('/tasks',json={'prompt':'a','project':'test'}).json()
    bridge.update(t['id'],state='launching')
    code='import json; print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":"done"}}))'
    monkeypatch.setattr(runner,'command',lambda _,*args: [sys.executable,'-c',code])
    runner.run(t['id'])
    assert bridge.task(t['id'])['state']=='succeeded'
    assert client.get('/tasks/'+t['id']+'/result').json()['result']=='done'
    assert 'agent_message' in client.get('/tasks/'+t['id']+'/logs').json()['text']

def test_runner_failure(client,monkeypatch):
    import runner,sys
    t=client.post('/tasks',json={'prompt':'a','project':'test'}).json()
    bridge.update(t['id'],state='launching')
    monkeypatch.setattr(runner,'command',lambda _,*args: [sys.executable,'-c','import sys; sys.exit(3)'])
    runner.run(t['id'])
    assert bridge.task(t['id'])['state']=='failed'
    assert bridge.task(t['id'])['exit_code']==3
    assert client.post('/tasks/'+t['id']+'/retry').json()['state']=='pending_approval'

def test_runner_timeout(client,monkeypatch):
    import runner,sys
    t=client.post('/tasks',json={'prompt':'a','project':'test'}).json()
    bridge.update(t['id'],state='launching',timeout=1)
    monkeypatch.setattr(runner,'command',lambda _,*args: [sys.executable,'-c','import time; time.sleep(60)'])
    runner.run(t['id'])
    assert bridge.task(t['id'])['state']=='timed_out'

def test_runner_cancel_active(client,monkeypatch):
    import runner,sys,threading,time
    t=client.post('/tasks',json={'prompt':'a','project':'test'}).json()
    bridge.update(t['id'],state='launching')
    monkeypatch.setattr(runner,'command',lambda _,*args: [sys.executable,'-c','import time; time.sleep(60)'])
    def stop():
        time.sleep(.5)
        bridge.update(t['id'],state='cancel_requested')
    th=threading.Thread(target=stop)
    th.start()
    runner.run(t['id'])
    th.join()
    assert bridge.task(t['id'])['state']=='cancelled'

def test_body_limit(client):
    assert client.post('/tasks',content=b'x'*40000,headers={'Content-Type':'application/json'}).status_code==413

def test_concurrency_and_no_duplicate_approval(client,monkeypatch):
    first=client.post('/tasks',json={'prompt':'first','project':'test'}).json()
    second=client.post('/tasks',json={'prompt':'second','project':'test'}).json()
    monkeypatch.setattr(bridge,'orca',lambda *a,**k:{'terminal':{'handle':'fixture'}})
    bridge.approve(first['id'])
    assert bridge.task(first['id'])['terminal']=='fixture'
    with pytest.raises(bridge.HTTPException): bridge.approve(first['id'])
    with pytest.raises(bridge.HTTPException): bridge.approve(second['id'])

def test_stop_refuses_changed_process(tmp_path,monkeypatch):
    import process_control as pc
    monkeypatch.setattr(pc,'ROOT',tmp_path)
    monkeypatch.setattr(pc,'identity',lambda _: 'original')
    pc.record('test',123)
    monkeypatch.setattr(pc,'identity',lambda _: 'different')
    with pytest.raises(RuntimeError,match='identity changed'): pc.stop('test')

def test_http_audit_contains_operation_without_prompt(client):
    client.post('/tasks',json={'prompt':'never-log-this-prompt','project':'test'})
    text=(bridge.DATA/'audit.jsonl').read_text()
    assert 'http_create_200' in text
    assert 'never-log-this-prompt' not in text
    assert 'test-token' not in text

def test_agents_checks_local_login_without_disclosing_output(client,monkeypatch):
    from types import SimpleNamespace
    calls=[]
    monkeypatch.setattr(bridge.shutil,'which',lambda _: '/local/codex')
    def login(*args,**kwargs):
        calls.append((args,kwargs))
        return SimpleNamespace(returncode=0,stdout='sentinel-secret',stderr='')
    monkeypatch.setattr(bridge.subprocess,'run',login)
    r=client.get('/agents')
    assert r.json()['authentication_verified'] is True
    assert r.json()['authentication_check']=='local_cli_login_status'
    assert 'sentinel-secret' not in r.text
    assert calls[0][0][0]==['/local/codex','login','status']
    assert not any(k.startswith('BRIDGE_') for k in calls[0][1]['env'])

def test_agents_reports_not_logged_in(client,monkeypatch):
    from types import SimpleNamespace
    monkeypatch.setattr(bridge.shutil,'which',lambda _: '/local/codex')
    monkeypatch.setattr(bridge.subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=1))
    r=client.get('/agents').json()
    assert r['authentication_verified'] is False
    assert r['authentication_status']=='not_logged_in'

def test_agents_reports_login_check_timeout(client,monkeypatch):
    monkeypatch.setattr(bridge.shutil,'which',lambda _: '/local/codex')
    def timeout(*a,**k): raise bridge.subprocess.TimeoutExpired('codex',5)
    monkeypatch.setattr(bridge.subprocess,'run',timeout)
    r=client.get('/agents').json()
    assert r['authentication_verified'] is False
    assert r['authentication_status']=='unknown'

def test_launch_uses_current_interpreter(client,monkeypatch):
    import shlex
    monkeypatch.setattr(bridge.sys,"executable","/fixture/python with spaces")
    calls=[]
    monkeypatch.setattr(bridge,"orca",lambda *a,**k: calls.append(a) or {"handle":"fixture"})
    t=client.post("/tasks",json={"prompt":"a","project":"test"}).json()
    bridge.approve(t["id"])
    command=calls[0][calls[0].index("--command")+1]
    assert shlex.split(command)==[bridge.sys.executable,str(bridge.ROOT/"runner.py"),t["id"]]
