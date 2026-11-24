"""
Unit and integration tests for Phase 6:
1. Runtime-Verified Blast Radius (runtime_tracer.py)
2. Deployment Impact Forecast (deployment_mapper.py)
3. Scorer weighting and pipeline integration
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.deployment_mapper import DeploymentMapper
from app.models import Base, PRAnalysisRun, Repository
from app.runtime_tracer import RuntimeTracer
from app.scorer import HunkMetrics, calculate_hunk_risk
from app.worker import process_pr_job


@pytest.fixture
def in_memory_db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = Session()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# 1. Runtime vs. Static Merge & Effective Weighting
# ---------------------------------------------------------------------------

def test_runtime_vs_static_merge():
    tracer = RuntimeTracer()

    static_callers = ["api/routes.py:route_a", "api/routes.py:route_b", "utils/legacy.py:dead_helper"]
    trace_data = {
        "edges": [
            {"caller": "api.routes.route_a", "callee": "auth.verify_token", "count": 25},
            {"caller": "api.routes.route_b", "callee": "auth.verify_token", "count": 12},
            {"caller": "dynamic_hook", "callee": "auth.verify_token", "count": 5},
        ]
    }

    blast_res = tracer.analyze_symbol(
        symbol_name="verify_token",
        file_path="auth/verify_token.py",
        static_callers=static_callers,
        trace_data=trace_data,
    )

    assert blast_res.runtime_verified_fan_in == 2
    assert blast_res.static_only_fan_in == 1
    assert blast_res.dynamic_only_fan_in == 1
    assert blast_res.blast_radius_confidence > 0.7

    statuses = {cv.caller: cv.status for cv in blast_res.caller_verifications}
    assert statuses["api/routes.py:route_a"] == "runtime_verified"
    assert statuses["api/routes.py:route_b"] == "runtime_verified"
    assert statuses["utils/legacy.py:dead_helper"] == "static_only"
    assert statuses["dynamic_hook"] == "dynamic_only"

    # Effective fan-in: 2 * 1.0 + 1 * 1.2 + 1 * 0.35 = 3.55
    assert blast_res.total_effective_fan_in == pytest.approx(3.55, rel=1e-2)

    # Scorer test: Runtime-verified hot callers vs all static-only callers
    metrics_hot = HunkMetrics(
        domain_sensitivity=0.8,
        fan_in=3,
        effective_fan_in=blast_res.total_effective_fan_in,  # 3.55
        coverage=0.3,
        delta_loc=20,
    )
    score_hot = calculate_hunk_risk(metrics_hot)

    # If all 3 callers were unverified dead code (3 * 0.35 = 1.05)
    metrics_dead = HunkMetrics(
        domain_sensitivity=0.8,
        fan_in=3,
        effective_fan_in=1.05,
        coverage=0.3,
        delta_loc=20,
    )
    score_dead = calculate_hunk_risk(metrics_dead)

    assert score_hot > score_dead, f"Expected hot runtime path ({score_hot}) to score higher than dead code ({score_dead})"


# ---------------------------------------------------------------------------
# 2. OpenTelemetry Span Parsing & Graceful Fallback
# ---------------------------------------------------------------------------

def test_opentelemetry_span_parsing():
    tracer = RuntimeTracer()

    otel_spans = [
        {
            "span_id": "span-1",
            "name": "HTTP POST /login",
            "attributes": {"code.function": "login_handler", "code.filepath": "api/auth.py"},
        },
        {
            "span_id": "span-2",
            "parent_span_id": "span-1",
            "name": "verify_credentials",
            "attributes": {"code.function": "verify_token", "code.filepath": "auth/tokens.py"},
        },
    ]

    blast_res = tracer.analyze_symbol(
        symbol_name="verify_token",
        file_path="auth/tokens.py",
        static_callers=["api/auth.py:login_handler"],
        trace_data=otel_spans,
    )

    assert blast_res.runtime_verified_fan_in == 1
    assert blast_res.static_only_fan_in == 0
    assert blast_res.caller_verifications[0].status == "runtime_verified"


def test_missing_trace_graceful_fallback():
    tracer = RuntimeTracer()

    static_callers = ["api/routes.py:route_a", "api/routes.py:route_b"]
    blast_res = tracer.analyze_symbol(
        symbol_name="verify_token",
        file_path="auth/verify_token.py",
        static_callers=static_callers,
        trace_data=None,  # No trace available
    )

    # Gracefully falls back to static fan-in with neutral confidence
    assert blast_res.runtime_verified_fan_in == 0
    assert blast_res.static_only_fan_in == 2
    assert blast_res.total_effective_fan_in == 2.0
    assert blast_res.blast_radius_confidence == 0.5


# ---------------------------------------------------------------------------
# 3. Deployment Impact Forecast
# ---------------------------------------------------------------------------

def test_deployment_impact_forecast():
    mapper = DeploymentMapper()

    docker_compose_yml = """
