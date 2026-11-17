"""No model calls: exercise the installed Codex/macOS sandbox with harmless fixtures."""
import json
from pathlib import Path
import shutil
import socket
import subprocess
import pytest
import runner

@pytest.mark.skipif(not shutil.which('codex'),reason='Codex CLI required')
def test_os_denies_outside_read_write_and_network(tmp_path):
    project=tmp_path/'project'
    project.mkdir()
    (project/'.env').write_text('fixture-secret')
    canary=tmp_path/'private-canary'
    canary.write_text('harmless-fixture')
    cmd=runner.command(project)
    flags=[value for i,arg in enumerate(cmd) if arg=='-c' for value in cmd[i:i+2]]
    # The fixture root is outside project; only system Python and project are readable.
    listener=socket.socket()
    listener.bind(('127.0.0.1',0))
    listener.listen()
    code='''import pathlib,socket,sys
root=pathlib.Path(sys.argv[1]); private=pathlib.Path(sys.argv[2])
(root/'inside.txt').write_text('allowed')
assert (root/'inside.txt').read_text()=='allowed'
try: (root/'.env').read_text()
except PermissionError: pass
else: raise AssertionError('project secret read permitted')
try: private.read_text()
except PermissionError: pass
else: raise AssertionError('outside read permitted')
try: private.with_suffix('.write').write_text('denied')
except PermissionError: pass
else: raise AssertionError('outside write permitted')
s=socket.socket(); s.settimeout(1)
try: s.connect(('127.0.0.1',int(sys.argv[3])))
except OSError: pass
else: raise AssertionError('network connection permitted')
print('sandbox checks passed')
'''
    p=subprocess.run([cmd[0],'sandbox','-P','bridge','-C',str(project),*flags,'/usr/bin/python3','-B','-c',code,str(project),str(canary),str(listener.getsockname()[1])],capture_output=True,text=True,timeout=20)
    listener.close()
    assert p.returncode==0,p.stderr
    assert 'sandbox checks passed' in p.stdout
