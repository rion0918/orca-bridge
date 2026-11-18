"""Provision only trusted Bridge test dependencies from the local uv cache."""
import os
import subprocess
import sys
import shutil
import bridge

def prepare(project):
 root=bridge.project_path(project);venv=root/'.venv'
 if venv.is_symlink() or (venv.exists() and venv.resolve()!=venv.absolute()):
  raise bridge.HTTPException(409,'Unsafe environment location')
 uv=shutil.which('uv')
 if not uv:raise bridge.HTTPException(409,'uv is not installed on the Mac')
 if venv.exists():
  raise bridge.HTTPException(409,'Existing .venv requires local review; refusing to execute a project-supplied interpreter')
 python=venv/'bin/python'
 commands=[
  [uv,'--no-config','venv','--offline','--no-project','--python',sys.executable,str(venv)],
  [uv,'--no-config','pip','install','--offline','--no-build','--python',str(python),'-r',str(bridge.ROOT/'requirements.lock')],
 ]
 env={key:os.environ[key] for key in ('HOME','PATH','LANG','TMPDIR') if key in os.environ}
 for cmd in commands:
  try:r=subprocess.run(cmd,cwd=root,capture_output=True,text=True,timeout=60,env=env)
  except (OSError,subprocess.TimeoutExpired):raise bridge.HTTPException(409,'Environment preparation incomplete; inspect before retrying')
  if r.returncode:raise bridge.HTTPException(409,'Offline preparation failed; dependencies may be absent from the local cache. No network fallback performed.')
  if (venv.resolve()!=venv or (venv/'bin').is_symlink() or (venv/'pyvenv.cfg').is_symlink()
      or not python.is_file() or not (venv/'pyvenv.cfg').is_file()):
   raise bridge.HTTPException(409,'Environment preparation incomplete; inspect before retrying')
 bridge.audit('python_environment_prepared',project)
 return {'project':project,'python':str(venv/'bin/python'),'offline':True,'tests_executed':False}
