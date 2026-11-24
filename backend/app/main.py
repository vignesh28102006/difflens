import hashlib
import hmac
import logging
import os
from typing import Any, Dict

from fastapi import FastAPI, Header, HTTPException, Request, Response, status

logger = logging.getLogger("difflens")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "development_webhook_secret_key")

app = FastAPI(
    title="DiffLens Webhook Ingress",
    version="0.1.0",
    description="Deterministic code risk analyzer webhook ingress",
)


def verify_github_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    """
    Verifies the HMAC-SHA256 signature provided in the X-Hub-Signature-256 header.
    Constant-time comparison via hmac.compare_digest prevents timing attacks.
    """
    if not signature_header or not secret:
        return False

    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False

    expected_sig = signature_header[len(prefix) :]
    computed_sig = hmac.new(
        key=secret.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(expected_sig, computed_sig)


@app.get("/healthz", tags=["Health"])
async def healthz() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/webhooks/github", tags=["Webhooks"])
async def handle_github_webhook(
    request: Request,
    x_hub_signature_256: str | None = Header(None, alias="X-Hub-Signature-256"),
    x_github_event: str | None = Header(None, alias="X-GitHub-Event"),
    x_github_delivery: str | None = Header(None, alias="X-GitHub-Delivery"),
) -> Dict[str, Any]:
    raw_body = await request.body()

    # Step 1: Signature verification
    if not verify_github_signature(raw_body, x_hub_signature_256, GITHUB_WEBHOOK_SECRET):
        logger.warning(
            "Rejected webhook delivery %s due to invalid or missing signature",
            x_github_delivery or "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Hub-Signature-256 header",
        )

    # Step 2: Event routing
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    action = payload.get("action")
    repo_name = payload.get("repository", {}).get("full_name", "unknown")

    logger.info(
        "Received verified GitHub event: event=%s, action=%s, repo=%s, delivery=%s",
        x_github_event,
        action,
        repo_name,
        x_github_delivery,
    )

    if x_github_event == "ping":
        return {"status": "accepted", "event": "ping", "message": "Pong"}

    if x_github_event == "pull_request":
        pr_number = payload.get("number")
        pr_title = payload.get("pull_request", {}).get("title")
        logger.info("Enqueuing analysis for PR #%s: %s (action=%s)", pr_number, pr_title, action)
        # In worker milestone: enqueue job onto Redis queue
        return {
            "status": "accepted",
            "event": "pull_request",
            "action": action,
            "pr_number": pr_number,
            "repo": repo_name,
        }

    return {"status": "ignored", "event": x_github_event}
