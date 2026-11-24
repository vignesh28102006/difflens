"""
Lightweight Deployment Impact Forecast Engine for DiffLens.

Maps changed files and their upstream callers to exposed services, HTTP endpoints,
and CI/CD workflows without requiring live Kubernetes/cloud access:
- docker-compose.yml / yaml parsing (services, build contexts, ports, depends_on)
- .difflens-services.yaml custom mapping
- .github/workflows/*.yml CI/CD triggers
- AST / decorator HTTP route discovery (@app.get, @router.post, Next.js route.ts)

GUARDRAILS:
- 100% deterministic, offline-testable, zero external cloud or network dependencies.
- Graceful degradation: returns 'Isolated' impact tier when manifests are absent.
"""

from __future__ import annotations

import fnmatch
import logging
import os
import re
from typing import Any, Dict, List, Optional, Set
from pydantic import BaseModel, Field

logger = logging.getLogger("difflens.deployment_mapper")


class DeploymentImpact(BaseModel):
    affected_services: List[str] = Field(default_factory=list)
    affected_endpoints: List[str] = Field(default_factory=list)
    affected_workflows: List[str] = Field(default_factory=list)
    impact_tier: str = "Isolated"  # "Isolated", "Service-Local", "Cross-Service", "Public-API"
    details: Dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "affected_services": self.affected_services,
            "affected_endpoints": self.affected_endpoints,
            "affected_workflows": self.affected_workflows,
            "impact_tier": self.impact_tier,
            "details": self.details,
        }


