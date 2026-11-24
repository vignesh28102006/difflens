"""
Structured LLM risk explanation and targeted test generator.

GUARDRAILS:
- Strictly deterministic in risk score calculation: explainer.py NEVER calculates,
  alters, or overrides risk_score or risk_level.
- Only generates plain-English risk rationales, remediation steps, review checklists,
  and runnable test stubs for flagged hunks (risk_score >= threshold).
- Gracefully degrades to a deterministic fallback when no API key is present or on network/API errors.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional
import httpx
from pydantic import BaseModel, Field

from app.crypto import decrypt_secret

logger = logging.getLogger("difflens.explainer")


class HunkExplanation(BaseModel):
    risk_summary: str = Field(
        description="1-2 sentence plain-English summary of what this code change introduces and why it needs attention."
    )
    why_its_risky: str = Field(
        description="Concrete failure mode referencing domain tags, blast-radius callers, and missing test coverage."
    )
    how_to_fix: str = Field(
        description="Actionable remediation guidance or defensive coding best practices to make this change safe."
    )
    review_checklist: List[str] = Field(
        description="2-4 concrete verification checks the reviewer must inspect before approving."
    )
    suggested_test_code: str = Field(
        description="Runnable pytest or Jest/TypeScript test stub verifying regression or edge cases."
    )


class Explainer:
    """
    Multi-provider structured explainer supporting:
    1. Google Gemini via google-genai SDK
    2. Local Ollama via JSON format
    3. Deterministic rule-based template fallback (zero external calls)
    """

    def __init__(
        self,
        provider: str = "gemini",
        model: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.provider = (provider or "gemini").lower()
        self.model = model
        self.api_key = api_key

    def explain(
        self,
        file_path: str,
        symbol_name: str,
        risk_score: float,
        domain_score: float,
        domain_tags: List[str],
        fan_in: int,
        callers: List[str],
        coverage_score: float,
        uncovered_lines: List[int],
        diff_patch: str,
    ) -> HunkExplanation:
        """
        Generates structured HunkExplanation without altering risk_score.
        """
        # 1. Check if provider is explicit 'none'
        if self.provider == "none":
            return self._deterministic_fallback(
                file_path, symbol_name, risk_score, domain_tags, fan_in, callers, coverage_score, uncovered_lines, diff_patch
            )

        # 2. Try Gemini BYOK
        if self.provider == "gemini":
            key = self.api_key or os.getenv("GEMINI_API_KEY")
            if key:
                try:
                    return self._call_gemini(
                        key, file_path, symbol_name, risk_score, domain_tags, fan_in, callers, coverage_score, uncovered_lines, diff_patch
                    )
                except Exception as e:
                    logger.warning("Gemini explanation failed, falling back to deterministic template: %s", e)
            else:
                logger.info("No Gemini API key available; using deterministic explanation template.")

        # 3. Try Local Ollama
        elif self.provider == "ollama":
            try:
                return self._call_ollama(
                    file_path, symbol_name, risk_score, domain_tags, fan_in, callers, coverage_score, uncovered_lines, diff_patch
                )
            except Exception as e:
                logger.warning("Ollama explanation failed, falling back to deterministic template: %s", e)

        # 4. Deterministic fallback
        return self._deterministic_fallback(
            file_path, symbol_name, risk_score, domain_tags, fan_in, callers, coverage_score, uncovered_lines, diff_patch
        )

    def _call_gemini(
        self,
        api_key: str,
        file_path: str,
        symbol_name: str,
        risk_score: float,
        domain_tags: List[str],
        fan_in: int,
        callers: List[str],
        coverage_score: float,
        uncovered_lines: List[int],
        diff_patch: str,
    ) -> HunkExplanation:
        from google import genai
        from google.genai import types

        model_name = self.model or os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
        client = genai.Client(api_key=api_key)

        prompt = f"""
You are DiffLens, an automated code risk analyzer. Explain this risky code hunk for a pull request review.

CODE INFORMATION:
- File: {file_path}
- Symbol: {symbol_name}
- Risk Score: {risk_score}/100
- Sensitive Domains: {', '.join(domain_tags) if domain_tags else 'General'}
- Blast Radius (fan-in): {fan_in} callers ({', '.join(callers[:5]) if callers else 'None'})
- Test Coverage: {int(coverage_score * 100)}% (Uncovered lines: {uncovered_lines[:10]})

DIFF PATCH:
```
{diff_patch[:2000]}
```

