import pytest
from app.ast_analyzer import (
    ASTAnalyzer,
    DiffHunk,
    HunkAnalysis,
    SymbolDefinition,
)
from app.scorer import calculate_hunk_risk


@pytest.fixture
def analyzer():
    return ASTAnalyzer()


# ---------------------------------------------------------------------------
# 1. Python & TypeScript Symbol Extraction
# ---------------------------------------------------------------------------

def test_extract_python_symbols(analyzer):
    py_code = """
@router.post("/login")
async def login_user(username: str, password: str):
    return {"token": "jwt_token"}

class UserManager:
    def __init__(self, db):
        self.db = db

    async def get_user(self, user_id: int):
        return self.db.query(user_id)
"""
    symbols = analyzer.parse_symbols(py_code, "app/auth.py")
    assert len(symbols) == 4

    # Function 1: login_user
    login_fn = next(s for s in symbols if s.symbol_name == "login_user")
    assert login_fn.qualified_name == "login_user"
    assert login_fn.is_async is True
    assert login_fn.kind == "function"
    assert any("router.post" in d for d in login_fn.decorators)
    assert login_fn.start_line <= login_fn.end_line

    # Class: UserManager
    user_cls = next(s for s in symbols if s.symbol_name == "UserManager")
    assert user_cls.kind == "class"

    # Method: get_user
    get_user_m = next(s for s in symbols if s.symbol_name == "get_user")
    assert get_user_m.qualified_name == "UserManager.get_user"
    assert get_user_m.is_async is True
    assert get_user_m.kind == "method"


def test_extract_typescript_symbols(analyzer):
    ts_code = """
export class AuthService {
    async verifyToken(token: string): Promise<boolean> {
        return token.length > 0;
    }
}

export const checkAuth = async (token: string) => {
    return true;
};

function helper(val: number): number {
    return val + 1;
}
"""
    symbols = analyzer.parse_symbols(ts_code, "src/auth.ts")
    assert len(symbols) == 4

    auth_cls = next(s for s in symbols if s.symbol_name == "AuthService")
    assert auth_cls.kind == "class"

    verify_m = next(s for s in symbols if s.symbol_name == "verifyToken")
    assert verify_m.qualified_name == "AuthService.verifyToken"
    assert verify_m.is_async is True
    assert verify_m.kind == "method"

    arrow_fn = next(s for s in symbols if s.symbol_name == "checkAuth")
    assert arrow_fn.qualified_name == "checkAuth"
    assert arrow_fn.is_async is True
    assert arrow_fn.kind == "function"

    helper_fn = next(s for s in symbols if s.symbol_name == "helper")
    assert helper_fn.is_async is False
    assert helper_fn.kind == "function"


# ---------------------------------------------------------------------------
# 2. Hunk-to-Symbol Mapping
# ---------------------------------------------------------------------------

def test_hunk_to_symbol_mapping_enclosed_method(analyzer):
    code = """def first_func():
    return 1

def target_func():
    x = 10
    return x * 2

def third_func():
    return 3
"""
    symbols = analyzer.parse_symbols(code, "utils.py")
    # target_func spans lines 4 to 6
    mapped = analyzer.map_hunk_to_symbols("utils.py", start_line=5, end_line=5, file_symbols=symbols)
    assert len(mapped) == 1
    assert mapped[0].symbol_name == "target_func"


def test_hunk_to_symbol_mapping_module_level(analyzer):
    code = """import os
import sys

API_KEY = "dev_secret"

def calculate():
    return 42
"""
    symbols = analyzer.parse_symbols(code, "config.py")
    # Hunk modifying lines 1-3 (top-level imports and constant)
    mapped = analyzer.map_hunk_to_symbols("config.py", start_line=1, end_line=3, file_symbols=symbols)
    assert len(mapped) == 1
    assert mapped[0].symbol_name == "<module>"
    assert mapped[0].kind == "module"


# ---------------------------------------------------------------------------
# 3. Call-Graph & fan_in Calculation
# ---------------------------------------------------------------------------

