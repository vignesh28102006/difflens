import json
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import fakeredis

from app.crypto import decrypt_secret, encrypt_secret, mask_secret
from app.db import Base, get_db
from app.explainer import Explainer, HunkExplanation, create_explainer_for_repo
from app.github_reporter import post_or_update_pr_report, render_pr_markdown_report
from app.main import app
from app.models import HunkRiskFinding, PRAnalysisRun, Repository

# Set up hermetic in-memory SQLite database
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


# ---------------------------------------------------------------------------
# 1. BYOK Encryption, Masking & API Key Security
# ---------------------------------------------------------------------------

def test_crypto_roundtrip_and_masking():
    plain = "sk-proj-super-secret-api-key-9999"
    token = encrypt_secret(plain)
    assert token != plain
    decrypted = decrypt_secret(token)
    assert decrypted == plain

    masked = mask_secret(plain)
    assert masked == "••••9999"
    assert "super-secret" not in masked


def test_repo_settings_api_masks_keys_never_leaks():
    session = TestingSessionLocal()
    repo = Repository(
        full_name="owner/payment-gateway",
        github_repo_id=5555,
        risk_threshold_medium=40.0,
        risk_threshold_high=70.0,
        llm_provider="gemini",
    )
    session.add(repo)
    session.commit()
    session.refresh(repo)
    repo_id = repo.id
    session.close()

    # 1. PATCH settings with API key
    patch_res = client.patch(
        f"/api/repos/{repo_id}/settings",
        json={
            "risk_threshold_medium": 45.0,
            "risk_threshold_high": 75.0,
            "llm_provider": "gemini",
            "llm_model": "gemini-2.5-flash",
            "api_key": "AIzaSy-super-confidential-key-1234",
        },
    )
    assert patch_res.status_code == 200
    data = patch_res.json()
    assert data["has_api_key"] is True
    assert data["masked_api_key"] == "••••1234"
    assert "super-confidential" not in json.dumps(data)
    assert data["risk_threshold_medium"] == 45.0

    # 2. GET /api/repos verifies secret is masked in repo list
    list_res = client.get("/api/repos")
    assert list_res.status_code == 200
    repos = list_res.json()
    target = next(r for r in repos if r["id"] == repo_id)
    assert target["has_api_key"] is True
    assert target["masked_api_key"] == "••••1234"
    assert "super-confidential" not in json.dumps(repos)


# ---------------------------------------------------------------------------
# 2. Explainer Provider Routing & Deterministic Fallback
# ---------------------------------------------------------------------------

def test_explainer_deterministic_fallback():
    # explainer with provider='none' or empty key
    explainer = Explainer(provider="none")
    result = explainer.explain(
        file_path="app/auth.py",
        symbol_name="verify_jwt_token",
        risk_score=85.0,
        domain_score=0.85,
        domain_tags=["auth"],
        fan_in=3,
        callers=["login", "checkout", "profile"],
        coverage_score=0.0,
        uncovered_lines=[10, 11, 12],
        diff_patch="def verify_jwt_token(token): pass",
    )

    assert isinstance(result, HunkExplanation)
    assert "Critical" in result.risk_summary or "Elevated" in result.risk_summary
    assert "auth" in result.why_its_risky.lower()
    assert len(result.review_checklist) >= 2
    assert "def test_verify_jwt_token" in result.suggested_test_code


