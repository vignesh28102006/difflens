"use client";

import { useEffect, useState } from "react";
import { Sliders, Shield, Key, Save, CheckCircle, AlertCircle, RefreshCw } from "lucide-react";

interface Repository {
  id: number;
  github_repo_id: number;
  full_name: string;
  risk_threshold_medium: number;
  risk_threshold_high: number;
  llm_provider: string;
  llm_model: string | null;
  has_api_key: boolean;
  masked_api_key: string | null;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export default function SettingsPage() {
  const [repos, setRepos] = useState<Repository[]>([]);
  const [loading, setLoading] = useState(true);
  const [saveStatus, setSaveStatus] = useState<Record<number, string>>({});
  const [formState, setFormState] = useState<
    Record<
      number,
      {
        medium: number;
        high: number;
        provider: string;
        model: string;
        apiKey: string;
        clearKey: boolean;
      }
    >
  >({});

  const fetchRepos = async () => {
    try {
      setLoading(true);
      const res = await fetch(`${API_BASE}/api/repos`);
      if (res.ok) {
        const data: Repository[] = await res.json();
        setRepos(data);

        // Initialize form state
        const initialForm: typeof formState = {};
        data.forEach((r) => {
          initialForm[r.id] = {
            medium: r.risk_threshold_medium,
            high: r.risk_threshold_high,
            provider: r.llm_provider || "gemini",
            model: r.llm_model || "",
            apiKey: "",
            clearKey: false,
          };
        });
        setFormState(initialForm);
      }
    } catch (err) {
      console.error("Failed to load repos:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchRepos();
  }, []);

  const handleSave = async (repoId: number) => {
    const state = formState[repoId];
    if (!state) return;

    try {
      setSaveStatus((prev) => ({ ...prev, [repoId]: "saving" }));

      const body: any = {
        risk_threshold_medium: Number(state.medium),
        risk_threshold_high: Number(state.high),
        llm_provider: state.provider,
        llm_model: state.model.trim() || null,
        clear_api_key: state.clearKey,
      };

      if (state.apiKey.trim()) {
        body.api_key = state.apiKey.trim();
      }

      const res = await fetch(`${API_BASE}/api/repos/${repoId}/settings`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });

      if (res.ok) {
        const updated = await res.json();
        setRepos((prev) => prev.map((r) => (r.id === repoId ? updated : r)));
        setFormState((prev) => ({
          ...prev,
          [repoId]: {
            ...prev[repoId],
            apiKey: "",
            clearKey: false,
          },
        }));
        setSaveStatus((prev) => ({ ...prev, [repoId]: "success" }));
        setTimeout(() => setSaveStatus((prev) => ({ ...prev, [repoId]: "" })), 3000);
      } else {
        setSaveStatus((prev) => ({ ...prev, [repoId]: "error" }));
      }
    } catch (e) {
      setSaveStatus((prev) => ({ ...prev, [repoId]: "error" }));
    }
  };

  return (
    <div className="space-y-8 max-w-4xl mx-auto pb-12">
      <div className="border-b border-border pb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white flex items-center space-x-2.5">
            <Sliders className="w-6 h-6 text-accent" />
            <span>Repository & BYOK Settings</span>
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Configure risk thresholds, LLM providers (Gemini, local Ollama), and encrypted API keys.
          </p>
        </div>
        <button
          onClick={fetchRepos}
          className="p-2 rounded-lg bg-surface border border-border text-slate-300 hover:text-white hover:border-slate-600 transition-colors"
          title="Reload"
        >
          <RefreshCw className={`w-4 h-4 ${loading ? "animate-spin" : ""}`} />
        </button>
      </div>

      {loading && repos.length === 0 ? (
        <div className="text-center py-20 text-slate-500 font-mono text-sm">Loading repository settings...</div>
      ) : repos.length === 0 ? (
        <div className="text-center py-20 bg-surface rounded-xl border border-border">
          <Shield className="w-10 h-10 text-slate-600 mx-auto mb-3" />
          <h3 className="text-base font-semibold text-slate-200">No Repositories Connected</h3>
          <p className="text-sm text-slate-400 max-w-sm mx-auto mt-1">
            Repositories are automatically discovered when GitHub webhook deliveries are received.
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {repos.map((repo) => {
            const form = formState[repo.id] || {
              medium: repo.risk_threshold_medium,
              high: repo.risk_threshold_high,
              provider: repo.llm_provider || "gemini",
              model: repo.llm_model || "",
              apiKey: "",
              clearKey: false,
            };
            const status = saveStatus[repo.id];

            return (
              <div key={repo.id} className="bg-surface rounded-xl border border-border overflow-hidden">
                <div className="p-5 border-b border-border bg-[#0B1020] flex items-center justify-between">
                  <div className="flex items-center space-x-3">
                    <span className="font-mono text-base font-bold text-white">{repo.full_name}</span>
                    <span className="text-xs font-mono text-slate-400">ID: {repo.github_repo_id || repo.id}</span>
                  </div>

                  <div className="flex items-center space-x-2 text-xs font-mono">
                    <span className="text-slate-400">API Key:</span>
                    {repo.has_api_key ? (
                      <span className="text-emerald-400 bg-emerald-500/10 px-2 py-0.5 rounded border border-emerald-500/30">
                        {repo.masked_api_key}
                      </span>
                    ) : (
                      <span className="text-slate-500 bg-slate-500/10 px-2 py-0.5 rounded border border-slate-500/30">
                        Unset (Fallback Active)
                      </span>
                    )}
                  </div>
                </div>

                <div className="p-6 space-y-6">
                  {/* Thresholds */}
                  <div className="grid sm:grid-cols-2 gap-6">
                    <div className="space-y-2">
                      <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                        Medium Risk Threshold (score ≥ X)
                      </label>
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="1"
                        value={form.medium}
                        onChange={(e) =>
                          setFormState((prev) => ({
                            ...prev,
                            [repo.id]: { ...form, medium: parseFloat(e.target.value) || 0 },
                          }))
                        }
                        className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-accent"
                      />
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                        High Risk Threshold (score ≥ X)
                      </label>
                      <input
                        type="number"
                        min="0"
                        max="100"
                        step="1"
                        value={form.high}
                        onChange={(e) =>
                          setFormState((prev) => ({
                            ...prev,
                            [repo.id]: { ...form, high: parseFloat(e.target.value) || 0 },
                          }))
                        }
                        className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-accent"
                      />
                    </div>
                  </div>

                  {/* LLM Provider Selection */}
                  <div className="grid sm:grid-cols-2 gap-6">
                    <div className="space-y-2">
                      <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                        LLM Explanation Provider
                      </label>
                      <select
                        value={form.provider}
                        onChange={(e) =>
                          setFormState((prev) => ({
                            ...prev,
                            [repo.id]: { ...form, provider: e.target.value },
                          }))
                        }
                        className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-accent"
                      >
                        <option value="gemini">Google Gemini (google-genai BYOK)</option>
                        <option value="ollama">Local Ollama (Local LLM)</option>
                        <option value="none">Deterministic Only (Zero LLM)</option>
                      </select>
                    </div>

                    <div className="space-y-2">
                      <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
                        Model Name Override (Optional)
                      </label>
                      <input
                        type="text"
                        placeholder="e.g. gemini-2.5-flash or qwen2.5-coder"
                        value={form.model}
                        onChange={(e) =>
                          setFormState((prev) => ({
                            ...prev,
                            [repo.id]: { ...form, model: e.target.value },
                          }))
                        }
                        className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-accent placeholder:text-slate-600"
                      />
                    </div>
                  </div>

                  {/* BYOK API Key */}
                  <div className="space-y-2">
                    <label className="text-xs font-semibold text-slate-300 uppercase tracking-wider flex items-center justify-between">
                      <span className="flex items-center space-x-1.5">
                        <Key className="w-3.5 h-3.5 text-accent" />
                        <span>Bring-Your-Own-Key (BYOK) Secret</span>
                      </span>
                      {repo.has_api_key && (
                        <label className="flex items-center space-x-1.5 cursor-pointer text-xs text-red-400 font-normal">
                          <input
                            type="checkbox"
                            checked={form.clearKey}
                            onChange={(e) =>
                              setFormState((prev) => ({
                                ...prev,
                                [repo.id]: { ...form, clearKey: e.target.checked },
                              }))
                            }
                            className="rounded bg-background border-border text-red-500 focus:ring-0"
                          />
                          <span>Clear saved key</span>
                        </label>
                      )}
                    </label>

                    <input
                      type="password"
                      placeholder={repo.has_api_key ? "Leave blank to keep existing key" : "Paste your Gemini API key"}
                      disabled={form.clearKey}
                      value={form.apiKey}
                      onChange={(e) =>
                        setFormState((prev) => ({
                          ...prev,
                          [repo.id]: { ...form, apiKey: e.target.value },
                        }))
                      }
                      className="w-full bg-background border border-border rounded-lg px-3 py-2 text-sm text-white font-mono focus:outline-none focus:border-accent placeholder:text-slate-600 disabled:opacity-50"
                    />
                    <p className="text-[11px] text-slate-500">
                      Encrypted at rest using Fernet with DIFFLENS_ENCRYPTION_KEY. Never exposed in API responses or logs.
                    </p>
                  </div>

                  {/* Save Action */}
                  <div className="flex items-center justify-end space-x-4 pt-4 border-t border-border">
                    {status === "success" && (
                      <span className="inline-flex items-center space-x-1.5 text-xs text-emerald-400 font-mono">
                        <CheckCircle className="w-4 h-4" />
                        <span>Settings saved!</span>
                      </span>
                    )}
                    {status === "error" && (
                      <span className="inline-flex items-center space-x-1.5 text-xs text-red-400 font-mono">
                        <AlertCircle className="w-4 h-4" />
                        <span>Save failed</span>
                      </span>
                    )}

                    <button
                      onClick={() => handleSave(repo.id)}
                      disabled={status === "saving"}
                      className="inline-flex items-center space-x-2 px-4 py-2 rounded-lg bg-accent text-background font-semibold text-xs tracking-wide hover:bg-accent-hover transition-colors disabled:opacity-50"
                    >
                      <Save className="w-4 h-4" />
                      <span>{status === "saving" ? "Saving..." : "Save Settings"}</span>
                    </button>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