def test_repository_call_graph_and_fan_in(analyzer):
    repo_files = {
        "auth.py": """
def verify_token(token: str) -> bool:
    return len(token) > 10
""",
        "routes_a.py": """
from auth import verify_token

def handle_profile(req):
    if not verify_token(req.token):
        return 401
    return 200

def handle_settings(req):
    verify_token(req.token)
    return 200
""",
        "routes_b.py": """
from auth import verify_token

def handle_admin(req):
    return verify_token(req.token)
""",
    }

    callees_to_callers, symbols_by_file = analyzer.build_repository_call_graph(repo_files)
    auth_sym = next(s for s in symbols_by_file["auth.py"] if s.symbol_name == "verify_token")

    fan_in, callers = analyzer.compute_fan_in(auth_sym, callees_to_callers)

    assert fan_in == 3
    assert "handle_profile" in callers
    assert "handle_settings" in callers
    assert "handle_admin" in callers


# ---------------------------------------------------------------------------
# 4. Domain Sensitivity (D_h)
# ---------------------------------------------------------------------------

def test_domain_sensitivity_auth(analyzer):
    code = """
def update_jwt_session(token: str, secret: str):
    session = decode_jwt(token)
    return session
"""
    syms = analyzer.parse_symbols(code, "session.py")
    score, tags = analyzer.detect_domain_sensitivity(syms, code, "session.py")

    assert score >= 0.8
    assert "auth" in tags


def test_domain_sensitivity_database(analyzer):
    code = """
def run_user_migration(db):
    db.execute("ALTER TABLE users ADD COLUMN is_active BOOLEAN")
    db.commit()
"""
    syms = analyzer.parse_symbols(code, "migrations.py")
    score, tags = analyzer.detect_domain_sensitivity(syms, code, "migrations.py")

    assert score >= 0.8
    assert "database" in tags


def test_domain_sensitivity_neutral_utility(analyzer):
    code = """
def format_title_string(text: str) -> str:
    return text.strip().title()
"""
    syms = analyzer.parse_symbols(code, "formatters.py")
    score, tags = analyzer.detect_domain_sensitivity(syms, code, "formatters.py")

    assert score <= 0.2
    assert len(tags) == 0


# ---------------------------------------------------------------------------
# 5. Unsupported File Fallback
# ---------------------------------------------------------------------------

def test_unsupported_file_graceful_fallback(analyzer):
    md_content = "# Project Documentation\n\nThis is a readme."
    symbols = analyzer.parse_symbols(md_content, "README.md")
    assert symbols == []

    calls = analyzer.extract_calls(md_content, "README.md")
    assert calls == []

    score, tags = analyzer.detect_domain_sensitivity([], md_content, "README.md")
    assert score <= 0.1
    assert tags == []

    complexity = analyzer.compute_hunk_complexity(md_content, "README.md", 1, 3)
    assert complexity.branches_count == 0
    assert complexity.max_nesting_depth == 0


# ---------------------------------------------------------------------------
# 6. Unified Analysis to Scorer Integration
# ---------------------------------------------------------------------------

def test_analyze_pr_files_to_scorer_integration(analyzer):
    auth_code = """
def verify_token(token: str):
    if not token:
        return False
    return True
"""
    routes_code = """
def handle_route_1(token):
    verify_token(token)

def handle_route_2(token):
    verify_token(token)
"""
    repo = {
        "auth.py": auth_code,
        "routes.py": routes_code,
    }

    hunk = DiffHunk(
        file_path="auth.py",
        start_line=2,
        end_line=5,
        added_lines=["    if not token: return False", "    return True"],
        hunk_content="def verify_token(token: str):\n    if not token:\n        return False",
    )

    analyses = analyzer.analyze_pr_files(
        changed_files=["auth.py"],
        file_contents={"auth.py": auth_code},
        hunks=[hunk],
        repo_snapshot=repo,
    )

    assert len(analyses) == 1
    analysis = analyses[0]

    assert analysis.fan_in == 2
    assert "handle_route_1" in analysis.callers
    assert "handle_route_2" in analysis.callers
    assert "auth" in analysis.domain_tags
    assert analysis.domain_score >= 0.85

    # Check direct conversion to HunkMetrics and deterministic scoring
    hunk_metrics = analysis.to_hunk_metrics(coverage=0.0, delta_loc=10)
    risk_score = calculate_hunk_risk(hunk_metrics)

    # High domain risk + 2 callers + 0% coverage -> elevated risk score
    assert risk_score > 85.0
