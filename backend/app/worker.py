from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
from sqlalchemy.orm import Session

from app.ast_analyzer import ASTAnalyzer, DiffHunk
from app.coverage_mapper import compute_hunk_coverage
from app.db import SessionLocal
from app.explainer import create_explainer_for_repo
from app.github_reporter import post_or_update_pr_report
from app.models import HunkRiskFinding, PRAnalysisRun, Repository
from app.queue import get_redis_client, pop_pr_job
from app.scorer import aggregate_pr_risk, calculate_hunk_risk

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
        hunks_raw: List[Dict[str, Any]] = job_payload.get("hunks", [])

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

        # 1. AST Analysis
        ast_analyzer = ASTAnalyzer()
        hunk_analyses = ast_analyzer.analyze_pr_files(
            changed_files=changed_files,
            file_contents=file_contents,
            hunks=diff_hunks,
            repo_snapshot=repo_snapshot,
        )

        hunk_scores: List[float] = []
        created_findings: List[HunkRiskFinding] = []

        # 2. Coverage & Scoring per Hunk
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

            hunk_metrics = analysis.to_hunk_metrics(
                coverage=cov_res.coverage_score,
                delta_loc=len(hunk.added_lines) if hunk.added_lines else max(1, hunk.end_line - hunk.start_line + 1),
            )
            risk_score = calculate_hunk_risk(hunk_metrics)
            hunk_scores.append(risk_score)

            primary_symbol = analysis.symbols[0].qualified_name if analysis.symbols else "<module>"

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
                coverage_score=cov_res.coverage_score,
                coverage_source=cov_res.coverage_source,
                uncovered_lines=cov_res.uncovered_lines,
                diff_patch=hunk.hunk_content or "\n".join(hunk.added_lines),
            )
            session.add(finding)
            created_findings.append(finding)

        # 3. Explainer (strictly for rationale/tests, zero change to scores)
        repo = session.query(Repository).filter(Repository.id == run.repo_id).first()
        medium_threshold = repo.risk_threshold_medium if repo else 40.0
        flagged_findings = [f for f in created_findings if f.risk_score >= medium_threshold]
        flagged_findings.sort(key=lambda f: f.risk_score, reverse=True)

        if flagged_findings and repo:
            explainer = create_explainer_for_repo(repo)
            for f in flagged_findings[:5]:  # Capped at top 5 hunks
                try:
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
                    )
                    f.explanation = exp.model_dump_json()
                except Exception as exp_err:
                    logger.warning("Explanation generation failed for finding %s: %s", f.symbol_name, exp_err)

        # 4. Aggregate PR Score
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
        }

        session.commit()
        session.refresh(run)

        # 5. GitHub PR Comment Reporting
        if repo:
            try:
                post_or_update_pr_report(run, created_findings, repo)
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
