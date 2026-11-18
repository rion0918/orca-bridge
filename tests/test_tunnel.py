import json
import pytest
import tunnel

def test_ngrok_command_pins_domain_and_disables_inspection(monkeypatch):
    monkeypatch.setattr(tunnel.shutil,'which',lambda _: '/fixture/ngrok')
    cmd=tunnel.command('ngrok','https://your-domain.example')
    assert '--inspect=false' in cmd
    assert 'http://127.0.0.1:8765' in cmd
    assert cmd[cmd.index('--url')+1]=='https://your-domain.example'
    assert not any('authtoken' in arg for arg in cmd)


@pytest.mark.parametrize('url',[None,'https://','http://your-domain.example','https://user@your-domain.example',
                               'https://your-domain.example/path','https://your-domain.example?x=y',
                               'https://your-domain.example#fragment','https://your-domain.example:80'])
def test_ngrok_rejects_missing_or_invalid_fixed_url(url):
    with pytest.raises(ValueError,match='BRIDGE_NGROK_URL'):
        tunnel.command('ngrok',url)


def test_unexpected_tunnel_url_never_receives_bridge_credentials(monkeypatch):
    from types import SimpleNamespace
    proc=SimpleNamespace(pid=123,stdout=iter(['{"url":"https://other-domain.example"}\n']),
                         poll=lambda:0)
    monkeypatch.setattr(tunnel,'load',lambda:None)
    monkeypatch.setenv('BRIDGE_NGROK_URL','https://your-domain.example')
    monkeypatch.setattr(tunnel.shutil,'which',lambda _: '/fixture/ngrok')
    monkeypatch.setattr(tunnel.subprocess,'Popen',lambda *args,**kwargs:proc)
    monkeypatch.setattr(tunnel,'record',lambda *args:None)
    monkeypatch.setattr(tunnel,'verify',lambda *args:pytest.fail('Unexpected endpoint must not be verified'))
    monkeypatch.setattr('sys.argv',['tunnel.py','--publish-approved','--provider','ngrok'])
    with pytest.raises(RuntimeError,match='does not match'):
        tunnel.main()

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
