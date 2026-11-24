"""
LLM summary and targeted test generator.

NOTE: Used strictly for generating plain-English risk rationales,
reviewer focus points, and concrete test cases for high-risk hunks.
The LLM does NOT calculate or modify numerical risk scores.
"""
from typing import Any, Dict, List
from pydantic import BaseModel


class RiskRationale(BaseModel):
    hunk_id: str
    risk_level: str
    rationale: str
    reviewer_checklist: List[str]
    suggested_test_code: str


class Explainer:
    """
    Handles LLM-assisted summaries and targeted test synthesis.
    """

    def __init__(self, model_name: str = "gemini-1.5-pro"):
        self.model_name = model_name

    def generate_explanation(
        self,
        hunk_diff: str,
        risk_score: float,
        sensitive_reasons: List[str],
    ) -> RiskRationale:
        """
        Synthesizes structured risk explanations and test stubs.
        """
        # ponytail: Will be bound to structured LLM output in Milestone 4
        return RiskRationale(
            hunk_id="hunk_1",
            risk_level="HIGH" if risk_score >= 70.0 else "MEDIUM" if risk_score >= 40.0 else "LOW",
            rationale=f"Hunk scored {risk_score} based on domain sensitivity: {', '.join(sensitive_reasons)}.",
            reviewer_checklist=["Verify auth tokens are properly validated", "Ensure rollback on DB failure"],
            suggested_test_code="# Auto-generated test stub\ndef test_regression():\n    pass",
        )
