import json
import pytest
import tunnel

def test_ngrok_command_disables_inspection():
    cmd=tunnel.command('ngrok')
    assert '--inspect=false' in cmd
    assert 'http://127.0.0.1:8765' in cmd
    assert not any('authtoken' in arg for arg in cmd)

def test_endpoint_preserves_distribution_files(tmp_path,monkeypatch):
    monkeypatch.setattr(tunnel,'ROOT',tmp_path)
    (tmp_path/'connector').mkdir()
    (tmp_path/'connector/connection.example.json').write_text(json.dumps({'base_url':'https://old.example','openapi_url':'https://old.example/openapi.json'}))
    (tmp_path/'connector/openapi.json').write_text(json.dumps({'servers':[{'url':'https://old.example'}]}))
    (tmp_path/'connector/muse-prompt.txt').write_text('Endpoint https://old.example\nSchema https://old.example/openapi.json')
    before={p.name:p.read_bytes() for p in (tmp_path/'connector').iterdir()}
    tunnel.save_endpoint('ngrok','https://tunnel.example.invalid',123)
    assert {p.name:p.read_bytes() for p in (tmp_path/'connector').iterdir()}==before
    data=json.loads((tmp_path/'state/connector-endpoint.json').read_text())
    assert data['base_url']=='https://tunnel.example.invalid'
    assert data['default_headers']['ngrok-skip-browser-warning']=='1'
    assert data['openapi_url']=='https://tunnel.example.invalid/openapi.json'

def test_reject_invalid_provider():
    with pytest.raises(ValueError): tunnel.command('shell')


def test_endpoint_without_prompt(tmp_path,monkeypatch,caplog):
    caplog.set_level('INFO',logger='tunnel')
    monkeypatch.setattr(tunnel,'ROOT',tmp_path)
    (tmp_path/'connector').mkdir()
    (tmp_path/'connector/connection.example.json').write_text('{}')
    tunnel.save_endpoint('ngrok','https://tunnel.example.invalid',123)
    assert (tmp_path/'state/connector-endpoint.json').is_file()
    assert 'skipping prompt update' in caplog.text
