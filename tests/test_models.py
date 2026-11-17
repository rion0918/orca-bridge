import json
from pathlib import Path
import pytest
import bridge,runner
from test_bridge import client

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
