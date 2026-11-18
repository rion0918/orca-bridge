# Orca Bridge

[日本語](README.ja.md) · [Connect Muse](#connect-muse) · [OpenAPI](connector/openapi.json) · [MIT License](LICENSE)

**Ask Muse to build and test software in Orca on your Mac.** Orca Bridge is an experimental, self-hosted HTTP API for Orca and Codex. Muse can submit a task, read its result, and send a follow-up when Codex needs a decision. Other HTTP clients can use the same API.

`Muse → HTTPS → Orca Bridge → Orca → Codex`

You install the Bridge on your Mac. To use it with Muse, you create a personal Custom Connector in your own Muse account and give it your Bridge's HTTPS URL and credential. The repository provides the API schema, a connection example, and a setup prompt below.

## Start the Bridge on your Mac

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

## Connect Muse

You need a Muse account with Custom Connectors available and an HTTPS endpoint that Muse can reach.

1. With the Bridge running, start an HTTPS tunnel in another terminal. The following example requires an installed and configured ngrok CLI. It exposes the authenticated API over HTTPS.

   ```sh
   .venv/bin/python tunnel.py --publish-approved --provider ngrok
   ```

   After the tunnel reports a verified HTTPS endpoint, open `state/connector-endpoint.json` and use its `base_url`, `openapi_url`, and `default_headers` as the connection details.
2. Paste the following prompt into Muse, replacing the example URL and providing the default header names and values from that file. Leave the bearer token out of the message.

   ```text
   Create a personal Custom Connector named Orca Bridge for this HTTPS API:
   https://YOUR-BRIDGE-HOST.example

   Load the API schema at /openapi.json. Use Authorization: Bearer with a
   credential you ask me to enter through Muse's secure credential input.
   Use these default request headers: <paste only default_headers here>.
   After setup, call GET /status and GET /agents. Report the actual responses.
   Do not submit a development task yet.
   ```

3. When Muse opens its secure credential input, enter the Bridge's read/write token. On the Mac, run `.venv/bin/python copy_token.py` to copy it without printing it. The read-only token works for connection checks but cannot submit development tasks.
4. After both checks succeed, ask Muse: “Use Orca Bridge to create hello.txt in sandbox, then read it back and tell me what it contains.” With the default configuration, approve the returned task ID on the Mac using `.venv/bin/python bridge.py approve TASK_ID`. Muse can then retrieve the result.

If you restart the tunnel and its URL changes, update the connector's endpoint and confirm the credential is associated with the new host. HTTP 401 calls for checking the bearer token. For an HTML block or forwarding error from the tunnel provider, check that Muse is reaching the Bridge itself first.

Keep the Mac, Orca, Bridge, and tunnel running for remote use. To stop the tunnel, run `.venv/bin/python process_control.py --tunnel-only`. See the [technical reference in Japanese](docs/reference.ja.md) for tunnel alternatives and configuration.

## Use the API from an agent

Use the [OpenAPI schema](connector/openapi.json) and [connection example](connector/connection.example.json) to configure your client. The running server also serves an authenticated schema at `GET /openapi.json`. The example uses a placeholder domain. Store the token in your client's credential store.

1. List the available projects with `GET /projects`, or create a local project with `POST /projects`.
2. Submit a development task with `POST /tasks`. Use an `Idempotency-Key` and reuse it when resending the same request.
3. Poll `GET /tasks/{id}` at intervals of at least two seconds. Read `GET /tasks/{id}/result` when the task settles.
4. If the task succeeds with `handoff.outcome=needs_decision`, submit a follow-up with the same `project`, the previous task ID in `continues_task`, and the answer in `prompt`. The Bridge supplies the consultation history. A consultation accepts one continuation and the chain is limited to eight follow-ups.
5. Treat `needs_approval` as a proposal for a human. An agent result cannot authorize an external action.

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

The macOS sandbox test uses a real Codex CLI. To run the suite without that integration test, add `--ignore=tests/test_sandbox.py`.

## License

Orca Bridge is available under the [MIT License](LICENSE).
