"""Project maintenance contracts: fixtures and mocked local commands, no network."""
import subprocess
import contextlib
from types import SimpleNamespace

import pytest
import bridge
from test_bridge import client


@pytest.fixture
def project(client):
    return bridge.PROJECTS['test']


@pytest.fixture(autouse=True)
def template_io(client, project, monkeypatch):
    original_file = bridge.project_file
    original_exists = bridge.Path.exists
    original_read = bridge.Path.read_bytes
    target = project/'.env.example'
    def checked(root, name, **kwargs):
        return target if root == project and name == '.env.example' and kwargs.get('template') else original_file(root, name, **kwargs)
    def read(path, *a, **kw):
        if path == target: raise FileNotFoundError('mock template missing')
        return original_read(path, *a, **kw)
    monkeypatch.setattr(bridge, 'project_file', checked)
    monkeypatch.setattr(bridge.Path, 'read_bytes', read)
    monkeypatch.setattr(bridge.Path, 'exists', lambda path: False if path == target else original_exists(path))


def test_environment_example_round_trip(client, project, monkeypatch):
    # Template IO is mocked to respect environments that deny all .env* IO.
    content = ['BRIDGE_TOKEN=private-write\nBRIDGE_READ_TOKEN="private-read"\nOTHER=keep\n']
    original_read = bridge.Path.read_bytes
    original_write = bridge.Path.write_bytes
    def read(path, *a, **kw):
        return content[0].encode('utf-8') if path == project/'.env.example' else original_read(path, *a, **kw)
    def write(path, text, *a, **kw):
        if path == project/'.env.example':
            content[0] = text.decode('utf-8')
            return len(text)
        return original_write(path, text, *a, **kw)
    monkeypatch.setattr(bridge.Path, 'read_bytes', read)
    monkeypatch.setattr(bridge.Path, 'write_bytes', write)
    response = client.get('/projects/test/environment-example')
    assert response.status_code == 200
    assert response.json() == {'project': 'test', 'content': 'BRIDGE_TOKEN=<redacted>\nBRIDGE_READ_TOKEN=<redacted>\nOTHER=keep\n'}
    replacement = ' export BRIDGE_TOKEN = new-private\r\nBRIDGE_READ_TOKEN=second-private\nOTHER=new\n'
    response = client.put('/projects/test/environment-example', json={'content': replacement})
    assert response.status_code == 200 and content[0] == replacement
    assert response.json() == client.get('/projects/test/environment-example').json()
    assert 'new-private' not in response.text and 'second-private' not in response.text


def test_environment_example_rename(client, project, monkeypatch):
    (project/'ENVIRONMENT.example').write_text('template')
    calls = []
    monkeypatch.setattr(bridge.Path, 'rename', lambda source, target: calls.append((source, target)))
    response = client.post('/projects/test/environment-example')
    assert response.status_code == 200
    assert response.json() == {'project': 'test', 'renamed_to': '.env.example'}
    assert calls == [(project/'ENVIRONMENT.example', project/'.env.example')]


def test_environment_example_missing_and_existing_destination(client, project, monkeypatch):
    assert client.get('/projects/test/environment-example').status_code == 404
    assert client.post('/projects/test/environment-example').status_code == 404
    (project/'ENVIRONMENT.example').write_text('template')
    original = bridge.Path.exists
    monkeypatch.setattr(bridge.Path, 'exists', lambda path: True if path == project/'.env.example' else original(path))
    assert client.post('/projects/test/environment-example').status_code == 409
    assert (project/'ENVIRONMENT.example').read_text() == 'template'


@pytest.fixture
def git_calls(monkeypatch, project):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        args = command[command.index('-C')+2:]
        output = ''
        if args == ['rev-parse', '--show-toplevel']: output = str(project)+'\n'
        if args[:2] == ['config', '--get']: output = 'fixture\n'
        if args == ['rev-parse', '--verify', 'HEAD']: output = 'a'*40+'\n'
        return SimpleNamespace(returncode=0, stdout=output, stderr='')
    monkeypatch.setattr(bridge.subprocess, 'run', run)
    return calls