Provide a structured, developer-focused explanation conforming strictly to the requested JSON schema.
Ensure your suggested test code is a complete, runnable test stub.
"""
        response = client.models.generate_content(
            model=model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=HunkExplanation,
                temperature=0.2,
            ),
        )
        return HunkExplanation.model_validate_json(response.text)

    def _call_ollama(
        self,
        file_path: str,
        symbol_name: str,
        risk_score: float,
        domain_tags: List[str],
        fan_in: int,
        callers: List[str],
        coverage_score: float,
        uncovered_lines: List[int],
        diff_patch: str,
    ) -> HunkExplanation:
        base_url = os.getenv("DIFFLENS_OLLAMA_BASE_URL", "http://localhost:11434").rstrip("/")
        model_name = self.model or os.getenv("DIFFLENS_OLLAMA_MODEL", "qwen2.5-coder")

        system_prompt = (
            "You are DiffLens risk analyzer. Return ONLY a valid JSON object matching this schema: "
            "{\"risk_summary\": string, \"why_its_risky\": string, \"how_to_fix\": string, "
            "\"review_checklist\": [string], \"suggested_test_code\": string}"
        )
        user_prompt = f"""
File: {file_path}, Symbol: {symbol_name}, Risk: {risk_score}/100.
Domains: {domain_tags}, Fan-in: {fan_in}, Callers: {callers[:3]}, Coverage: {coverage_score}.
Diff:
{diff_patch[:1500]}
"""
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(
                f"{base_url}/api/generate",
                json={
                    "model": model_name,
                    "prompt": f"{system_prompt}\n\n{user_prompt}",
                    "format": "json",
                    "stream": False,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            response_text = data.get("response", "{}")
            return HunkExplanation.model_validate_json(response_text)

    def _deterministic_fallback(
        self,
        file_path: str,
        symbol_name: str,
        risk_score: float,
        domain_tags: List[str],
        fan_in: int,
        callers: List[str],
        coverage_score: float,
        uncovered_lines: List[int],
        diff_patch: str,
    ) -> HunkExplanation:
        """
        Pure deterministic fallback with zero remote calls.
        Synthesizes high-fidelity rationales and tests from static AST and coverage signals.
        """
        domain_desc = ", ".join(domain_tags) if domain_tags else "general logic"
        clean_symbol = symbol_name if symbol_name != "<module>" else f"top-level in {file_path}"

        # Risk Summary
        level_str = "Critical" if risk_score >= 70.0 else "Elevated" if risk_score >= 40.0 else "Low"
        summary = (
            f"{level_str} risk change touching `{clean_symbol}` in `{file_path}`. "
            f"Modifies {domain_desc} with {fan_in} dependent caller(s)."
        )

        # Why it's risky
        risk_points = []
        if "auth" in domain_tags:
            risk_points.append("touches authentication/session tokens where security regressions allow unauthorized access")
        if "database" in domain_tags:
            risk_points.append("executes persistence/database operations without verified rollback on failure")
        if "api" in domain_tags:
            risk_points.append("modifies public API/webhook boundaries affecting external consumers")
        if fan_in > 0:
            callers_str = ", ".join(f"`{c}`" for c in callers[:3])
            risk_points.append(f"has high propagation blast radius invoked by {fan_in} upstream caller(s) ({callers_str})")
        if coverage_score < 0.5:
            uncovered_str = f" (lines {uncovered_lines[:5]})" if uncovered_lines else ""
            risk_points.append(f"lacks adequate automated test coverage{uncovered_str}")

        why_risky = "; ".join(risk_points).capitalize() + "." if risk_points else "General code churn with potential unintended side effects."

        # How to fix
        fix_points = [
            f"Add unit tests covering edge cases for `{clean_symbol}`.",
            "Validate all inputs against trust boundaries and check error return values.",
        ]
        if "auth" in domain_tags:
            fix_points.append("Ensure constant-time signature/token validation and verify token expiry.")
        if "database" in domain_tags:
            fix_points.append("Wrap database mutations in an explicit transaction block.")
        how_fix = " ".join(fix_points)

        # Review Checklist
        checklist = [
            f"Verify `{clean_symbol}` behavior with empty, null, and malformed inputs.",
            f"Check blast radius callers ({', '.join(callers[:2]) if callers else 'local calls'}) for breaking changes.",
            "Ensure test coverage delta includes new assertion tests.",
        ]

        # Suggested test stub
        is_python = file_path.endswith(".py")
        if is_python:
            test_stub = f'''import pytest

def test_{clean_symbol.replace(".", "_")}_regression():
    # Arrange: set up test fixture for {clean_symbol}
    # Act: exercise modified behavior
    # Assert: verify expected output and error states
    assert True
'''
        else:
            test_stub = f'''describe("{clean_symbol}", () => {{
    it("should handle valid and edge-case inputs safely", () => {{
        // Test regression assertion
        expect(true).toBe(true);
    }});
}});
'''

        return HunkExplanation(
            risk_summary=summary,
            why_its_risky=why_risky,
            how_to_fix=how_fix,
            review_checklist=checklist,
            suggested_test_code=test_stub,
        )


def create_explainer_for_repo(repo: Any) -> Explainer:
    """Factory creating an Explainer based on repository BYOK settings."""
    provider = getattr(repo, "llm_provider", "gemini") or "gemini"
    model = getattr(repo, "llm_model", None)
    enc_key = getattr(repo, "encrypted_api_key", None)
    api_key = decrypt_secret(enc_key) if enc_key else None

    return Explainer(provider=provider, model=model, api_key=api_key)
