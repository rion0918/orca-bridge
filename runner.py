"""Fixed local runner invoked by an Orca terminal; never accepts command text."""
import collections
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import uuid
import bridge

def command(project,profile="sol_low"):
    import model_policy
    config=model_policy.PROFILES[profile]
    if config["agent"]=="antigravity": return model_policy.antigravity_command(project,profile)
    codex=__import__('shutil').which('codex')
    if not codex: raise RuntimeError('codex_not_installed')
    env_rules=', '.join('"'+pattern+'"="deny"' for pattern in model_policy.ENV_DENY_GLOBS)
    return [codex,'exec','--model',config['model'],'-c','model_reasoning_effort="'+config['effort']+'"','--ignore-user-config','--ignore-rules','--ephemeral',
            '--skip-git-repo-check','--cd',str(project),
            '-c','default_permissions="bridge"',
            '-c','permissions.bridge.extends=":workspace"',
            '-c','permissions.bridge.filesystem={ ":root"="deny", ":minimal"="read", ":tmpdir"="deny", ":slash_tmp"="deny", ":workspace_roots"={"."="write", ".git"="read", ".codex"="read", ".agents"="read", ".aws"="deny", ".env"="deny", '+env_rules+', "**/*.pem"="deny"} }',
            '-c','permissions.bridge.network.enabled=false',
            '-c','approval_policy="never"',
            '-c','shell_environment_policy.inherit="none"',
            '-c','shell_environment_policy.set.PATH="/usr/bin:/bin:/usr/sbin:/sbin:/opt/homebrew/bin"',
            '-c','features.apps=false','-c','web_search="disabled"',
            '-c','project_doc_max_bytes=0',
            '--json','-']

def task_prompt(task):
    context=[]
    if task['continues_task']:
        with bridge.transaction() as db:
            context=bridge.continuation_context(db,task['continues_task'],task['project'])
    brief=json.dumps({'consultation_history':context,'instruction':task['prompt']},ensure_ascii=False)
    return (
        'Work only in this project. No deployment, publishing, purchases, outbound messages, '
        'network operations, secrets, other repositories, plugins or .codex configuration. '
        'Implement and test local changes. Pause at a material unresolved product or technical decision. '
        'Use consultation history as context and the latest instruction as the answer. '
        'If an external action is needed, stop before performing it. '
        'Your final message must be one JSON object without Markdown. '
        'Include outcome (completed, needs_decision, needs_approval or blocked) and summary. '
        'For needs_decision, explain the question, options and recommendation in summary. '
        'For needs_approval also include operation (github_create, github_publish, deployment or spending). '
        'Describe scope and, for spending, the quoted amount, currency and billing interval if known. '
        'These are proposals, never authorizations. Use summary for evidence on completion.\n\n'
        +brief
    )

def run(id):
    if str(uuid.UUID(id))!=id: raise ValueError('Invalid task ID')
    t=bridge.task(id)
    if t['state'] not in ('launching','launch_unknown','cancel_requested'): return
    if t['state']=='cancel_requested':
        bridge.update(id,state='cancelled')
        return
    project=bridge.project_path(t['project'])
    bridge.update(id,state='running')
    bridge.audit('runner_started',id)
    # Do not pass Bridge tokens, cloud keys, remote Orca routing or parent agent context.
    env={k:os.environ[k] for k in ('HOME','PATH','USER','LANG','TMPDIR') if k in os.environ}
    lines=collections.deque(maxlen=300)
    final=[]
    last_message=None
    def persist_logs():
        fd=os.open(bridge.DATA/(id+'.jsonl'),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f: f.writelines(list(lines))
    failed=[]
    requested=threading.Event()
    signal.signal(signal.SIGTERM,lambda *_:requested.set())
    signal.signal(signal.SIGINT,lambda *_:requested.set())
    proc=None
    try:
        prompt=task_prompt(t)
        selected=json.loads(t['execution'] or '{}')
        profile=selected.get('profile','sol_low')
        proc=subprocess.Popen(command(project,profile),stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                              text=True,env=env,cwd=project,start_new_session=True)
        proc.stdin.write(prompt)
        proc.stdin.close()
        def read():
            nonlocal last_message
            for line in iter(proc.stdout.readline,''):
                safe=bridge.redact(line[:16384])
                lines.append(safe)
                try:
                    event=json.loads(line)
                    if event.get('type')=='turn.completed' and event.get('usage'):
                        selected['usage']=event['usage']
                    item=event.get('item',{})
                    if item.get('type')=='agent_message':
                        last_message=item.get('text','')[:32768]
                        final.append(bridge.redact(last_message))
                    if event.get('type') in ('turn.failed','error'): failed.append(True)
                except ValueError: pass
        thread=threading.Thread(target=read,daemon=True)
        thread.start()
        deadline=time.monotonic()+t['timeout']
        state=None
        while proc.poll() is None:
            persist_logs()
            if requested.is_set() or bridge.task(id)['state']=='cancel_requested': state='cancelled'
            elif time.monotonic()>=deadline: state='timed_out'
            if state:
                os.killpg(proc.pid,signal.SIGTERM)
                try: proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid,signal.SIGKILL)
                    proc.wait(timeout=3)
                break
            time.sleep(.25)
        thread.join(timeout=2)
        # Kill any same-session helpers retained after the agent exits.
        try: os.killpg(proc.pid,signal.SIGKILL)
        except ProcessLookupError: pass
        state=state or ('succeeded' if proc.returncode==0 and not failed else 'failed')
        handoff=bridge.parse_handoff(last_message) if state=='succeeded' and last_message else None
        bridge.update(id,state=state,exit_code=proc.returncode,execution=json.dumps(selected),result='\n'.join(final)[-32768:],handoff=handoff,error=None if state=='succeeded' else state)
    except Exception as e:
        if proc and proc.poll() is None:
            try:
                os.killpg(proc.pid,signal.SIGKILL)
                proc.wait(timeout=3)
            except (ProcessLookupError,subprocess.TimeoutExpired):
                bridge.update(id,state='launch_unknown',error='stop_unconfirmed')
                return
        bridge.update(id,state='failed',error=type(e).__name__)
    finally:
        bridge.DATA.mkdir(parents=True,exist_ok=True)
        fd=os.open(bridge.DATA/(id+'.jsonl'),os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
        with os.fdopen(fd,'w') as f: f.writelines(lines)
        bridge.audit('runner_settled',id)

if __name__=='__main__': run(sys.argv[1])
