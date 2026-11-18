from fastapi.testclient import TestClient

from poppy_probe import create_app


def test_discovery_lists_only_isolated_probe():
    client = TestClient(create_app("https://probe.example"))
    manifest = client.get("/.well-known/poppy.json").json()
    assert manifest["organization"]["domain"] == "probe.example"
    assert manifest["extensions"] == {"operations": [{"version": "3"}]}
    assert manifest["apis"][0]["url"] == "https://probe.example/openapi.json"
    assert set(client.get("/openapi.json").json()["paths"]) == {"/echo"}


def test_probe_exposes_fixed_terms_only_after_negotiation():
    client = TestClient(create_app("https://probe.example"))
    assert client.post("/echo").json() == {
        "error": "extension_required", "extension": "operations"
    }
    headers = {"Poppy-Version": "1.0", "Poppy-Extensions": '"operations";v="3"'}
    response = client.post("/echo", headers=headers)
    assert response.status_code == 403
    payload = response.json()
    assert payload["error"] == "approval_required"
    assert payload["operation"]["terms"] == {"action": "test_echo", "cost": 0}
    assert client.post("/echo", headers=headers).json() == payload
    approval = 'operation_id="test_echo", revision="fixture", approver="user"'
    response = client.post("/echo", headers={**headers, "Poppy-Approval": approval})
    assert response.status_code == 403
    assert response.json() == {"error": "invalid_session"}
