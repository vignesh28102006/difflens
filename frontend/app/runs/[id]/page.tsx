"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import {
  ArrowLeft,
  CheckCircle,
  Copy,
  Check,
  AlertTriangle,
  Flame,
  ShieldAlert,
  Code,
  Layers,
  CheckSquare,
} from "lucide-react";

interface HunkFinding {
  id: number;
  run_id: number;
  file_path: string;
  start_line: number;
  end_line: number;
  symbol_name: string;
  risk_score: number;
  domain_score: number;
  domain_tags: string[];
  fan_in: number;
  callers: string[];
  coverage_score: number;
  coverage_source: string;
  uncovered_lines: number[];
  diff_patch: string;
  explanation: string | null;
}

interface ParsedExplanation {
  risk_summary: string;
  why_its_risky: string;
  how_to_fix: string;
  review_checklist: string[];
  suggested_test_code: string;
}

interface RunDetail {
  id: number;
  delivery_id: string;
  repo_full_name: string;
  pr_number: number;
  head_sha: string;
  status: string;
  overall_score: number;
  risk_level: string;
  summary: {
    hunks_count?: number;
    max_hunk_risk?: number;
    high_risk_hunks?: number;
  };
  findings: HunkFinding[];
  created_at: string;
  completed_at: string;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function RunDetailPage() {
  const params = useParams();
  const runId = params?.id as string;
  const [run, setRun] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [copiedId, setCopiedId] = useState<number | null>(null);

  useEffect(() => {
    if (!runId) return;
    fetch(`${API_BASE}/api/runs/${runId}`)
      .then((res) => res.json())
      .then((data) => setRun(data))
      .catch((err) => console.error("Error loading run:", err))
      .finally(() => setLoading(false));
  }, [runId]);

  const copyTestCode = (id: number, code: string) => {
    navigator.clipboard.writeText(code);
    setCopiedId(id);
    setTimeout(() => setCopiedId(null), 2000);
  };

  if (loading) {
    return <div className="text-center py-24 text-slate-500 font-mono text-sm">Loading run #{runId}...</div>;
  }

  if (!run || !run.id) {
    return (
      <div className="text-center py-24 space-y-4">
        <h2 className="text-xl font-bold text-white">Analysis Run Not Found</h2>
        <Link href="/" className="inline-flex items-center space-x-2 text-accent hover:underline text-sm font-medium">
          <ArrowLeft className="w-4 h-4" />
          <span>Back to PR Feed</span>
        </Link>
      </div>
    );
  }

  const getRiskBadge = (score: number) => {
    if (score >= 70.0) return "text-red-400 bg-red-500/10 border-red-500/30";
    if (score >= 40.0) return "text-amber-400 bg-amber-500/10 border-amber-500/30";
    return "text-emerald-400 bg-emerald-500/10 border-emerald-500/30";
  };

  return (
    <div className="space-y-8 pb-12">
      {/* Top Navigation */}
      <div>
        <Link href="/" className="inline-flex items-center space-x-2 text-slate-400 hover:text-white text-sm font-medium transition-colors">
          <ArrowLeft className="w-4 h-4" />
          <span>Back to PR Feed</span>
        </Link>
      </div>

      {/* Header Banner */}
      <div className="bg-surface rounded-2xl border border-border p-6 md:p-8 flex flex-col md:flex-row md:items-center justify-between gap-6">
        <div className="space-y-3">
          <div className="flex items-center space-x-3">
            <span className="text-xs font-mono px-2.5 py-1 rounded bg-background border border-border text-slate-300">
              {run.repo_full_name}
            </span>
            <span className="text-lg font-bold text-white">PR #{run.pr_number}</span>
            <span className={`text-xs font-semibold px-2.5 py-0.5 rounded-full border ${getRiskBadge(run.overall_score)}`}>
              {run.risk_level} Risk
            </span>
          </div>

          <div className="flex flex-wrap items-center gap-4 text-xs font-mono text-slate-400">
            <span>Commit: {run.head_sha}</span>
            <span>•</span>
            <span>Status: {run.status.toUpperCase()}</span>
            <span>•</span>
            <span>{run.findings.length} Hunk(s) Analyzed</span>
          </div>
        </div>

        <div className="flex items-center space-x-6 border-t md:border-t-0 md:border-l border-border pt-4 md:pt-0 md:pl-8">
          <div>
            <div className="text-3xl font-extrabold font-mono text-white">
              {run.overall_score.toFixed(1)}
              <span className="text-sm text-slate-500 font-normal"> / 100</span>
            </div>
            <div className="text-xs text-slate-400 mt-1">Aggregated PR Risk Score</div>
          </div>
        </div>
      </div>

      {/* Findings Section */}
      <div className="space-y-6">
        <div className="flex items-center justify-between">
          <h2 className="text-lg font-bold text-white flex items-center space-x-2">
            <ShieldAlert className="w-5 h-5 text-accent" />
            <span>Hunk Risk Findings & Explanations</span>
          </h2>
          <span className="text-xs font-mono text-slate-400">
            Ranked by deterministic score
          </span>
        </div>

        {run.findings.map((f) => {
          let parsedExp: ParsedExplanation | null = null;
          if (f.explanation) {
            try {
              parsedExp = JSON.parse(f.explanation);
            } catch (e) {}
          }

          return (
            <div
              key={f.id}
              className="bg-surface rounded-xl border border-border overflow-hidden shadow-sm"
            >
              {/* Card Header */}
              <div className="p-5 border-b border-border bg-[#0B1020] flex flex-col md:flex-row md:items-center justify-between gap-4">
                <div className="space-y-1.5">
                  <div className="flex items-center space-x-3">
                    <span className="font-mono font-semibold text-white text-sm">
                      {f.file_path}
                    </span>
                    <span className="text-xs font-mono text-slate-400">
                      lines {f.start_line}-{f.end_line}
                    </span>
                  </div>
                  <div className="text-xs text-accent font-mono">
                    symbol: <span className="font-bold">{f.symbol_name}</span>
                  </div>
                </div>

                <div className="flex flex-wrap items-center gap-3">
                  {f.domain_tags.map((tag) => (
                    <span
                      key={tag}
                      className="text-xs font-mono px-2 py-0.5 rounded bg-background border border-border text-slate-300"
                    >
                      {tag}
                    </span>
                  ))}

                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-background border border-border text-slate-300">
                    fan-in: {f.fan_in}
                  </span>

                  <span className="text-xs font-mono px-2 py-0.5 rounded bg-background border border-border text-slate-300">
                    cov: {Math.round(f.coverage_score * 100)}%
                  </span>

                  <span
                    className={`text-xs font-mono font-bold px-3 py-1 rounded-lg border ${getRiskBadge(
                      f.risk_score
                    )}`}
                  >
                    Risk: {f.risk_score.toFixed(1)}
                  </span>
                </div>
              </div>

              {/* Card Body */}
              <div className="p-6 space-y-6">
                {/* Structured Explanation */}
                {parsedExp ? (
                  <div className="space-y-4">
                    <div className="p-4 rounded-lg bg-background border border-border text-sm text-slate-200">
                      <span className="text-accent font-semibold">Summary: </span>
                      {parsedExp.risk_summary}
                    </div>

                    <div className="grid md:grid-cols-2 gap-4 text-xs">
                      <div className="p-4 rounded-lg bg-[#070B16] border border-border space-y-1">
                        <div className="font-bold text-red-400 uppercase tracking-wider">Why it's risky</div>
                        <p className="text-slate-300 leading-relaxed">{parsedExp.why_its_risky}</p>
                      </div>

                      <div className="p-4 rounded-lg bg-[#070B16] border border-border space-y-1">
                        <div className="font-bold text-emerald-400 uppercase tracking-wider">How to fix</div>
                        <p className="text-slate-300 leading-relaxed">{parsedExp.how_to_fix}</p>
                      </div>
                    </div>

                    {/* Reviewer Checklist */}
                    {parsedExp.review_checklist && parsedExp.review_checklist.length > 0 && (
                      <div className="space-y-2 pt-2">
                        <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center space-x-1.5">
                          <CheckSquare className="w-4 h-4 text-accent" />
                          <span>Reviewer Checklist</span>
                        </div>
                        <ul className="space-y-1.5">
                          {parsedExp.review_checklist.map((item, idx) => (
                            <li key={idx} className="flex items-start space-x-2 text-xs text-slate-300">
                              <span className="text-accent mt-0.5 font-mono">▪</span>
                              <span>{item}</span>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}

                    {/* Suggested Test Stub */}
                    {parsedExp.suggested_test_code && (
                      <div className="space-y-2 pt-2">
                        <div className="flex items-center justify-between">
                          <div className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center space-x-1.5">
                            <Code className="w-4 h-4 text-accent" />
                            <span>Suggested Targeted Test Stub</span>
                          </div>
                          <button
                            onClick={() => copyTestCode(f.id, parsedExp!.suggested_test_code)}
                            className="inline-flex items-center space-x-1 text-xs text-slate-400 hover:text-accent font-mono transition-colors"
                          >
                            {copiedId === f.id ? (
                              <>
                                <Check className="w-3.5 h-3.5 text-emerald-400" />
                                <span className="text-emerald-400">Copied!</span>
                              </>
                            ) : (
                              <>
                                <Copy className="w-3.5 h-3.5" />
                                <span>Copy Test</span>
                              </>
                            )}
                          </button>
                        </div>
                        <pre className="p-4 rounded-lg bg-[#05070A] border border-border text-xs font-mono text-emerald-300 overflow-x-auto">
                          <code>{parsedExp.suggested_test_code.trim()}</code>
                        </pre>
                      </div>
                    )}
                  </div>
                ) : (
                  <div className="text-xs text-slate-400 italic">
                    Deterministic score calculated. Explanation overlay omitted (below medium threshold or provider unconfigured).
                  </div>
                )}

                {/* Diff Preview */}
                {f.diff_patch && (
                  <details className="group pt-2">
                    <summary className="text-xs font-mono text-slate-400 hover:text-white cursor-pointer select-none">
                      ▸ View Diff Patch Snippet
                    </summary>
                    <pre className="mt-3 p-4 rounded-lg bg-[#05070A] border border-border text-xs font-mono text-slate-300 overflow-x-auto">
                      <code>{f.diff_patch}</code>
                    </pre>
                  </details>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
