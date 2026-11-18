import json
import sys

import bridge
import runner
from test_bridge import client


def test_orca_consults_and_muse_continues_same_project(client, monkeypatch):
    monkeypatch.setenv('BRIDGE_AUTORUN_SANDBOX', '1')
    monkeypatch.setattr(bridge, 'orca', lambda *a, **kw: {'handle': 'fixture'})
    root = client.post('/tasks', json={'project': 'test', 'prompt': 'Build a booking app'}).json()
    bridge.dispatch_once()
    events = [
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'Checking the design'}},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': json.dumps({
            'outcome': 'needs_decision', 'summary': 'Should bookings expire?',
        })}},
        {'type': 'turn.completed'},
    ]
    script = f"import sys; sys.stdin.read(); print({chr(10).join(map(json.dumps, events))!r})"
    monkeypatch.setattr(runner, 'command', lambda *args: [sys.executable, '-c', script])
    runner.run(root['id'])
    result = client.get(f"/tasks/{root['id']}/result").json()
    assert result['state'] == 'succeeded'
    assert result['handoff'] == {'outcome': 'needs_decision', 'summary': 'Should bookings expire?'}
    url = '/tasks'
    payload = {'project': 'test', 'prompt': 'Expire after 24 hours', 'continues_task': root['id']}
    headers = {'Idempotency-Key': 'answer-1'}
    child = client.post(url, json=payload, headers=headers)
    assert child.status_code == 200, child.text
    assert child.json()['continues_task'] == root['id']
    assert client.post(url, json=payload, headers=headers).json()['id'] == child.json()['id']
    assert client.post(url, json=payload).status_code == 409
    context = runner.task_prompt(bridge.task(child.json()['id']))
    assert all(s in context for s in ('Build a booking app', 'Should bookings expire?', 'Expire after 24 hours'))
    bridge.dispatch_once()
    assert bridge.task(child.json()['id'])['state'] == 'launching'


def test_critical_handoff_cannot_be_continued_as_development(client):
    root = client.post('/tasks', json={'project': 'test', 'prompt': 'Plan notification support'}).json()
    bridge.update(root['id'], state='succeeded', handoff=json.dumps({
        'outcome': 'needs_approval', 'summary': 'Paid delivery requires $20/mo', 'operation': 'spending',
    }))
    r = client.post('/tasks', json={'project': 'test', 'prompt': 'I approve the charge', 'continues_task': root['id']})
    assert r.status_code == 409
    assert client.get(f"/tasks/{root['id']}/result").json()['handoff']['operation'] == 'spending'


def test_bridge_rejects_cross_project_and_unsettled_continuation(client, tmp_path):
    other = tmp_path / 'other'
    other.mkdir()
    bridge.PROJECTS['other'] = other
    root = client.post('/tasks', json={'project': 'test', 'prompt': 'Start'}).json()
    payload = {'project': 'test', 'prompt': 'Continue', 'continues_task': root['id']}
    assert client.post('/tasks', json=payload).status_code == 409
    bridge.update(root['id'], state='succeeded')
    assert client.post('/tasks', json={**payload, 'project': 'other'}).status_code == 409