def test_commit_only_requested_existing_files(client, project, git_calls):
    (project/'chosen.txt').write_text('chosen')
    (project/'unrelated.txt').write_text('unrelated')
    response = client.post('/projects/test/commit', json={'paths': ['chosen.txt'], 'message': 'local change'})
    assert response.status_code == 200
    assert response.json() == {'project': 'test', 'commit': 'a'*40, 'files': 1, 'published': False}
    commands = [cmd for cmd, kw in git_calls]
    add = next(cmd for cmd in commands if 'add' in cmd)
    commit = next(cmd for cmd in commands if 'commit' in cmd)
    assert add[add.index('--')+1:] == ['chosen.txt']
    assert commit[commit.index('--')+1:] == ['chosen.txt']
    assert '--only' in commit and '--literal-pathspecs' in add
    assert not any('push' in cmd or 'fetch' in cmd for cmd in commands)
    assert all('core.hooksPath=/dev/null' in cmd for cmd in commands)


@pytest.mark.parametrize('stop', ['user.name', 'user.email', 'staged', 'git-error'])
def test_commit_stops_before_staging(client, project, monkeypatch, stop):
    (project/'file').write_text('data')
    calls = []
    def run(command, **kwargs):
        calls.append(command)
        args = command[command.index('-C')+2:]
        code, output = 0, ''
        if args == ['rev-parse', '--show-toplevel']: output = str(project)+'\n'
        if args[:2] == ['config', '--get']:
            code, output = (1, '') if args[-1] == stop else (0, 'fixture\n')
        if args[:2] == ['diff', '--cached']:
            code = 1 if stop == 'staged' else 128 if stop == 'git-error' else 0
        return SimpleNamespace(returncode=code, stdout=output, stderr='private diagnostic')
    monkeypatch.setattr(bridge.subprocess, 'run', run)
    response = client.post('/projects/test/commit', json={'paths': ['file'], 'message': 'change'})
    assert response.status_code == 409
    assert 'private diagnostic' not in response.text
    assert not any('add' in cmd or 'commit' in cmd for cmd in calls)


@pytest.mark.parametrize('path', ['missing', '.', '../escape', '/absolute', '.git/config', '.env', '.env.example'])
def test_commit_rejects_invalid_paths(client, path, git_calls):
    response = client.post('/projects/test/commit', json={'paths': [path], 'message': 'change'})
    assert response.status_code in (404, 422)
    assert not git_calls


@pytest.mark.parametrize('body', [{}, {'paths': ['x']}, {'message': 'x'}, {'paths': [], 'message': 'x'}, {'paths': ['x'], 'message': '  '}])
def test_commit_requires_paths_and_message(client, body):
    assert client.post('/projects/test/commit', json=body).status_code == 422


def test_cleanup_dry_run_and_cache_removal(client, project, cleanup_io_errors):
    cache = project/'__pycache__'
    cache.mkdir()
    (cache/'generated.pyc').write_bytes(b'cache')
    body = {'paths': ['__pycache__'], 'recursive': True, 'dry_run': True}
    response = client.post('/projects/test/cleanup', json=body)
    assert response.status_code == 200
    assert response.json() == {'project': 'test', 'paths': ['__pycache__'], 'dry_run': True}
    assert (cache/'generated.pyc').exists()
    assert client.post('/projects/test/cleanup', json={**body, 'dry_run': False}).status_code == 200, cleanup_io_errors
    assert not cache.exists()


def test_cleanup_preflights_all_paths(client, project):
    (project/'keep').write_text('keep')
    (project/'source').mkdir()
    (project/'source/file').write_text('source')
    response = client.post('/projects/test/cleanup', json={'paths': ['keep', 'source'], 'recursive': True})
    assert response.status_code == 422
    assert (project/'keep').exists() and (project/'source/file').exists()
    assert client.post('/projects/test/cleanup', json={'paths': ['source']}).status_code == 409


@pytest.mark.parametrize('path', ['.', '../escape', '/absolute', '.git', '.env', '.aws', '.codex'])
def test_cleanup_rejects_unsafe_paths_even_in_dry_run(client, path):
    assert client.post('/projects/test/cleanup', json={'paths': [path], 'recursive': True, 'dry_run': True}).status_code == 422


def test_cleanup_empty_tree(client, project, cleanup_io_errors):
    tree = project/'empty'
    (tree/'nested/leaf').mkdir(parents=True)
    body = {'paths': ['empty'], 'empty_tree': True}
    assert client.post('/projects/test/cleanup', json={**body, 'dry_run': True}).status_code == 200
    assert tree.exists()
    assert client.post('/projects/test/cleanup', json=body).status_code == 200, cleanup_io_errors
    assert not tree.exists()
    (tree/'nested').mkdir(parents=True)
    (tree/'nested/file').write_text('keep')
    assert client.post('/projects/test/cleanup', json=body).status_code == 409
    assert (tree/'nested/file').read_text() == 'keep'


