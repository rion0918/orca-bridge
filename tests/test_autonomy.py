import pytest
import bridge
from test_bridge import client

def test_development_is_queued_and_dispatched_without_local_approval(client,monkeypatch):
    monkeypatch.setenv('BRIDGE_AUTORUN_SANDBOX','1')
    calls=[]
    monkeypatch.setattr(bridge,'orca',lambda *a,**k:calls.append(a) or {'terminal':{'handle':'owned'}})
    t=client.post('/tasks',json={'project':'test','prompt':'Create a local file'}).json()
    assert t['state']=='queued'
    # Test fixture supplies the allowed project, independent of its production alias.
    bridge.dispatch_once()
    assert bridge.task(t['id'])['state']=='launching'
    assert len(calls)==1

def test_autonomy_preserves_single_execution(client,monkeypatch):
    monkeypatch.setenv('BRIDGE_AUTORUN_SANDBOX','1')
    calls=[]
    monkeypatch.setattr(bridge,'orca',lambda *a,**k:calls.append(a) or {'handle':'owned'})
    a=client.post('/tasks',json={'project':'test','prompt':'first'}).json()
    b=client.post('/tasks',json={'project':'test','prompt':'second'}).json()
    bridge.dispatch_once(); bridge.dispatch_once()
    assert len(calls)==1 and bridge.task(b['id'])['state']=='queued'
    bridge.update(a['id'],state='succeeded')
    bridge.dispatch_once()
    assert len(calls)==2

def test_critical_actions_require_human_and_are_not_dispatched(client,monkeypatch):
    monkeypatch.setenv('BRIDGE_AUTORUN_SANDBOX','1')
    for operation in ['deployment','spending']:
        r=client.post('/tasks',json={'project':'test','prompt':'perform action','operation':operation})
        assert r.status_code==409
        assert r.json()['detail']['code']=='human_approval_required'

def test_cancel_queued_task(client,monkeypatch):
    monkeypatch.setenv('BRIDGE_AUTORUN_SANDBOX','1')
    t=client.post('/tasks',json={'project':'test','prompt':'first'}).json()
    assert client.post('/tasks/'+t['id']+'/cancel').json()['state']=='cancelled'

def test_unknown_launch_stops_queue(client,monkeypatch):
    monkeypatch.setenv('BRIDGE_AUTORUN_SANDBOX','1')
    calls=[]
    def unknown(*a,**k):
        calls.append(a)
        raise bridge.CLIError('timeout',unknown=True)
    monkeypatch.setattr(bridge,'orca',unknown)
    a=client.post('/tasks',json={'project':'test','prompt':'first'}).json()
    b=client.post('/tasks',json={'project':'test','prompt':'second'}).json()
    bridge.dispatch_once(); bridge.dispatch_once()
    assert bridge.task(a['id'])['state']=='launch_unknown'
    assert bridge.task(b['id'])['state']=='queued'
    assert len(calls)==1
