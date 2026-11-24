from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from sqlalchemy import (
    BigInteger,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Repository(Base):
    __tablename__ = "repositories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    github_repo_id = Column(BigInteger, unique=True, index=True, nullable=True)
    full_name = Column(String(255), unique=True, nullable=False, index=True)
    installation_id = Column(BigInteger, nullable=True)
    risk_threshold_high = Column(Float, default=70.0, nullable=False)
    risk_threshold_medium = Column(Float, default=40.0, nullable=False)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    runs = relationship("PRAnalysisRun", back_populates="repository", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "github_repo_id": self.github_repo_id,
            "full_name": self.full_name,
            "installation_id": self.installation_id,
            "risk_threshold_high": self.risk_threshold_high,
            "risk_threshold_medium": self.risk_threshold_medium,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class PRAnalysisRun(Base):
    __tablename__ = "pr_analysis_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    delivery_id = Column(String(255), unique=True, nullable=False, index=True)
    repo_id = Column(Integer, ForeignKey("repositories.id"), nullable=False, index=True)
    pr_number = Column(Integer, nullable=False, index=True)
    head_sha = Column(String(40), nullable=False)
    base_sha = Column(String(40), nullable=True)
    status = Column(String(50), default="queued", nullable=False)  # queued, running, completed, failed
    overall_score = Column(Float, default=0.0, nullable=False)
    risk_level = Column(String(20), default="Low", nullable=False)  # Low, Medium, High
    summary_json = Column(JSON, default=dict, nullable=False)
    error_message = Column(Text, nullable=True)
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
    completed_at = Column(DateTime(timezone=True), nullable=True)

    repository = relationship("Repository", back_populates="runs")
    findings = relationship(
        "HunkRiskFinding",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="HunkRiskFinding.id",
    )

    def to_dict(self, include_findings: bool = False) -> Dict[str, Any]:
        data: Dict[str, Any] = {
            "id": self.id,
            "delivery_id": self.delivery_id,
            "repo_id": self.repo_id,
            "repo_full_name": self.repository.full_name if self.repository else None,
            "pr_number": self.pr_number,
            "head_sha": self.head_sha,
            "base_sha": self.base_sha,
            "status": self.status,
            "overall_score": self.overall_score,
            "risk_level": self.risk_level,
            "summary": self.summary_json,
            "error_message": self.error_message,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }
        if include_findings:
            data["findings"] = [f.to_dict() for f in self.findings]
        return data


class HunkRiskFinding(Base):
    __tablename__ = "hunk_risk_findings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(Integer, ForeignKey("pr_analysis_runs.id"), nullable=False, index=True)
    file_path = Column(String(500), nullable=False)
    start_line = Column(Integer, nullable=False)
    end_line = Column(Integer, nullable=False)
    symbol_name = Column(String(255), nullable=False)
    risk_score = Column(Float, nullable=False)
    domain_score = Column(Float, nullable=False)
    domain_tags = Column(JSON, default=list, nullable=False)
    fan_in = Column(Integer, default=0, nullable=False)
    callers = Column(JSON, default=list, nullable=False)
    coverage_score = Column(Float, default=0.0, nullable=False)
    coverage_source = Column(String(50), default="none", nullable=False)
    uncovered_lines = Column(JSON, default=list, nullable=False)
    diff_patch = Column(Text, default="", nullable=False)
    explanation = Column(Text, nullable=True)

    run = relationship("PRAnalysisRun", back_populates="findings")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "file_path": self.file_path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "symbol_name": self.symbol_name,
            "risk_score": self.risk_score,
            "domain_score": self.domain_score,
            "domain_tags": self.domain_tags,
            "fan_in": self.fan_in,
            "callers": self.callers,
            "coverage_score": self.coverage_score,
            "coverage_source": self.coverage_source,
            "uncovered_lines": self.uncovered_lines,
            "diff_patch": self.diff_patch,
            "explanation": self.explanation,
        }
