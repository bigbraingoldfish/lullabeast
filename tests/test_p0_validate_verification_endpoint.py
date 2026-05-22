"""Integration tests for POST /api/setup/validate-verification (Stage C).

Sibling of /api/setup/validate-roadmap. Body: {content: str}. Returns
{valid: bool, errors: list}. No file writes.
"""


def load_server():
    from ui.server import app
    from fastapi.testclient import TestClient
    return TestClient(app)


VALID_DOC = """# Verification

## Project type
cli

## Entry point
- Command: `mycli --help`
- Ready signal: process exits 0

## Public surface
1. Add a task with `mycli add`
2. List tasks with `mycli list`

## Verification stack
- Acceptance tool: subprocess + assertions
"""


class TestValidateVerificationEndpoint:

    def test_endpoint_returns_valid_true_for_valid_doc(self):
        client = load_server()
        r = client.post("/api/setup/validate-verification", json={"content": VALID_DOC})
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is True
        assert data["errors"] == []

    def test_endpoint_returns_errors_for_invalid_doc(self):
        client = load_server()
        # Missing Verification stack section
        broken = VALID_DOC.split("## Verification stack")[0]
        r = client.post("/api/setup/validate-verification", json={"content": broken})
        assert r.status_code == 200
        data = r.json()
        assert data["valid"] is False
        assert len(data["errors"]) >= 1

    def test_endpoint_422_on_missing_content_field(self):
        client = load_server()
        r = client.post("/api/setup/validate-verification", json={})
        # Mirror /api/setup/validate-roadmap behavior: content defaults to "" and
        # the validator returns valid=False with the missing-heading error.
        # Either 422 (strict) or 200 with valid=False is acceptable; assert one.
        assert r.status_code in (200, 422)
        if r.status_code == 200:
            assert r.json()["valid"] is False
