import json
import pytest
from app.coverage_mapper import (
    CoverageReportParser,
    compute_hunk_coverage,
    is_test_file,
)
from app.scorer import HunkMetrics, calculate_hunk_risk


def test_is_test_file_detection():
    assert is_test_file("tests/test_auth.py") is True
    assert is_test_file("backend/app/auth_test.py") is True
    assert is_test_file("src/components/Header.test.tsx") is True
    assert is_test_file("src/utils/math.spec.js") is True
    assert is_test_file("src/auth.py") is False
    assert is_test_file("src/components/Header.tsx") is False


# ---------------------------------------------------------------------------
# 1. Strategy A: coverage.py JSON Report
# ---------------------------------------------------------------------------

def test_coverage_json_parsing_and_scoring():
    coverage_json_data = {
        "files": {
            "app/auth.py": {
                "executed_lines": [10, 11, 12, 13],
                "missing_lines": [14, 15],
            }
        }
    }

    # Hunk spans lines 10-15: 4 covered, 2 missing -> 4/6 = 0.6667
    res = compute_hunk_coverage(
        file_path="app/auth.py",
        hunk_lines=[10, 11, 12, 13, 14, 15],
        touched_symbols=["login_user"],
        coverage_report=coverage_json_data,
    )

    assert res.coverage_source == "report"
    assert round(res.coverage_score, 2) == 0.67
    assert res.covered_lines == [10, 11, 12, 13]
    assert res.uncovered_lines == [14, 15]


def test_coverage_json_completely_uncovered():
    coverage_json_data = {
        "files": {
            "app/auth.py": {
                "executed_lines": [1, 2, 3],
                "missing_lines": [10, 11, 12],
            }
        }
    }

    res = compute_hunk_coverage(
        file_path="app/auth.py",
        hunk_lines=[10, 11, 12],
        touched_symbols=["new_untested_func"],
        coverage_report=coverage_json_data,
    )

    assert res.coverage_source == "report"
    assert res.coverage_score == 0.0
    assert res.covered_lines == []
    assert res.uncovered_lines == [10, 11, 12]


# ---------------------------------------------------------------------------
# 2. Strategy A: Cobertura XML Report
# ---------------------------------------------------------------------------

def test_cobertura_xml_parsing_and_scoring():
    xml_data = """<?xml version="1.0" ?>
<coverage version="7.4.0">
    <packages>
        <package name="app">
            <classes>
                <class filename="app/db.py" name="db.py">
                    <lines>
                        <line hits="1" number="20"/>
                        <line hits="1" number="21"/>
                        <line hits="0" number="22"/>
                        <line hits="0" number="23"/>
                    </lines>
                </class>
            </classes>
        </package>
    </packages>
</coverage>
"""
    # 2 hits out of 4 lines in hunk 20-23 -> 50% coverage
    res = compute_hunk_coverage(
        file_path="app/db.py",
        hunk_lines=[20, 21, 22, 23],
        touched_symbols=["commit_tx"],
        coverage_report=xml_data,
    )

    assert res.coverage_source == "report"
    assert res.coverage_score == 0.50
    assert res.covered_lines == [20, 21]
    assert res.uncovered_lines == [22, 23]


# ---------------------------------------------------------------------------
# 3. Strategy B: Heuristic Fallback
# ---------------------------------------------------------------------------

def test_heuristic_untested_symbol():
    repo_snapshot = {
        "tests/test_other.py": "def test_something(): assert 1 == 1"
    }

    res = compute_hunk_coverage(
        file_path="app/secret.py",
        hunk_lines=[1, 2, 3],
        touched_symbols=["untested_auth_token"],
        coverage_report=None,
        changed_files=["app/secret.py"],
        repo_snapshot=repo_snapshot,
    )

    assert res.coverage_source == "none"
    assert res.coverage_score == 0.0
    assert res.uncovered_lines == [1, 2, 3]


def test_heuristic_symbol_in_existing_tests():
    repo_snapshot = {
        "tests/test_auth.py": "def test_login(): verify_jwt('test_token')"
    }

    res = compute_hunk_coverage(
        file_path="app/auth.py",
        hunk_lines=[5, 6, 7],
        touched_symbols=["verify_jwt"],
        coverage_report=None,
        changed_files=["app/auth.py"],  # No test files in this PR
        repo_snapshot=repo_snapshot,
    )

    assert res.coverage_source == "heuristic"
    assert res.coverage_score == 0.50


def test_heuristic_symbol_in_pr_modified_tests():
    repo_snapshot = {
        "tests/test_auth.py": "def test_login(): verify_jwt('new_token')"
    }

    res = compute_hunk_coverage(
        file_path="app/auth.py",
        hunk_lines=[5, 6, 7],
        touched_symbols=["verify_jwt"],
        coverage_report=None,
        changed_files=["app/auth.py", "tests/test_auth.py"],  # PR modifies both
        repo_snapshot=repo_snapshot,
    )

    assert res.coverage_source == "heuristic"
    assert res.coverage_score == 0.75


# ---------------------------------------------------------------------------
# 4. Integration with Risk Scorer
# ---------------------------------------------------------------------------

def test_coverage_penalty_in_risk_formula():
    # Identical changes except coverage: 100% covered vs 0% covered
    covered_metrics = HunkMetrics(
        domain_sensitivity=0.30,
        fan_in=0,
        coverage=1.0,  # Full test coverage
        delta_loc=10,
    )
    uncovered_metrics = HunkMetrics(
        domain_sensitivity=0.30,
        fan_in=0,
        coverage=0.0,  # Zero test coverage
        delta_loc=10,
    )

    covered_risk = calculate_hunk_risk(covered_metrics)
    uncovered_risk = calculate_hunk_risk(uncovered_metrics)

    # Missing coverage must noticeably amplify risk
    assert uncovered_risk > covered_risk
    assert (uncovered_risk - covered_risk) >= 15.0
