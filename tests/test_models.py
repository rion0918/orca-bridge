import json
import sys
from pathlib import Path
from types import SimpleNamespace
import pytest
import bridge,runner
from test_bridge import client

@pytest.mark.parametrize(('kind','profile'), [('implementation','sol_low'),('design','sol_high'),('recovery','sol_max')])
def test_routes_by_work_kind(client,kind,profile):
    r=client.post('/tasks',json={'project':'test','prompt':'work','work_kind':kind})
    assert r.status_code==200
    assert r.json()['execution']['profile']==profile

def test_claude_only_routes_to_a_manually_approved_task(client,monkeypatch):
    import model_policy
    monkeypatch.setenv('BRIDGE_AGENTS','claude')
    monkeypatch.setenv('BRIDGE_AUTORUN_SANDBOX','1')
    monkeypatch.setattr(model_policy,'claude_state',lambda:'subscription')
    monkeypatch.setattr(model_policy,'availability',lambda _:model_policy.claude_availability())
    r=client.post('/tasks',json={'project':'test','prompt':'review this design','work_kind':'design'})
    assert r.status_code==200, r.text
    task=r.json()
    assert task['execution']['agent']=='claude'
    assert task['execution']['profile']=='claude_design'
    assert task['state']=='pending_approval'
    assert bridge.task(task['id'])['state']=='pending_approval'
    with pytest.raises(bridge.HTTPException):
        bridge.approve(task['id'],automatic=True)

def test_auto_skips_exhausted_provider(client,monkeypatch):
    import model_policy
    monkeypatch.setenv('BRIDGE_AGENTS','codex,claude')
    monkeypatch.setattr(model_policy,'availability',lambda profile:
        {'status':'exhausted'} if profile.startswith('sol_') else
        {'status':'available','automatic':False,'quota_status':'unknown'})
    r=client.post('/tasks',json={'project':'test','prompt':'work'})
    assert r.status_code==200, r.text
    assert r.json()['execution']['agent']=='claude'

def test_claude_api_billing_auth_is_not_eligible(client,monkeypatch):
    import model_policy
    monkeypatch.setenv('BRIDGE_AGENTS','claude')
    monkeypatch.setattr(model_policy.shutil,'which',lambda _: '/fixture/claude')
    def inspect(args,**kwargs):
        assert 'ANTHROPIC_API_KEY' not in kwargs['env']
        stdout='2.1.319 (Claude Code)' if args[-1]=='--version' else '{"authMethod":"api_key"}'
        return SimpleNamespace(returncode=0,stdout=stdout)
    monkeypatch.setattr(model_policy.subprocess,'run',inspect)
    monkeypatch.setattr(model_policy,'availability',lambda _:model_policy.claude_availability())
    r=client.post('/tasks',json={'project':'test','prompt':'work'})
    assert r.status_code==409
    assert r.json()['detail']['availability']['authentication_status']=='subscription_required'

def test_claude_runner_reads_result_after_local_approval(client,monkeypatch):
    import model_policy
    monkeypatch.setenv('BRIDGE_AGENTS','claude')
    monkeypatch.setattr(model_policy,'availability',lambda _:{'status':'available','automatic':False})
    monkeypatch.setattr(bridge,'orca',lambda *a,**kw:{'handle':'fixture'})
    task=client.post('/tasks',json={'project':'test','prompt':'draft a change'}).json()
    assert bridge.approve(task['id'])['state']=='launching'
    event={'type':'result','is_error':False,'result':json.dumps({'outcome':'completed','summary':'Drafted'})}
    script=f"import sys; sys.stdin.read(); print({json.dumps(event)!r})"
    monkeypatch.setattr(runner,'command',lambda *a:[sys.executable,'-c',script])
    runner.run(task['id'])
    result=client.get('/tasks/'+task['id']+'/result').json()
    assert result['state']=='succeeded'
    assert result['handoff']=={'outcome':'completed','summary':'Drafted'}

def test_claude_command_uses_only_file_tools(monkeypatch):
    import model_policy
    monkeypatch.setattr(model_policy.shutil,'which',lambda _: '/fixture/claude')
    cmd=runner.command(Path('/tmp/project'),'claude_implementation')
    assert '--restricted' in cmd and '--safe-mode' in cmd
    assert cmd[cmd.index('--tools')+1]=='Read,Glob,Grep,Edit,Write'
    assert cmd[cmd.index('--disallowedTools')+1]=='mcp__*'
    settings=json.loads(cmd[cmd.index('--settings')+1])
    assert settings['permissions']['blockReadsOutsideWorkingDirectories']
    assert settings['permissions']['blockWritesOutsideWorkingDirectories']

