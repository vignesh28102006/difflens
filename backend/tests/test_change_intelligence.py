import json
import subprocess
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
import fakeredis

from app.db import Base, get_db
from app.interaction_analyzer import InteractionAnalyzer
from app.main import app
from app.models import HunkRiskFinding, PRAnalysisRun, Repository
from app.regression_miner import GitCommitInfo, RegressionMiner, mine_git_history
from app.test_selector import TestSelector

# Set up in-memory SQLite database
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
# 1. Regression Miner & Co-Change Coupling
# ---------------------------------------------------------------------------

def test_regression_miner_with_git_repo(tmp_path):
    repo_dir = tmp_path / "test_repo"
    repo_dir.mkdir()

    # Initialize temporary git repository
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Dev"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "dev@example.com"], cwd=repo_dir, check=True)

    # Commit 1: Bug fix modifying auth.py and session.py together
    (repo_dir / "auth.py").write_text("def auth(): pass")
    (repo_dir / "session.py").write_text("def session(): pass")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "fix: resolve session token validation bug"], cwd=repo_dir, check=True)

    # Commit 2: Second bug fix modifying auth.py and session.py together
    (repo_dir / "auth.py").write_text("def auth(): return True")
    (repo_dir / "session.py").write_text("def session(): return True")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "hotfix: patch session expiry regression"], cwd=repo_dir, check=True)

    # Commit 3: Normal feature
    (repo_dir / "profile.py").write_text("def profile(): pass")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "feat: add user profile page"], cwd=repo_dir, check=True)

    # Mine history
    miner = RegressionMiner(min_co_occurrences=2, coupling_threshold=0.60)
    result = miner.analyze(changed_files=["auth.py"], repo_path=str(repo_dir))

    # Assert broken co-change warning was emitted because session.py was NOT modified in PR
    assert len(result.co_change_warnings) == 1
    warning = result.co_change_warnings[0]
    assert warning.target_file == "auth.py"
    assert warning.missing_partner == "session.py"
    assert warning.coupling_ratio == 1.0  # 2 of 2 bug-fix commits
    assert len(warning.supporting_commits) == 2
    assert any("resolve session token" in s for s in warning.supporting_commits)

    # Risk boost applied for broken co-change pair
    assert result.risk_boosts_by_file["auth.py"] >= 15.0


def test_regression_miner_structured_input():
    commits = [
        GitCommitInfo(
            hash="c111",
            subject="fix: resolve payment race condition",
            files_changed=["billing/charge.py", "billing/ledger.py"],
            is_bug_fix=True,
        ),
        GitCommitInfo(
            hash="c222",
            subject="bug: rollback double charging error",
            files_changed=["billing/charge.py", "billing/ledger.py"],
            is_bug_fix=True,
        ),
    ]

    miner = RegressionMiner(min_co_occurrences=2, coupling_threshold=0.60)
    result = miner.analyze(changed_files=["billing/charge.py"], commits=commits)

    assert len(result.co_change_warnings) == 1
    w = result.co_change_warnings[0]
    assert w.missing_partner == "billing/ledger.py"
    assert w.coupling_ratio == 1.0


# ---------------------------------------------------------------------------
# 2. Cross-File Change Interaction Engine
# ---------------------------------------------------------------------------

def test_signature_drift_detection():
    base_auth = """
def verify_token(token: str, strict: bool = True):
    return len(token) > 5
"""
    head_auth = """
def verify_token(token: str, scope: str, strict: bool = True):
    return len(token) > 5 and scope == "admin"
"""
    routes_code = """
from auth import verify_token

def handle_request(req):
    return verify_token(req.token)
"""
    repo_snapshot = {
        "auth.py": head_auth,
        "routes.py": routes_code,
    }

    analyzer = InteractionAnalyzer()
    findings = analyzer.analyze_interactions(
        changed_files=["auth.py"],  # routes.py is NOT changed in this PR
        base_contents={"auth.py": base_auth},
        head_contents={"auth.py": head_auth},
        repo_snapshot=repo_snapshot,
    )

    drift_findings = [f for f in findings if f.interaction_type == "signature_drift"]
    assert len(drift_findings) >= 1
    drift = drift_findings[0]
    assert drift.source_symbol == "verify_token"
    assert drift.severity == "HIGH"
    assert "routes" in drift.target_symbol or "handle_request" in drift.target_symbol
    assert "required parameter" in drift.evidence.lower()


