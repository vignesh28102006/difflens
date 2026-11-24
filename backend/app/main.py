import hashlib
import hmac
import logging
import os
import uuid
from typing import Any, Dict, List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.crypto import encrypt_secret
from app.db import get_db, get_or_create_repo, init_db
from app.models import HunkRiskFinding, PRAnalysisRun, Repository
from app.queue import enqueue_pr_job, get_redis_client, is_delivery_duplicate

logger = logging.getLogger("difflens")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

GITHUB_WEBHOOK_SECRET = os.getenv("GITHUB_WEBHOOK_SECRET", "development_webhook_secret_key")

app = FastAPI(
    title="DiffLens Webhook Ingress & API",
    version="0.2.0",
    description="Deterministic code risk analyzer webhook ingress & PR inspection API",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional dependency overrides for hermetic testing and local dev fallback
_redis_client_override: Optional[Any] = None


def get_redis() -> Any:
    global _redis_client_override
    if _redis_client_override is not None:
        return _redis_client_override
    try:
        client = get_redis_client()
        client.ping()
        return client
    except Exception:
        import fakeredis
        _redis_client_override = fakeredis.FakeRedis(decode_responses=True)
        return _redis_client_override


# Ensure database tables exist on startup/import
init_db()


def verify_github_signature(raw_body: bytes, signature_header: str | None, secret: str) -> bool:
    """
    Verifies HMAC-SHA256 signature in X-Hub-Signature-256.
    Constant-time comparison prevents timing attacks.
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
    db: Session = Depends(get_db),
) -> Response:
    raw_body = await request.body()

    # 1. Signature verification
    if not verify_github_signature(raw_body, x_hub_signature_256, GITHUB_WEBHOOK_SECRET):
        logger.warning(
            "Rejected webhook delivery %s due to invalid or missing signature",
            x_github_delivery or "unknown",
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-Hub-Signature-256 header",
        )

    # 2. Parse payload
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    action = payload.get("action")
    repo_data = payload.get("repository", {})
    repo_name = repo_data.get("full_name", "unknown")
    delivery_id = x_github_delivery or f"gen-{uuid.uuid4()}"

    logger.info(
        "Verified GitHub event: event=%s, action=%s, repo=%s, delivery=%s",
        x_github_event,
        action,
        repo_name,
        delivery_id,
    )

    if x_github_event == "ping":
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={"status": "accepted", "event": "ping", "message": "Pong"},
        )

    if x_github_event == "pull_request":
        # Delivery idempotency check (Redis + DB)
        redis_conn = get_redis()
        if is_delivery_duplicate(redis_conn, delivery_id):
            logger.info("Ignoring duplicate delivery via Redis cache: %s", delivery_id)
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={"status": "duplicate_ignored", "delivery_id": delivery_id},
            )

        existing_run = db.query(PRAnalysisRun).filter_by(delivery_id=delivery_id).first()
        if existing_run:
            logger.info("Ignoring duplicate delivery via DB record: %s", delivery_id)
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={"status": "duplicate_ignored", "delivery_id": delivery_id},
            )

        # Ensure repository record
        github_repo_id = repo_data.get("id")
        installation_id = payload.get("installation", {}).get("id")
        repo = get_or_create_repo(db, full_name=repo_name, github_repo_id=github_repo_id, installation_id=installation_id)

        pr_info = payload.get("pull_request", {})
        pr_number = payload.get("number") or pr_info.get("number", 0)
        head_sha = pr_info.get("head", {}).get("sha", "head_sha")
        base_sha = pr_info.get("base", {}).get("sha", "base_sha")

        # Create queued run record
        run = PRAnalysisRun(
            delivery_id=delivery_id,
            repo_id=repo.id,
            pr_number=pr_number,
            head_sha=head_sha,
            base_sha=base_sha,
            status="queued",
        )
        db.add(run)
        db.commit()
        db.refresh(run)

        # Enqueue job onto Redis
        job_payload = {
            "run_id": run.id,
            "delivery_id": delivery_id,
            "repo_full_name": repo_name,
            "pr_number": pr_number,
            "head_sha": head_sha,
            "base_sha": base_sha,
            "changed_files": payload.get("changed_files", []),
            "file_contents": payload.get("file_contents", {}),
            "repo_snapshot": payload.get("repo_snapshot", {}),
            "hunks": payload.get("hunks", []),
            "coverage_report": payload.get("coverage_report"),
        }
        enqueue_pr_job(redis_conn, job_payload)

        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "status": "enqueued",
                "event": "pull_request",
                "action": action,
                "run_id": run.id,
                "delivery_id": delivery_id,
                "pr_number": pr_number,
                "repo": repo_name,
            },
        )

    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={"status": "ignored", "event": x_github_event},
    )


# ---------------------------------------------------------------------------
# PR Runs & Findings Read API
# ---------------------------------------------------------------------------

@app.get("/api/runs", tags=["Runs"])
def list_runs(
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    runs = (
        db.query(PRAnalysisRun)
        .order_by(PRAnalysisRun.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [r.to_dict(include_findings=False) for r in runs]


@app.get("/api/runs/{run_id}", tags=["Runs"])
def get_run(
    run_id: int,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    run = db.query(PRAnalysisRun).filter(PRAnalysisRun.id == run_id).first()
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    return run.to_dict(include_findings=True)


# ---------------------------------------------------------------------------
# Repository & BYOK Settings API
# ---------------------------------------------------------------------------

class RepoSettingsUpdate(BaseModel):
    risk_threshold_medium: Optional[float] = None
    risk_threshold_high: Optional[float] = None
    llm_provider: Optional[str] = None  # "gemini", "ollama", "none"
    llm_model: Optional[str] = None
    api_key: Optional[str] = None
    clear_api_key: Optional[bool] = False


@app.get("/api/repos", tags=["Repositories"])
def list_repositories(
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    repos = db.query(Repository).order_by(Repository.id.asc()).all()
    return [r.to_dict() for r in repos]


@app.patch("/api/repos/{repo_id}/settings", tags=["Repositories"])
def update_repo_settings(
    repo_id: int,
    payload: RepoSettingsUpdate,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    repo = db.query(Repository).filter(Repository.id == repo_id).first()
    if not repo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Repository not found")

    if payload.risk_threshold_medium is not None:
        repo.risk_threshold_medium = float(payload.risk_threshold_medium)
    if payload.risk_threshold_high is not None:
        repo.risk_threshold_high = float(payload.risk_threshold_high)
    if payload.llm_provider is not None:
        repo.llm_provider = payload.llm_provider.strip().lower()
    if payload.llm_model is not None:
        repo.llm_model = payload.llm_model.strip() or None

    if payload.clear_api_key:
        repo.encrypted_api_key = None
    elif payload.api_key is not None and payload.api_key.strip():
        repo.encrypted_api_key = encrypt_secret(payload.api_key.strip())

    db.commit()
    db.refresh(repo)
    return repo.to_dict()
