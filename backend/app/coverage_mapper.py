"""
Deterministic test coverage mapper (C_h in [0.0, 1.0]).

GUARDRAIL: 100% deterministic. Zero LLM calls, zero network calls, zero API keys.
"""

from __future__ import annotations

import json
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set


@dataclass
class CoverageResult:
    coverage_score: float  # C_h in [0.0, 1.0]
    coverage_source: str   # "report", "heuristic", "none"
    covered_lines: list[int] = field(default_factory=list)
    uncovered_lines: list[int] = field(default_factory=list)
    executable_lines: list[int] = field(default_factory=list)


TEST_FILE_PATTERNS = [
    re.compile(r"(^|[/\\])(test_[^/\\]+\.py|[^/\\]+_test\.py)$", re.IGNORECASE),
    re.compile(r"(^|[/\\])[^/\\]+\.(test|spec)\.(ts|tsx|js|jsx)$", re.IGNORECASE),
]


def is_test_file(file_path: str) -> bool:
    """Checks if a file path is a test file based on standard naming conventions."""
    normalized = file_path.replace("\\", "/")
    return any(p.search(normalized) is not None for p in TEST_FILE_PATTERNS)


def normalize_file_path(path_str: str) -> str:
    """Normalizes file path for comparison across systems and report formats."""
    return path_str.replace("\\", "/").strip().lstrip("./")


# ---------------------------------------------------------------------------
# Strategy A: Explicit Coverage Report Parsing
# ---------------------------------------------------------------------------

class CoverageReportParser:
    """
    Parses coverage reports:
    - coverage.py JSON (files[path].executed_lines / missing_lines)
    - Cobertura XML (class[filename] -> line[number, hits])
    - Jest / Istanbul coverage-final.json (statementMap, s)
    """

    @staticmethod
    def parse_report_data(report_content: str | bytes | dict) -> dict[str, dict[str, set[int]]]:
        """
        Returns normalized mapping:
        {
            normalized_file_path: {
                "executed": {1, 2, ...},
                "missing": {3, 4, ...}
            }
        }
        """
        if isinstance(report_content, (str, bytes)):
            text = report_content.decode("utf-8") if isinstance(report_content, bytes) else report_content.strip()
            if text.startswith("<"):
                return CoverageReportParser._parse_cobertura_xml(text)
            else:
                try:
                    data = json.loads(text)
                    return CoverageReportParser._parse_json_report(data)
                except json.JSONDecodeError:
                    return {}
        elif isinstance(report_content, dict):
            return CoverageReportParser._parse_json_report(report_content)
        return {}

    @staticmethod
    def _parse_cobertura_xml(xml_content: str) -> dict[str, dict[str, set[int]]]:
        result: dict[str, dict[str, set[int]]] = {}
        try:
            root = ET.fromstring(xml_content)
            for cls_elem in root.iter("class"):
                filename = cls_elem.get("filename")
                if not filename:
                    continue
                norm_path = normalize_file_path(filename)
                executed: set[int] = set()
                missing: set[int] = set()

                for line_elem in cls_elem.iter("line"):
                    try:
                        line_num = int(line_elem.get("number", "0"))
                        hits = int(line_elem.get("hits", "0"))
                        if hits > 0:
                            executed.add(line_num)
                        else:
                            missing.add(line_num)
                    except ValueError:
                        continue

                result[norm_path] = {"executed": executed, "missing": missing}
        except ET.ParseError:
            pass
        return result

    @staticmethod
    def _parse_json_report(data: dict[str, Any]) -> dict[str, dict[str, set[int]]]:
        result: dict[str, dict[str, set[int]]] = {}

        # 1. coverage.py JSON format
        if "files" in data and isinstance(data["files"], dict):
            for file_path, file_data in data["files"].items():
                norm_path = normalize_file_path(file_path)
                executed = set(file_data.get("executed_lines", []))
                missing = set(file_data.get("missing_lines", []))
                result[norm_path] = {"executed": executed, "missing": missing}
            return result

        # 2. Jest / Istanbul format (keys are file paths directly)
        for file_path, file_data in data.items():
            if isinstance(file_data, dict) and "statementMap" in file_data and "s" in file_data:
                norm_path = normalize_file_path(file_path)
                statement_map = file_data.get("statementMap", {})
                hits_map = file_data.get("s", {})
                executed = set()
                missing = set()

                for stmt_id, stmt_loc in statement_map.items():
                    start_line = stmt_loc.get("start", {}).get("line")
                    if start_line is not None:
                        hits = hits_map.get(str(stmt_id), 0)
                        if hits > 0:
                            executed.add(start_line)
                        else:
                            missing.add(start_line)

                result[norm_path] = {"executed": executed, "missing": missing}

        return result


