"""
Tree-sitter AST parser, symbol extractor, call graph, and domain sensitivity analyzer.

GUARDRAIL: 100% deterministic. Zero LLM calls, zero network calls, zero API keys.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from tree_sitter import Language, Node, Parser
import tree_sitter_javascript as tsjs
import tree_sitter_python as tspy
import tree_sitter_typescript as tsts

from app.scorer import HunkMetrics

# ---------------------------------------------------------------------------
# Language & Parser Setup
# ---------------------------------------------------------------------------

PYTHON_LANGUAGE = Language(tspy.language())
JAVASCRIPT_LANGUAGE = Language(tsjs.language())
TYPESCRIPT_LANGUAGE = Language(tsts.language_typescript())
TSX_LANGUAGE = Language(tsts.language_tsx())

SUPPORTED_EXTENSIONS: dict[str, tuple[Language, str]] = {
    ".py": (PYTHON_LANGUAGE, "python"),
    ".js": (JAVASCRIPT_LANGUAGE, "javascript"),
    ".jsx": (JAVASCRIPT_LANGUAGE, "javascript"),
    ".ts": (TYPESCRIPT_LANGUAGE, "typescript"),
    ".tsx": (TSX_LANGUAGE, "tsx"),
}

COMMON_BUILTINS = {
    # Python
    "len", "print", "range", "str", "int", "float", "bool", "list", "dict",
    "set", "tuple", "isinstance", "issubclass", "getattr", "setattr", "hasattr",
    "open", "super", "type", "enumerate", "zip", "sum", "min", "max", "all",
    "any", "round", "sorted", "reversed", "id", "hash", "repr", "iter", "next",
    # JavaScript / TypeScript
    "console", "log", "error", "warn", "info", "debug", "map", "filter", "reduce",
    "tostring", "valueof", "push", "pop", "shift", "unshift", "foreach", "slice",
    "splice", "includes", "indexof", "join", "split", "keys", "values", "entries",
    "parse", "stringify", "resolve", "reject", "then", "catch", "finally",
}

# ---------------------------------------------------------------------------
# Data Models
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SymbolDefinition:
    symbol_name: str
    qualified_name: str
    file_path: str
    start_line: int  # 1-indexed
    end_line: int    # 1-indexed
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    kind: str = "function"  # "function", "method", "class", "module"


@dataclass
class ComplexityMetrics:
    max_nesting_depth: int = 0
    branches_count: int = 0
    async_calls_count: int = 0
    external_calls_count: int = 0


@dataclass
class DiffHunk:
    file_path: str
    start_line: int
    end_line: int
    added_lines: list[str] = field(default_factory=list)
    deleted_lines: list[str] = field(default_factory=list)
    hunk_content: str = ""


@dataclass
class HunkAnalysis:
    file_path: str
    start_line: int
    end_line: int
    symbols: list[SymbolDefinition]
    fan_in: int
    callers: list[str]
    domain_score: float
    domain_tags: list[str]
    complexity: ComplexityMetrics

    def to_hunk_metrics(self, coverage: float = 1.0, delta_loc: int | None = None) -> HunkMetrics:
        """
        Converts AST analysis directly into HunkMetrics for the deterministic scorer.
        """
        loc = delta_loc if delta_loc is not None else max(1, self.end_line - self.start_line + 1)
        return HunkMetrics(
            domain_sensitivity=self.domain_score,
            fan_in=self.fan_in,
            coverage=coverage,
            delta_loc=loc,
        )


# ---------------------------------------------------------------------------
# AST Parser & Symbol Extractor
# ---------------------------------------------------------------------------

class ASTAnalyzer:
    """
    Deterministic AST analyzer using Tree-sitter for Python and JS/TS.
    """

    def __init__(self) -> None:
        pass

    def get_parser(self, file_path: str) -> tuple[Optional[Parser], Optional[str]]:
        ext = Path(file_path).suffix.lower()
        if ext in SUPPORTED_EXTENSIONS:
            lang, lang_name = SUPPORTED_EXTENSIONS[ext]
            return Parser(lang), lang_name
        return None, None

    def parse_symbols(self, code: str, file_path: str) -> list[SymbolDefinition]:
        """
        Parses source code into symbols (functions, methods, classes).
        Returns empty list for unsupported file types.
        """
        parser, lang_name = self.get_parser(file_path)
        if parser is None or not code.strip():
            return []

        tree = parser.parse(code.encode("utf-8"))
        symbols: list[SymbolDefinition] = []

        if lang_name == "python":
            self._extract_python_symbols(tree.root_node, code.encode("utf-8"), file_path, symbols)
        elif lang_name in ("javascript", "typescript", "tsx"):
            self._extract_js_ts_symbols(tree.root_node, code.encode("utf-8"), file_path, symbols)

        return sorted(symbols, key=lambda s: (s.start_line, s.end_line))

    # -----------------------------------------------------------------------
    # Python Symbol Extraction
    # -----------------------------------------------------------------------

    def _extract_python_symbols(
        self,
        node: Node,
        code_bytes: bytes,
        file_path: str,
        symbols: list[SymbolDefinition],
        current_class: Optional[str] = None,
        pending_decorators: Optional[list[str]] = None,
    ) -> None:
        decorators = pending_decorators or []

        if node.type == "decorated_definition":
            extracted_decs: list[str] = []
            target_def: Optional[Node] = None
            for child in node.children:
                if child.type == "decorator":
                    extracted_decs.append(child.text.decode("utf-8").strip())
                elif child.type in ("function_definition", "class_definition"):
                    target_def = child

            if target_def is not None:
                self._extract_python_symbols(
                    target_def,
                    code_bytes,
                    file_path,
                    symbols,
                    current_class=current_class,
                    pending_decorators=extracted_decs,
                )
            return

        elif node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text:
                name = name_node.text.decode("utf-8")
                is_async = any(c.type == "async" for c in node.children)
                start_line = node.start_point.row + 1
                end_line = node.end_point.row + 1
                kind = "method" if current_class else "function"
                qname = f"{current_class}.{name}" if current_class else name

                symbols.append(
                    SymbolDefinition(
                        symbol_name=name,
                        qualified_name=qname,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        decorators=decorators,
                        is_async=is_async,
                        kind=kind,
                    )
                )

        elif node.type == "class_definition":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text:
                class_name = name_node.text.decode("utf-8")
                start_line = node.start_point.row + 1
                end_line = node.end_point.row + 1
                symbols.append(
                    SymbolDefinition(
                        symbol_name=class_name,
                        qualified_name=class_name,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        decorators=decorators,
                        is_async=False,
                        kind="class",
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        self._extract_python_symbols(
                            child,
                            code_bytes,
                            file_path,
                            symbols,
                            current_class=class_name,
                        )
                return

        # Recurse children for general blocks
        for child in node.children:
            self._extract_python_symbols(
                child,
                code_bytes,
                file_path,
                symbols,
                current_class=current_class,
            )

    # -----------------------------------------------------------------------
    # JS / TS Symbol Extraction
    # -----------------------------------------------------------------------

    def _extract_js_ts_symbols(
        self,
        node: Node,
        code_bytes: bytes,
        file_path: str,
        symbols: list[SymbolDefinition],
        current_class: Optional[str] = None,
        pending_decorators: Optional[list[str]] = None,
    ) -> None:
        decorators = pending_decorators or []

        # Handle export statements
        if node.type in ("export_statement", "export_default_declaration"):
            for child in node.children:
                if child.type in (
                    "function_declaration",
                    "generator_function_declaration",
                    "class_declaration",
                    "lexical_declaration",
                    "variable_declaration",
                ):
                    self._extract_js_ts_symbols(
                        child,
                        code_bytes,
                        file_path,
                        symbols,
                        current_class=current_class,
                        pending_decorators=decorators,
                    )
            return

        if node.type in ("function_declaration", "generator_function_declaration"):
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text:
                name = name_node.text.decode("utf-8")
                is_async = any(c.type == "async" for c in node.children)
                start_line = node.start_point.row + 1
                end_line = node.end_point.row + 1
                kind = "method" if current_class else "function"
                qname = f"{current_class}.{name}" if current_class else name
                symbols.append(
                    SymbolDefinition(
                        symbol_name=name,
                        qualified_name=qname,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        decorators=decorators,
                        is_async=is_async,
                        kind=kind,
                    )
                )

        elif node.type == "class_declaration":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text:
                class_name = name_node.text.decode("utf-8")
                start_line = node.start_point.row + 1
                end_line = node.end_point.row + 1
                symbols.append(
                    SymbolDefinition(
                        symbol_name=class_name,
                        qualified_name=class_name,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        decorators=decorators,
                        is_async=False,
                        kind="class",
                    )
                )
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        self._extract_js_ts_symbols(
                            child,
                            code_bytes,
                            file_path,
                            symbols,
                            current_class=class_name,
                        )
                return

        elif node.type == "method_definition":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text:
                name = name_node.text.decode("utf-8")
                is_async = any(c.type == "async" for c in node.children)
                start_line = node.start_point.row + 1
                end_line = node.end_point.row + 1
                qname = f"{current_class}.{name}" if current_class else name
                symbols.append(
                    SymbolDefinition(
                        symbol_name=name,
                        qualified_name=qname,
                        file_path=file_path,
                        start_line=start_line,
                        end_line=end_line,
                        decorators=decorators,
                        is_async=is_async,
                        kind="method",
                    )
                )

        elif node.type in ("lexical_declaration", "variable_declaration"):
            for child in node.children:
                if child.type == "variable_declarator":
                    id_node = child.child_by_field_name("name")
                    val_node = child.child_by_field_name("value")
                    if (
                        id_node
                        and id_node.text
                        and val_node
                        and val_node.type in ("arrow_function", "function_expression")
                    ):
                        name = id_node.text.decode("utf-8")
                        is_async = any(c.type == "async" for c in val_node.children)
                        start_line = node.start_point.row + 1
                        end_line = node.end_point.row + 1
                        qname = f"{current_class}.{name}" if current_class else name
                        symbols.append(
                            SymbolDefinition(
                                symbol_name=name,
                                qualified_name=qname,
                                file_path=file_path,
                                start_line=start_line,
                                end_line=end_line,
                                decorators=decorators,
                                is_async=is_async,
                                kind="function",
                            )
                        )

        for child in node.children:
            if child.type not in ("class_body",):
                self._extract_js_ts_symbols(
                    child,
                    code_bytes,
                    file_path,
                    symbols,
                    current_class=current_class,
                )

    # -----------------------------------------------------------------------
    # Diff Hunk -> Symbol Mapping
    # -----------------------------------------------------------------------

    def map_hunk_to_symbols(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
        file_symbols: list[SymbolDefinition],
    ) -> list[SymbolDefinition]:
        """
        Maps a diff hunk [start_line, end_line] to the overlapping symbols.
        If modification is outside any function/class, maps to a synthetic <module> scope.
        """
        overlapping = [
            sym
            for sym in file_symbols
            if max(start_line, sym.start_line) <= min(end_line, sym.end_line)
        ]

        if not overlapping:
            return [
                SymbolDefinition(
                    symbol_name="<module>",
                    qualified_name="<module>",
                    file_path=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    kind="module",
                )
            ]

        return overlapping

    # -----------------------------------------------------------------------
    # Call-Graph & fan_in Calculation
    # -----------------------------------------------------------------------

    def extract_calls(self, code: str, file_path: str) -> list[tuple[str, int]]:
        """
        Extracts direct and attribute function calls (callee_name, line_number).
        Ignores common language built-ins to avoid inflating blast radius.
        """
        parser, lang_name = self.get_parser(file_path)
        if parser is None or not code.strip():
            return []

        tree = parser.parse(code.encode("utf-8"))
        calls: list[tuple[str, int]] = []

        def _traverse(node: Node) -> None:
            if lang_name == "python" and node.type == "call":
                func_node = node.child_by_field_name("function")
                if func_node and func_node.text:
                    callee_text = func_node.text.decode("utf-8")
                    name = callee_text.split(".")[-1].strip()
                    if name.lower() not in COMMON_BUILTINS:
                        calls.append((name, node.start_point.row + 1))

            elif lang_name in ("javascript", "typescript", "tsx") and node.type == "call_expression":
                func_node = node.child_by_field_name("function")
                if func_node and func_node.text:
                    callee_text = func_node.text.decode("utf-8")
                    name = callee_text.split(".")[-1].strip()
                    if name.lower() not in COMMON_BUILTINS:
                        calls.append((name, node.start_point.row + 1))

            for child in node.children:
                _traverse(child)

        _traverse(tree.root_node)
        return calls

    def build_repository_call_graph(
        self, repo_files: Mapping[str, str]
    ) -> tuple[dict[str, set[str]], dict[str, list[SymbolDefinition]]]:
        """
        Builds repository-wide call graph:
        Returns:
            callees_to_callers: {callee_name: set_of_caller_qualified_names}
            symbols_by_file: {file_path: list_of_symbols}
        """
        symbols_by_file: dict[str, list[SymbolDefinition]] = {}
        for path, content in repo_files.items():
            symbols_by_file[path] = self.parse_symbols(content, path)

        callees_to_callers: dict[str, set[str]] = {}

        for path, content in repo_files.items():
            file_syms = symbols_by_file.get(path, [])
            calls = self.extract_calls(content, path)

            for callee_name, call_line in calls:
                # Find which symbol in this file contains the call line
                enclosing = [
                    s for s in file_syms
                    if s.kind in ("function", "method") and s.start_line <= call_line <= s.end_line
                ]
                # Smallest enclosing symbol
                caller_sym = min(enclosing, key=lambda s: s.end_line - s.start_line) if enclosing else None
                caller_identifier = caller_sym.qualified_name if caller_sym else f"{Path(path).stem}:<module>"

                if callee_name not in callees_to_callers:
                    callees_to_callers[callee_name] = set()
                callees_to_callers[callee_name].add(caller_identifier)

        return callees_to_callers, symbols_by_file

    def compute_fan_in(
        self,
        symbol: SymbolDefinition,
        callees_to_callers: Mapping[str, set[str]],
    ) -> tuple[int, list[str]]:
        """
        Computes fan-in (number of distinct caller symbols) and distinct callers list.
        """
        callers = set()
        # Look up by symbol_name and qualified_name
        if symbol.symbol_name in callees_to_callers:
            callers.update(callees_to_callers[symbol.symbol_name])
        if symbol.qualified_name in callees_to_callers:
            callers.update(callees_to_callers[symbol.qualified_name])

        # Exclude self-recursive calls from blast radius
        callers.discard(symbol.qualified_name)
        callers.discard(symbol.symbol_name)

        sorted_callers = sorted(list(callers))
        return len(sorted_callers), sorted_callers

    # -----------------------------------------------------------------------
    # Domain Sensitivity Detector (D_h)
    # -----------------------------------------------------------------------

    AUTH_KEYWORDS = {
        "token", "jwt", "oauth", "session", "password", "passwd", "hash",
        "bcrypt", "argon2", "permission", "permissions", "rbac", "auth",
        "authenticate", "authorize", "authorization", "login", "logout",
        "bearer", "api_key", "secret", "credentials", "verifier",
    }

    DB_KEYWORDS = {
        "select", "insert", "update", "delete", "drop", "alter", "truncate",
        "commit", "rollback", "cursor", "migration", "alembic", "transaction",
        "session.add", "session.delete", "executemany", "prisma", "typeorm",
        "sequelize", "sqlalchemy", "execute_sql",
    }

    DB_SQL_PATTERNS = [
        re.compile(r"\b(SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|DROP\s+TABLE|ALTER\s+TABLE|CREATE\s+TABLE|TRUNCATE)\b", re.IGNORECASE),
        re.compile(r"\b(BEGIN|COMMIT|ROLLBACK)\b", re.IGNORECASE),
    ]

    API_KEYWORDS = {
        "webhook", "x-hub-signature", "signature", "payload", "httpx",
        "requests", "fetch", "axios", "router", "endpoint",
    }

    API_DECORATOR_PATTERNS = [
        re.compile(r"@(app|router)\.(get|post|put|delete|patch|options|head)", re.IGNORECASE),
        re.compile(r"\bapp\.use\b", re.IGNORECASE),
    ]

    def detect_domain_sensitivity(
        self,
        symbols: Sequence[SymbolDefinition],
        code_snippet: str,
        file_path: str,
    ) -> tuple[float, list[str]]:
        """
        Determines domain sensitivity D_h in [0.0, 1.0] and returns matched domain tags.
        Auth: base 0.85
        DB: base 0.80
        API: base 0.65
        Multi-domain overlap increases score up to 1.0.
        Neutral utility code returns <= 0.1.
        """
        path_lower = file_path.lower()
        snippet_lower = code_snippet.lower()
        tags: set[str] = set()

        # Combine text context
        combined_text_parts = [snippet_lower, path_lower]
        for sym in symbols:
            combined_text_parts.append(sym.symbol_name.lower())
            combined_text_parts.append(sym.qualified_name.lower())
            for d in sym.decorators:
                combined_text_parts.append(d.lower())

        combined_text = " ".join(combined_text_parts)

        # 1. Auth check
        for kw in self.AUTH_KEYWORDS:
            if re.search(rf"\b{re.escape(kw)}\b", combined_text):
                tags.add("auth")
                break

        # 2. Database check
        for kw in self.DB_KEYWORDS:
            if kw in combined_text:
                tags.add("database")
                break
        if "database" not in tags:
            for pat in self.DB_SQL_PATTERNS:
                if pat.search(code_snippet):
                    tags.add("database")
                    break

        # 3. API check
        for kw in self.API_KEYWORDS:
            if re.search(rf"\b{re.escape(kw)}\b", combined_text):
                tags.add("api")
                break
        if "api" not in tags:
            for pat in self.API_DECORATOR_PATTERNS:
                if pat.search(code_snippet) or any(pat.search(d) for s in symbols for d in s.decorators):
                    tags.add("api")
                    break

        sorted_tags = sorted(list(tags))
        if not sorted_tags:
            return 0.05 if code_snippet.strip() else 0.0, []

        # Calculate composite score
        domain_weights = {"auth": 0.85, "database": 0.80, "api": 0.65}
        base_max = max(domain_weights.get(t, 0.5) for t in sorted_tags)

        if len(sorted_tags) >= 3:
            final_score = 1.0
        elif len(sorted_tags) == 2:
            final_score = min(1.0, base_max + 0.10)
        else:
            final_score = base_max

        return round(final_score, 2), sorted_tags

    # -----------------------------------------------------------------------
    # Structural Complexity Metrics
    # -----------------------------------------------------------------------

    BRANCH_NODES = {
        "if_statement", "elif_clause", "else_clause", "except_clause",
        "catch_clause", "case_clause", "switch_case", "conditional_expression",
        "match_statement", "case_pattern",
    }

    NESTING_NODES = {
        "if_statement", "for_statement", "while_statement", "try_statement",
        "switch_statement", "with_statement",
    }

    def compute_hunk_complexity(
        self,
        code: str,
        file_path: str,
        start_line: int,
        end_line: int,
    ) -> ComplexityMetrics:
        """
        Computes AST-level complexity metrics inside the given line span.
        """
        parser, lang_name = self.get_parser(file_path)
        if parser is None or not code.strip():
            return ComplexityMetrics()

        tree = parser.parse(code.encode("utf-8"))
        branches = 0
        async_calls = 0
        external_calls = 0
        max_nesting = 0

        def _walk(node: Node, current_depth: int) -> None:
            nonlocal branches, async_calls, external_calls, max_nesting

            node_start = node.start_point.row + 1
            node_end = node.end_point.row + 1

            # Check overlap with hunk lines
            if max(node_start, start_line) <= min(node_end, end_line):
                new_depth = current_depth
                if node.type in self.NESTING_NODES:
                    new_depth += 1
                    if new_depth > max_nesting:
                        max_nesting = new_depth

                if node.type in self.BRANCH_NODES:
                    branches += 1

                if node.type in ("await", "await_expression"):
                    async_calls += 1

                if (lang_name == "python" and node.type == "call") or (
                    lang_name in ("javascript", "typescript", "tsx") and node.type == "call_expression"
                ):
                    node_text = node.text.decode("utf-8", errors="ignore").lower()
                    if any(k in node_text for k in ("http", "fetch", "axios", "execute", "commit", "query")):
                        external_calls += 1

                for child in node.children:
                    _walk(child, new_depth)
            else:
                for child in node.children:
                    _walk(child, current_depth)

        _walk(tree.root_node, 0)
        return ComplexityMetrics(
            max_nesting_depth=max_nesting,
            branches_count=branches,
            async_calls_count=async_calls,
            external_calls_count=external_calls,
        )

    # -----------------------------------------------------------------------
    # Unified Analysis Entrypoint
    # -----------------------------------------------------------------------

    def analyze_pr_files(
        self,
        changed_files: Sequence[str],
        file_contents: Mapping[str, str],
        hunks: Sequence[DiffHunk],
        repo_snapshot: Optional[Mapping[str, str]] = None,
    ) -> list[HunkAnalysis]:
        """
        Analyzes PR diff hunks across changed files and computes per-hunk AST signals.
        """
        snapshot = repo_snapshot if repo_snapshot is not None else file_contents
        callees_to_callers, symbols_by_file = self.build_repository_call_graph(snapshot)

        results: list[HunkAnalysis] = []

        for hunk in hunks:
            fpath = hunk.file_path
            content = file_contents.get(fpath, "")
            file_symbols = symbols_by_file.get(fpath) or self.parse_symbols(content, fpath)

            # 1. Map hunk to symbols
            touched_symbols = self.map_hunk_to_symbols(
                fpath, hunk.start_line, hunk.end_line, file_symbols
            )

            # 2. Fan-in & callers
            all_callers: set[str] = set()
            max_fan_in = 0
            for sym in touched_symbols:
                fin, callers = self.compute_fan_in(sym, callees_to_callers)
                if fin > max_fan_in:
                    max_fan_in = fin
                all_callers.update(callers)

            # 3. Domain sensitivity
            hunk_code = hunk.hunk_content or "\n".join(hunk.added_lines)
            if not hunk_code and content:
                # Fallback to lines in file content
                lines = content.splitlines()
                start_idx = max(0, hunk.start_line - 1)
                end_idx = min(len(lines), hunk.end_line)
                hunk_code = "\n".join(lines[start_idx:end_idx])

            domain_score, domain_tags = self.detect_domain_sensitivity(
                touched_symbols, hunk_code, fpath
            )

            # 4. Complexity
            complexity = self.compute_hunk_complexity(
                content, fpath, hunk.start_line, hunk.end_line
            )

            results.append(
                HunkAnalysis(
                    file_path=fpath,
                    start_line=hunk.start_line,
                    end_line=hunk.end_line,
                    symbols=touched_symbols,
                    fan_in=max_fan_in,
                    callers=sorted(list(all_callers)),
                    domain_score=domain_score,
                    domain_tags=domain_tags,
                    complexity=complexity,
                )
            )

        return results
