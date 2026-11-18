from test_bridge import client


def test_automatic_routing_and_live_operations_share_one_api(client, monkeypatch):
    monkeypatch.setenv('BRIDGE_AGENTS', 'codex')
    task = client.post('/tasks', json={
        'project': 'test', 'prompt': 'Review the design', 'work_kind': 'design',
    })
    assert task.status_code == 200
    assert task.json()['execution']['profile'] == 'sol_high'

    schema = client.get('/openapi.json').json()['paths']
    assert '/projects/{project}/preview' in schema
    assert '/publication-requests' in schema
    assert '/publication-approvals/{id}' not in schema
    monkeypatch.delenv('BRIDGE_PREVIEW_PROJECTS', raising=False)
    assert client.post('/projects/test/preview').status_code == 403
    assert client.get('/publication-approvals/unknown').status_code == 401
