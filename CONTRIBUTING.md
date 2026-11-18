# Contributing to Orca Bridge

Use [Issues](https://github.com/rion0918/orca-bridge/issues) to report a reproducible bug or suggest a change. For vulnerabilities, follow the [security policy](SECURITY.md).

1. Fork the repository and create a topic branch from `main`.
2. Follow the [local setup](README.md#start-the-bridge-on-your-mac). Use an isolated test project and fixture credentials.
3. Include a regression test when changing behavior. Update the English and Japanese setup guides when the setup changes. Keep the bundled OpenAPI schema in sync when changing the API contract.
4. Run the tests and open a pull request against `main`. Explain why the change is needed, how you verified it, and any effect on authentication, isolation, or approval.

```sh
.venv/bin/python -m pip install pytest
.venv/bin/python -B -m pytest -p no:cacheprovider tests
```

The macOS isolation test requires a local Codex CLI. If you cannot run it, add `--ignore=tests/test_sandbox.py` and mention the omission in the pull request.

Keep tokens, real tunnel URLs, and private source or logs out of issues, pull requests, and commits. The maintainer reviews and merges pull requests into `main`.
