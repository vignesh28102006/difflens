import math
from typing import List, Sequence
from pydantic import BaseModel, Field


class HunkMetrics(BaseModel):
    """
    Deterministic signals extracted from AST, call-graph, and coverage.
    """
    domain_sensitivity: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Domain risk: Auth/session (1.0), DB/transactions (0.8), API boundary (0.6), UI/docs (0.1)"
    )
    fan_in: int = Field(
        default=0,
        ge=0,
        description="Number of upstream callers/dependents in symbol dependency graph"
    )
    coverage: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Test coverage ratio of touched symbol/file [0.0 = completely uncovered, 1.0 = full]"
    )
    delta_loc: int = Field(
        default=0,
        ge=0,
        description="Total lines of code changed (added + modified) in hunk"
    )


class ScorerConfig(BaseModel):
    """Weights and normalization baseline for the risk formula."""
    w_dom: float = 2.0      # Domain sensitivity weight
    w_blast: float = 1.0    # Call-graph blast radius weight
    w_cov: float = 2.5      # Uncovered code penalty weight
    w_churn: float = 1.0    # Churn / code jump weight
    l_0: float = 50.0       # Normalization scale factor for LOC


def calculate_hunk_risk(
    metrics: HunkMetrics,
    config: ScorerConfig = ScorerConfig(),
    regression_boost: float = 0.0,
) -> float:
    """
    Computes deterministic risk score for a single code hunk on a [0.0, 100.0] scale.

    Formula:
    Risk(h) = 100 * (1 - exp(-(
        w_dom * D_h
        + w_blast * log(1 + fan_in_h)
        + w_cov * (1 - C_h)
        + w_churn * (ΔLOC_h / L_0)
    ))) + regression_boost
    """
    exponent = (
        config.w_dom * metrics.domain_sensitivity
        + config.w_blast * math.log(1.0 + metrics.fan_in)
        + config.w_cov * (1.0 - metrics.coverage)
        + config.w_churn * (metrics.delta_loc / config.l_0)
    )
    base_risk = 100.0 * (1.0 - math.exp(-exponent))
    bounded_boost = min(30.0, max(0.0, regression_boost))
    total_risk = base_risk + bounded_boost
    return round(max(0.0, min(100.0, total_risk)), 2)


def aggregate_pr_risk(
    hunk_scores: Sequence[float],
    high_risk_threshold: float = 70.0,
    max_hunk_weight: float = 0.65,
    density_weight: float = 0.35,
) -> float:
    """
    Aggregates hunk scores into a PR-level risk score [0.0, 100.0].
    Prevents dilution: a single critical auth/DB bug isn't washed out by 50 safe hunks.
    """
    if not hunk_scores:
        return 0.0

    max_risk = max(hunk_scores)
    high_risk_count = sum(1 for s in hunk_scores if s >= high_risk_threshold)
    density_ratio = high_risk_count / len(hunk_scores)
    density_risk = density_ratio * 100.0

    pr_score = (max_hunk_weight * max_risk) + (density_weight * density_risk)
    return round(max(0.0, min(100.0, pr_score)), 2)
