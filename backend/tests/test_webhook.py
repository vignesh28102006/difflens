import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app, GITHUB_WEBHOOK_SECRET

init_db()
client = TestClient(app)


def compute_signature(payload_bytes: bytes, secret: str = GITHUB_WEBHOOK_SECRET) -> str:
    sig = hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


def test_healthz():
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_missing_signature():
    payload = {"action": "opened"}
    response = client.post(
        "/webhooks/github",
        json=payload,
        headers={"X-GitHub-Event": "pull_request"},
    )
    assert response.status_code == 401
    assert "Invalid or missing" in response.json()["detail"]


def test_webhook_invalid_signature():
    payload = {"action": "opened"}
    response = client.post(
        "/webhooks/github",
        json=payload,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": "sha256=invalid_hex_digest_12345",
        },
    )
    assert response.status_code == 401
    assert "Invalid or missing" in response.json()["detail"]


def test_webhook_valid_signature_ping():
    payload_data = {"zen": "Keep it logically awesome."}
    payload_bytes = json.dumps(payload_data).encode("utf-8")
    sig = compute_signature(payload_bytes)

    response = client.post(
        "/webhooks/github",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "ping",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": "delivery-ping-001",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("accepted", "enqueued")
    assert data["event"] == "ping"


def test_webhook_valid_signature_pull_request():
    payload_data = {
        "action": "opened",
        "number": 42,
        "pull_request": {
            "title": "feat: introduce jwt token validation",
            "head": {"sha": "abc1234"},
        },
        "repository": {"full_name": "octocat/Hello-World"},
    }
    payload_bytes = json.dumps(payload_data).encode("utf-8")
    sig = compute_signature(payload_bytes)

    response = client.post(
        "/webhooks/github",
        content=payload_bytes,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "pull_request",
            "X-Hub-Signature-256": sig,
            "X-GitHub-Delivery": "delivery-pr-42",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["status"] in ("accepted", "enqueued")
    assert data["event"] == "pull_request"
    assert data["action"] == "opened"
    assert data["pr_number"] == 42
    assert data["repo"] == "octocat/Hello-World"
