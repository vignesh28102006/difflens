"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  Activity,
  Flame,
  Shield,
  AlertTriangle,
  CheckCircle,
  RefreshCw,
  GitPullRequest,
  TrendingUp,
  FileCode,
  ShieldAlert,
} from "lucide-react";

interface Repository {
  id: number;
  github_repo_id: number;
  full_name: string;
}

interface RecurringHotspot {
  file_path: string;
  symbol_name: string;
  occurrences: number;
  max_risk_score: number;
}

interface ChronicCoverageGap {
  symbol_name: string;
  uncovered_occurrences: number;
  latest_coverage: number;
}

interface RiskDebtReport {
  repo_id: number;
  repo_name: string;
  total_runs: number;
  avg_repo_score: number;
  risk_distribution: {
    High: number;
    Medium: number;
    Low: number;
  };
  recurring_hotspots: RecurringHotspot[];
  chronic_coverage_gaps: ChronicCoverageGap[];
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function RiskDebtPage() {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [selectedRepoId, setSelectedRepoId] = useState<number | null>(null);
  const [debtReport, setDebtReport] = useState<RiskDebtReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [reportLoading, setReportLoading] = useState(false);

  useEffect(() => {
    fetch(`${API_BASE}/api/repos`)
      .then((res) => res.json())
      .then((data: Repository[]) => {
        setRepos(data);
        if (data.length > 0) {
          setSelectedRepoId(data[0].id);
        }
      })
      .catch((err) => console.error("Failed to load repos:", err))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!selectedRepoId) return;
    setReportLoading(true);
    fetch(`${API_BASE}/api/repos/${selectedRepoId}/risk-debt`)
      .then((res) => res.json())
      .then((data: RiskDebtReport) => setDebtReport(data))
      .catch((err) => console.error("Failed to load risk debt report:", err))
      .finally(() => setReportLoading(false));
  }, [selectedRepoId]);

