"""Dependency-free checks of actual maintenance functions extracted from bridge.py.

Run: python3 -B tests/check_project_operations.py
This does not import FastAPI or replace the HTTP/pytest suite.
"""
import ast
import contextlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
NAMES={
    'connection','transaction','project_path','project_io','project_file','example_content',
    'rename_environment_example','get_environment_example','put_environment_example',
    'local_git','commit_project','cleanup_tree','cleanup_project','python_environment',
}
class HTTPException(Exception):
    def __init__(self,status_code,detail):
        self.status_code=status_code
        self.detail=detail
        super().__init__(detail)

def extracted():
    tree=ast.parse((ROOT/'bridge.py').read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
    if {n.name for n in nodes}!=NAMES:
        raise AssertionError('Maintenance implementation is missing')
    for n in nodes: n.decorator_list=[d for d in n.decorator_list if not isinstance(d,ast.Call)]
    namespace=dict(globals(),HTTPException=HTTPException,CommitRequest=SimpleNamespace,
                   CleanupRequest=SimpleNamespace,EnvironmentExampleRequest=SimpleNamespace,
                   ACTIVE=('launching','launch_unknown','running','cancel_requested'),
                   GENERATED_CACHES={'__pycache__','.pytest_cache','.mypy_cache','.ruff_cache'})
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'bridge.py','exec'),namespace)
    return namespace

