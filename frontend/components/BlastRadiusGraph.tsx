"use client";

import React, { useState } from "react";
import { Network, Globe, AlertTriangle, Radio, Info } from "lucide-react";

interface CallerMap {
  [caller: string]: string;
}

interface HunkFinding {
  id: number;
  file_path: string;
  symbol_name: string;
  risk_score: number;
  fan_in: number;
  callers: string[];
  caller_verifications?: CallerMap;
}

interface CoChangeWarning {
  changed_file: string;
  missing_partner: string;
  coupling_ratio: number;
}

interface InteractionFinding {
  source_file: string;
  source_symbol: string;
  target_file: string;
  target_symbol: string;
  interaction_type: string;
  severity: string;
  evidence: string;
}

interface DeploymentImpact {
  affected_services: string[];
  affected_endpoints: string[];
  affected_workflows: string[];
  impact_tier: string;
}

interface BlastRadiusGraphProps {
  findings: HunkFinding[];
  coChangeWarnings?: CoChangeWarning[];
  interactionFindings?: InteractionFinding[];
  deploymentImpact?: DeploymentImpact;
}

interface GraphNode {
  id: string;
  label: string;
  sublabel: string;
  x: number;
  y: number;
  type: "caller" | "symbol" | "cochange" | "service" | "endpoint";
  status?: string;
  risk?: number;
  detail: string;
}

interface GraphEdge {
  from: string;
  to: string;
  status: "runtime_verified" | "static_only" | "dynamic_only" | "drift" | "cochange" | "deploy";
}

