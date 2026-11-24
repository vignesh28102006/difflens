"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { GitPullRequest, ArrowRight, AlertTriangle, ShieldCheck, Clock, RefreshCw } from "lucide-react";

interface PRRun {
  id: number;
  delivery_id: string;
  repo_id: number;
  repo_full_name: string | null;
  pr_number: number;
  head_sha: string;
  base_sha: string | null;
  status: string;
  overall_score: number;
  risk_level: string;
  summary: {
    hunks_count?: number;
    max_hunk_risk?: number;
    high_risk_hunks?: number;
  };
  created_at: string;
  completed_at: string | null;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function PRRunsFeed() {
  const [runs, setRuns] = useState<PRRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState<"ALL" | "High" | "Medium" | "Low">("ALL");

  const fetchRuns = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE}/api/runs`);
      if (res.ok) {
        const data = await res.json();
        setRuns(data);
      }
    } catch (err) {
      console.error("Failed to load runs:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRuns();
  }, []);

  const filteredRuns = runs.filter((r) => {
    if (filter === "ALL") return true;
    return r.risk_level === filter;
  });

  const getRiskColor = (level: string) => {
    switch (level) {
      case "High":
        return "text-red-400 bg-red-500/10 border-red-500/30";
      case "Medium":
        return "text-amber-400 bg-amber-500/10 border-amber-500/30";
      case "Low":
        return "text-emerald-400 bg-emerald-500/10 border-emerald-500/30";
      default:
        return "text-slate-400 bg-slate-500/10 border-slate-500/30";
    }
  };

  return (
    <div className="space-y-6">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4 pb-6 border-b border-border">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">Pull Request Risk Runs</h1>
          <p className="text-sm text-slate-400 mt-1">
            Deterministic AST blast-radius and coverage analysis across inspected pull requests.
          </p>
        </div>
        <button
          onClick={fetchRuns}
          className="inline-flex items-center space-x-2 px-3 py-1.5 rounded-lg bg-surface border border-border text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors self-start sm:self-auto"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
          <span>Refresh</span>
        </button>
      </div>

      {/* Filter Tabs */}
      <div className="flex items-center space-x-2 border-b border-border pb-4">
        {(["ALL", "High", "Medium", "Low"] as const).map((lvl) => (
          <button
            key={lvl}
            onClick={() => setFilter(lvl)}
            className={`px-3 py-1 rounded-md text-xs font-medium transition-colors ${
              filter === lvl
                ? "bg-accent/10 text-accent border border-accent/40"
                : "text-slate-400 hover:text-white hover:bg-surface"
            }`}
          >
            {lvl === "ALL" ? "All Runs" : `${lvl} Risk`}
          </button>
        ))}
      </div>

      {/* Runs List */}
      {loading && runs.length === 0 ? (
        <div className="text-center py-20 text-slate-500 font-mono text-sm">Loading PR analysis records...</div>
      ) : filteredRuns.length === 0 ? (
        <div className="text-center py-20 bg-surface rounded-xl border border-border">
          <GitPullRequest className="w-10 h-10 text-slate-600 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-slate-200">No PR Runs Recorded Yet</h3>
          <p className="text-sm text-slate-400 max-w-sm mx-auto mt-1">
            Deliver GitHub webhook payloads to <code>/webhooks/github</code> to trigger automated deterministic PR scoring.
          </p>
        </div>
      ) : (
        <div className="grid gap-4">
          {filteredRuns.map((run) => (
            <Link
              key={run.id}
              href={`/runs/${run.id}`}
              className="group block bg-surface hover:bg-[#0E1528] border border-border hover:border-accent/40 rounded-xl p-5 transition-all shadow-sm"
            >
              <div className="flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="space-y-2">
                  <div className="flex items-center space-x-3">
                    <span className="text-xs font-mono text-slate-400 bg-background px-2 py-0.5 rounded border border-border">
                      {run.repo_full_name || "Repository"}
                    </span>
                    <span className="text-sm font-semibold text-white group-hover:text-accent transition-colors">
                      PR #{run.pr_number}
                    </span>
                    <span
                      className={`text-xs font-semibold px-2.5 py-0.5 rounded-full border ${getRiskColor(
                        run.risk_level
                      )}`}
                    >
                      {run.risk_level} Risk
                    </span>
                  </div>

                  <div className="flex items-center space-x-4 text-xs font-mono text-slate-400">
                    <span>Commit: {run.head_sha.substring(0, 7)}</span>
                    <span>•</span>
                    <span>{run.summary?.hunks_count || 0} Hunks Analyzed</span>
                    <span>•</span>
                    <span>{run.summary?.high_risk_hunks || 0} Flagged</span>
                  </div>
                </div>

                <div className="flex items-center space-x-6 self-end md:self-auto">
                  <div className="text-right">
                    <div className="text-2xl font-bold font-mono text-white">
                      {run.overall_score.toFixed(1)}
                      <span className="text-xs text-slate-500 font-normal"> / 100</span>
                    </div>
                    <div className="text-[11px] text-slate-400 flex items-center justify-end space-x-1 mt-0.5">
                      <Clock className="w-3 h-3" />
                      <span>{new Date(run.created_at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
                    </div>
                  </div>

                  <div className="w-8 h-8 rounded-lg bg-surface border border-border group-hover:border-accent/50 flex items-center justify-center text-slate-400 group-hover:text-accent transition-colors">
                    <ArrowRight className="w-4 h-4" />
                  </div>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