def test_default_profile_pins_sol_low(client):
    t=client.post('/tasks',json={'project':'test','prompt':'implement'}).json()
    assert t['execution']['model']=='gpt-6.1-sol'
    assert t['execution']['effort']=='low'

def test_profiles_pin_allowed_model_and_effort(client):
    for profile,effort in [('sol_high','high'),('sol_max','max')]:
        r=client.post('/tasks',json={'project':'test','prompt':'analyze','execution_profile':profile}).json()
        assert r['execution']['effort']==effort
        cmd=runner.command(Path('/tmp/project'),profile)
        assert cmd[cmd.index('--model')+1]=='gpt-6.1-sol'
        assert 'model_reasoning_effort="'+effort+'"' in cmd

def test_raw_model_and_unknown_profile_are_rejected(client):
    for body in [{'model':'other'},{'execution_profile':'other'}]:
        assert client.post('/tasks',json={'project':'test','prompt':'test',**body}).status_code==422

def test_antigravity_does_not_bypass_permissions():
    import model_policy
    cmd=runner.command(Path('/tmp/project'),'hydrogen_high')
    assert '--dangerously-skip-permissions' not in cmd
    assert '--sandbox' in cmd
    settings=json.loads(cmd[cmd.index('--settings')+1])
    assert settings['toolPermission']=='proceed-in-sandbox'
    assert 'read_url(*)' in settings['permissions']['deny']

def test_retry_keeps_execution_profile(client):
    t=client.post('/tasks',json={'project':'test','prompt':'test','execution_profile':'sol_high'}).json()
    bridge.update(t['id'],state='failed')
    r=client.post('/tasks/'+t['id']+'/retry').json()
    assert r['execution']['profile']=='sol_high'

def test_exhausted_profile_is_rejected_before_task_creation(client,monkeypatch):
    import model_policy
    monkeypatch.setattr(model_policy,'availability',lambda _: {'status':'exhausted','windows':[]})
    r=client.post('/tasks',json={'project':'test','prompt':'test'})
    assert r.status_code==409 and r.json()['detail']['code']=='model_quota_exhausted'

def test_unknown_profile_availability_stops_execution(client,monkeypatch):
    import model_policy
    monkeypatch.setattr(model_policy,'availability',lambda _: {'status':'unknown','windows':[]})
    assert client.post('/tasks',json={'project':'test','prompt':'test'}).status_code==409

def test_unverified_antigravity_executor_cannot_be_enabled_by_remaining_quota(client,monkeypatch):
    import model_policy
    monkeypatch.setattr(model_policy,'availability',lambda _: {'status':'available','windows':[],'execution_ready':False})
    r=client.post('/tasks',json={'project':'test','prompt':'test','execution_profile':'hydrogen_high'})
    assert r.status_code==409 and r.json()['detail']['code']=='model_execution_not_ready'

def test_idempotent_replay_does_not_require_remaining_quota(client,monkeypatch):
    import model_policy
    body={'project':'test','prompt':'test'}
    first=client.post('/tasks',json=body,headers={'Idempotency-Key':'quota-replay'}).json()
    monkeypatch.setattr(model_policy,'availability',lambda _: {'status':'exhausted','windows':[]})
    second=client.post('/tasks',json=body,headers={'Idempotency-Key':'quota-replay'})
    assert second.status_code==200 and second.json()['id']==first['id']

def test_quota_loss_before_launch_waits_without_execution(client,monkeypatch):
    import model_policy
    monkeypatch.setenv('BRIDGE_AUTORUN_SANDBOX','1')
    first=client.post('/tasks',json={'project':'test','prompt':'test'}).json()
    monkeypatch.setattr(model_policy,'availability',lambda _: {'status':'exhausted','windows':[]})
    bridge.dispatch_once()
    t=bridge.task(first['id'])
    assert t['state']=='queued' and t['error']=='model_quota_exhausted'
    monkeypatch.setattr(model_policy,'availability',lambda _: {'status':'available','windows':[]})
    monkeypatch.setattr(bridge,'orca',lambda *a,**k:{'handle':'owned'})
    bridge.dispatch_once()
    t=bridge.task(first['id'])
    assert t['state']=='launching' and t['error'] is None
