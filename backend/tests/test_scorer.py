from app.scorer import HunkMetrics, calculate_hunk_risk, aggregate_pr_risk


def test_hunk_risk_zero_baseline():
    metrics = HunkMetrics(
        domain_sensitivity=0.0,
        fan_in=0,
        coverage=1.0,
        delta_loc=0,
    )
    score = calculate_hunk_risk(metrics)
    assert score == 0.0


def test_hunk_risk_critical_auth():
    # Sensitive auth changes without tests and high callers
    metrics = HunkMetrics(
        domain_sensitivity=1.0,  # Auth
        fan_in=10,               # 10 callers
        coverage=0.0,            # 0% coverage
        delta_loc=100,           # 100 lines changed
    )
    score = calculate_hunk_risk(metrics)
    # Exponent: 2*1 + 1*ln(11) + 2.5*1 + 1*(100/50) = 2 + 2.398 + 2.5 + 2 = 8.898
    # 100 * (1 - e^-8.898) ~ 99.99
    assert score > 95.0


def test_pr_aggregation_no_dilution():
    # 1 critical auth hunk and 9 trivial low-risk hunks
    hunk_scores = [95.0, 5.0, 4.0, 5.0, 6.0, 3.0, 5.0, 4.0, 5.0, 2.0]
    pr_score = aggregate_pr_risk(hunk_scores, high_risk_threshold=70.0)

    # Simple average would be 13.4, but our formula prioritizes max_hunk_weight
    # max = 95.0 * 0.65 = 61.75; density = (1/10)*100 * 0.35 = 3.5 -> ~65.25
    assert pr_score >= 60.0