def find_file_in_report(target_file: str, parsed_report: dict[str, dict[str, set[int]]]) -> Optional[dict[str, set[int]]]:
    """Matches a target file path against the parsed report paths using suffix/normalized matches."""
    target_norm = normalize_file_path(target_file)
    if target_norm in parsed_report:
        return parsed_report[target_norm]

    # Try suffix match (e.g. "backend/app/auth.py" matches "app/auth.py" or vice versa)
    for rep_path, file_cov in parsed_report.items():
        if rep_path.endswith(target_norm) or target_norm.endswith(rep_path):
            return file_cov
    return None


# ---------------------------------------------------------------------------
# Strategy B: Heuristic Fallback
# ---------------------------------------------------------------------------

def compute_heuristic_coverage(
    file_path: str,
    hunk_lines: Sequence[int],
    touched_symbols: Sequence[str],
    changed_files: Optional[Sequence[str]] = None,
    repo_snapshot: Optional[Mapping[str, str]] = None,
) -> CoverageResult:
    """
    Computes heuristic coverage when no explicit report covers the file:
    - 0.75 if matching test file was added/modified in this PR and references symbol
    - 0.50 if existing repo test files reference symbol
    - 0.00 if symbol is unreferenced in tests
    """
    snapshot = repo_snapshot or {}
    changed = changed_files or []

    # Check if this PR touches any test files
    pr_test_modified = any(is_test_file(f) for f in changed)

    # Gather test file contents from repo
    test_contents: list[str] = []
    for fpath, code in snapshot.items():
        if is_test_file(fpath):
            test_contents.append(code)

    all_test_code = "\n".join(test_contents)

    symbol_referenced = False
    for sym in touched_symbols:
        if not sym or sym == "<module>":
            continue
        base_name = sym.split(".")[-1]
        pattern = re.compile(rf"\b{re.escape(base_name)}\b")
        if pattern.search(all_test_code):
            symbol_referenced = True
            break

    if symbol_referenced and pr_test_modified:
        return CoverageResult(
            coverage_score=0.75,
            coverage_source="heuristic",
            covered_lines=list(hunk_lines),
            uncovered_lines=[],
            executable_lines=list(hunk_lines),
        )
    elif symbol_referenced:
        return CoverageResult(
            coverage_score=0.50,
            coverage_source="heuristic",
            covered_lines=list(hunk_lines),
            uncovered_lines=[],
            executable_lines=list(hunk_lines),
        )

    return CoverageResult(
        coverage_score=0.0,
        coverage_source="none",
        covered_lines=[],
        uncovered_lines=list(hunk_lines),
        executable_lines=list(hunk_lines),
    )


# ---------------------------------------------------------------------------
# Main Entrypoint: compute_hunk_coverage
# ---------------------------------------------------------------------------

def compute_hunk_coverage(
    file_path: str,
    hunk_lines: Sequence[int],
    touched_symbols: Sequence[str],
    coverage_report: Optional[str | bytes | dict] = None,
    changed_files: Optional[Sequence[str]] = None,
    repo_snapshot: Optional[Mapping[str, str]] = None,
) -> CoverageResult:
    """
    Computes C_h in [0.0, 1.0] for a code hunk using:
    1. Explicit coverage report parsing (Strategy A) if available.
    2. Heuristic AST / test reference analysis (Strategy B) if no report is present.
    """
    hunk_set = set(hunk_lines)

    if coverage_report:
        parsed_report = CoverageReportParser.parse_report_data(coverage_report)
        file_cov = find_file_in_report(file_path, parsed_report)

        if file_cov is not None:
            executed = file_cov.get("executed", set())
            missing = file_cov.get("missing", set())

            covered_in_hunk = sorted(list(executed.intersection(hunk_set)))
            uncovered_in_hunk = sorted(list(missing.intersection(hunk_set)))
            total_executable = len(covered_in_hunk) + len(uncovered_in_hunk)

            if total_executable > 0:
                score = round(len(covered_in_hunk) / total_executable, 4)
                return CoverageResult(
                    coverage_score=score,
                    coverage_source="report",
                    covered_lines=covered_in_hunk,
                    uncovered_lines=uncovered_in_hunk,
                    executable_lines=covered_in_hunk + uncovered_in_hunk,
                )

    # Strategy B fallback
    return compute_heuristic_coverage(
        file_path=file_path,
        hunk_lines=hunk_lines,
        touched_symbols=touched_symbols,
        changed_files=changed_files,
        repo_snapshot=repo_snapshot,
    )