services:
  api:
    build: ./backend
    ports:
      - "8000:8000"
  worker:
    build: ./backend
    depends_on:
      - api
      - redis
  frontend:
    build: ./frontend
    ports:
      - "3000:3000"
"""

    routes_code = """
from fastapi import APIRouter
from auth import verify_token

router = APIRouter()

@router.post("/v1/charge")
def handle_charge(payload: dict):
    verify_token(payload.get("token"))
    return {"status": "ok"}
"""

    repo_snapshot = {
        "docker-compose.yml": docker_compose_yml,
        "backend/api/routes.py": routes_code,
        ".github/workflows/deploy-api.yml": "name: Deploy API\non:\n  push:\n    paths:\n      - 'backend/**'",
    }

    impact = mapper.analyze_impact(
        changed_files=["backend/auth/verify_token.py"],
        changed_symbols=["verify_token"],
        callers=["backend/api/routes.py:handle_charge"],
        repo_snapshot=repo_snapshot,
    )

    # 1. Directly affected 'api' service triggers downstream 'worker' service via depends_on
    assert "api" in impact.affected_services
    assert "worker" in impact.affected_services
    assert "frontend" not in impact.affected_services

    # 2. HTTP route discovered
    assert any("POST /v1/charge" in ep for ep in impact.affected_endpoints)

    # 3. Public-API impact tier because exposed endpoints are reachable
    assert impact.impact_tier == "Public-API"

    # 4. Triggered CI/CD workflow
    assert ".github/workflows/deploy-api.yml" in impact.affected_workflows


# ---------------------------------------------------------------------------
# 4. End-to-End Pipeline Worker Integration with Phase 6 Signals
# ---------------------------------------------------------------------------

def test_worker_phase6_integration(in_memory_db):
    repo = Repository(
        full_name="acme/phase6-repo",
        github_repo_id=778899,
        risk_threshold_high=70.0,
        risk_threshold_medium=40.0,
    )
    in_memory_db.add(repo)
    in_memory_db.commit()
    in_memory_db.refresh(repo)

    run = PRAnalysisRun(
        delivery_id="deliv-phase6-test",
        repo_id=repo.id,
        pr_number=55,
        head_sha="head123456789012345678901234567890123456",
        base_sha="base123456789012345678901234567890123456",
        status="queued",
    )
    in_memory_db.add(run)
    in_memory_db.commit()
    in_memory_db.refresh(run)

    auth_code = """
def verify_token(token: str):
    return len(token) > 0
"""
    routes_code = """
@app.post("/v1/pay")
def pay_route():
    return verify_token("test")
"""
    compose_yml = """
services:
  api:
    build: .
"""

    job_payload = {
        "run_id": run.id,
        "repo_full_name": repo.full_name,
        "pr_number": 55,
        "head_sha": run.head_sha,
        "base_sha": run.base_sha,
        "changed_files": ["auth.py"],
        "file_contents": {"auth.py": auth_code},
        "diff_hunks": [
            {
                "file_path": "auth.py",
                "start_line": 2,
                "end_line": 3,
                "hunk_content": "+    return len(token) > 0",
                "added_lines": ["    return len(token) > 0"],
            }
        ],
        "repo_snapshot": {
            "auth.py": auth_code,
            "routes.py": routes_code,
            "docker-compose.yml": compose_yml,
        },
        "runtime_trace": {
            "edges": [
                {"caller": "routes.py:pay_route", "callee": "auth.py:verify_token", "count": 10}
            ]
        },
    }

    completed_run = process_pr_job(job_payload, in_memory_db)

    assert completed_run.status == "completed"
    assert "deployment_impact" in completed_run.summary_json
    assert "runtime_blast_radius" in completed_run.summary_json

    dep = completed_run.summary_json["deployment_impact"]
    assert "api" in dep["affected_services"]
    assert any("POST /v1/pay" in ep for ep in dep["affected_endpoints"])

    assert len(completed_run.findings) == 1
    finding = completed_run.findings[0]
    assert finding.caller_verifications is not None
