"""Isolated Poppy discovery and approval challenge diagnostic."""

import argparse
from datetime import datetime, timedelta, timezone
import logging
from urllib.parse import urlsplit
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def create_app(origin: str) -> FastAPI:
    origin = origin.rstrip("/")
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Probe origin must be an HTTPS origin")

    app = FastAPI(
        title="Orca Bridge Poppy Diagnostic",
        servers=[{"url": origin}],
        docs_url=None,
        redoc_url=None,
    )
    operation = {
        "operation_id": str(uuid4()),
        "revision": str(uuid4()),
        "summary": "Test a Muse approval prompt with a simulated echo. No cost or external changes.",
        "terms": {"action": "test_echo", "cost": 0},
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat(),
    }

    @app.get("/.well-known/poppy.json", include_in_schema=False)
    def discovery():
        return {
            "protocol_version": "1.0",
            "organization": {"name": app.title, "domain": parsed.netloc},
            "web": {"disabled": True},
            "apis": [{"type": "openapi", "url": origin + "/openapi.json",
                      "description": "A simulated echo for testing an approval prompt."}],
            "extensions": {"operations": [{"version": "3"}]},
        }

    @app.post("/echo", summary="Request a simulated echo", responses={
        403: {"description": "Approval challenge or rejected session"},
    })
    def echo(request: Request):
        headers = request.headers
        declares_operations = '"operations";v="3"' in headers.get("Poppy-Extensions", "")
        has_approval = "Poppy-Approval" in headers
        logging.getLogger("uvicorn.error").info(
            "Poppy echo: session_present=%s operations_v3=%s approval_present=%s",
            "Poppy-Session" in headers, declares_operations, has_approval,
        )
        # This diagnostic does not authenticate sessions or accept any approval.
        if has_approval:
            payload = {"error": "invalid_session"}
        elif not declares_operations:
            payload = {"error": "extension_required", "extension": "operations"}
        elif headers.get("Poppy-Version") != "1.0":
            return JSONResponse({"error": "version_unsupported"}, status_code=400)
        else:
            payload = {"error": "approval_required", "operation": operation}
        return JSONResponse(payload, status_code=403, headers={"Cache-Control": "no-store"})

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--origin", default="https://localhost:8766")
    args = parser.parse_args()
    uvicorn.run(create_app(args.origin), host="127.0.0.1", port=8766, access_log=False)
