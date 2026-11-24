"""
GitHub PR Risk Report formatter and Check Run / PR comment publisher.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional
import httpx

from app.models import HunkRiskFinding, PRAnalysisRun, Repository

logger = logging.getLogger("difflens.github_reporter")


def render_pr_markdown_report(
    run: PRAnalysisRun,
    findings: List[HunkRiskFinding],
    repo: Repository,
    co_change_warnings: Optional[List[Any]] = None,
    interaction_findings: Optional[List[Any]] = None,
    test_selection: Optional[Any] = None,
) -> str:
    """
    Renders a scannable, developer-friendly Markdown report for GitHub PR comments.
    Includes hidden marker tag `<!-- difflens-report:pr-{pr_number} -->` for clean in-place updates.
    """
    marker = f"<!-- difflens-report:pr-{run.pr_number} -->"

    risk_badge = "🔴 HIGH RISK" if run.risk_level == "High" else "🟡 MEDIUM RISK" if run.risk_level == "Medium" else "🟢 LOW RISK"
    flagged_findings = [f for f in findings if f.risk_score >= repo.risk_threshold_medium]

    summary_data = run.summary_json or {}
    co_changes = co_change_warnings if co_change_warnings is not None else summary_data.get("co_change_warnings", [])
    interactions = interaction_findings if interaction_findings is not None else summary_data.get("interaction_findings", [])
    test_sel = test_selection if test_selection is not None else summary_data.get("test_selection")

    lines = [
        marker,
        f"## 🔍 DiffLens Risk Analysis — PR #{run.pr_number}",
        "",
        "| Overall Risk Level | PR Risk Score | Flagged Risky Hunks | Total Hunks Analyzed |",
        "| :---: | :---: | :---: | :---: |",
        f"| **{risk_badge}** | **`{run.overall_score:.1f} / 100`** | **`{len(flagged_findings)}`** | **`{len(findings)}`** |",
        "",
    ]

    # Prioritized CI Test Selection
    if test_sel and test_sel.get("command"):
        lines.extend([
            "### ⚡ Prioritized CI Test Selection",
            f"> **{test_sel.get('reduction_summary', 'Prioritized test subset')}**",
            "",
            "```bash",
            test_sel.get("command"),
            "```",
            "",
        ])

    # Broken Co-Change Warnings
    if co_changes:
        lines.extend([
            "### 🚨 Broken Co-Change Warnings",
            "Historical bug-fix analysis detected coupled files that were omitted from this PR:",
            "",
            "| Target Modified File | Missing Coupled Partner | Historical Bug Coupling | Evidence Commits |",
            "| :--- | :--- | :---: | :--- |",
        ])
        for cc in co_changes:
            target = cc.get("target_file") if isinstance(cc, dict) else getattr(cc, "target_file", "")
            partner = cc.get("missing_partner") if isinstance(cc, dict) else getattr(cc, "missing_partner", "")
            ratio = cc.get("coupling_ratio") if isinstance(cc, dict) else getattr(cc, "coupling_ratio", 0.0)
            supports = cc.get("supporting_commits") if isinstance(cc, dict) else getattr(cc, "supporting_commits", [])
            evidence_str = ", ".join(f"`{s.split(':')[0]}`" for s in supports[:3]) if supports else "Multiple bug commits"
            lines.append(f"| `{target}` | ⚠️ **`{partner}`** | **{int(ratio * 100)}%** | {evidence_str} |")
        lines.append("")

    # Cross-File Interaction Alerts
    if interactions:
        lines.extend([
            "### 🔗 Cross-File Interaction Alerts",
            "AST contract and call-chain analysis flagged cross-file boundary risks:",
            "",
        ])
        for inter in interactions:
            itype = inter.get("interaction_type") if isinstance(inter, dict) else getattr(inter, "interaction_type", "")
            sev = inter.get("severity") if isinstance(inter, dict) else getattr(inter, "severity", "MEDIUM")
            evidence = inter.get("evidence") if isinstance(inter, dict) else getattr(inter, "evidence", "")
            icon = "🔴" if sev == "HIGH" else "🟡"
            lines.append(f"- {icon} **[{sev}] `{itype}`**: {evidence}")
        lines.append("")

    if flagged_findings:
        lines.extend([
            "### ⚠️ Priority Touchpoints Requiring Review",
            "| File & Symbol | Score | Domains | Blast Radius (fan-in) | Coverage |",
            "| :--- | :---: | :---: | :---: | :---: |",
        ])

        for f in flagged_findings:
            domain_badge = ", ".join(f"`{d}`" for d in f.domain_tags) if f.domain_tags else "General"
            cov_str = f"{int(f.coverage_score * 100)}% ({f.coverage_source})"
            callers_str = f"{f.fan_in} caller(s)"
            score_badge = f"**{f.risk_score:.1f}** 🔴" if f.risk_score >= repo.risk_threshold_high else f"**{f.risk_score:.1f}** 🟡"

            lines.append(
                f"| `{f.file_path}:{f.symbol_name}` | {score_badge} | {domain_badge} | {callers_str} | {cov_str} |"
            )

        lines.append("")
        lines.append("### 📋 Risk Explanations & Review Checklist")

        for f in flagged_findings:
            lines.append(f"<details>")
            lines.append(
                f"<summary><strong><code>{f.file_path}</code>: <code>{f.symbol_name}</code></strong> (Risk: {f.risk_score:.1f}/100)</summary>\n"
            )

            # Parse explanation if available
            explanation_data: Dict[str, Any] = {}
            if f.explanation:
                try:
                    explanation_data = json.loads(f.explanation)
                except Exception:
                    pass

            summary = explanation_data.get("risk_summary", f"Flagged risk hunk modifying {f.symbol_name}.")
            why_risky = explanation_data.get("why_its_risky", "Potentially risky code modification.")
            how_to_fix = explanation_data.get("how_to_fix", "Add automated tests and verify inputs.")
            checklist = explanation_data.get("review_checklist", ["Verify input boundaries", "Check callers"])
            suggested_test = explanation_data.get("suggested_test_code", "")

            lines.append(f"> **Summary:** {summary}\n")
            lines.append(f"- **Why it's risky:** {why_risky}")
            lines.append(f"- **How to fix:** {how_to_fix}")
            if f.callers:
                lines.append(f"- **Affected Callers:** {', '.join(f'`{c}`' for c in f.callers[:5])}")
            lines.append("")

            lines.append("#### Reviewer Checklist")
            for item in checklist:
                lines.append(f"- [ ] {item}")
            lines.append("")

            if suggested_test:
                lang = "python" if f.file_path.endswith(".py") else "typescript"
                lines.append(f"#### Suggested Targeted Test Stub")
                lines.append(f"```{lang}")
                lines.append(suggested_test.strip())
                lines.append("```\n")

            lines.append("</details>\n")
    else:
        lines.append("✅ **All modified code hunks passed risk thresholds with adequate test coverage and low blast radius.**\n")

    lines.append("---")
    lines.append("*Report generated deterministically by DiffLens with LLM explanation overlay.*")

    return "\n".join(lines)


def post_or_update_pr_report(
    run: PRAnalysisRun,
    findings: List[HunkRiskFinding],
    repo: Repository,
    github_token: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Posts the report as a GitHub PR comment, updating the existing comment if one exists.
    Gracefully no-ops when no token is configured.
    """
    token = github_token or os.getenv("GITHUB_TOKEN") or os.getenv("GITHUB_APP_TOKEN")
    if not token:
        logger.info("No GITHUB_TOKEN configured; skipping remote comment posting.")
        return None

    report_markdown = render_pr_markdown_report(run, findings, repo)
    marker = f"<!-- difflens-report:pr-{run.pr_number} -->"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    base_url = "https://api.github.com"

    try:
        with httpx.Client(base_url=base_url, headers=headers, timeout=10.0) as client:
            # 1. Check for existing comment
            comments_resp = client.get(f"/repos/{repo.full_name}/issues/{run.pr_number}/comments")
            if comments_resp.status_code == 200:
                comments = comments_resp.json()
                for comment in comments:
                    if marker in comment.get("body", ""):
                        # Update existing comment in place
                        comment_id = comment["id"]
                        patch_resp = client.patch(
                            f"/repos/{repo.full_name}/issues/comments/{comment_id}",
                            json={"body": report_markdown},
                        )
                        patch_resp.raise_for_status()
                        logger.info("Updated existing DiffLens comment #%s on PR #%s", comment_id, run.pr_number)
                        return patch_resp.json()

            # 2. Post new comment
            post_resp = client.post(
                f"/repos/{repo.full_name}/issues/{run.pr_number}/comments",
                json={"body": report_markdown},
            )
            post_resp.raise_for_status()
            logger.info("Created new DiffLens comment on PR #%s", run.pr_number)
            return post_resp.json()

    except Exception as e:
        logger.warning("Failed to post/update GitHub PR report: %s", e)
        return None
