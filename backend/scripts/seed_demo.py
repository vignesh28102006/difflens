"""
DiffLens Demo Database Seeding Script.

Populates the database with realistic multi-PR Change Intelligence data:
- Sample repo: acme/payments-core (github_repo_id=990011)
- 4 PR runs:
    * PR #142 (High Risk ~86) - Signature drift, broken co-change, low coverage, prioritized CI tests
    * PR #139 (High Risk ~78) - Recurring hotspot & chronic coverage gap on auth/verify_token.py
    * PR #135 (Medium Risk ~52) - Webhook event dispatcher refactor
    * PR #131 (Low Risk ~18) - Currency formatting utility change
- Fully idempotent: running multiple times safely replaces demo data without duplicates.
"""

from datetime import datetime, timedelta, timezone
import json
import logging
import sys

from app.crypto import encrypt_secret
from app.db import SessionLocal, init_db
from app.models import HunkRiskFinding, PRAnalysisRun, Repository

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("difflens-seed")


def seed_demo_data() -> None:
    init_db()
    session = SessionLocal()

    try:
        logger.info("Initializing demo seed for DiffLens...")

        # 1. Idempotent Repository Upsert
        repo_full_name = "acme/payments-core"
        github_repo_id = 990011

        repo = session.query(Repository).filter(Repository.full_name == repo_full_name).first()
        if not repo:
            repo = Repository(
                full_name=repo_full_name,
                github_repo_id=github_repo_id,
                installation_id=45678,
                risk_threshold_high=70.0,
                risk_threshold_medium=40.0,
                llm_provider="gemini",
                llm_model="gemini-2.5-flash",
                encrypted_api_key=encrypt_secret("AIzaSy-DemoMockedKeyForTestingDiffLensBYOK"),
            )
            session.add(repo)
            session.commit()
            session.refresh(repo)
            logger.info("Created demo repository '%s' (id=%d)", repo.full_name, repo.id)
        else:
            repo.github_repo_id = github_repo_id
            repo.risk_threshold_high = 70.0
            repo.risk_threshold_medium = 40.0
            repo.llm_provider = "gemini"
            repo.llm_model = "gemini-2.5-flash"
            repo.encrypted_api_key = encrypt_secret("AIzaSy-DemoMockedKeyForTestingDiffLensBYOK")
            session.commit()
            session.refresh(repo)
            logger.info("Updated existing demo repository '%s' (id=%d)", repo.full_name, repo.id)

        # Clear existing demo runs for this repo to guarantee idempotent, clean state
        existing_runs = session.query(PRAnalysisRun).filter(PRAnalysisRun.repo_id == repo.id).all()
        if existing_runs:
            for r in existing_runs:
                session.delete(r)
            session.commit()
            logger.info("Cleared %d existing runs for clean idempotency", len(existing_runs))

        now = datetime.now(timezone.utc)

        # -----------------------------------------------------------------------
        # PR #142 (HIGH RISK — Score ~86.4)
        # -----------------------------------------------------------------------
        pr142_summary = {
            "hunks_count": 1,
            "max_hunk_risk": 86.4,
            "high_risk_hunks": 1,
            "regression_signals": {
                "bug_fix_rates": {
                    "auth/verify_token.py": 0.42,
                    "auth/session_store.py": 0.38,
                },
                "co_change_warnings": [
                    {
                        "changed_file": "auth/verify_token.py",
                        "missing_partner": "auth/session_store.py",
                        "coupling_ratio": 0.85,
                        "co_change_commits": [
                            "e1f2a34 fix(auth): synchronize token verification with session store",
                            "b5c6d78 fix(security): revoke invalidated sessions in verify_token",
                            "f9a0b12 fix(auth): update claims format across token & session",
                        ],
                    }
                ],
            },
            "interaction_findings": [
                {
                    "source_file": "auth/verify_token.py",
                    "source_symbol": "verify_token",
                    "target_file": "api/routes.py",
                    "target_symbol": "handle_payment_webhook",
                    "interaction_type": "signature_drift",
                    "severity": "HIGH",
                    "evidence": "Function 'verify_token' added required parameter 'scope', but caller in 'api/routes.py' still invokes it with 1 argument.",
                }
            ],
            "test_selection": {
                "prioritized_tests": [
                    {
                        "test_file": "tests/test_auth.py",
                        "impact_score": 86.4,
                        "target_symbols": ["verify_token"],
                        "rationale": "Directly exercises modified function 'verify_token'",
                    },
                    {
                        "test_file": "tests/test_routes.py",
                        "impact_score": 69.1,
                        "target_symbols": ["handle_payment_webhook"],
                        "rationale": "Exercises upstream caller 'handle_payment_webhook' with signature drift",
                    },
                ],
                "total_tests_available": 32,
                "selected_tests_count": 7,
                "estimated_time_reduction_percent": 78,
                "runnable_command": "pytest tests/test_auth.py tests/test_routes.py -v",
            },
            "deployment_impact": {
                "affected_services": ["api", "worker"],
                "affected_endpoints": ["POST /v1/charge", "POST /webhooks/stripe", "POST /api/payments"],
                "affected_workflows": [".github/workflows/deploy-api.yml"],
                "impact_tier": "Public-API",
            },
            "runtime_blast_radius": {
                "total_runtime_verified": 4,
                "total_static_only": 2,
                "total_dynamic_only": 0,
                "confidence": 0.85,
            },
        }

        pr142_explanation = json.dumps({
            "risk_summary": "Added required parameter 'scope' to verify_token without updating 6 upstream callers, and omitted coupled partner auth/session_store.py.",
            "why_its_risky": "Un-updated callers in api/routes.py will raise TypeError at runtime when processing live payment webhooks. Furthermore, session revocation logic in auth/session_store.py was omitted despite historical 85% co-change coupling across 3 critical security patches.",
            "how_to_fix": "Provide a backward-compatible default value for 'scope' (e.g., scope: str = 'payment:read') or update all 6 calling sites in api/routes.py and middleware/auth.py. Also review whether auth/session_store.py needs corresponding claim validation updates.",
            "review_checklist": [
                "Verify all 6 caller invocations pass valid scope tokens",
                "Inspect auth/session_store.py to ensure session revocation handles the new scope parameter",
                "Add unit test verifying TypeError is not thrown when default scope is used",
                "Ensure integration tests in tests/test_routes.py pass with new scope requirements",
            ],
            "suggested_test_code": (
                "def test_verify_token_backward_compat():\n"
                "    # Verify default scope avoids TypeError in legacy callers\n"
                "    claims = verify_token('valid.jwt.token')\n"
                "    assert claims['scope'] == 'default'\n\n"
                "def test_routes_with_scope():\n"
                "    client = TestClient(app)\n"
                "    res = client.post('/api/payments', headers={'Authorization': 'Bearer test'})\n"
                "    assert res.status_code == 200\n"
            ),
        })

        run_142 = PRAnalysisRun(
            delivery_id="seed-pr-142-delivery-001",
            repo_id=repo.id,
            pr_number=142,
            head_sha="a1b2c3d4e5f60718293a4b5c6d7e8f9a0b1c2d3e",
            base_sha="f0e1d2c3b4a5968778695a4b3c2d1e0f1a2b3c4d",
            status="completed",
            overall_score=86.4,
            risk_level="High",
            summary_json=pr142_summary,
            created_at=now - timedelta(minutes=15),
            completed_at=now - timedelta(minutes=14),
        )
        session.add(run_142)
        session.commit()
        session.refresh(run_142)

        finding_142 = HunkRiskFinding(
            run_id=run_142.id,
            file_path="auth/verify_token.py",
            start_line=18,
            end_line=42,
            symbol_name="verify_token",
            risk_score=86.4,
            domain_score=0.85,
            domain_tags=["auth", "security", "tokens"],
            fan_in=6,
            callers=[
                "api/routes.py:handle_payment_webhook",
                "api/routes.py:refund_transaction",
                "api/routes.py:create_subscription",
                "middleware/auth.py:authenticate_request",
                "jobs/reconciliation.py:verify_batch_token",
                "admin/views.py:admin_override",
            ],
            caller_verifications={
                "api/routes.py:handle_payment_webhook": "runtime_verified",
                "api/routes.py:refund_transaction": "runtime_verified",
                "api/routes.py:create_subscription": "runtime_verified",
                "middleware/auth.py:authenticate_request": "runtime_verified",
                "jobs/reconciliation.py:verify_batch_token": "static_only",
                "admin/views.py:admin_override": "static_only",
            },
            coverage_score=0.15,
            coverage_source="coverage.json",
            uncovered_lines=[24, 25, 26, 27, 28, 29, 30, 31, 35, 36],
            diff_patch=(
                "@@ -18,7 +18,10 @@ def verify_token(token: str) -> dict:\n"
                "-def verify_token(token: str) -> dict:\n"
                "+def verify_token(token: str, scope: str) -> dict:\n"
                "+    if not check_scope_permissions(scope):\n"
                "+        raise PermissionError('Invalid scope for token access')\n"
                "     decoded = jwt.decode(token, SECRET_KEY, algorithms=['HS256'])\n"
            ),
            explanation=pr142_explanation,
        )
        session.add(finding_142)

        # -----------------------------------------------------------------------
        # PR #139 (HIGH RISK — Score ~78.5)
        # Recurring hotspot and chronic coverage gap on auth/verify_token.py
        # -----------------------------------------------------------------------
        pr139_summary = {
            "hunks_count": 2,
            "max_hunk_risk": 78.5,
            "high_risk_hunks": 2,
            "regression_signals": {
                "bug_fix_rates": {"auth/verify_token.py": 0.40, "db/ledger_queries.py": 0.35},
                "co_change_warnings": [],
            },
            "interaction_findings": [],
            "test_selection": {
                "prioritized_tests": [
                    {
                        "test_file": "tests/test_auth.py",
                        "impact_score": 78.5,
                        "target_symbols": ["verify_token"],
                        "rationale": "Modified critical auth verification",
                    },
                    {
                        "test_file": "tests/test_ledger.py",
                        "impact_score": 76.0,
                        "target_symbols": ["execute_ledger_transaction"],
                        "rationale": "Modified financial database transaction queries",
                    },
                ],
                "total_tests_available": 32,
                "selected_tests_count": 8,
                "estimated_time_reduction_percent": 75,
                "runnable_command": "pytest tests/test_auth.py tests/test_ledger.py -v",
            },
        }

        run_139 = PRAnalysisRun(
            delivery_id="seed-pr-139-delivery-002",
            repo_id=repo.id,
            pr_number=139,
            head_sha="c3d4e5f6a1b20718293a4b5c6d7e8f9a0b1c2d3e",
            base_sha="d4e5f6a1b2c30718293a4b5c6d7e8f9a0b1c2d3e",
            status="completed",
            overall_score=78.5,
            risk_level="High",
            summary_json=pr139_summary,
            created_at=now - timedelta(hours=3),
            completed_at=now - timedelta(hours=3, minutes=-1),
        )
        session.add(run_139)
        session.commit()
        session.refresh(run_139)

        finding_139_a = HunkRiskFinding(
            run_id=run_139.id,
            file_path="auth/verify_token.py",
            start_line=45,
            end_line=70,
            symbol_name="verify_token",
            risk_score=78.5,
            domain_score=0.85,
            domain_tags=["auth", "security"],
            fan_in=5,
            callers=["api/routes.py", "middleware/auth.py", "services/auth_service.py"],
            coverage_score=0.20,
            coverage_source="coverage.json",
            uncovered_lines=[50, 51, 52, 53, 54],
            diff_patch="@@ -45,6 +45,9 @@ def verify_token(token: str):\n+    if is_blacklisted(token):\n+        raise SecurityException('Revoked token')",
            explanation=None,
        )
        finding_139_b = HunkRiskFinding(
            run_id=run_139.id,
            file_path="db/ledger_queries.py",
            start_line=12,
            end_line=40,
            symbol_name="execute_ledger_transaction",
            risk_score=76.0,
            domain_score=0.90,
            domain_tags=["database", "financial", "ledger"],
            fan_in=4,
            callers=["services/ledger.py:record_entry", "api/payments.py:capture"],
            coverage_score=0.18,
            coverage_source="coverage.json",
            uncovered_lines=[20, 21, 22, 23],
            diff_patch="@@ -12,5 +12,12 @@ def execute_ledger_transaction(tx_id: str):\n+    with db_conn.transaction(isolation='SERIALIZABLE'):\n+        apply_balance_lock(tx_id)",
            explanation=None,
        )
        session.add_all([finding_139_a, finding_139_b])

        # -----------------------------------------------------------------------
        # PR #135 (MEDIUM RISK — Score ~52.0)
        # -----------------------------------------------------------------------
        pr135_summary = {
            "hunks_count": 1,
            "max_hunk_risk": 52.0,
            "high_risk_hunks": 0,
            "regression_signals": {
                "bug_fix_rates": {"api/webhooks.py": 0.22},
                "co_change_warnings": [],
            },
            "interaction_findings": [],
            "test_selection": {
                "prioritized_tests": [
                    {
                        "test_file": "tests/test_webhooks.py",
                        "impact_score": 52.0,
                        "target_symbols": ["dispatch_event"],
                        "rationale": "Covers modified webhook dispatcher function",
                    }
                ],
                "total_tests_available": 32,
                "selected_tests_count": 4,
                "estimated_time_reduction_percent": 58,
                "runnable_command": "pytest tests/test_webhooks.py -v",
            },
        }

        run_135 = PRAnalysisRun(
            delivery_id="seed-pr-135-delivery-003",
            repo_id=repo.id,
            pr_number=135,
            head_sha="e5f6a1b2c3d40718293a4b5c6d7e8f9a0b1c2d3e",
            base_sha="f6a1b2c3d4e50718293a4b5c6d7e8f9a0b1c2d3e",
            status="completed",
            overall_score=52.0,
            risk_level="Medium",
            summary_json=pr135_summary,
            created_at=now - timedelta(days=1, hours=2),
            completed_at=now - timedelta(days=1, hours=1, minutes=58),
        )
        session.add(run_135)
        session.commit()
        session.refresh(run_135)

        finding_135 = HunkRiskFinding(
            run_id=run_135.id,
            file_path="api/webhooks.py",
            start_line=15,
            end_line=45,
            symbol_name="dispatch_event",
            risk_score=52.0,
            domain_score=0.60,
            domain_tags=["api", "network"],
            fan_in=3,
            callers=["main.py:webhook_ingress", "jobs/worker.py:retry_event"],
            coverage_score=0.55,
            coverage_source="coverage.json",
            uncovered_lines=[28, 29, 30],
            diff_patch="@@ -15,6 +15,10 @@ def dispatch_event(event_type: str, data: dict):\n+    headers = {'X-Delivery-Attempt': str(data.get('attempt', 1))}\n+    return client.post(WEBHOOK_URL, json=data, headers=headers)",
            explanation=None,
        )
        session.add(finding_135)

        # -----------------------------------------------------------------------
        # PR #131 (LOW RISK — Score ~18.0)
        # -----------------------------------------------------------------------
        pr131_summary = {
            "hunks_count": 1,
            "max_hunk_risk": 18.0,
            "high_risk_hunks": 0,
            "regression_signals": {
                "bug_fix_rates": {"utils/formatting.py": 0.05},
                "co_change_warnings": [],
            },
            "interaction_findings": [],
            "test_selection": {
                "prioritized_tests": [
                    {
                        "test_file": "tests/test_formatting.py",
                        "impact_score": 18.0,
                        "target_symbols": ["format_currency_amount"],
                        "rationale": "Covers string currency formatting utility",
                    }
                ],
                "total_tests_available": 32,
                "selected_tests_count": 2,
                "estimated_time_reduction_percent": 90,
                "runnable_command": "pytest tests/test_formatting.py -v",
            },
        }

        run_131 = PRAnalysisRun(
            delivery_id="seed-pr-131-delivery-004",
            repo_id=repo.id,
            pr_number=131,
            head_sha="f6a1b2c3d4e50718293a4b5c6d7e8f9a0b1c2d3e",
            base_sha="1b2c3d4e5f6a0718293a4b5c6d7e8f9a0b1c2d3e",
            status="completed",
            overall_score=18.0,
            risk_level="Low",
            summary_json=pr131_summary,
            created_at=now - timedelta(days=3),
            completed_at=now - timedelta(days=3, minutes=-1),
        )
        session.add(run_131)
        session.commit()
        session.refresh(run_131)

        finding_131 = HunkRiskFinding(
            run_id=run_131.id,
            file_path="utils/formatting.py",
            start_line=8,
            end_line=25,
            symbol_name="format_currency_amount",
            risk_score=18.0,
            domain_score=0.10,
            domain_tags=["utility"],
            fan_in=1,
            callers=["views/receipt.py:render_receipt"],
            coverage_score=0.92,
            coverage_source="coverage.json",
            uncovered_lines=[22],
            diff_patch="@@ -8,4 +8,7 @@ def format_currency_amount(cents: int) -> str:\n-    return f'${cents / 100:.2f}'\n+    dollars = cents / 100\n+    return f'${dollars:,.2f}'",
            explanation=None,
        )
        session.add(finding_131)

        session.commit()
        logger.info("Successfully seeded 4 PR runs and associated findings for '%s'!", repo_full_name)

    except Exception as e:
        session.rollback()
        logger.exception("Failed to seed demo data: %s", e)
        sys.exit(1)
    finally:
        session.close()


if __name__ == "__main__":
    seed_demo_data()
