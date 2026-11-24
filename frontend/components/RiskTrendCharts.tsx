"use client";

import React, { useState } from "react";
import Link from "next/link";
import { TrendingUp, PieChart } from "lucide-react";

interface RecentRun {
  id: number;
  pr_number: number;
  head_sha: string;
  overall_score: number;
  risk_level: string;
  created_at: string | null;
}

interface RiskTrendChartsProps {
  recentRuns: RecentRun[];
  distribution: {
    High: number;
    Medium: number;
    Low: number;
  };
}

export default function RiskTrendCharts({ recentRuns, distribution }: RiskTrendChartsProps) {
  const [hoveredRun, setHoveredRun] = useState<RecentRun | null>(null);

  // Order chronologically (oldest -> newest)
  const chronologicalRuns = [...recentRuns].sort((a, b) => a.id - b.id);
  const totalRuns = distribution.High + distribution.Medium + distribution.Low;

  // Chart dimensions
  const width = 560;
  const height = 220;
  const padLeft = 40;
  const padRight = 25;
  const padTop = 25;
  const padBottom = 35;
  const plotW = width - padLeft - padRight;
  const plotH = height - padTop - padBottom;

  const getX = (idx: number, count: number) => {
    if (count <= 1) return padLeft + plotW / 2;
    return padLeft + (idx / (count - 1)) * plotW;
  };

  const getY = (score: number) => {
    const clamped = Math.max(0, Math.min(100, score));
    return padTop + plotH - (clamped / 100) * plotH;
  };

  const points = chronologicalRuns.map((r, idx) => ({
    x: getX(idx, chronologicalRuns.length),
    y: getY(r.overall_score),
    run: r,
  }));

  const linePath =
    points.length > 1
      ? points.map((p, idx) => `${idx === 0 ? "M" : "L"} ${p.x} ${p.y}`).join(" ")
      : "";

  const areaPath =
    points.length > 1
      ? `${linePath} L ${points[points.length - 1].x} ${padTop + plotH} L ${points[0].x} ${padTop + plotH} Z`
      : "";

  return (
    <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
      {/* PR Risk Velocity Chart */}
      <div className="lg:col-span-2 bg-surface rounded-xl border border-border p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-2">
            <TrendingUp className="w-5 h-5 text-accent" />
            <h2 className="text-base font-bold text-white">PR Risk Velocity Over Time</h2>
          </div>
          {hoveredRun ? (
            <span className="text-xs font-mono px-2.5 py-0.5 rounded bg-background border border-accent/40 text-accent">
              PR #{hoveredRun.pr_number}: {hoveredRun.overall_score.toFixed(1)} ({hoveredRun.risk_level})
            </span>
          ) : (
            <span className="text-xs font-mono text-slate-400">Hover or click points to inspect PR</span>
          )}
        </div>

        {chronologicalRuns.length === 0 ? (
          <div className="h-[220px] flex items-center justify-center text-xs font-mono text-slate-500">
            Not enough PR history to plot risk trend yet.
          </div>
        ) : (
          <div className="bg-[#05070A] rounded-lg border border-border p-3 overflow-x-auto">
            <svg viewBox={`0 0 ${width} ${height}`} className="w-full min-w-[440px] h-[210px] select-none">
              <defs>
                <linearGradient id="riskAreaGrad" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0%" stopColor="#38BDF8" stopOpacity="0.35" />
                  <stop offset="100%" stopColor="#38BDF8" stopOpacity="0.0" />
                </linearGradient>
              </defs>

              {/* Horizontal Grid & Threshold Lines */}
              {[0, 40, 70, 100].map((val) => {
                const y = getY(val);
                const isHigh = val === 70;
                const isMed = val === 40;
                return (
                  <g key={val}>
                    <line
                      x1={padLeft}
                      y1={y}
                      x2={width - padRight}
                      y2={y}
                      stroke={isHigh ? "#EF4444" : isMed ? "#F59E0B" : "#1E293B"}
                      strokeWidth={isHigh || isMed ? 1 : 0.75}
                      strokeDasharray={isHigh || isMed ? "4,4" : undefined}
                      opacity={isHigh || isMed ? 0.5 : 0.7}
                    />
                    <text
                      x={padLeft - 8}
                      y={y + 3}
                      textAnchor="end"
                      fill={isHigh ? "#F87171" : isMed ? "#FBBF24" : "#64748B"}
                      fontSize="10"
                      fontFamily="monospace"
                    >
                      {val}
                    </text>
                  </g>
                );
              })}

              {/* Area & Line */}
              {areaPath && <path d={areaPath} fill="url(#riskAreaGrad)" />}
              {linePath && (
                <path d={linePath} fill="none" stroke="#38BDF8" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" />
              )}

              {/* Data Points */}
              {points.map(({ x, y, run }) => {
                const color =
                  run.overall_score >= 70
                    ? "#EF4444"
                    : run.overall_score >= 40
                    ? "#F59E0B"
                    : "#10B981";
                const isHovered = hoveredRun?.id === run.id;

                return (
                  <g key={run.id} className="cursor-pointer">
                    <Link href={`/runs/${run.id}`}>
                      <circle
                        cx={x}
                        cy={y}
                        r={isHovered ? 7 : 5}
                        fill={color}
                        stroke="#05070A"
                        strokeWidth="2"
                        onMouseEnter={() => setHoveredRun(run)}
                        onMouseLeave={() => setHoveredRun(null)}
                      />
                      <text
                        x={x}
                        y={padTop + plotH + 18}
                        textAnchor="middle"
                        fill={isHovered ? "#FFFFFF" : "#94A3B8"}
                        fontSize="10"
                        fontFamily="monospace"
                      >
                        #{run.pr_number}
                      </text>
                    </Link>
                  </g>
                );
              })}
            </svg>
          </div>
        )}
      </div>

      {/* Risk Distribution Card */}
      <div className="bg-surface rounded-xl border border-border p-6 flex flex-col justify-between space-y-6">
        <div className="space-y-1">
          <div className="flex items-center space-x-2">
            <PieChart className="w-5 h-5 text-accent" />
            <h2 className="text-base font-bold text-white">Risk Distribution</h2>
          </div>
          <p className="text-xs text-slate-400">
            Breakdown of historical PR runs by risk severity tier.
          </p>
        </div>

        {/* Stacked Progress Bar */}
        <div className="space-y-3">
          <div className="w-full h-4 rounded-full bg-background border border-border overflow-hidden flex">
            {totalRuns > 0 && (
              <>
                <div
                  style={{ width: `${(distribution.High / totalRuns) * 100}%` }}
                  className="bg-red-500 h-full transition-all"
                  title={`High Risk: ${distribution.High}`}
                />
                <div
                  style={{ width: `${(distribution.Medium / totalRuns) * 100}%` }}
                  className="bg-amber-400 h-full transition-all"
                  title={`Medium Risk: ${distribution.Medium}`}
                />
                <div
                  style={{ width: `${(distribution.Low / totalRuns) * 100}%` }}
                  className="bg-emerald-400 h-full transition-all"
                  title={`Low Risk: ${distribution.Low}`}
                />
              </>
            )}
          </div>

          <div className="space-y-2.5 pt-2 text-xs font-mono">
            <div className="flex items-center justify-between p-2.5 rounded-lg bg-background border border-border">
              <span className="flex items-center space-x-2 text-red-400">
                <span className="w-2.5 h-2.5 rounded-full bg-red-500" />
                <span>High Risk (&ge; 70)</span>
              </span>
              <span className="font-bold text-white">
                {distribution.High} ({totalRuns ? Math.round((distribution.High / totalRuns) * 100) : 0}%)
              </span>
            </div>

            <div className="flex items-center justify-between p-2.5 rounded-lg bg-background border border-border">
              <span className="flex items-center space-x-2 text-amber-400">
                <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
                <span>Medium Risk (40–69)</span>
              </span>
              <span className="font-bold text-white">
                {distribution.Medium} ({totalRuns ? Math.round((distribution.Medium / totalRuns) * 100) : 0}%)
              </span>
            </div>

            <div className="flex items-center justify-between p-2.5 rounded-lg bg-background border border-border">
              <span className="flex items-center space-x-2 text-emerald-400">
                <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
                <span>Low Risk (&lt; 40)</span>
              </span>
              <span className="font-bold text-white">
                {distribution.Low} ({totalRuns ? Math.round((distribution.Low / totalRuns) * 100) : 0}%)
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