  return (
    <div className="space-y-8 max-w-6xl mx-auto pb-12">
      {/* Header */}
      <div className="border-b border-border pb-6 flex flex-col md:flex-row md:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center space-x-2.5">
            <Activity className="w-6 h-6 text-accent" />
            <span>Repository Risk Debt Ledger</span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Aggregated cross-PR intelligence: recurring high-risk hotspots, chronic coverage gaps, and organizational risk velocity.
          </p>
        </div>

        {repos.length > 0 && (
          <div className="flex items-center space-x-3">
            <label className="text-xs font-mono text-slate-400">Repository:</label>
            <select
              value={selectedRepoId || ""}
              onChange={(e) => setSelectedRepoId(Number(e.target.value))}
              className="px-3 py-1.5 rounded-lg bg-surface border border-border text-xs font-mono text-white focus:outline-none focus:border-accent"
            >
              {repos.map((r) => (
                <option key={r.id} value={r.id}>
                  {r.full_name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {loading ? (
        <div className="text-center py-20 text-slate-500 font-mono text-sm">Loading repositories...</div>
      ) : repos.length === 0 ? (
        <div className="text-center py-20 bg-surface rounded-xl border border-border">
          <Shield className="w-10 h-10 text-slate-600 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-slate-200">No Repositories Recorded</h3>
          <p className="text-sm text-slate-400 max-w-sm mx-auto mt-1">
            Risk debt accumulates as PRs are analyzed across webhook deliveries.
          </p>
        </div>
      ) : reportLoading ? (
        <div className="text-center py-20 text-slate-500 font-mono text-sm">Aggregating risk debt metrics...</div>
      ) : debtReport ? (
        <div className="space-y-8">
          {/* Key Metrics Overview */}
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
            <div className="bg-surface rounded-xl border border-border p-5">
              <div className="text-xs font-mono text-slate-400">Total PR Runs</div>
              <div className="text-2xl font-bold font-mono text-white mt-1">
                {debtReport.total_runs}
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">Historical analyzed PRs</div>
            </div>

            <div className="bg-surface rounded-xl border border-border p-5">
              <div className="text-xs font-mono text-slate-400">Average Repo Score</div>
              <div className="text-2xl font-bold font-mono text-accent mt-1">
                {debtReport.avg_repo_score.toFixed(1)}
                <span className="text-xs text-slate-500 font-normal"> / 100</span>
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">Baseline risk across commits</div>
            </div>

            <div className="bg-surface rounded-xl border border-border p-5">
              <div className="text-xs font-mono text-slate-400">High Risk PRs</div>
              <div className="text-2xl font-bold font-mono text-red-400 mt-1">
                {debtReport.risk_distribution.High}
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">Flagged above high threshold</div>
            </div>

            <div className="bg-surface rounded-xl border border-border p-5">
              <div className="text-xs font-mono text-slate-400">Medium & Low Runs</div>
              <div className="text-2xl font-bold font-mono text-emerald-400 mt-1">
                {debtReport.risk_distribution.Medium + debtReport.risk_distribution.Low}
              </div>
              <div className="text-[11px] text-slate-500 mt-0.5">Controlled or low blast radius</div>
            </div>
          </div>

          {/* Hotspots & Gaps Split */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            {/* Recurring High-Risk Hotspots */}
            <div className="bg-surface rounded-xl border border-border p-6 space-y-4">
              <div className="flex items-center space-x-2">
                <Flame className="w-5 h-5 text-red-400" />
                <h2 className="text-base font-bold text-white">Recurring High-Risk Hotspots</h2>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">
                Functions, classes, or files repeatedly appearing in high-risk PR hunks without comprehensive safety nets.
              </p>

              {debtReport.recurring_hotspots.length === 0 ? (
                <div className="text-center py-8 text-xs font-mono text-slate-500">
                  No recurring hotspots detected yet.
                </div>
              ) : (
                <div className="space-y-2.5">
                  {debtReport.recurring_hotspots.map((spot, idx) => (
                    <div
                      key={idx}
                      className="p-3.5 rounded-lg bg-background border border-border flex items-center justify-between gap-3 text-xs"
                    >
                      <div className="space-y-1">
                        <div className="font-mono text-slate-200 font-semibold">{spot.file_path}</div>
                        <div className="text-accent font-mono text-[11px]">
                          symbol: <span className="font-bold">{spot.symbol_name}</span>
                        </div>
                      </div>
                      <div className="flex items-center space-x-3 text-right font-mono">
                        <div>
                          <div className="text-red-400 font-bold">{spot.max_risk_score.toFixed(1)} max risk</div>
                          <div className="text-[10px] text-slate-400">{spot.occurrences} PR occurrences</div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Chronic Coverage Gaps */}
            <div className="bg-surface rounded-xl border border-border p-6 space-y-4">
              <div className="flex items-center space-x-2">
                <AlertTriangle className="w-5 h-5 text-amber-400" />
                <h2 className="text-base font-bold text-white">Chronic Coverage Gaps</h2>
              </div>
              <p className="text-xs text-slate-400 leading-relaxed">
                Critical symbols repeatedly modified across PRs with zero or near-zero test coverage (&lt; 30%).
              </p>

              {debtReport.chronic_coverage_gaps.length === 0 ? (
                <div className="text-center py-8 text-xs font-mono text-slate-500">
                  No chronic coverage gaps detected.
                </div>
              ) : (
                <div className="space-y-2.5">
                  {debtReport.chronic_coverage_gaps.map((gap, idx) => (
                    <div
                      key={idx}
                      className="p-3.5 rounded-lg bg-background border border-border flex items-center justify-between gap-3 text-xs"
                    >
                      <div className="space-y-1">
                        <div className="font-mono text-slate-200 font-semibold">{gap.symbol_name}</div>
                        <div className="text-slate-400 text-[11px]">
                          Uncovered in {gap.uncovered_occurrences} separate PR edit(s)
                        </div>
                      </div>
                      <div className="flex items-center space-x-3 text-right font-mono">
                        <span className="px-2.5 py-1 rounded bg-amber-500/10 border border-amber-500/30 text-amber-400 font-semibold text-[11px]">
                          {Math.round(gap.latest_coverage * 100)}% latest cov
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}