def test_cleanup_file_and_empty_directory(client, project, cleanup_io_errors):
    (project/'file').write_text('remove')
    (project/'empty').mkdir()
    assert client.post('/projects/test/cleanup', json={'paths': ['file', 'empty']}).status_code == 200, cleanup_io_errors
    assert not (project/'file').exists() and not (project/'empty').exists()


def test_symlinks_rejected(client, project):
    (project/'real').mkdir()
    (project/'real/file').write_text('keep')
    (project/'link').symlink_to(project/'real', target_is_directory=True)
    assert client.post('/projects/test/cleanup', json={'paths': ['link'], 'recursive': True}).status_code == 422
    assert client.post('/projects/test/commit', json={'paths': ['link/file'], 'message': 'x'}).status_code == 422
    cache = project/'__pycache__'
    cache.mkdir()
    (cache/'link').symlink_to(project/'real', target_is_directory=True)
    assert client.post('/projects/test/cleanup', json={'paths': ['__pycache__'], 'recursive': True}).status_code == 422
    assert (project/'real/file').exists()


@pytest.mark.parametrize('method,suffix,body', [('post', 'commit', {'paths': ['x'], 'message': 'x'}), ('post', 'cleanup', {'paths': ['x']}), ('post', 'environment-example', None), ('put', 'environment-example', {'content': 'x'}), ('post', 'python-environment', None)])
def test_read_token_cannot_maintain_project(client, monkeypatch, method, suffix, body):
    monkeypatch.setenv('BRIDGE_READ_TOKEN', 'reader')
    response = getattr(client, method)('/projects/test/'+suffix, json=body, headers={'Authorization': 'Bearer reader'})
    assert response.status_code == 403


def test_python_environment_offline(client, project, monkeypatch):
    calls = []
    def run(command, **kwargs):
        calls.append((command, kwargs))
        (project/'.venv/bin').mkdir(parents=True)
        (project/'.venv/bin/python').write_text('fixture')
        (project/'.venv/pyvenv.cfg').write_text('fixture')
        return SimpleNamespace(returncode=0, stdout='', stderr='')
    monkeypatch.setattr(bridge.subprocess, 'run', run)
    response = client.post('/projects/test/python-environment')
    assert response.status_code == 200
    assert response.json() == {'project': 'test', 'python': str(project/'.venv/bin/python'), 'offline': True, 'tests_executed': False}
    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command == [bridge.sys.executable, '-m', 'venv', str(project/'.venv')]
    assert kwargs['env']['PIP_NO_INDEX'] == '1'
    assert not any(key.startswith('BRIDGE_') for key in kwargs['env'])


@pytest.mark.parametrize('state', bridge.ACTIVE)
def test_python_environment_waits_for_tasks(client, project, monkeypatch, state):
    t = client.post('/tasks', json={'prompt': 'work', 'project': 'test'}).json()
    bridge.update(t['id'], state=state)
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **kw: pytest.fail('Must not prepare while active'))
    assert client.post('/projects/test/python-environment').status_code == 409


def test_python_environment_failure(client, project, monkeypatch):
    def fail(*a, **kw):
        raise subprocess.TimeoutExpired('venv', 60)
    monkeypatch.setattr(bridge.subprocess, 'run', fail)
    assert client.post('/projects/test/python-environment').status_code == 409


def test_environment_example_read_token(client, monkeypatch):
    monkeypatch.setenv('BRIDGE_READ_TOKEN', 'reader')
    monkeypatch.setattr(bridge.Path, 'read_bytes', lambda path: b'BRIDGE_TOKEN=private\n')
    response = client.get('/projects/test/environment-example', headers={'Authorization': 'Bearer reader'})
    assert response.status_code == 200
    assert response.json()['content'] == 'BRIDGE_TOKEN=<redacted>\n'