def test_explainer_mocked_gemini_structured_output():
    mock_payload = {
        "risk_summary": "Modifies sensitive JWT authentication handler without token expiry check.",
        "why_its_risky": "Allows forged tokens to authenticate requests across 3 upstream routes.",
        "how_to_fix": "Add claims validation and verify signature using public key.",
        "review_checklist": ["Check expiry validation", "Inspect caller error handling"],
        "suggested_test_code": "def test_jwt_expiry():\n    assert False",
    }

    mock_response = MagicMock()
    mock_response.text = json.dumps(mock_payload)

    with patch("google.genai.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.models.generate_content.return_value = mock_response
        mock_client_cls.return_value = mock_instance

        explainer = Explainer(provider="gemini", api_key="test-api-key")
        result = explainer.explain(
            file_path="app/auth.py",
            symbol_name="verify_jwt",
            risk_score=90.0,
            domain_score=0.85,
            domain_tags=["auth"],
            fan_in=3,
            callers=["login_route"],
            coverage_score=0.2,
            uncovered_lines=[5, 6],
            diff_patch="diff ...",
        )

        assert result.risk_summary == mock_payload["risk_summary"]
        assert result.why_its_risky == mock_payload["why_its_risky"]
        assert result.review_checklist == mock_payload["review_checklist"]
        assert result.suggested_test_code == mock_payload["suggested_test_code"]


def test_explainer_graceful_degradation_on_gemini_error():
    with patch("google.genai.Client") as mock_client_cls:
        mock_instance = MagicMock()
        mock_instance.models.generate_content.side_effect = RuntimeError("Quota exceeded / timeout")
        mock_client_cls.return_value = mock_instance

        explainer = Explainer(provider="gemini", api_key="faulty-key")
        result = explainer.explain(
            file_path="app/auth.py",
            symbol_name="login",
            risk_score=75.0,
            domain_score=0.85,
            domain_tags=["auth"],
            fan_in=1,
            callers=["handler"],
            coverage_score=0.0,
            uncovered_lines=[2, 3],
            diff_patch="diff ...",
        )

        # Must cleanly fall back to deterministic template rather than throwing
        assert isinstance(result, HunkExplanation)
        assert len(result.review_checklist) > 0


# ---------------------------------------------------------------------------
# 3. GitHub PR Markdown Report & Comment Upsert
# ---------------------------------------------------------------------------

def test_render_pr_markdown_report_formatting():
    repo = Repository(full_name="acme/web-app", risk_threshold_medium=40.0, risk_threshold_high=70.0)
    run = PRAnalysisRun(
        delivery_id="del-1",
        repo_id=1,
        pr_number=42,
        head_sha="abc",
        status="completed",
        overall_score=82.5,
        risk_level="High",
    )

    finding = HunkRiskFinding(
        run_id=1,
        file_path="auth/jwt.py",
        start_line=10,
        end_line=25,
        symbol_name="verify_token",
        risk_score=82.5,
        domain_score=0.85,
        domain_tags=["auth"],
        fan_in=3,
        callers=["login", "api_gateway"],
        coverage_score=0.25,
        coverage_source="report",
        uncovered_lines=[15, 16],
        diff_patch="diff content",
        explanation=json.dumps({
            "risk_summary": "Alters core token verification.",
            "why_its_risky": "Uncovered auth lines can allow spoofed signatures.",
            "how_to_fix": "Verify secret key and clock skew.",
            "review_checklist": ["Check clock skew", "Verify secret"],
            "suggested_test_code": "def test_spoofed(): pass",
        }),
    )

    md = render_pr_markdown_report(run, [finding], repo)

    assert "<!-- difflens-report:pr-42 -->" in md
    assert "## 🔍 DiffLens Risk Analysis — PR #42" in md
    assert "🔴 HIGH RISK" in md
    assert "82.5 / 100" in md
    assert "`auth/jwt.py:verify_token`" in md
    assert "<details>" in md
    assert "Uncovered auth lines can allow spoofed signatures." in md
    assert "def test_spoofed(): pass" in md


def test_post_or_update_pr_report_updates_in_place():
    repo = Repository(full_name="acme/web-app", risk_threshold_medium=40.0, risk_threshold_high=70.0)
    run = PRAnalysisRun(
        delivery_id="del-1",
        repo_id=1,
        pr_number=42,
        head_sha="abc",
        status="completed",
        overall_score=55.0,
        risk_level="Medium",
    )

    # Mock GitHub API responses:
    # 1. Existing comments list containing the marker
    existing_comment_id = 98765
    comments_list = [
        {"id": 11111, "body": "Great PR!"},
        {"id": existing_comment_id, "body": "<!-- difflens-report:pr-42 -->\nOld report content"},
    ]

    mock_client = MagicMock()
    mock_get = MagicMock(status_code=200)
    mock_get.json.return_value = comments_list
    mock_client.get.return_value = mock_get

    mock_patch = MagicMock(status_code=200)
    mock_patch.json.return_value = {"id": existing_comment_id, "body": "updated"}
    mock_client.patch.return_value = mock_patch

    with patch("httpx.Client") as mock_httpx:
        mock_httpx.return_value.__enter__.return_value = mock_client

        res = post_or_update_pr_report(run, [], repo, github_token="fake-gh-token")

        assert res is not None
        assert res["id"] == existing_comment_id
        # Verify PATCH was called on the existing comment URL
        mock_client.patch.assert_called_once()
        called_url = mock_client.patch.call_args[0][0]
        assert f"/repos/acme/web-app/issues/comments/{existing_comment_id}" in called_url
