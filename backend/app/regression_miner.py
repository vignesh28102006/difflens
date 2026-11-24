"""
Historical Regression Fingerprint Engine.

Mines local Git history to extract empirical bug-fix hotspots and temporal co-change couplings.
GUARDRAIL: 100% deterministic and unit-testable without network dependencies.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Set, Tuple

BUG_FIX_PATTERN = re.compile(
    r"\b(fix|bug|hotfix|revert|rollback|regression|patch)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class GitCommitInfo:
    hash: str
    subject: str
    files_changed: list[str]
    is_bug_fix: bool


@dataclass
class CoChangeWarning:
    target_file: str
    missing_partner: str
    coupling_ratio: float
    supporting_commits: list[str] = field(default_factory=list)


@dataclass
class FileRegressionProfile:
    file_path: str
    total_commits: int
    bug_fix_commits: int
    bug_fix_rate: float
    coupled_partners: list[str] = field(default_factory=list)


@dataclass
class RegressionMiningResult:
    file_profiles: dict[str, FileRegressionProfile]
    co_change_warnings: list[CoChangeWarning]
    risk_boosts_by_file: dict[str, float]


def normalize_repo_path(p: str) -> str:
    """Normalizes relative file paths across platforms."""
    return p.replace("\\", "/").strip().lstrip("./")


def mine_git_history(
    repo_path: str,
    max_commits: int = 500,
) -> list[GitCommitInfo]:
    """
    Extracts commit history and changed files using local git log subprocess.
    Returns empty list if directory is not a git repository or git fails.
    """
    if not os.path.exists(repo_path):
        return []

    cmd = [
        "git",
        "-C",
        repo_path,
        "log",
        f"-n{max_commits}",
        "--name-only",
        "--pretty=format:COMMIT_START%n%H%n%s",
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=15)
        raw_output = res.stdout
    except Exception:
        return []

    commits: list[GitCommitInfo] = []
    chunks = raw_output.split("COMMIT_START\n")

    for chunk in chunks:
        lines = chunk.strip().splitlines()
        if len(lines) < 2:
            continue

        c_hash = lines[0].strip()
        c_subj = lines[1].strip()
        c_files = [normalize_repo_path(l) for l in lines[2:] if l.strip()]

        if not c_hash:
            continue

        is_fix = bool(BUG_FIX_PATTERN.search(c_subj))
        commits.append(
            GitCommitInfo(
                hash=c_hash,
                subject=c_subj,
                files_changed=c_files,
                is_bug_fix=is_fix,
            )
        )

    return commits


class RegressionMiner:
    """
    Deterministic empirical risk and co-change analyzer.
    """

    def __init__(
        self,
        min_co_occurrences: int = 2,
        coupling_threshold: float = 0.60,
    ) -> None:
        self.min_co_occurrences = min_co_occurrences
        self.coupling_threshold = coupling_threshold

    def analyze(
        self,
        changed_files: Sequence[str],
        commits: Optional[Sequence[GitCommitInfo]] = None,
        repo_path: Optional[str] = None,
    ) -> RegressionMiningResult:
        """
        Mines commit history to compute bug-fix rates, missing co-change partners,
        and deterministic regression risk boosts for changed files.
        """
        commit_list: list[GitCommitInfo] = list(commits) if commits is not None else []
        if not commit_list and repo_path:
            commit_list = mine_git_history(repo_path)

        norm_changed = {normalize_repo_path(f) for f in changed_files}

        # 1. Tally commits per file
        file_total_commits: Dict[str, int] = {}
        file_bug_commits: Dict[str, int] = {}
        # Co-change tracker: A -> {B -> count of bug-fix commits touching both}
        co_occurrence: Dict[str, Dict[str, int]] = {}
        # Supporting commits map: (A, B) -> list of "hash: subject"
        pair_supporting_commits: Dict[Tuple[str, str], list[str]] = {}

        for c in commit_list:
            files_in_commit = set(c.files_changed)

            for f in files_in_commit:
                file_total_commits[f] = file_total_commits.get(f, 0) + 1
                if c.is_bug_fix:
                    file_bug_commits[f] = file_bug_commits.get(f, 0) + 1

            if c.is_bug_fix and len(files_in_commit) > 1:
                commit_label = f"{c.hash[:7]}: {c.subject}"
                for f1 in files_in_commit:
                    if f1 not in co_occurrence:
                        co_occurrence[f1] = {}
                    for f2 in files_in_commit:
                        if f1 == f2:
                            continue
                        co_occurrence[f1][f2] = co_occurrence[f1].get(f2, 0) + 1
                        pair_key = (f1, f2)
                        if pair_key not in pair_supporting_commits:
                            pair_supporting_commits[pair_key] = []
                        pair_supporting_commits[pair_key].append(commit_label)

        # 2. Build Profiles
        profiles: Dict[str, FileRegressionProfile] = {}
        co_change_warnings: List[CoChangeWarning] = []
        risk_boosts: Dict[str, float] = {}

        for f in norm_changed:
            total = file_total_commits.get(f, 0)
            bug_fixes = file_bug_commits.get(f, 0)
            rate = round(bug_fixes / total, 3) if total > 0 else 0.0

            # Discover coupled partners
            coupled_partners: List[str] = []
            f_boost = 0.0

            # Fragile hotspot boost
            if bug_fixes >= 2 and rate >= 0.50:
                f_boost += 10.0

            if f in co_occurrence and bug_fixes >= self.min_co_occurrences:
                for partner, count in co_occurrence[f].items():
                    ratio = count / bug_fixes
                    if count >= self.min_co_occurrences and ratio >= self.coupling_threshold:
                        coupled_partners.append(partner)

                        # Broken Co-Change Detection: partner missing from current PR!
                        if partner not in norm_changed:
                            supports = pair_supporting_commits.get((f, partner), [])
                            co_change_warnings.append(
                                CoChangeWarning(
                                    target_file=f,
                                    missing_partner=partner,
                                    coupling_ratio=round(ratio, 2),
                                    supporting_commits=supports[:5],
                                )
                            )
                            # Elevate risk for breaking a historically coupled pair
                            f_boost += 15.0

            profiles[f] = FileRegressionProfile(
                file_path=f,
                total_commits=total,
                bug_fix_commits=bug_fixes,
                bug_fix_rate=rate,
                coupled_partners=coupled_partners,
            )
            risk_boosts[f] = min(30.0, f_boost)

        return RegressionMiningResult(
            file_profiles=profiles,
            co_change_warnings=co_change_warnings,
            risk_boosts_by_file=risk_boosts,
        )
