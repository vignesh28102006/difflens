"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { GitPullRequest, Sliders, Shield, Activity } from "lucide-react";

export default function Navbar() {
  const pathname = usePathname();

  const links = [
    { href: "/", label: "PR Runs", icon: GitPullRequest },
    { href: "/risk-debt", label: "Risk Debt Ledger", icon: Activity },
    { href: "/settings", label: "Settings & BYOK", icon: Sliders },
  ];

  return (
    <header className="sticky top-0 z-50 border-b border-border bg-[#05070A]/90 backdrop-blur-md">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 h-16 flex items-center justify-between">
        <div className="flex items-center space-x-8">
          <Link href="/" className="flex items-center space-x-3 group">
            <div className="w-9 h-9 rounded-lg bg-surface border border-accent/40 flex items-center justify-center group-hover:border-accent transition-colors">
              <Shield className="w-5 h-5 text-accent" />
            </div>
            <div>
              <span className="font-bold text-lg tracking-tight text-white group-hover:text-accent transition-colors">
                Diff<span className="text-accent">Lens</span>
              </span>
              <span className="ml-2 text-[10px] uppercase font-mono px-1.5 py-0.5 rounded bg-surface border border-border text-slate-400">
                v0.2
              </span>
            </div>
          </Link>

          <nav className="hidden md:flex space-x-1">
            {links.map((link) => {
              const Icon = link.icon;
              const isActive = pathname === link.href;
              return (
                <Link
                  key={link.href}
                  href={link.href}
                  className={`flex items-center space-x-2 px-3 py-1.5 rounded-md text-sm font-medium transition-colors ${
                    isActive
                      ? "bg-surface text-accent border border-border"
                      : "text-slate-400 hover:text-white hover:bg-surface/50"
                  }`}
                >
                  <Icon className="w-4 h-4" />
                  <span>{link.label}</span>
                </Link>
              );
            })}
          </nav>
        </div>

        <div className="flex items-center space-x-4">
          <div className="flex items-center space-x-2 text-xs font-mono px-2.5 py-1 rounded-full bg-surface border border-border text-emerald-400">
            <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
            <span>Deterministic Engine Active</span>
          </div>
        </div>
      </div>
    </header>
  );
}