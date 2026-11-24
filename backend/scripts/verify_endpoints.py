"""
Verification script for DiffLens API endpoints against the seeded database.
"""

from fastapi.testclient import TestClient
from app.main import app

def test_seeded_endpoints():
    client = TestClient(app)

    # 1. GET /api/repos
    res = client.get("/api/repos")
    assert res.status_code == 200, f"GET /api/repos failed: {res.text}"
    repos = res.json()
    assert len(repos) >= 1, "No repositories returned"
    demo_repo = next((r for r in repos if r["full_name"] == "acme/payments-core"), None)
    assert demo_repo is not None, "acme/payments-core not found in repos"
    repo_id = demo_repo["id"]
    print(f"✓ GET /api/repos -> 200 OK ({len(repos)} repo(s), demo_id={repo_id})")

    # 2. GET /api/runs
    res = client.get("/api/runs")
    assert res.status_code == 200, f"GET /api/runs failed: {res.text}"
    runs = res.json()
    assert len(runs) >= 4, f"Expected at least 4 runs, got {len(runs)}"
    run_142 = next((r for r in runs if r["pr_number"] == 142), None)
    assert run_142 is not None, "PR #142 not found in runs"
    run_id = run_142["id"]
    print(f"✓ GET /api/runs -> 200 OK ({len(runs)} run(s) listed)")

    # 3. GET /api/runs/{id}
    res = client.get(f"/api/runs/{run_id}")
    assert res.status_code == 200, f"GET /api/runs/{run_id} failed: {res.text}"
    run_detail = res.json()
    assert run_detail["pr_number"] == 142
    assert len(run_detail["findings"]) >= 1
    assert "test_selection" in run_detail["summary"]
    assert "regression_signals" in run_detail["summary"]
    assert "interaction_findings" in run_detail["summary"]
    print(f"✓ GET /api/runs/{run_id} -> 200 OK (findings={len(run_detail['findings'])}, overall_score={run_detail['overall_score']})")

    # 4. PATCH /api/repos/{id}/settings
    res = client.patch(
        f"/api/repos/{repo_id}/settings",
        json={"risk_threshold_high": 72.0, "risk_threshold_medium": 38.0},
    )
    assert res.status_code == 200, f"PATCH /api/repos/{repo_id}/settings failed: {res.text}"
    updated_repo = res.json()
    assert updated_repo["risk_threshold_high"] == 72.0
    assert updated_repo["risk_threshold_medium"] == 38.0
    print(f"✓ PATCH /api/repos/{repo_id}/settings -> 200 OK (thresholds updated: high={updated_repo['risk_threshold_high']}, med={updated_repo['risk_threshold_medium']})")

    # 5. GET /api/repos/{id}/risk-debt
    res = client.get(f"/api/repos/{repo_id}/risk-debt")
    assert res.status_code == 200, f"GET /api/repos/{repo_id}/risk-debt failed: {res.text}"
    risk_debt = res.json()
    assert risk_debt["total_runs"] == 4
    assert risk_debt["risk_distribution"]["High"] == 2
    assert risk_debt["risk_distribution"]["Medium"] == 1
    assert risk_debt["risk_distribution"]["Low"] == 1
    assert len(risk_debt["recurring_hotspots"]) >= 1
    top_spot = risk_debt["recurring_hotspots"][0]
    assert top_spot["file_path"] == "auth/verify_token.py"
    assert top_spot["symbol_name"] == "verify_token"
    assert top_spot["occurrences"] == 2

    assert len(risk_debt["chronic_coverage_gaps"]) >= 1
    top_gap = risk_debt["chronic_coverage_gaps"][0]
    assert top_gap["symbol_name"] == "verify_token"
    assert top_gap["uncovered_occurrences"] == 2
    print(f"✓ GET /api/repos/{repo_id}/risk-debt -> 200 OK (hotspots={len(risk_debt['recurring_hotspots'])}, gaps={len(risk_debt['chronic_coverage_gaps'])})")

    print("\nALL 5 API ENDPOINTS VERIFIED SUCCESSFULLY WITH SEEDED DATA!")

if __name__ == "__main__":
    test_seeded_endpoints()
