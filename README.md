# Orca Bridge

[日本語](README.ja.md) · [Platform support](#platform-support) · [Quick start](#start-the-bridge-on-your-mac) · [Connect Muse](#connect-muse)

Orca Bridge lets you ask Muse to build and test software with Orca and Codex on a machine you control. Muse can check progress, read results, and send an answer when Codex needs a decision. Other clients can use the same HTTP API.

`Muse → HTTPS → Orca Bridge → Orca → Codex`

## Platform support

| Operating system | Use Muse to control a remote Bridge | Run Orca Bridge locally |
| --- | --- | --- |
| macOS | [Muse on the web](https://muse.ai/) | Verified. See the quick start below. |
| Windows | [Muse on the web](https://muse.ai/) | Native Windows and WSL are unverified. |
| Linux | [Muse on the web](https://muse.ai/) | Unverified. |
| iOS / Android | Muse mobile app | Use a separate Bridge host. |

[Orca itself runs on macOS, Windows, and Linux](https://orca.dev/docs/install). Linux uses the `orca-ide` CLI. This project's Bridge relies on Unix process groups for cancellation and uses macOS clipboard tooling for its token helper. Additional host platforms need verification of task cancellation and filesystem and network isolation.

## Start the Bridge on your Mac

Install Git, Python 3.10 or later (tested with 3.13), [Orca and its CLI](https://orca.dev/docs/install), and a signed-in Codex CLI. Start Orca before running the Bridge.

```sh
git clone https://github.com/rion0918/orca-bridge.git
cd orca-bridge
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python serve.py
```

The Bridge listens on `127.0.0.1:8765` and generates read/write and read-only tokens in a private `.env` file on first start. In another terminal, run `.venv/bin/python client.py status` to check that Orca is reachable.

## Connect Muse

Use a Muse account with personal Custom Connectors available. The setup is per account. You can use the same connector from your phone or computer.

1. **Expose the authenticated Bridge to Muse.** With a configured ngrok CLI, run the following on the Mac in another terminal. It exposes the API over HTTPS.

   ```sh
   .venv/bin/python tunnel.py --publish-approved --provider ngrok
   ```

2. **Create your connector in Muse.** Copy this prompt and replace the placeholders using `state/connector-endpoint.json` on the Mac.

   ```text
   Create a personal Custom Connector named Orca Bridge.

   Base URL: <base_url>
   OpenAPI URL: <openapi_url>
   Default HTTP headers: <default_headers>

   Use Authorization: Bearer. Ask me to enter the token through Muse's
   secure credential input. After setup, call GET /status and GET /agents
   and report the responses. Do not submit a development task yet.
   ```

3. **Enter the Bridge token.** Run `.venv/bin/python copy_token.py` on the Mac and paste the read/write token into Muse's secure credential input. Keep it out of chat. If you set up Muse on a different device, you can do this step from the Mac while signed in to the same Muse account.
4. **Try a task.** Once both checks succeed, ask Muse: “Use Orca Bridge to create `hello.txt` in `sandbox`. Read it back and report the contents.” With the default settings, approve the returned ID on the Mac with `.venv/bin/python bridge.py approve TASK_ID`. Muse can then read the result.

Keep the Mac, Orca, Bridge, and tunnel running for remote use. If the URL changes, update the connector and check its credential binding. A Bridge HTTP 401 means the token needs checking. An HTML error from the tunnel provider points to the connection path.

To stop remote access, run `.venv/bin/python process_control.py --tunnel-only` on the Mac.

## API and scope

See the [OpenAPI schema](connector/openapi.json), [connection example](connector/connection.example.json), and [environment template](.env.example). The live schema is available at authenticated `GET /openapi.json`.

- Tasks require local approval by default. `BRIDGE_AUTORUN_SANDBOX=1` enables automatic execution across eligible projects, including projects outside the default sandbox.
- The read/write token can operate across configured projects. Start with a test project that contains no sensitive data. Task data and logs stay on the host and have no automatic expiry.
- GitHub publication, production deployment, and billing execution are outside this public build's scope.

## Contributing and license

[Contribution guide](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md) · [Security policy](SECURITY.md) · [MIT License](LICENSE)
