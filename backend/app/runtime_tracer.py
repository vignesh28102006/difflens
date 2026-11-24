"""
Runtime-Verified Blast Radius Engine for DiffLens.

Merges static AST call graph dependencies with observed runtime execution traces
(lightweight call-edge JSON, OpenTelemetry spans, or coverage.py dynamic contexts)
to distinguish hot production/test execution paths from dead or theoretical code.

GUARDRAILS:
- 100% deterministic, offline-testable, zero external network/LLM dependencies.
- Graceful degradation: when no trace is supplied, falls back cleanly to static-only signals.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pydantic import BaseModel, Field

logger = logging.getLogger("difflens.runtime_tracer")


class CallerVerification(BaseModel):
    caller: str
    status: str  # "runtime_verified", "static_only", "dynamic_only"
    call_count: int = 1
    evidence: Optional[str] = None


class SymbolBlastRadius(BaseModel):
    symbol_name: str
    file_path: str
    static_callers: List[str] = Field(default_factory=list)
    runtime_callers: List[str] = Field(default_factory=list)
    caller_verifications: List[CallerVerification] = Field(default_factory=list)
    runtime_verified_fan_in: int = 0
    static_only_fan_in: int = 0
    dynamic_only_fan_in: int = 0
    total_effective_fan_in: float = 0.0
    blast_radius_confidence: float = 0.5  # 0.0 to 1.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "symbol_name": self.symbol_name,
            "file_path": self.file_path,
            "static_callers": self.static_callers,
            "runtime_callers": self.runtime_callers,
            "caller_verifications": [cv.model_dump() for cv in self.caller_verifications],
            "runtime_verified_fan_in": self.runtime_verified_fan_in,
            "static_only_fan_in": self.static_only_fan_in,
            "dynamic_only_fan_in": self.dynamic_only_fan_in,
            "total_effective_fan_in": self.total_effective_fan_in,
            "blast_radius_confidence": self.blast_radius_confidence,
        }


class RuntimeTracer:
    """
    Parses runtime traces and merges them with static AST callers.
    """

    def __init__(self):
        pass

    def _normalize_name(self, name: str) -> str:
        """Normalizes symbol identifiers for cross-representation matching."""
        if not name:
            return ""
        # Remove file prefixes if given like path:symbol or path/to/file.py:symbol
        clean = name.split(":")[-1]
        # Replace / or . with dots
        clean = clean.replace("/", ".").replace("\\", ".")
        # Grab the terminal function/symbol name
        parts = [p for p in clean.split(".") if p]
        return parts[-1] if parts else clean

    def _extract_edges_from_trace(self, trace_data: Any) -> List[Tuple[str, str, int]]:
        """
        Extracts directed caller->callee edges with counts from supported trace formats:
        1. Lightweight JSON call-edge: {"edges": [{"caller": "...", "callee": "...", "count": 10}]}
           or list of dicts [{"caller": "...", "callee": "..."}]
        2. OpenTelemetry span list: [{"span_id": "...", "parent_span_id": "...", "name": "...", "attributes": {...}}]
        3. coverage.py dynamic context map: {"contexts": {"test_name": ["file.py:line"]}}
        """
        if not trace_data:
            return []

        edges: List[Tuple[str, str, int]] = []

        # Format 1: Call-edge dictionary or list
        if isinstance(trace_data, dict) and "edges" in trace_data:
            for item in trace_data.get("edges", []):
                caller = item.get("caller") or item.get("source") or ""
                callee = item.get("callee") or item.get("target") or ""
                count = int(item.get("count", 1))
                if caller and callee:
                    edges.append((caller, callee, count))
            return edges

        if isinstance(trace_data, list):
            # Check if it's a list of call-edge dicts
            if trace_data and isinstance(trace_data[0], dict) and ("caller" in trace_data[0] or "callee" in trace_data[0]):
                for item in trace_data:
                    caller = item.get("caller", "")
                    callee = item.get("callee", "")
                    count = int(item.get("count", 1))
                    if caller and callee:
                        edges.append((caller, callee, count))
                return edges

            # Check if it's an OpenTelemetry span list
            span_map: Dict[str, Dict[str, Any]] = {}
            for item in trace_data:
                if isinstance(item, dict) and ("span_id" in item or "id" in item):
                    sid = item.get("span_id") or item.get("id")
                    span_map[str(sid)] = item

            if span_map:
                for sid, span in span_map.items():
                    parent_id = str(span.get("parent_span_id") or span.get("parent_id") or "")
                    if parent_id and parent_id in span_map:
                        parent_span = span_map[parent_id]
                        # Caller is parent span, callee is current span
                        caller_name = (
                            parent_span.get("attributes", {}).get("code.function")
                            or parent_span.get("attributes", {}).get("code.filepath")
                            or parent_span.get("name")
                            or "unknown_caller"
                        )
                        callee_name = (
                            span.get("attributes", {}).get("code.function")
                            or span.get("attributes", {}).get("code.filepath")
                            or span.get("name")
                            or "unknown_callee"
                        )
                        edges.append((str(caller_name), str(callee_name), 1))
                return edges

        # Format 3: coverage.py dynamic context map
        if isinstance(trace_data, dict) and "contexts" in trace_data:
            for ctx_name, lines in trace_data.get("contexts", {}).items():
                for line_ref in lines:
                    file_part = line_ref.split(":")[0] if ":" in line_ref else line_ref
                    edges.append((str(ctx_name), str(file_part), 1))
            return edges

        return edges

    def analyze_symbol(
        self,
        symbol_name: str,
        file_path: str,
        static_callers: List[str],
        trace_data: Optional[Any] = None,
    ) -> SymbolBlastRadius:
        """
        Merges static callers with observed runtime trace edges for a symbol.
        """
        norm_symbol = self._normalize_name(symbol_name)
        norm_file = file_path.replace("\\", "/")

        edges = self._extract_edges_from_trace(trace_data)
        has_trace = bool(trace_data and edges)

        # Find runtime callers that invoke this symbol
        runtime_callers_map: Dict[str, int] = {}
        for caller, callee, count in edges:
            norm_callee = self._normalize_name(callee)
            # Match callee against symbol name or file path
            callee_clean = callee.replace("\\", "/")
            if norm_callee == norm_symbol or (norm_symbol and norm_symbol in norm_callee) or (norm_file and norm_file in callee_clean):
                runtime_callers_map[caller] = runtime_callers_map.get(caller, 0) + count

        verifications: List[CallerVerification] = []
        matched_runtime_callers: Set[str] = set()

        # Classify static callers
        for static_c in static_callers:
            norm_static = self._normalize_name(static_c)
            # Check if this static caller matches any observed runtime caller
            runtime_match = None
            for rt_caller, count in runtime_callers_map.items():
                norm_rt = self._normalize_name(rt_caller)
                if norm_static == norm_rt or norm_static in norm_rt or norm_rt in norm_static:
                    runtime_match = (rt_caller, count)
                    matched_runtime_callers.add(rt_caller)
                    break

            if runtime_match:
                verifications.append(
                    CallerVerification(
                        caller=static_c,
                        status="runtime_verified",
                        call_count=runtime_match[1],
                        evidence=f"Observed {runtime_match[1]} invocation(s) in runtime trace",
                    )
                )
            else:
                verifications.append(
                    CallerVerification(
                        caller=static_c,
                        status="static_only",
                        call_count=0,
                        evidence="Discovered in AST call graph; not observed in runtime trace",
                    )
                )

        # Classify any dynamic-only callers (observed at runtime, absent from AST)
        for rt_caller, count in runtime_callers_map.items():
            if rt_caller not in matched_runtime_callers:
                norm_rt = self._normalize_name(rt_caller)
                already_covered = any(
                    self._normalize_name(cv.caller) == norm_rt for cv in verifications
                )
                if not already_covered:
                    verifications.append(
                        CallerVerification(
                            caller=rt_caller,
                            status="dynamic_only",
                            call_count=count,
                            evidence=f"Dynamically invoked at runtime ({count} calls); absent from static AST",
                        )
                    )

        runtime_verified_count = sum(1 for v in verifications if v.status == "runtime_verified")
        static_only_count = sum(1 for v in verifications if v.status == "static_only")
        dynamic_only_count = sum(1 for v in verifications if v.status == "dynamic_only")
        total_callers = len(verifications)

        # Effective fan-in weighting:
        # Runtime paths are weighted heavily (1.0 for verified, 1.2 for dynamic).
        # Static-only callers are discounted (0.35) so dead code doesn't inflate risk.
        if has_trace:
            effective_fan_in = round(
                (1.0 * runtime_verified_count)
                + (1.2 * dynamic_only_count)
                + (0.35 * static_only_count),
                2,
            )
            confidence = (
                round((runtime_verified_count + dynamic_only_count) / max(1, total_callers), 2)
                if total_callers > 0
                else 1.0
            )
        else:
            effective_fan_in = float(len(static_callers))
            confidence = 0.5  # Neutral baseline confidence when no runtime trace is provided

        return SymbolBlastRadius(
            symbol_name=symbol_name,
            file_path=file_path,
            static_callers=static_callers,
            runtime_callers=list(runtime_callers_map.keys()),
            caller_verifications=verifications,
            runtime_verified_fan_in=runtime_verified_count,
            static_only_fan_in=static_only_count,
            dynamic_only_fan_in=dynamic_only_count,
            total_effective_fan_in=effective_fan_in,
            blast_radius_confidence=confidence,
        )