# ---------------------------------------------------------------------------
# 3. Risk-Weighted Test Selection Engine
# ---------------------------------------------------------------------------

def test_risk_weighted_test_selection():
    repo_snapshot = {
        "tests/test_auth.py": """
def test_token():
    verify_token('secret')
""",
        "tests/test_helpers.py": """
def test_formatting():
    format_title('hello')
""",
        "tests/test_unrelated.py": """
def test_noop():
    assert 1 == 1
""",
    }

    # High-risk auth symbol vs low-risk helper
    changed_symbols_with_risk = [
        ("verify_token", 92.0, 3),   # High risk, 3 callers
        ("format_title", 12.0, 0),   # Low risk, 0 callers
    ]

    selector = TestSelector()
    result = selector.select_tests(changed_symbols_with_risk, repo_snapshot)

    assert len(result.prioritized_test_files) >= 1
    # test_auth.py must be ranked first due to high risk weight
    assert result.prioritized_test_files[0] == "tests/test_auth.py"
    assert "pytest tests/test_auth.py" in result.command
    assert result.coverage_percentage > 50.0
    assert "test suites prioritized" in result.reduction_summary


# ---------------------------------------------------------------------------
# 4. Risk Debt Ledger API
# ---------------------------------------------------------------------------

def test_risk_debt_ledger_endpoint():
    session = TestingSessionLocal()
    repo = Repository(full_name="org/core-platform", github_repo_id=12345)
    session.add(repo)
    session.commit()
    session.refresh(repo)

    # Simulate 2 PR runs touching the same auth hotspot
    run1 = PRAnalysisRun(
        delivery_id="del-run-1",
        repo_id=repo.id,
        pr_number=10,
        head_sha="sha1",
        status="completed",
        overall_score=85.0,
        risk_level="High",
    )
    run2 = PRAnalysisRun(
        delivery_id="del-run-2",
        repo_id=repo.id,
        pr_number=11,
        head_sha="sha2",
        status="completed",
        overall_score=78.0,
        risk_level="High",
    )
    session.add_all([run1, run2])
    session.commit()
    session.refresh(run1)
    session.refresh(run2)

    f1 = HunkRiskFinding(
        run_id=run1.id,
        file_path="auth/jwt.py",
        start_line=1,
        end_line=10,
        symbol_name="verify_jwt",
        risk_score=85.0,
        domain_score=0.85,
        domain_tags=["auth"],
        coverage_score=0.2,
        coverage_source="report",
        diff_patch="diff1",
    )
    f2 = HunkRiskFinding(
        run_id=run2.id,
        file_path="auth/jwt.py",
        start_line=5,
        end_line=15,
        symbol_name="verify_jwt",
        risk_score=78.0,
        domain_score=0.85,
        domain_tags=["auth"],
        coverage_score=0.2,
        coverage_source="report",
        diff_patch="diff2",
    )
    session.add_all([f1, f2])
    session.commit()
    repo_id = repo.id
    session.close()

    res = client.get(f"/api/repos/{repo_id}/risk-debt")
    assert res.status_code == 200
    data = res.json()

    assert data["total_runs"] == 2
    assert data["risk_distribution"]["High"] == 2
    assert data["avg_repo_score"] == 81.5

    # Recurring hotspot
    hotspots = data["recurring_hotspots"]
    assert len(hotspots) >= 1
    top_spot = hotspots[0]
    assert top_spot["file_path"] == "auth/jwt.py"
    assert top_spot["symbol_name"] == "verify_jwt"
    assert top_spot["occurrences"] == 2
    assert top_spot["max_risk_score"] == 85.0

    # Chronic coverage gap
    gaps = data["chronic_coverage_gaps"]
    assert len(gaps) >= 1
    assert gaps[0]["symbol_name"] == "verify_jwt"
    assert gaps[0]["uncovered_occurrences"] == 2