export default function BlastRadiusGraph({
  findings,
  coChangeWarnings = [],
  interactionFindings = [],
  deploymentImpact,
}: BlastRadiusGraphProps) {
  const [viewMode, setViewMode] = useState<"callgraph" | "servicemap">("callgraph");
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  if (!findings || findings.length === 0) return null;

  // Build Call Graph DAG nodes & edges
  const buildCallGraph = () => {
    const nodes: GraphNode[] = [];
    const edges: GraphEdge[] = [];

    const primaryFindings = findings.slice(0, 3);
    const centerX = 430;
    const leftX = 120;
    const rightX = 740;

    // 1. Center Symbol Nodes
    primaryFindings.forEach((f, idx) => {
      const y = primaryFindings.length === 1 ? 180 : 100 + idx * 120;
      const symId = `sym-${f.id}`;
      nodes.push({
        id: symId,
        label: f.symbol_name,
        sublabel: f.file_path,
        x: centerX,
        y,
        type: "symbol",
        risk: f.risk_score,
        detail: `Modified symbol in ${f.file_path} • Risk Score: ${f.risk_score.toFixed(1)} • Fan-in: ${f.fan_in}`,
      });

      // 2. Left Upstream Callers
      const verifs = f.caller_verifications || {};
      const callers = (f.callers || []).slice(0, 6);
      callers.forEach((c, cIdx) => {
        const callerId = `caller-${f.id}-${cIdx}`;
        const status = verifs[c] || "static_only";
        const hasDrift = interactionFindings.some(
          (inter) =>
            inter.target_file &&
            c.includes(inter.target_file.split("/").pop() || "") &&
            inter.interaction_type === "signature_drift"
        );
        const cy = 55 + cIdx * 54;
        const shortName = c.includes(":") ? c.split(":")[1] : c;
        const filePart = c.includes(":") ? c.split(":")[0] : "";

        nodes.push({
          id: callerId,
          label: shortName,
          sublabel: filePart,
          x: leftX,
          y: cy,
          type: "caller",
          status: hasDrift ? "drift" : status,
          detail: hasDrift
            ? `SIGNATURE DRIFT ALERT: ${c} calls ${f.symbol_name} with mismatched arguments`
            : `Upstream caller ${c} (${status.replace("_", " ")})`,
        });

        edges.push({
          from: callerId,
          to: symId,
          status: hasDrift ? "drift" : (status as any),
        });
      });
    });

    // 3. Right Side: Co-Change Warnings & Affected Endpoints
    let rightIdx = 0;
    coChangeWarnings.slice(0, 3).forEach((w, idx) => {
      const ccId = `cc-${idx}`;
      nodes.push({
        id: ccId,
        label: w.missing_partner.split("/").pop() || w.missing_partner,
        sublabel: `Omitted (${Math.round(w.coupling_ratio * 100)}% coupled)`,
        x: rightX,
        y: 80 + rightIdx * 75,
        type: "cochange",
        detail: `Broken co-change: ${w.changed_file} is historically coupled with ${w.missing_partner} (${Math.round(w.coupling_ratio * 100)}%)`,
      });
      if (nodes.find((n) => n.type === "symbol")) {
        edges.push({
          from: nodes.find((n) => n.type === "symbol")!.id,
          to: ccId,
          status: "cochange",
        });
      }
      rightIdx++;
    });

    if (deploymentImpact) {
      deploymentImpact.affected_endpoints.slice(0, 3).forEach((ep, idx) => {
        const epId = `ep-${idx}`;
        nodes.push({
          id: epId,
          label: ep,
          sublabel: "Exposed Public Endpoint",
          x: rightX,
          y: 80 + rightIdx * 75,
          type: "endpoint",
          detail: `Public API endpoint reachable from modified code: ${ep}`,
        });
        if (nodes.find((n) => n.type === "symbol")) {
          edges.push({
            from: nodes.find((n) => n.type === "symbol")!.id,
            to: epId,
            status: "deploy",
          });
        }
        rightIdx++;
      });
    }

    return { nodes, edges };
  };

  // Build Service & Deployment Map
  const buildServiceMap = () => {
    const nodes: GraphNode[] = [];
    const edges: GraphEdge[] = [];

    const mainFile = findings[0];
    const centerId = "pr-change";
    nodes.push({
      id: centerId,
      label: mainFile.symbol_name,
      sublabel: mainFile.file_path,
      x: 160,
      y: 180,
      type: "symbol",
      risk: mainFile.risk_score,
      detail: `Root PR Change in ${mainFile.file_path} (Score: ${mainFile.risk_score.toFixed(1)})`,
    });

    const services = deploymentImpact?.affected_services || ["core-app"];
    services.forEach((svc, idx) => {
      const sId = `svc-${idx}`;
      nodes.push({
        id: sId,
        label: svc,
        sublabel: "Container / Service",
        x: 440,
        y: services.length === 1 ? 180 : 100 + idx * 120,
        type: "service",
        detail: `Impacted deployment service: ${svc} (${deploymentImpact?.impact_tier || "Service-Local"})`,
      });
      edges.push({ from: centerId, to: sId, status: "deploy" });

      const endpoints = deploymentImpact?.affected_endpoints || [];
      endpoints.slice(0, 3).forEach((ep, eIdx) => {
        if (idx === 0) {
          const epId = `ep-map-${eIdx}`;
          nodes.push({
            id: epId,
            label: ep,
            sublabel: "Public Route",
            x: 720,
            y: 80 + eIdx * 90,
            type: "endpoint",
            detail: `Public HTTP route exposed by ${svc}: ${ep}`,
          });
          edges.push({ from: sId, to: epId, status: "runtime_verified" });
        }
      });
    });

    return { nodes, edges };
  };

  const { nodes, edges } = viewMode === "callgraph" ? buildCallGraph() : buildServiceMap();

  const getEdgeColor = (status: GraphEdge["status"]) => {
    switch (status) {
      case "drift":
        return "#F87171"; // red-400
      case "runtime_verified":
        return "#34D399"; // emerald-400
      case "dynamic_only":
        return "#60A5FA"; // blue-400
      case "cochange":
        return "#FBBF24"; // amber-400
      case "deploy":
        return "#38BDF8"; // cyan-400
      default:
        return "#475569"; // slate-600
    }
  };

  const getNodeStyle = (n: GraphNode) => {
    if (n.type === "symbol") {
      const isHigh = (n.risk || 0) >= 70;
      const isMed = (n.risk || 0) >= 40;
      return {
        bg: isHigh ? "#450A0A" : isMed ? "#451A03" : "#064E3B",
        border: isHigh ? "#EF4444" : isMed ? "#F59E0B" : "#10B981",
        text: "#FFFFFF",
      };
    }
    if (n.type === "caller") {
      if (n.status === "drift") return { bg: "#450A0A", border: "#EF4444", text: "#FCA5A5" };
      if (n.status === "runtime_verified") return { bg: "#064E3B", border: "#10B981", text: "#6EE7B7" };
      if (n.status === "dynamic_only") return { bg: "#172554", border: "#3B82F6", text: "#93C5FD" };
      return { bg: "#0F172A", border: "#475569", text: "#94A3B8" };
    }
    if (n.type === "cochange") {
      return { bg: "#451A03", border: "#F59E0B", text: "#FDE68A" };
    }
    if (n.type === "service") {
      return { bg: "#083344", border: "#06B6D4", text: "#A5F3FC" };
    }
    return { bg: "#064E3B", border: "#34D399", text: "#A7F3D0" };
  };

  return (
    <div className="bg-surface rounded-xl border border-border overflow-hidden shadow-sm">
      {/* Graph Header & Mode Switch */}
      <div className="p-4 border-b border-border bg-[#0B1020] flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="flex items-center space-x-2.5">
          <Network className="w-5 h-5 text-accent" />
          <div>
            <h3 className="text-sm font-bold text-white">Interactive Blast-Radius & Dependency Graph</h3>
            <p className="text-[11px] text-slate-400">
              Click any node to inspect Tree-sitter AST call edges, runtime verification, and deployment impact
            </p>
          </div>
        </div>

        <div className="flex items-center space-x-1 bg-background p-1 rounded-lg border border-border text-xs font-mono">
          <button
            onClick={() => {
              setViewMode("callgraph");
              setSelectedNode(null);
            }}
            className={`px-3 py-1 rounded-md transition-colors ${
              viewMode === "callgraph"
                ? "bg-surface text-accent border border-border font-semibold"
                : "text-slate-400 hover:text-white"
            }`}
          >
            Call Graph DAG
          </button>
          <button
            onClick={() => {
              setViewMode("servicemap");
              setSelectedNode(null);
            }}
            className={`px-3 py-1 rounded-md transition-colors ${
              viewMode === "servicemap"
                ? "bg-surface text-cyan-400 border border-border font-semibold"
                : "text-slate-400 hover:text-white"
            }`}
          >
            Service Impact Map
          </button>
        </div>
      </div>

      {/* Interactive SVG Canvas */}
      <div className="relative bg-[#05070A] p-4 overflow-x-auto">
        <svg viewBox="0 0 880 380" className="w-full min-w-[700px] h-[340px] select-none">
          <defs>
            <marker id="arrow-verified" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1 L 10 5 L 0 9 z" fill="#34D399" />
            </marker>
            <marker id="arrow-drift" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1 L 10 5 L 0 9 z" fill="#F87171" />
            </marker>
            <marker id="arrow-static" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1 L 10 5 L 0 9 z" fill="#475569" />
            </marker>
            <marker id="arrow-cochange" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1 L 10 5 L 0 9 z" fill="#FBBF24" />
            </marker>
            <marker id="arrow-deploy" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
              <path d="M 0 1 L 10 5 L 0 9 z" fill="#38BDF8" />
            </marker>
          </defs>

          {/* Subtle Grid Background */}
          <pattern id="grid" width="32" height="32" patternUnits="userSpaceOnUse">
            <path d="M 32 0 L 0 0 0 32" fill="none" stroke="#1E293B" strokeWidth="0.5" opacity="0.4" />
          </pattern>
          <rect width="100%" height="100%" fill="url(#grid)" />

          {/* Column Labels */}
          <text x="120" y="24" textAnchor="middle" fill="#64748B" fontSize="11" fontFamily="monospace" fontWeight="bold">
            {viewMode === "callgraph" ? "UPSTREAM CALLERS" : "MODIFIED CODE"}
          </text>
          <text x="430" y="24" textAnchor="middle" fill="#64748B" fontSize="11" fontFamily="monospace" fontWeight="bold">
            {viewMode === "callgraph" ? "TOUCHED SYMBOLS" : "AFFECTED SERVICES"}
          </text>
          <text x="740" y="24" textAnchor="middle" fill="#64748B" fontSize="11" fontFamily="monospace" fontWeight="bold">
            {viewMode === "callgraph" ? "BLAST & CO-CHANGE" : "PUBLIC ENDPOINTS"}
          </text>

          {/* Edges */}
          {edges.map((e, idx) => {
            const fromNode = nodes.find((n) => n.id === e.from);
            const toNode = nodes.find((n) => n.id === e.to);
            if (!fromNode || !toNode) return null;

            const x1 = fromNode.x + 85;
            const y1 = fromNode.y;
            const x2 = toNode.x - 85;
            const y2 = toNode.y;
            const midX = (x1 + x2) / 2;
            const pathD = `M ${x1} ${y1} C ${midX} ${y1}, ${midX} ${y2}, ${x2} ${y2}`;

            const color = getEdgeColor(e.status);
            const isDashed = e.status === "static_only" || e.status === "cochange";
            const marker =
              e.status === "drift"
                ? "url(#arrow-drift)"
                : e.status === "runtime_verified"
                ? "url(#arrow-verified)"
                : e.status === "cochange"
                ? "url(#arrow-cochange)"
                : e.status === "deploy"
                ? "url(#arrow-deploy)"
                : "url(#arrow-static)";

            return (
              <path
                key={idx}
                d={pathD}
                fill="none"
                stroke={color}
                strokeWidth={e.status === "drift" || e.status === "runtime_verified" ? 2.2 : 1.5}
                strokeDasharray={isDashed ? "5,5" : undefined}
                markerEnd={marker}
                opacity={0.85}
              />
            );
          })}

          {/* Nodes */}
          {nodes.map((n) => {
            const style = getNodeStyle(n);
            const isSelected = selectedNode?.id === n.id;

            return (
              <g
                key={n.id}
                transform={`translate(${n.x - 85}, ${n.y - 22})`}
                onClick={() => setSelectedNode(n)}
                className="cursor-pointer"
              >
                <rect
                  width="170"
                  height="44"
                  rx="8"
                  fill={style.bg}
                  stroke={isSelected ? "#FFFFFF" : style.border}
                  strokeWidth={isSelected ? 2.5 : 1.5}
                />
                <text
                  x="85"
                  y="19"
                  textAnchor="middle"
                  fill={style.text}
                  fontSize="11.5"
                  fontFamily="monospace"
                  fontWeight="bold"
                >
                  {n.label.length > 20 ? n.label.slice(0, 18) + "…" : n.label}
                </text>
                <text
                  x="85"
                  y="34"
                  textAnchor="middle"
                  fill="#94A3B8"
                  fontSize="9.5"
                  fontFamily="monospace"
                >
                  {n.sublabel.length > 24 ? n.sublabel.slice(0, 22) + "…" : n.sublabel}
                </text>
              </g>
            );
          })}
        </svg>
      </div>

      {/* Legend & Inspector Footer */}
      <div className="p-3.5 border-t border-border bg-[#0B1020] flex flex-col md:flex-row md:items-center justify-between gap-3 text-[11px] font-mono">
        <div className="flex flex-wrap items-center gap-4 text-slate-400">
          <span className="flex items-center space-x-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-emerald-400" />
            <span>Runtime Verified</span>
          </span>
          <span className="flex items-center space-x-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-red-400" />
            <span>Signature Drift / High Risk</span>
          </span>
          <span className="flex items-center space-x-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-amber-400" />
            <span>Omitted Co-Change</span>
          </span>
          <span className="flex items-center space-x-1.5">
            <span className="w-2.5 h-2.5 rounded-full bg-slate-500" />
            <span>Static Only</span>
          </span>
        </div>

        {selectedNode ? (
          <div className="flex items-center space-x-2 text-accent bg-background px-3 py-1 rounded border border-accent/30">
            <Info className="w-3.5 h-3.5 shrink-0" />
            <span className="truncate max-w-md">{selectedNode.detail}</span>
          </div>
        ) : (
          <div className="text-slate-500 italic">Click any graph node to inspect details</div>
        )}
      </div>
    </div>
  );
}