class DeploymentMapper:
    """
    Analyzes repository manifests, CI workflows, and route decorators
    to determine downstream deployment impact.
    """

    def __init__(self):
        pass

    def _parse_simple_yaml(self, content: str) -> Dict[str, Any]:
        """
        Lightweight deterministic YAML parser for docker-compose and workflow structures.
        Falls back to PyYAML if installed, or uses regex line extraction.
        """
        try:
            import yaml
            parsed = yaml.safe_load(content)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass

        # Simple line-based fallback parser
        data: Dict[str, Any] = {}
        current_section = None
        for line in content.splitlines():
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue
            if line_str.endswith(":") and not line.startswith(" "):
                current_section = line_str[:-1]
                data[current_section] = {}
            elif current_section and ":" in line_str:
                k, v = line_str.split(":", 1)
                data[current_section][k.strip()] = v.strip()
        return data

    def _extract_compose_services(
        self, compose_content: str
    ) -> Dict[str, Dict[str, Any]]:
        """
        Extracts services, build contexts, ports, and depends_on from docker-compose.
        """
        parsed = self._parse_simple_yaml(compose_content)
        services_dict = parsed.get("services", {}) if isinstance(parsed, dict) else {}
        if not isinstance(services_dict, dict):
            # Try top-level services
            services_dict = {
                k: v for k, v in parsed.items() if isinstance(v, dict) and ("build" in v or "image" in v)
            }

        result: Dict[str, Dict[str, Any]] = {}
        for s_name, s_cfg in services_dict.items():
            if not isinstance(s_cfg, dict):
                continue

            build_ctx = ""
            if "build" in s_cfg:
                if isinstance(s_cfg["build"], str):
                    build_ctx = s_cfg["build"].strip("./\\")
                elif isinstance(s_cfg["build"], dict):
                    build_ctx = s_cfg["build"].get("context", "").strip("./\\")

            deps = s_cfg.get("depends_on", [])
            if isinstance(deps, dict):
                deps = list(deps.keys())
            elif not isinstance(deps, list):
                deps = [str(deps)] if deps else []

            ports = s_cfg.get("ports", [])
            if not isinstance(ports, list):
                ports = [str(ports)] if ports else []

            result[s_name] = {
                "build_context": build_ctx,
                "depends_on": deps,
                "ports": ports,
            }
        return result

    def _extract_http_routes(
        self,
        file_path: str,
        content: str,
    ) -> List[Tuple[str, str, str]]:
        """
        Extracts (HTTP_METHOD, ROUTE_PATH, HANDLER_FUNC) from Python FastAPI/Flask or Next.js files.
        """
        routes: List[Tuple[str, str, str]] = []

        # 1. FastAPI / Flask decorators: @app.get("/path"), @router.post("/path")
        decorator_pattern = re.compile(
            r"""@(?:app|router|api)\.(get|post|put|delete|patch|route)\s*\(\s*["']([^"']+)["']""",
            re.IGNORECASE,
        )
        def_pattern = re.compile(r"""(?:async\s+)?def\s+([a-zA-Z0-9_]+)\s*\(""")

        lines = content.splitlines()
        for idx, line in enumerate(lines):
            m = decorator_pattern.search(line)
            if m:
                http_method = m.group(1).upper()
                if http_method == "ROUTE":
                    http_method = "ANY"
                route_path = m.group(2)
                # Find associated function name in next few lines
                handler_name = ""
                for next_line in lines[idx + 1 : idx + 6]:
                    def_match = def_pattern.search(next_line)
                    if def_match:
                        handler_name = def_match.group(1)
                        break
                routes.append((http_method, route_path, handler_name))

        # 2. Next.js App Router: app/api/**/route.ts
        norm_path = file_path.replace("\\", "/")
        if "app/api/" in norm_path and (norm_path.endswith("/route.ts") or norm_path.endswith("/route.js")):
            api_part = norm_path.split("app/api/")[-1].rsplit("/route.", 1)[0]
            route_path = f"/api/{api_part}"
            # Extract exported HTTP functions: export async function POST, etc.
            method_matches = re.findall(
                r"""export\s+(?:async\s+)?function\s+(GET|POST|PUT|DELETE|PATCH)\b""",
                content,
            )
            for m in method_matches:
                routes.append((m.upper(), route_path, m.lower()))

        return routes

    def analyze_impact(
        self,
        changed_files: List[str],
        changed_symbols: List[str],
        callers: List[str],
        repo_snapshot: Dict[str, str],
    ) -> DeploymentImpact:
        """
        Computes the DeploymentImpact for the set of changed files and symbols.
        """
        affected_services: Set[str] = set()
        affected_endpoints: Set[str] = set()
        affected_workflows: Set[str] = set()

        all_relevant_files = set(changed_files)
        # Extract files from callers (e.g., "api/routes.py:charge" -> "api/routes.py")
        for c in callers:
            c_file = c.split(":")[0] if ":" in c else c
            if c_file and (c_file.endswith(".py") or c_file.endswith(".ts") or c_file.endswith(".js")):
                all_relevant_files.add(c_file)

        # 1. Parse Docker Compose
        compose_content = (
            repo_snapshot.get("docker-compose.yml")
            or repo_snapshot.get("docker-compose.yaml")
        )
        services = self._extract_compose_services(compose_content) if compose_content else {}

        # Direct service match based on file paths and build contexts
        for s_name, s_meta in services.items():
            b_ctx = s_meta["build_context"]
            for f in all_relevant_files:
                norm_f = f.replace("\\", "/")
                if not b_ctx or b_ctx in (".", "/"):
                    # Root build context builds from whole repo
                    affected_services.add(s_name)
                elif norm_f.startswith(b_ctx) or b_ctx in norm_f or s_name.lower() in norm_f.lower():
                    affected_services.add(s_name)

        # Transitive downstream service impact via depends_on
        # If service A is affected, any service B that depends on A is also impacted!
        expanded = True
        while expanded:
            expanded = False
            for s_name, s_meta in services.items():
                if s_name not in affected_services:
                    for dep in s_meta["depends_on"]:
                        if dep in affected_services:
                            affected_services.add(s_name)
                            expanded = True
                            break

        # 2. Parse Custom Services Mapping (.difflens-services.yaml)
        custom_svc_content = repo_snapshot.get(".difflens-services.yaml")
        if custom_svc_content:
            custom_map = self._parse_simple_yaml(custom_svc_content)
            for glob_pattern, svc_name in custom_map.items():
                if isinstance(svc_name, str):
                    for f in all_relevant_files:
                        if fnmatch.fnmatch(f, glob_pattern):
                            affected_services.add(svc_name)

        # 3. Discover Affected HTTP Endpoints
        # Inspect files in repo snapshot for route declarations matching changed symbols or callers
        symbols_to_check = {s.split(":")[-1].split(".")[-1] for s in changed_symbols if s}
        for c in callers:
            c_sym = c.split(":")[-1].split(".")[-1]
            if c_sym:
                symbols_to_check.add(c_sym)

        for f_path, content in repo_snapshot.items():
            if not (f_path.endswith(".py") or f_path.endswith(".ts") or f_path.endswith(".js")):
                continue

            routes = self._extract_http_routes(f_path, content)
            for method, path, handler in routes:
                # Check if this route handler calls or matches our symbols, or is in a changed file
                route_str = f"{method} {path}"
                if f_path in changed_files:
                    affected_endpoints.add(route_str)
                elif handler and (handler in symbols_to_check or any(s in handler for s in symbols_to_check)):
                    affected_endpoints.add(route_str)
                else:
                    # Check if handler code invokes any changed symbol
                    for s in symbols_to_check:
                        if s and re.search(r"\b" + re.escape(s) + r"\b", content):
                            # Symbol is referenced in the file containing this route
                            affected_endpoints.add(route_str)
                            break

        # 4. Discover Triggered GitHub Workflows
        for f_path, content in repo_snapshot.items():
            if f_path.startswith(".github/workflows/") and (f_path.endswith(".yml") or f_path.endswith(".yaml")):
                wf_data = self._parse_simple_yaml(content)
                # Check path filters
                matched_wf = False
                for cf in changed_files:
                    norm_cf = cf.replace("\\", "/")
                    if "paths" in content and any(
                        p in norm_cf for p in ["backend", "frontend", "api", "auth", "src"]
                    ):
                        matched_wf = True
                        break
                # If changed file is in workflow itself or relevant directory
                if matched_wf or any(cf.startswith(".github/") for cf in changed_files):
                    affected_workflows.add(f_path)
                elif "ci" in f_path.lower() or "test" in f_path.lower():
                    # Default CI workflow is affected by any code changes
                    affected_workflows.add(f_path)

        # Determine Impact Tier:
        # "Public-API": touches or reaches exposed HTTP endpoints
        # "Cross-Service": affects more than 1 service or downstream dependencies
        # "Service-Local": contained within a single service
        # "Isolated": no external services or endpoints affected
        if affected_endpoints:
            impact_tier = "Public-API"
        elif len(affected_services) > 1:
            impact_tier = "Cross-Service"
        elif len(affected_services) == 1:
            impact_tier = "Service-Local"
        else:
            impact_tier = "Isolated"

        sorted_services = sorted(affected_services)
        sorted_endpoints = sorted(affected_endpoints)
        sorted_workflows = sorted(affected_workflows)

        return DeploymentImpact(
            affected_services=sorted_services,
            affected_endpoints=sorted_endpoints,
            affected_workflows=sorted_workflows,
            impact_tier=impact_tier,
            details={
                "direct_services": [s for s in sorted_services if not services.get(s, {}).get("depends_on")],
                "downstream_services": [s for s in sorted_services if services.get(s, {}).get("depends_on")],
                "routes_count": len(sorted_endpoints),
            },
        )
