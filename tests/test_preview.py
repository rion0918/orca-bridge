import time

from fastapi.testclient import TestClient

import bridge
import preview
from test_bridge import client


def test_preview_requires_local_project_opt_in(client):
    assert client.post('/projects/test/preview').status_code == 403


def test_static_preview_is_separate_and_immutable(client, monkeypatch):
    monkeypatch.setenv('BRIDGE_PREVIEW_PROJECTS', 'test')
    monkeypatch.setenv('BRIDGE_PREVIEW_ORIGIN', 'https://preview.example')
    root = bridge.PROJECTS['test']
    (root / 'index.html').write_text('<script src="app.js"></script>')
    (root / 'app.js').write_text('const app = "first";')
    (root / '.env').write_text('PRIVATE=hidden')
    (root / 'linked.js').symlink_to(root / '.env')
    response = client.post('/projects/test/preview')
    assert response.status_code == 200, response.text
    data = response.json()
    path = f"/p/{data['id']}/"
    assert data['url'] == 'https://preview.example' + path
    assert client.get(path).status_code == 404
    (root / 'app.js').write_text('const app = "changed";')
    with TestClient(preview.app) as browser:
        page = browser.get(path)
        assert page.status_code == 200
        assert 'worker-src' in page.headers['content-security-policy']
        assert "connect-src 'none'" in page.headers['content-security-policy']
        assert "form-action 'none'" in page.headers['content-security-policy']
        assert page.headers['referrer-policy'] == 'no-referrer'
        assert browser.get(path+'app.js').text == 'const app = "first";'
        assert browser.get(path+'.env').status_code == 404
        assert browser.get(path+'linked.js').status_code == 404
        assert browser.get('/status').status_code == 404
        assert client.delete('/previews/'+data['id']).status_code == 204
        assert browser.get(path).status_code == 404


def test_preview_expires(client, monkeypatch):
    monkeypatch.setenv('BRIDGE_PREVIEW_PROJECTS', 'test')
    monkeypatch.setenv('BRIDGE_PREVIEW_ORIGIN', 'https://preview.example')
    (bridge.PROJECTS['test'] / 'index.html').write_text('<p>demo</p>')
    data = client.post('/projects/test/preview').json()
    now = time.time()
    monkeypatch.setattr(preview.time,'time',lambda:now+1000)
    with TestClient(preview.app) as browser:
        assert browser.get(f"/p/{data['id']}/").status_code == 404


def test_bridge_origin_cannot_be_reused_for_preview(client, monkeypatch):
    monkeypatch.setenv('BRIDGE_PREVIEW_PROJECTS', 'test')
    monkeypatch.setenv('BRIDGE_PREVIEW_ORIGIN', 'https://bridge.example')
    monkeypatch.setenv('BRIDGE_NGROK_URL', 'https://bridge.example')
    assert client.post('/projects/test/preview').status_code == 409
