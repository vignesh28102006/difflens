import hashlib
import hmac
import json
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import fakeredis

from app.db import Base, get_db, init_db
from app.main import app, GITHUB_WEBHOOK_SECRET
from app.models import HunkRiskFinding, PRAnalysisRun, Repository
from app.worker import process_pr_job

# Set up in-memory SQLite database for deterministic hermetic testing
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


def override_get_db():
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


fake_redis = fakeredis.FakeRedis(decode_responses=True)
from app import main as app_module
app_module._redis_client_override = fake_redis

client = TestClient(app)


@pytest.fixture(autouse=True)
def setup_db():
    Base.metadata.create_all(bind=test_engine)
    fake_redis.flushall()
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.pop(get_db, None)
    Base.metadata.drop_all(bind=test_engine)


def compute_sig(payload_bytes: bytes) -> str:
    sig = hmac.new(GITHUB_WEBHOOK_SECRET.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()
    return f"sha256={sig}"


# ---------------------------------------------------------------------------
# 1. Webhook Deduplication / Idempotency
# ---------------------------------------------------------------------------

def test_webhook_deduplication():
    delivery_id = "delivery-unique-12345"
    payload = {
        "action": "opened",
        "number": 101,
        "repository": {"id": 9991, "full_name": "owner/repo"},
        "pull_request": {
            "title": "feat: auth update",
            "head": {"sha": "sha_head_1"},
            "base": {"sha": "sha_base_1"},
        },
    }
    raw_bytes = json.dumps(payload).encode("utf-8")
    sig = compute_sig(raw_bytes)

    headers = {
        "Content-Type": "application/json",
        "X-GitHub-Event": "pull_request",
        "X-GitHub-Delivery": delivery_id,
        "X-Hub-Signature-256": sig,
    }

    # First delivery: enqueued
    res1 = client.post("/webhooks/github", content=raw_bytes, headers=headers)
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["status"] == "enqueued"
    assert data1["delivery_id"] == delivery_id

    # Second delivery with identical delivery_id: 202 Accepted (duplicate ignored)
    res2 = client.post("/webhooks/github", content=raw_bytes, headers=headers)
    assert res2.status_code == 202
    data2 = res2.json()
    assert data2["status"] == "duplicate_ignored"
    assert data2["delivery_id"] == delivery_id


# ---------------------------------------------------------------------------
# 2. Worker Pipeline & DB Persistence End-to-End
# ---------------------------------------------------------------------------

def test_worker_pipeline_and_db_persistence():
    session = TestingSessionLocal()

    # Create Repo and Run in DB
    repo = Repository(full_name="org/security-repo", github_repo_id=7788)
    session.add(repo)
    session.commit()

    run = PRAnalysisRun(
        delivery_id="delivery-worker-test-01",
        repo_id=repo.id,
        pr_number=7,
        head_sha="head123",
        base_sha="base123",
        status="queued",
    )
    session.add(run)
    session.commit()
    session.refresh(run)

    auth_code = """
def verify_api_key(key: str) -> bool:
    if not key:
        return False
    return True
"""
    routes_code = """
from auth import verify_api_key

def handle_login(req):
    return verify_api_key(req.key)
"""

    job_payload = {
        "run_id": run.id,
        "delivery_id": run.delivery_id,
        "repo_full_name": repo.full_name,
        "pr_number": 7,
        "head_sha": "head123",
        "base_sha": "base123",
        "changed_files": ["auth.py"],
        "file_contents": {
            "auth.py": auth_code,
            "routes.py": routes_code,
        },
        "repo_snapshot": {
            "auth.py": auth_code,
            "routes.py": routes_code,
        },
        "hunks": [
            {
                "file_path": "auth.py",
                "start_line": 2,
                "end_line": 5,
                "added_lines": ["    if not key:", "        return False", "    return True"],
                "hunk_content": auth_code,
            }
        ],
        "coverage_report": {
            "files": {
                "auth.py": {
                    "executed_lines": [2, 3],
                    "missing_lines": [4, 5],
                }
            }
        },
    }

    # Execute deterministic worker pipeline
    completed_run = process_pr_job(job_payload, session)

    assert completed_run.status == "completed"
    assert completed_run.overall_score > 0.0
    assert completed_run.risk_level in ("Low", "Medium", "High")
    assert completed_run.completed_at is not None
    assert completed_run.summary_json["hunks_count"] == 1

    # Verify HunkRiskFinding persistence
    findings = session.query(HunkRiskFinding).filter_by(run_id=completed_run.id).all()
    assert len(findings) == 1
    f = findings[0]
    assert f.file_path == "auth.py"
    assert f.symbol_name == "verify_api_key"
    assert f.domain_score >= 0.8  # 'api_key' keyword triggers auth
    assert "auth" in f.domain_tags
    assert f.fan_in == 1
    assert "handle_login" in f.callers
    assert f.coverage_source == "report"
    assert round(f.coverage_score, 2) == 0.50

    session.close()

    # 3. Verify Read API endpoints
    # GET /api/runs
    res_list = client.get("/api/runs")
    assert res_list.status_code == 200
    runs_data = res_list.json()
    assert len(runs_data) >= 1
    assert runs_data[0]["id"] == completed_run.id
    assert runs_data[0]["status"] == "completed"

    # GET /api/runs/{run_id}
    res_detail = client.get(f"/api/runs/{completed_run.id}")
    assert res_detail.status_code == 200
    detail_data = res_detail.json()
    assert detail_data["id"] == completed_run.id
    assert len(detail_data["findings"]) == 1
    assert detail_data["findings"][0]["symbol_name"] == "verify_api_key"
