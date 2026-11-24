"""
Risk-Weighted Test Selection Engine.

Maps changed symbols and caller blast radius to test suites, ranks impacted test files
by risk weight, and generates a ready-to-run prioritized CI test command with reduction metrics.
GUARDRAIL: 100% deterministic and unit-testable without network dependencies.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from app.coverage_mapper import is_test_file, normalize_file_path


@dataclass
class TestImpactRecord:
    test_file: str
    matched_symbols: list[str]
    impact_score: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "test_file": self.test_file,
            "matched_symbols": self.matched_symbols,
            "impact_score": round(self.impact_score, 2),
        }


@dataclass
class TestSelectionResult:
    prioritized_test_files: list[str]
    command: str
    reduction_summary: str
    coverage_percentage: float
    impact_records: list[TestImpactRecord]

    def to_dict(self) -> dict[str, Any]:
        return {
            "prioritized_test_files": self.prioritized_test_files,
            "command": self.command,
            "reduction_summary": self.reduction_summary,
            "coverage_percentage": round(self.coverage_percentage, 1),
            "impact_records": [r.to_dict() for r in self.impact_records],
        }


class TestSelector:
    """
    Deterministic risk-driven test selection engine.
    """

    def select_tests(
        self,
        changed_symbols_with_risk: Sequence[Tuple[str, float, int]],  # (symbol_name, risk_score, fan_in)
        repo_snapshot: Mapping[str, str],
        max_tests_to_select: int = 10,
    ) -> TestSelectionResult:
        """
        Ranks test files by impact and computes prioritized test execution commands.
        """
        # 1. Discover all test files in repo
        test_files: dict[str, str] = {}
        for fpath, content in repo_snapshot.items():
            if is_test_file(fpath):
                norm_p = normalize_file_path(fpath)
                test_files[norm_p] = content

        if not test_files:
            return TestSelectionResult(
                prioritized_test_files=[],
                command="# No test files detected in repository",
                reduction_summary="0 of 0 test suites prioritized (0% coverage)",
                coverage_percentage=0.0,
                impact_records=[],
            )

        # 2. Score each test file based on referenced symbols and their risk scores
        test_scores: dict[str, float] = {}
        test_matches: dict[str, set[str]] = {}

        for test_path, test_code in test_files.items():
            for sym, risk, fan_in in changed_symbols_with_risk:
                if not sym or sym == "<module>":
                    continue
                clean_sym = sym.split(".")[-1]
                pattern = re.compile(rf"\b{re.escape(clean_sym)}\b")

                if pattern.search(test_code):
                    # blast radius multiplier: (1 + 0.2 * log(1 + fan_in))
                    weight = 1.0 + 0.2 * math.log(1.0 + fan_in)
                    impact = risk * weight

                    test_scores[test_path] = max(test_scores.get(test_path, 0.0), impact)
                    if test_path not in test_matches:
                        test_matches[test_path] = set()
                    test_matches[test_path].add(clean_sym)

        # 3. Sort tests by impact score descending
        ranked_tests = sorted(
            test_scores.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        selected_paths = [t[0] for t in ranked_tests[:max_tests_to_select]]
        records = [
            TestImpactRecord(
                test_file=t[0],
                matched_symbols=sorted(list(test_matches.get(t[0], []))),
                impact_score=t[1],
            )
            for t in ranked_tests[:max_tests_to_select]
        ]

        # 4. Compute test command and reduction metrics
        total_tests = len(test_files)
        selected_count = len(selected_paths)

        has_python = any(f.endswith(".py") for f in selected_paths)
        has_js_ts = any(f.endswith((".ts", ".tsx", ".js", ".jsx")) for f in selected_paths)

        if has_python and not has_js_ts:
            cmd = f"pytest {' '.join(selected_paths)}" if selected_paths else "pytest"
        elif has_js_ts and not has_python:
            cmd = f"npx vitest run {' '.join(selected_paths)}" if selected_paths else "npm test"
        else:
            cmd = f"pytest {' '.join(selected_paths)}" if selected_paths else "pytest"

        # Estimated coverage ratio
        total_risk_mass = sum(t[1] for t in ranked_tests)
        selected_risk_mass = sum(r.impact_score for r in records)
        cov_pct = (
            round((selected_risk_mass / total_risk_mass) * 100.0, 1)
            if total_risk_mass > 0
            else 100.0 if selected_count > 0 else 0.0
        )

        summary = f"{selected_count} of {total_tests} test suites prioritized for {cov_pct:.0f}% risk coverage"

        return TestSelectionResult(
            prioritized_test_files=selected_paths,
            command=cmd,
            reduction_summary=summary,
            coverage_percentage=cov_pct,
            impact_records=records,
        )
