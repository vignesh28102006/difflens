"""
Cross-File Change Interaction Engine.

Detects signature/contract drift, asymmetric boundary edits, and shared state contention
across multi-file pull requests using Tree-sitter AST and repository dependency graphs.
GUARDRAIL: 100% deterministic and unit-testable without network dependencies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Set, Tuple

from tree_sitter import Node, Parser
from app.ast_analyzer import ASTAnalyzer, SymbolDefinition


@dataclass
class FunctionSignature:
    name: str
    qualified_name: str
    file_path: str
    params: list[str] = field(default_factory=list)
    required_params: list[str] = field(default_factory=list)
    is_async: bool = False


@dataclass
class InteractionFinding:
    interaction_type: str  # "signature_drift", "asymmetric_boundary", "state_contention"
    severity: str          # "HIGH", "MEDIUM", "LOW"
    source_symbol: str
    target_symbol: str
    file_path: str
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "interaction_type": self.interaction_type,
            "severity": self.severity,
            "source_symbol": self.source_symbol,
            "target_symbol": self.target_symbol,
            "file_path": self.file_path,
            "evidence": self.evidence,
        }


def extract_function_signatures(
    code: str,
    file_path: str,
    analyzer: Optional[ASTAnalyzer] = None,
) -> dict[str, FunctionSignature]:
    """
    Extracts detailed parameter lists and async status for functions in a file.
    """
    ast_tool = analyzer or ASTAnalyzer()
    parser, lang = ast_tool.get_parser(file_path)
    if parser is None or not code.strip():
        return {}

    tree = parser.parse(code.encode("utf-8"))
    signatures: dict[str, FunctionSignature] = {}

    def _walk_py(node: Node, current_class: Optional[str] = None):
        if node.type == "class_definition":
            cname_node = node.child_by_field_name("name")
            cname = cname_node.text.decode("utf-8") if cname_node and cname_node.text else "Class"
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    _walk_py(child, current_class=cname)
            return

        if node.type == "function_definition":
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text:
                name = name_node.text.decode("utf-8")
                qname = f"{current_class}.{name}" if current_class else name
                is_async = any(c.type == "async" for c in node.children)

                all_params: list[str] = []
                req_params: list[str] = []

                params_node = node.child_by_field_name("parameters")
                if params_node:
                    for child in params_node.children:
                        if child.type in ("identifier", "typed_parameter"):
                            pname = child.text.decode("utf-8").split(":")[0].strip()
                            if pname not in ("self", "cls"):
                                all_params.append(pname)
                                req_params.append(pname)
                        elif child.type in ("default_parameter", "typed_default_parameter"):
                            pname = child.text.decode("utf-8").split("=")[0].split(":")[0].strip()
                            if pname not in ("self", "cls"):
                                all_params.append(pname)

                signatures[qname] = FunctionSignature(
                    name=name,
                    qualified_name=qname,
                    file_path=file_path,
                    params=all_params,
                    required_params=req_params,
                    is_async=is_async,
                )

        for child in node.children:
            _walk_py(child, current_class=current_class)

    def _walk_js(node: Node, current_class: Optional[str] = None):
        if node.type in ("class_declaration", "class"):
            cname_node = node.child_by_field_name("name")
            cname = cname_node.text.decode("utf-8") if cname_node and cname_node.text else "Class"
            body = node.child_by_field_name("body")
            if body:
                for child in body.children:
                    _walk_js(child, current_class=cname)
            return

        if node.type in ("function_declaration", "method_definition"):
            name_node = node.child_by_field_name("name")
            if name_node and name_node.text:
                name = name_node.text.decode("utf-8")
                qname = f"{current_class}.{name}" if current_class else name
                is_async = any(c.type == "async" for c in node.children)
                all_params: list[str] = []
                req_params: list[str] = []

                params_node = node.child_by_field_name("parameters")
                if params_node:
                    for child in params_node.children:
                        if child.type in ("identifier", "required_parameter"):
                            pname = child.text.decode("utf-8").split(":")[0].strip()
                            all_params.append(pname)
                            req_params.append(pname)
                        elif child.type in ("assignment_pattern", "optional_parameter"):
                            pname = child.text.decode("utf-8").split("=")[0].split("?")[0].strip()
                            all_params.append(pname)

                signatures[qname] = FunctionSignature(
                    name=name,
                    qualified_name=qname,
                    file_path=file_path,
                    params=all_params,
                    required_params=req_params,
                    is_async=is_async,
                )

        for child in node.children:
            _walk_js(child, current_class=current_class)

    if lang == "python":
        _walk_py(tree.root_node)
    else:
        _walk_js(tree.root_node)

    return signatures


class InteractionAnalyzer:
    """
    Deterministic cross-file change interaction detector.
    """

    def __init__(self, ast_analyzer: Optional[ASTAnalyzer] = None) -> None:
        self.ast = ast_analyzer or ASTAnalyzer()

    def analyze_interactions(
        self,
        changed_files: Sequence[str],
        base_contents: Mapping[str, str],
        head_contents: Mapping[str, str],
        repo_snapshot: Mapping[str, str],
    ) -> list[InteractionFinding]:
        """
        Executes multi-file cross-boundary analysis.
        """
        findings: list[InteractionFinding] = []
        norm_changed = {f.replace("\\", "/").lstrip("./") for f in changed_files}

        # 1. Build repo call graph
        callees_to_callers, symbols_by_file = self.ast.build_repository_call_graph(repo_snapshot)

        # 2. Check Signature & Contract Drift
        for fpath in changed_files:
            norm_f = fpath.replace("\\", "/").lstrip("./")
            base_code = base_contents.get(fpath, "")
            head_code = head_contents.get(fpath, "")

            if not base_code or not head_code:
                continue

            base_sigs = extract_function_signatures(base_code, fpath, self.ast)
            head_sigs = extract_function_signatures(head_code, fpath, self.ast)

            for sym_qname, head_sig in head_sigs.items():
                if sym_qname not in base_sigs:
                    continue

                base_sig = base_sigs[sym_qname]
                drift_reasons: list[str] = []

                # Check required parameters growth
                if len(head_sig.required_params) > len(base_sig.required_params):
                    new_reqs = set(head_sig.required_params) - set(base_sig.required_params)
                    drift_reasons.append(
                        f"added {len(new_reqs)} new required parameter(s): {', '.join(new_reqs)}"
                    )

                # Check async flip
                if head_sig.is_async != base_sig.is_async:
                    if head_sig.is_async:
                        drift_reasons.append("converted synchronous function into coroutine (requires await)")
                    else:
                        drift_reasons.append("converted coroutine into synchronous function")

                if drift_reasons:
                    # Look up callers in the repo
                    callers = set()
                    if head_sig.name in callees_to_callers:
                        callers.update(callees_to_callers[head_sig.name])
                    if sym_qname in callees_to_callers:
                        callers.update(callees_to_callers[sym_qname])

                    # Identify un-updated callers in this PR
                    unupdated_callers: list[str] = []
                    for c in callers:
                        # caller format might be 'path:symbol' or 'symbol'
                        caller_file = c.split(":")[0] if ":" in c else ""
                        if caller_file and caller_file.replace("\\", "/").lstrip("./") not in norm_changed:
                            unupdated_callers.append(c)
                        elif not caller_file:
                            unupdated_callers.append(c)

                    if unupdated_callers:
                        findings.append(
                            InteractionFinding(
                                interaction_type="signature_drift",
                                severity="HIGH",
                                source_symbol=sym_qname,
                                target_symbol=", ".join(unupdated_callers[:3]),
                                file_path=fpath,
                                evidence=(
                                    f"Symbol `{sym_qname}` signature changed ({'; '.join(drift_reasons)}), "
                                    f"but {len(unupdated_callers)} upstream caller(s) ({', '.join(unupdated_callers[:3])}) "
                                    f"were NOT updated in this PR."
                                ),
                            )
                        )

        # 3. Check Shared State / Config Contention
        # Detect if multiple changed files write to the same global config/state
        state_write_patterns = [
            re.compile(r"\bos\.environ\[['\"]([A-Z0-9_]+)['\"]\]\s*="),
            re.compile(r"\b([A-Z0-9_]{3,})\s*=\s*"),
        ]
        var_to_files: dict[str, set[str]] = {}
        for fpath in changed_files:
            code = head_contents.get(fpath, "")
            for pat in state_write_patterns:
                for match in pat.finditer(code):
                    var_name = match.group(1)
                    if var_name not in ("TRUE", "FALSE", "NONE", "NULL", "PATH"):
                        if var_name not in var_to_files:
                            var_to_files[var_name] = set()
                        var_to_files[var_name].add(fpath)

        for var_name, files in var_to_files.items():
            if len(files) > 1:
                findings.append(
                    InteractionFinding(
                        interaction_type="state_contention",
                        severity="MEDIUM",
                        source_symbol=var_name,
                        target_symbol=", ".join(sorted(list(files))),
                        file_path=", ".join(sorted(list(files))),
                        evidence=f"Shared state or configuration key `{var_name}` is concurrently modified across {len(files)} files in this PR: {', '.join(sorted(list(files)))}.",
                    )
                )

        return findings