@pytest.mark.parametrize('content', [
    'BRIDGE_TOKEN="first\nsecond"\nOTHER=keep\n',
    "BRIDGE_READ_TOKEN='first\nsecond'\nOTHER=keep\n",
    'BRIDGE_TOKEN="first\\\nsecond"\nOTHER=keep\n',
    'BRIDGE_TOKEN="first\nsecond',
])
def test_environment_example_multiline_token_values(content):
    result = bridge.example_content(content)
    assert 'first' not in result and 'second' not in result
    assert '<redacted>' in result


def test_python_environment_reuses_local_environment(client, project, monkeypatch):
    (project/'.venv/bin').mkdir(parents=True)
    (project/'.venv/bin/python').write_text('fixture')
    (project/'.venv/pyvenv.cfg').write_text('fixture')
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **kw: pytest.fail('Must reuse local environment'))
    assert client.post('/projects/test/python-environment').status_code == 200


@pytest.mark.parametrize('path', ['.venv', '.venv/bin', '.venv/pyvenv.cfg'])
def test_python_environment_rejects_symlink_paths(client, project, monkeypatch, path):
    (project/'target').mkdir()
    link = project/path
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to(project/'target', target_is_directory=True)
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **kw: pytest.fail('Unsafe environment'))
    assert client.post('/projects/test/python-environment').status_code == 422


@pytest.mark.parametrize('result', [1, 0])
def test_python_environment_rejects_failed_or_incomplete_preparation(client, project, monkeypatch, result):
    monkeypatch.setattr(bridge.subprocess, 'run', lambda *a, **kw: SimpleNamespace(returncode=result))
    assert client.post('/projects/test/python-environment').status_code == 409


def test_commit_subprocess_failure_does_not_disclose_output(client, project, monkeypatch):
    (project/'file').write_text('change')
    def unavailable(*a, **kw):
        raise subprocess.TimeoutExpired('git', 30, output='private diagnostic')
    monkeypatch.setattr(bridge.subprocess, 'run', unavailable)
    response = client.post('/projects/test/commit', json={'paths': ['file'], 'message': 'change'})
    assert response.status_code == 409 and 'private diagnostic' not in response.text


@pytest.mark.parametrize('method,suffix,body', [
    ('get', 'environment-example', None),
    ('post', 'environment-example', None),
    ('put', 'environment-example', {'content': 'x'}),
    ('post', 'commit', {'paths': ['x'], 'message': 'x'}),
    ('post', 'cleanup', {'paths': ['x']}),
    ('post', 'python-environment', None),
])
def test_project_operations_require_authentication(client, method, suffix, body):
    kwargs = {'headers': {'Authorization': ''}}
    if method != 'get': kwargs['json'] = body
    assert getattr(client, method)('/projects/test/'+suffix, **kwargs).status_code == 401


@pytest.fixture
def cleanup_io_errors(monkeypatch):
    original = bridge.project_io
    errors = []
    @contextlib.contextmanager
    def capture():
        with original():
            try:
                yield
            except OSError as error:
                errors.append(str(error))
                raise
    monkeypatch.setattr(bridge, 'project_io', capture)
    return errors


@pytest.mark.parametrize('name,options', [
    *[(name, {'recursive': True}) for name in sorted(bridge.GENERATED_CACHES)],
    ('empty', {'empty_tree': True}),
    ('empty', {}),
])
def test_cleanup_deletion_plan(client, project, monkeypatch, name, options):
    target = project/name
    target.mkdir()
    if options.get('recursive'):
        (target/'generated.pyc').write_bytes(b'cache')
        expected = [('rmtree', target)]
    elif options.get('empty_tree'):
        (target/'nested/leaf').mkdir(parents=True)
        expected = [('rmdir', target/'nested/leaf'), ('rmdir', target/'nested'), ('rmdir', target)]
    else:
        (project/'file').write_text('remove')
        expected = [('rmdir', target), ('unlink', project/'file')]
    calls = []
    monkeypatch.setattr(bridge.shutil, 'rmtree', lambda path: calls.append(('rmtree', path)))
    monkeypatch.setattr(bridge.Path, 'rmdir', lambda path: calls.append(('rmdir', path)))
    monkeypatch.setattr(bridge.Path, 'unlink', lambda path: calls.append(('unlink', path)))
    body = {'paths': [name] if options else [name, 'file'], **options}
    response = client.post('/projects/test/cleanup', json={**body, 'dry_run': True})
    assert response.status_code == 200, response.text
    assert calls == []
    response = client.post('/projects/test/cleanup', json=body)
    assert response.status_code == 200, response.text
    assert calls == expected