class ProjectOperations(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp=Path(tempfile.mkdtemp(dir=ROOT/'.tmp'))
        self.addCleanup(self.remove_fixture_files)
        self.root=self.temp/'project'
        self.root.mkdir()
        self.ns=extracted()
        self.ns.update(PROJECTS={'test':self.root},DATA=self.temp/'state')
    def remove_fixture_files(self):
        # Directory removal is denied in the task environment; do not retry it.
        for directory,dirs,files in os.walk(self.temp,followlinks=False):
            for name in files: (Path(directory)/name).unlink()
            for name in dirs:
                target=Path(directory)/name
                if target.is_symlink(): target.unlink()
    def call(self,name,*args,**kwargs): return self.ns[name](*args,**kwargs)
    def error(self,code,name,*args):
        with self.assertRaises(HTTPException) as caught: self.call(name,*args)
        self.assertEqual(caught.exception.status_code,code)
    def cleanup(self,paths,**kw):
        return self.call('cleanup_project','test',SimpleNamespace(paths=paths,recursive=kw.get('recursive',False),dry_run=kw.get('dry_run',False),empty_tree=kw.get('empty_tree',False)))
    def test_contract_routes_and_static_openapi(self):
        tree=ast.parse((ROOT/'bridge.py').read_text())
        routes=set()
        for n in tree.body:
            if not isinstance(n,ast.FunctionDef): continue
            for d in n.decorator_list:
                if not isinstance(d,ast.Call) or not isinstance(d.func,ast.Attribute): continue
                if not isinstance(d.func.value,ast.Name) or d.func.value.id!='app': continue
                if any(k.arg=='include_in_schema' and isinstance(k.value,ast.Constant) and k.value.value is False for k in d.keywords): continue
                routes.add((d.func.attr,d.args[0].value))
        spec=json.loads((ROOT/'connector/openapi.json').read_text())
        operations={(m,p) for p,item in spec['paths'].items() for m in item if m in {'get','post','put','delete','patch'}}
        self.assertEqual(len(routes),18)
        self.assertEqual(routes,operations)
        self.assertEqual(spec['info']['version'],'0.5.3')
        self.assertEqual(spec['security'],[{'BearerAuth':[]}])
    def test_redaction(self):
        text='BRIDGE_TOKEN=write-value\nBRIDGE_READ_TOKEN="read-value"\nOTHER=keep\n export BRIDGE_TOKEN = duplicate\r\n'
        self.assertEqual(self.call('example_content',text),'BRIDGE_TOKEN=<redacted>\nBRIDGE_READ_TOKEN=<redacted>\nOTHER=keep\n export BRIDGE_TOKEN =<redacted>\r\n')
        quoted='BRIDGE_TOKEN="first\nsecond"\nOTHER=keep\n'
        self.assertEqual(self.call('example_content',quoted),'BRIDGE_TOKEN=<redacted>\nOTHER=keep\n')
    def test_escaped_and_unterminated_multiline_tokens(self):
        for content in ('BRIDGE_TOKEN="first\\\\\\nsecond"\\nOTHER=keep\\n', 'BRIDGE_TOKEN="first\\nsecond'):
            result=self.call('example_content',content)
            self.assertNotIn('first',result)
            self.assertNotIn('second',result)
            self.assertIn('<redacted>',result)
    def test_template_read_write_and_rename(self):
        target=self.root/'.env.example'
        content='BRIDGE_TOKEN=private\r\nOTHER=keep\r\n'
        original=self.ns['project_file']
        # No real .env* metadata or contents are accessed.
        self.ns['project_file']=lambda root,name,**kw: target if name=='.env.example' else original(root,name,**kw)
        with patch.object(Path,'read_bytes',return_value=content.encode('utf-8')) as read:
            result=self.call('get_environment_example','test')
            read.assert_called_once_with()
            self.assertNotIn('private',result['content'])
        with patch.object(Path,'write_bytes',return_value=len(content)) as write:
            result=self.call('put_environment_example','test',SimpleNamespace(content=content))
            write.assert_called_once_with(content.encode('utf-8'))
            self.assertNotIn('private',result['content'])
        (self.root/'ENVIRONMENT.example').write_text('template')
        with patch.object(Path,'exists',return_value=False), patch.object(Path,'rename') as rename:
            self.assertEqual(self.call('rename_environment_example','test')['renamed_to'],'.env.example')
            rename.assert_called_once_with(target)
        with patch.object(Path,'exists',return_value=True), patch.object(Path,'rename') as rename:
            self.error(409,'rename_environment_example','test')
            rename.assert_not_called()
    def test_path_boundary_and_symlinks(self):
        for path in ('','.', '..', '../escape','/absolute','.git/config','.env','.aws/credentials','.codex/config.toml','key.pem'):
            self.error(422,'project_file',self.root,path)
        with patch.object(Path,'is_symlink',return_value=False), patch.object(Path,'resolve',lambda path: path):
            self.assertEqual(self.call('project_file',self.root,'.env.example'),self.root/'.env.example')
        (self.root/'real').mkdir()
        (self.root/'link').symlink_to(self.root/'real',target_is_directory=True)
        self.error(422,'project_file',self.root,'link/file')
    def test_cleanup_dry_run_and_recursive(self):
        cache=self.root/'__pycache__'
        cache.mkdir()
        (cache/'file.pyc').write_text('cache')
        self.assertTrue(self.cleanup(['__pycache__'],recursive=True,dry_run=True)['dry_run'])
        self.assertTrue((cache/'file.pyc').exists())
        with patch.object(shutil,'rmtree') as remove:
            self.cleanup(['__pycache__'],recursive=True)
            remove.assert_called_once_with(cache)
    def test_cleanup_rejects_noncache_before_any_deletion(self):
        (self.root/'first').write_text('keep')
        (self.root/'source').mkdir()
        (self.root/'source/file').write_text('keep')
        with self.assertRaises(HTTPException) as caught: self.cleanup(['first','source'],recursive=True)
        self.assertEqual(caught.exception.status_code,422)
        self.assertTrue((self.root/'first').exists())
        with self.assertRaises(HTTPException) as caught: self.cleanup(['source'])
        self.assertEqual(caught.exception.status_code,409)
    def test_cleanup_empty_tree(self):
        tree=self.root/'empty'
        (tree/'nested/leaf').mkdir(parents=True)
        self.cleanup(['empty'],empty_tree=True,dry_run=True)
        self.assertTrue(tree.exists())
        with patch.object(Path,'rmdir',autospec=True) as remove:
            self.cleanup(['empty'],empty_tree=True)
            self.assertEqual([call.args[0] for call in remove.call_args_list],[tree/'nested/leaf',tree/'nested',tree])
        (tree/'nested/file').write_text('keep')
        with self.assertRaises(HTTPException) as caught: self.cleanup(['empty'],empty_tree=True)
        self.assertEqual(caught.exception.status_code,409)
        self.assertTrue((tree/'nested/file').exists())
    def test_cleanup_rejects_links_in_cache(self):
        cache=self.root/'.pytest_cache'
        cache.mkdir()
        (self.root/'real').mkdir()
        (cache/'link').symlink_to(self.root/'real',target_is_directory=True)
        with self.assertRaises(HTTPException) as caught: self.cleanup(['.pytest_cache'],recursive=True,dry_run=True)
        self.assertEqual(caught.exception.status_code,422)
    def fake_git(self,stop=None):
        calls=[]
        def run(command,**kwargs):
            calls.append((command,kwargs))
            args=command[command.index('-C')+2:]
            code,output=0,''
            if args==['rev-parse','--show-toplevel']: output=str(self.root)+'\n'
            if args[:2]==['config','--get']:
                code,output=(1,'') if args[-1]==stop else (0,'fixture\n')
            if args[:2]==['diff','--cached']: code=1 if stop=='staged' else 128 if stop=='git-error' else 0
            if args==['rev-parse','--verify','HEAD']: output='a'*40+'\n'
            return SimpleNamespace(returncode=code,stdout=output,stderr='private diagnostic')
        return calls,run
    def test_commit_stop_conditions(self):
        (self.root/'file').write_text('change')
        for stop in ('user.name','user.email','staged','git-error'):
            calls,run=self.fake_git(stop)
            with patch.object(subprocess,'run',side_effect=run):
                self.error(409,'commit_project','test',SimpleNamespace(paths=['file'],message='change'))
            self.assertFalse(any('add' in c or 'commit' in c for c,k in calls))
    def test_commit_local_only_and_literal_paths(self):
        (self.root/'chosen[1]').write_text('change')
        calls,run=self.fake_git()
        with patch.object(subprocess,'run',side_effect=run):
            result=self.call('commit_project','test',SimpleNamespace(paths=['chosen[1]','chosen[1]'],message='local'))
        self.assertEqual(result,{'project':'test','commit':'a'*40,'files':1,'published':False})
        self.assertFalse(any('push' in c or 'fetch' in c for c,k in calls))
        for c,k in calls:
            self.assertIn('--literal-pathspecs',c)
            self.assertIn('core.hooksPath=/dev/null',c)
        add=next(c for c,k in calls if 'add' in c)
        self.assertEqual(add[add.index('--')+1:],['chosen[1]'])
        commit=next(c for c,k in calls if 'commit' in c)
        self.assertIn('--only',commit)
    def test_python_offline(self):
        def run(command,**kw):
            self.assertEqual(command,[sys.executable,'-m','venv',str(self.root/'.venv')])
            self.assertEqual(kw['env']['PIP_NO_INDEX'],'1')
            self.assertFalse(any(k.startswith('BRIDGE_') for k in kw['env']))
            (self.root/'.venv/bin').mkdir(parents=True)
            (self.root/'.venv/bin/python').write_text('fixture')
            (self.root/'.venv/pyvenv.cfg').write_text('fixture')
            return SimpleNamespace(returncode=0)
        with patch.object(subprocess,'run',side_effect=run) as invoked:
            result=self.call('python_environment','test')
            self.assertEqual(invoked.call_count,1)
        self.assertEqual(result,{'project':'test','python':str(self.root/'.venv/bin/python'),'offline':True,'tests_executed':False})
        with patch.object(subprocess,'run',side_effect=AssertionError('must reuse')):
            self.assertEqual(self.call('python_environment','test'),result)
    def test_python_active_task_conflict(self):
        for state in self.ns['ACTIVE']:
            with self.call('transaction') as c:
                c.execute('delete from tasks')
                c.execute('insert into tasks(id,project,state) values(?,?,?)',('active','other',state))
            with patch.object(subprocess,'run',side_effect=AssertionError('must wait')):
                self.error(409,'python_environment','test')
    def test_python_failure(self):
        with patch.object(subprocess,'run',side_effect=subprocess.TimeoutExpired('venv',60)):
            self.error(409,'python_environment','test')

if __name__=='__main__': unittest.main(verbosity=2)
