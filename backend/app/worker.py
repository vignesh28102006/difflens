from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.ast_analyzer import ASTAnalyzer, DiffHunk
from app.coverage_mapper import compute_hunk_coverage
from app.db import SessionLocal
from app.deployment_mapper import DeploymentMapper
from app.explainer import create_explainer_for_repo
from app.github_reporter import post_or_update_pr_report
from app.interaction_analyzer import InteractionAnalyzer
from app.models import HunkRiskFinding, PRAnalysisRun, Repository
from app.queue import get_redis_client, pop_pr_job
from app.regression_miner import RegressionMiner
from app.runtime_tracer import RuntimeTracer, SymbolBlastRadius
from app.scorer import aggregate_pr_risk, calculate_hunk_risk
from app.test_selector import TestSelector

logger = logging.getLogger("difflens.worker")


def process_pr_job(
    job_payload: Dict[str, Any],
    session: Session,
    redis_client: Optional[Any] = None,
) -> PRAnalysisRun:
    """
    Deterministic background analysis pipeline:
    1. Update run status to 'running'
    2. Run AST analyzer to extract symbols, call graph, and fan-in
    3. Run coverage mapper to compute C_h (Strategy A report or Strategy B heuristic)
    4. Calculate hunk risk scores and aggregate to PR risk level
    5. Persist HunkRiskFinding records and mark run 'completed'
    """
    run_id = job_payload["run_id"]
    run = session.query(PRAnalysisRun).filter(PRAnalysisRun.id == run_id).first()
    if not run:
        raise ValueError(f"PRAnalysisRun with id {run_id} not found")

    run.status = "running"
    session.commit()

    try:
        changed_files: List[str] = job_payload.get("changed_files", [])
        file_contents: Dict[str, str] = job_payload.get("file_contents", {})
        repo_snapshot: Dict[str, str] = job_payload.get("repo_snapshot", file_contents)
        coverage_report = job_payload.get("coverage_report")
        hunks_raw: List[Dict[str, Any]] = job_payload.get("hunks") or job_payload.get("diff_hunks", [])

        # Materialize DiffHunk objects
        diff_hunks: List[DiffHunk] = []
        for h in hunks_raw:
            diff_hunks.append(
                DiffHunk(
                    file_path=h["file_path"],
                    start_line=h["start_line"],
                    end_line=h["end_line"],
                    added_lines=h.get("added_lines", []),
                    deleted_lines=h.get("deleted_lines", []),
                    hunk_content=h.get("hunk_content", ""),
                )
            )

        # 1. Historical Regression Mining
        regression_miner = RegressionMiner()
        repo_path = job_payload.get("repo_path")
        commits_data = job_payload.get("commits")
        reg_result = regression_miner.analyze(
            changed_files=changed_files,
            commits=commits_data,
            repo_path=repo_path,
        )
        risk_boosts_by_file = reg_result.risk_boosts_by_file
        co_change_warnings = reg_result.co_change_warnings

        # 2. AST Analysis
        ast_analyzer = ASTAnalyzer()
        hunk_analyses = ast_analyzer.analyze_pr_files(
            changed_files=changed_files,
            file_contents=file_contents,
            hunks=diff_hunks,
            repo_snapshot=repo_snapshot,
        )

        hunk_scores: List[float] = []
        created_findings: List[HunkRiskFinding] = []
        all_blast_results: List[SymbolBlastRadius] = []
        runtime_tracer = RuntimeTracer()
        trace_data = job_payload.get("runtime_trace") or job_payload.get("trace_data")

        # 3. Coverage, Runtime Blast Radius & Scoring per Hunk (with regression boost)
        for hunk, analysis in zip(diff_hunks, hunk_analyses):
            hunk_lines = list(range(hunk.start_line, hunk.end_line + 1))
            symbol_names = [s.symbol_name for s in analysis.symbols]

            cov_res = compute_hunk_coverage(
                file_path=hunk.file_path,
                hunk_lines=hunk_lines,
                touched_symbols=symbol_names,
                coverage_report=coverage_report,
                changed_files=changed_files,
                repo_snapshot=repo_snapshot,
            )

            primary_symbol = analysis.symbols[0].qualified_name if analysis.symbols else "<module>"
            blast_res = runtime_tracer.analyze_symbol(
                symbol_name=primary_symbol,
                file_path=hunk.file_path,
                static_callers=analysis.callers,
                trace_data=trace_data,
            )
            all_blast_results.append(blast_res)

            hunk_metrics = analysis.to_hunk_metrics(
                coverage=cov_res.coverage_score,
                delta_loc=len(hunk.added_lines) if hunk.added_lines else max(1, hunk.end_line - hunk.start_line + 1),
            )
            hunk_metrics.effective_fan_in = blast_res.total_effective_fan_in
            hunk_metrics.runtime_verified_fan_in = blast_res.runtime_verified_fan_in
            hunk_metrics.static_only_fan_in = blast_res.static_only_fan_in
            hunk_metrics.dynamic_only_fan_in = blast_res.dynamic_only_fan_in

            reg_boost = risk_boosts_by_file.get(hunk.file_path, 0.0)
            risk_score = calculate_hunk_risk(hunk_metrics, regression_boost=reg_boost)
            hunk_scores.append(risk_score)

            caller_verifs = {cv.caller: cv.status for cv in blast_res.caller_verifications}

            finding = HunkRiskFinding(
                run_id=run.id,
                file_path=hunk.file_path,
                start_line=hunk.start_line,
                end_line=hunk.end_line,
                symbol_name=primary_symbol,
                risk_score=risk_score,
                domain_score=analysis.domain_score,
                domain_tags=analysis.domain_tags,
                fan_in=analysis.fan_in,
                callers=analysis.callers,
                caller_verifications=caller_verifs,
                coverage_score=cov_res.coverage_score,
                coverage_source=cov_res.coverage_source,
                uncovered_lines=cov_res.uncovered_lines,
                diff_patch=hunk.hunk_content or "\n".join(hunk.added_lines),
            )
            session.add(finding)
            created_findings.append(finding)

        # 4. Cross-File Change Interaction Analysis
        interaction_analyzer = InteractionAnalyzer(ast_analyzer=ast_analyzer)
        interaction_findings = interaction_analyzer.analyze_interactions(
            changed_files=changed_files,
            base_contents=job_payload.get("base_contents", {}),
            head_contents=file_contents,
            repo_snapshot=repo_snapshot,
        )

        # 5. Risk-Weighted Test Selection
        test_selector = TestSelector()
        changed_symbols_with_risk = [
            (f.symbol_name, f.risk_score, f.fan_in) for f in created_findings
        ]
        test_selection = test_selector.select_tests(
            changed_symbols_with_risk=changed_symbols_with_risk,
            repo_snapshot=repo_snapshot,
        )

        # 6. Deployment Impact Forecast
        deployment_mapper = DeploymentMapper()
        all_symbols = [f.symbol_name for f in created_findings]
        all_callers = [c for f in created_findings for c in (f.callers or [])]
        deployment_impact = deployment_mapper.analyze_impact(
            changed_files=changed_files,
            changed_symbols=all_symbols,
            callers=all_callers,
            repo_snapshot=repo_snapshot,
        )

        # 7. Explainer (strictly for rationale/tests, zero change to scores)
        repo = session.query(Repository).filter(Repository.id == run.repo_id).first()
        medium_threshold = repo.risk_threshold_medium if repo else 40.0
        flagged_findings = [f for f in created_findings if f.risk_score >= medium_threshold]
        flagged_findings.sort(key=lambda f: f.risk_score, reverse=True)

        if flagged_findings and repo:
            explainer = create_explainer_for_repo(repo)
            for f in flagged_findings[:5]:  # Capped at top 5 hunks
                try:
                    # Gather co-change, interaction & deployment evidence for this file/symbol
                    f_co_change = [
                        f"Missing coupled file `{w.missing_partner}` ({int(w.coupling_ratio * 100)}% historical co-change rate in bug fixes)"
                        for w in co_change_warnings if w.target_file == f.file_path
                    ]
                    f_interaction = [
                        f"[{i.severity}] {i.interaction_type}: {i.evidence}"
                        for i in interaction_findings if i.file_path == f.file_path or i.source_symbol == f.symbol_name
                    ]
                    if deployment_impact.affected_endpoints:
                        f_interaction.append(
                            f"[Deployment Impact: {deployment_impact.impact_tier}] Reaches public endpoints: {', '.join(deployment_impact.affected_endpoints[:3])}"
                        )

                    exp = explainer.explain(
                        file_path=f.file_path,
                        symbol_name=f.symbol_name,
                        risk_score=f.risk_score,
                        domain_score=f.domain_score,
                        domain_tags=f.domain_tags,
                        fan_in=f.fan_in,
                        callers=f.callers,
                        coverage_score=f.coverage_score,
                        uncovered_lines=f.uncovered_lines,
                        diff_patch=f.diff_patch,
                        co_change_evidence=f_co_change,
                        interaction_evidence=f_interaction,
                    )
                    f.explanation = exp.model_dump_json()
                except Exception as exp_err:
                    logger.warning("Explanation generation failed for finding %s: %s", f.symbol_name, exp_err)

        # 8. Aggregate PR Score
        overall_score = aggregate_pr_risk(hunk_scores)
        risk_level = (
            "High"
            if overall_score >= (repo.risk_threshold_high if repo else 70.0)
            else "Medium"
            if overall_score >= medium_threshold
            else "Low"
        )

        run.overall_score = overall_score
        run.risk_level = risk_level
        run.status = "completed"
        run.completed_at = datetime.now(timezone.utc)
        run.summary_json = {
            "hunks_count": len(hunk_scores),
            "max_hunk_risk": max(hunk_scores) if hunk_scores else 0.0,
            "high_risk_hunks": sum(1 for s in hunk_scores if s >= (repo.risk_threshold_high if repo else 70.0)),
            "co_change_warnings": [
                {
                    "target_file": w.target_file,
                    "missing_partner": w.missing_partner,
                    "coupling_ratio": w.coupling_ratio,
                    "supporting_commits": w.supporting_commits,
                }
                for w in co_change_warnings
            ],
            "interaction_findings": [i.to_dict() for i in interaction_findings],
            "test_selection": test_selection.to_dict(),
            "deployment_impact": deployment_impact.to_dict(),
            "runtime_blast_radius": {
                "total_runtime_verified": sum(b.runtime_verified_fan_in for b in all_blast_results),
                "total_static_only": sum(b.static_only_fan_in for b in all_blast_results),
                "total_dynamic_only": sum(b.dynamic_only_fan_in for b in all_blast_results),
                "confidence": round(sum(b.blast_radius_confidence for b in all_blast_results) / max(1, len(all_blast_results)), 2) if all_blast_results else 0.5,
            },
        }

        session.commit()
        session.refresh(run)

        # 9. GitHub PR Comment Reporting
        if repo:
            try:
                post_or_update_pr_report(
                    run,
                    created_findings,
                    repo,
                    co_change_warnings=co_change_warnings,
                    interaction_findings=interaction_findings,
                    test_selection=test_selection.to_dict(),
                    deployment_impact=deployment_impact.to_dict(),
                )
            except Exception as rep_err:
                logger.warning("GitHub PR reporting error: %s", rep_err)

        logger.info("Completed analysis for run #%s: score=%.2f (%s)", run.id, overall_score, risk_level)
        return run

    except Exception as exc:
        session.rollback()
        logger.exception("Pipeline failed for run #%s: %s", run.id, exc)
        run.status = "failed"
        run.error_message = str(exc)
        session.commit()
        session.refresh(run)
        raise exc


def run_worker(max_jobs: Optional[int] = None, poll_timeout: int = 2) -> None:
    """Worker daemon entrypoint."""
    redis_client = get_redis_client()
    jobs_processed = 0

    logger.info("DiffLens background worker started.")
    while max_jobs is None or jobs_processed < max_jobs:
        job = pop_pr_job(redis_client, timeout=poll_timeout)
        if job:
            with SessionLocal() as session:
                try:
                    process_pr_job(job, session, redis_client)
                    jobs_processed += 1
                except Exception as e:
                    logger.error("Job processing error: %s", e)


if __name__ == "__main__":
    run_worker()
