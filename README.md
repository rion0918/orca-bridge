# Orca Bridge

[日本語](README.ja.md) · [MIT License](LICENSE)

Orca Bridge is an experimental, self-hosted HTTP API for running local development tasks through Orca and Codex on macOS. A client such as Muse can submit a task, read its result, and send a follow-up when the coding agent needs a decision.

## Quick start

You need macOS, Python 3.10 or newer, Orca with its CLI, and a signed-in Codex CLI. Development uses Python 3.13. The pinned dependencies must be available for your Python version.

```sh
git clone https://github.com/rion0918/orca-bridge.git
cd orca-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python serve.py
```

The server listens on `127.0.0.1:8765`. On first start it creates a private `.env` file with separate read/write and read-only bearer tokens. See [the environment template](.env.example) for configuration. Keep those tokens out of prompts, commits, and shared logs.

In another terminal, submit a task from a text file and approve it locally:

```sh
.venv/bin/python client.py status
.venv/bin/python client.py agents
printf 'Create hello.txt with one line of text and read it back.\n' > /tmp/orca-bridge-request.txt
.venv/bin/python client.py submit --project sandbox --prompt-file /tmp/orca-bridge-request.txt
.venv/bin/python bridge.py show TASK_ID
.venv/bin/python bridge.py approve TASK_ID
.venv/bin/python client.py result TASK_ID
```

Replace `TASK_ID` with the returned ID. Use `client.py get` or `client.py logs` to check progress. Stop the foreground server with Ctrl+C. Cancel active work before stopping the Bridge if you need the task itself to stop.

## Connect an agent

Use the [OpenAPI schema](connector/openapi.json) and [connection example](connector/connection.example.json) to configure your client. The running server also serves an authenticated schema at `GET /openapi.json`. The example uses a placeholder domain. Store the token in your client's credential store.

1. List the available projects with `GET /projects`, or create a local project with `POST /projects`.
2. Submit a development task with `POST /tasks`. Use an `Idempotency-Key` and reuse it when resending the same request.
3. Poll `GET /tasks/{id}` at intervals of at least two seconds. Read `GET /tasks/{id}/result` when the task settles.
4. If the task succeeds with `handoff.outcome=needs_decision`, submit a follow-up with the same `project`, the previous task ID in `continues_task`, and the answer in `prompt`. The Bridge supplies the consultation history. A consultation accepts one continuation and the chain is limited to eight follow-ups.
5. Treat `needs_approval` as a proposal for a human. An agent result cannot authorize an external action.

For remote access, the repository includes an optional HTTPS tunnel command. Run it only when you intend to expose the service.

```sh
.venv/bin/python tunnel.py --publish-approved --provider ngrok
.venv/bin/python process_control.py --tunnel-only
```

The second command stops the tunnel. During remote use, keep the Mac, Orca, Bridge, and tunnel running. See [the Japanese reference](README.ja.md) for configuration, API details, and the Cloudflare alternative.

## Security and current scope

- Development requires local approval by default. Setting `BRIDGE_AUTORUN_SANDBOX=1` opts into automatic execution across eligible projects, including registered projects outside the default sandbox.
- The write token permits operations across the configured project set. There is no per-project token scope. Start with a dedicated non-sensitive project.
- The runner uses a fixed Codex command without accepting arbitrary commands over HTTP. Its isolation depends on the installed Codex CLI and macOS. The Bridge stores task data and logs locally and does not automatically expire them.
- This public build handles local development. GitHub publication, deployment, and billing execution are outside its scope. Muse's native approval flow has been explored with a diagnostic only.
- Codex is the supported runner. Antigravity presets are disabled.

## Contributing

Issues and pull requests are welcome. For bugs, include the macOS, Python, Orca, and Codex versions and a minimal reproduction with sensitive details removed.

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -B -m pytest -p no:cacheprovider tests
```

The macOS sandbox test uses a real Codex CLI. See [validation notes](VALIDATION.md) for the scope of previous checks.

## License

Orca Bridge is available under the [MIT License](LICENSE).
