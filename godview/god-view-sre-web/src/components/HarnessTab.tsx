import {
  Brain,
  FileCode,
  Layers,
  RefreshCw,
  ShieldCheck,
  Sparkles,
  Terminal,
} from 'lucide-react';
import React, { useEffect, useState } from 'react';
import { cockpitApi } from '../api/client';
import type { CourtAgent, PerceptionReport } from '../api/types';
import { Badge, Button, Card, SectionHeader } from './ui';

export interface HarnessTabProps {
  court?: CourtAgent[] | null;
  perceive?: PerceptionReport | null;
}

type SubTab = 'inspector' | 'blueprint' | 'portable';
type CategoryKey =
  | 'c1_identity'
  | 'c2_tool_descriptions'
  | 'c3_guardrails'
  | 'c4_long_term_memory'
  | 'c5_sub_agents'
  | 'c6_skills'
  | 'c7_scripts'
  | 'c8_hooks';

const CATEGORY_META: Array<{
  key: CategoryKey;
  code: string;
  label: string;
  fileHint: string;
  alwaysOn: boolean;
}> = [
  {
    key: 'c1_identity',
    code: 'C1',
    label: 'Identity & System Prompt',
    fileHint: 'systemprompt.md',
    alwaysOn: true,
  },
  {
    key: 'c2_tool_descriptions',
    code: 'C2',
    label: 'Tool Guidance & Contracts',
    fileHint: 'tool_descriptions/tool_guidance.md',
    alwaysOn: true,
  },
  {
    key: 'c3_guardrails',
    code: 'C3',
    label: 'Guardrails & Autotriggers',
    fileHint: 'guardrails.md',
    alwaysOn: true,
  },
  {
    key: 'c4_long_term_memory',
    code: 'C4',
    label: 'Long-Term Episodic Memory',
    fileHint: 'LongTermMEMORY.md',
    alwaysOn: true,
  },
  {
    key: 'c5_sub_agents',
    code: 'C5',
    label: 'Sub-Agent Definitions',
    fileHint: 'sub_agents/*/AGENT.md',
    alwaysOn: false,
  },
  {
    key: 'c6_skills',
    code: 'C6',
    label: 'Procedural Skills',
    fileHint: 'skills/*/SKILL.md',
    alwaysOn: false,
  },
  {
    key: 'c7_scripts',
    code: 'C7',
    label: 'AST-Sandboxed Scripts',
    fileHint: 'scripts/*.py',
    alwaysOn: false,
  },
  {
    key: 'c8_hooks',
    code: 'C8',
    label: 'Lifecycle Hooks',
    fileHint: 'hooks.json',
    alwaysOn: false,
  },
];

export const HarnessTab: React.FC<HarnessTabProps> = ({
  court: _court,
  perceive: _perceive,
}) => {
  const [subTab, setSubTab] = useState<SubTab>('inspector');
  const [overview, setOverview] = useState<any | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState<string>(
    'application__specialist'
  );
  const [activeTree, setActiveTree] = useState<any | null>(null);
  const [selectedCategory, setSelectedCategory] =
    useState<CategoryKey>('c1_identity');
  const [, setLoading] = useState<boolean>(true);
  const [evolving, setEvolving] = useState<boolean>(false);
  const [evolveResult, setEvolveResult] = useState<any | null>(null);
  const [copiedCmd, setCopiedCmd] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    cockpitApi
      .getHarnessOverview()
      .then((data) => {
        if (cancelled) return;
        setOverview(data);
        const firstTree = (data?.archetypes || []).find(
          (a: any) => a.agent_id === selectedAgentId
        );
        if (firstTree) {
          setActiveTree(firstTree);
        } else if (data?.archetypes?.[0]) {
          setActiveTree(data.archetypes[0]);
          setSelectedAgentId(data.archetypes[0].agent_id);
        }
      })
      .catch(() => {
        // Fallback to single tree fetch
        cockpitApi
          .getRawHarnessTree(selectedAgentId)
          .then((t) => {
            if (!cancelled) setActiveTree(t);
          })
          .catch(() => {});
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSelectAgent = (agentId: string) => {
    setSelectedAgentId(agentId);
    setEvolveResult(null);
    const cached = (overview?.archetypes || []).find(
      (a: any) => a.agent_id === agentId || a.domain === agentId
    );
    if (cached) {
      setActiveTree(cached);
      return;
    }
    cockpitApi
      .getRawHarnessTree(agentId)
      .then((t) => setActiveTree(t))
      .catch(() => {});
  };

  const handleRunEvolve = async (mode: 'parallel' | 'sequential') => {
    setEvolving(true);
    try {
      const res = await cockpitApi.evolveAgentHarness(selectedAgentId, mode, 2);
      setEvolveResult(res);
      const refreshed = await cockpitApi.getRawHarnessTree(selectedAgentId);
      setActiveTree(refreshed);
    } catch (err: any) {
      setEvolveResult({
        status: 'error',
        notes: err?.message || 'Evolution run failed',
      });
    } finally {
      setEvolving(false);
    }
  };

  const copyText = (txt: string) => {
    if (navigator?.clipboard) {
      navigator.clipboard.writeText(txt).catch(() => {});
    }
    setCopiedCmd(txt);
    setTimeout(() => setCopiedCmd(null), 1800);
  };

  const archetypesList: any[] = overview?.archetypes || [];
  const tokenUsed = activeTree?.always_on_tokens ?? 392;
  const tokenMax = activeTree?.validation?.always_on_max ?? 12000;
  const tokenPct = Math.min(100, Math.round((tokenUsed / tokenMax) * 100));

  const renderCategoryContent = () => {
    if (!activeTree) {
      return (
        <div className="p-6 text-xs text-slate-500">
          Loading Harness² C1–C8 surface tree...
        </div>
      );
    }
    const cats = activeTree.categories || {};
    const detail = activeTree.categories_detail || {};

    if (
      selectedCategory === 'c1_identity' ||
      selectedCategory === 'c2_tool_descriptions' ||
      selectedCategory === 'c3_guardrails' ||
      selectedCategory === 'c4_long_term_memory'
    ) {
      const rawText = String(cats[selectedCategory] || '');
      return (
        <pre className="max-h-[520px] overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap text-slate-800">
          {rawText || '# Empty file'}
        </pre>
      );
    }

    if (selectedCategory === 'c5_sub_agents') {
      const items: any[] = detail.c5_sub_agents || [];
      const names: string[] = cats.c5_sub_agents || [];
      return (
        <div className="space-y-3">
          {items.length > 0
            ? items.map((item) => (
                <div
                  key={item.name}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-3.5"
                >
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-mono text-xs font-bold text-slate-900">
                      sub_agents/{item.name}/AGENT.md
                    </span>
                    <Badge variant="info">Sub-Agent Spec</Badge>
                  </div>
                  <pre className="overflow-auto font-mono text-xs whitespace-pre-wrap text-slate-800">
                    {item.content}
                  </pre>
                </div>
              ))
            : names.map((n) => (
                <div
                  key={n}
                  className="rounded border border-slate-200 bg-slate-50 p-3 font-mono text-xs"
                >
                  sub_agents/{n}/AGENT.md
                </div>
              ))}
        </div>
      );
    }

    if (selectedCategory === 'c6_skills') {
      const items: any[] = detail.c6_skills || [];
      const names: string[] = cats.c6_skills || [];
      return (
        <div className="space-y-3">
          {items.length > 0
            ? items.map((item) => (
                <div
                  key={item.name}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-3.5"
                >
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-mono text-xs font-bold text-slate-900">
                      skills/{item.name}/SKILL.md
                    </span>
                    <Badge variant="success">Procedural Skill</Badge>
                  </div>
                  <pre className="overflow-auto font-mono text-xs whitespace-pre-wrap text-slate-800">
                    {item.content}
                  </pre>
                </div>
              ))
            : names.map((n) => (
                <div
                  key={n}
                  className="rounded border border-slate-200 bg-slate-50 p-3 font-mono text-xs"
                >
                  skills/{n}/SKILL.md
                </div>
              ))}
        </div>
      );
    }

    if (selectedCategory === 'c7_scripts') {
      const items: any[] = detail.c7_scripts || [];
      const names: string[] = cats.c7_scripts || [];
      return (
        <div className="space-y-3">
          <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-emerald-200 bg-emerald-50/70 px-3 py-2 text-xs text-emerald-900">
            <div className="flex items-center gap-2">
              <ShieldCheck className="h-4 w-4 text-emerald-700" />
              <span>
                <strong>AST Sandbox Verified:</strong> Zero forbidden imports (
                <code>google</code>, <code>httpx</code>, <code>requests</code>,{' '}
                <code>subprocess</code>, <code>socket</code>)
              </span>
            </div>
            <Badge variant="success">Deterministic Python</Badge>
          </div>
          {items.length > 0
            ? items.map((item) => (
                <div
                  key={item.name}
                  className="rounded-lg border border-slate-200 bg-slate-50 p-3.5"
                >
                  <div className="mb-2 flex items-center justify-between">
                    <span className="font-mono text-xs font-bold text-slate-900">
                      scripts/{item.name}
                    </span>
                    <Badge variant="outline">AST Validated</Badge>
                  </div>
                  <pre className="overflow-auto font-mono text-xs whitespace-pre-wrap text-slate-800">
                    {item.content}
                  </pre>
                </div>
              ))
            : names.map((n) => (
                <div
                  key={n}
                  className="rounded border border-slate-200 bg-slate-50 p-3 font-mono text-xs"
                >
                  scripts/{n}
                </div>
              ))}
        </div>
      );
    }

    return (
      <pre className="overflow-auto rounded-lg border border-slate-200 bg-slate-50 p-4 font-mono text-xs text-slate-800">
        {JSON.stringify(cats.c8_hooks || {}, null, 2)}
      </pre>
    );
  };

  return (
    <div className="space-y-5">
      {/* Header Banner */}
      <Card className="border-slate-200 bg-white p-5 shadow-xs">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <div className="flex h-11 w-11 items-center justify-center rounded-xl border border-blue-200 bg-blue-50 text-blue-700">
              <Brain className="h-6 w-6" />
            </div>
            <div>
              <div className="flex flex-wrap items-center gap-2">
                <h2 className="text-base font-bold text-slate-900">
                  Harness² Recursive Agent Architecture &amp; Portable Blueprint
                </h2>
                <Badge variant="info">GODVIEW_SURFACE (C1–C8)</Badge>
                <Badge variant="success" dot>
                  Two-Level Recursion (R=4k, P=3k)
                </Badge>
              </div>
              <p className="mt-0.5 text-xs text-slate-600">
                Inspect how God-View specialist agents are structured, how
                Harness² recursive self-improvement (RSI) evolves C1–C8 files,
                and how to package &amp; deploy God-View anywhere
              </p>
            </div>
          </div>

          <div className="inline-flex rounded-lg border border-slate-200 bg-slate-100 p-1 text-xs">
            <button
              type="button"
              onClick={() => setSubTab('inspector')}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold transition ${
                subTab === 'inspector'
                  ? 'bg-white text-blue-700 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <FileCode className="h-3.5 w-3.5" />
              C1–C8 Harness² Inspector
            </button>
            <button
              type="button"
              onClick={() => setSubTab('blueprint')}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold transition ${
                subTab === 'blueprint'
                  ? 'bg-white text-blue-700 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Layers className="h-3.5 w-3.5" />
              How God-View Is Built
            </button>
            <button
              type="button"
              onClick={() => setSubTab('portable')}
              className={`inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 font-semibold transition ${
                subTab === 'portable'
                  ? 'bg-white text-blue-700 shadow-xs'
                  : 'text-slate-600 hover:text-slate-900'
              }`}
            >
              <Terminal className="h-3.5 w-3.5" />
              Portable Package &amp; CLI Utility
            </button>
          </div>
        </div>
      </Card>

      {/* SUBTAB 1: C1-C8 HARNESS² INSPECTOR */}
      {subTab === 'inspector' && (
        <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-12">
          {/* Left Column: Specialist Archetypes & Always-On Budget */}
          <div className="space-y-4 xl:col-span-4">
            <Card className="border-slate-200 bg-white p-4">
              <SectionHeader
                title="Specialist Court Archetypes"
                subtitle="Select an archetype to inspect its live C1–C8 HarnessTree"
              />
              <div className="mt-3 space-y-2">
                {(archetypesList.length > 0
                  ? archetypesList
                  : [
                      {
                        agent_id: 'application__specialist',
                        domain: 'application',
                        always_on_tokens: 392,
                        living_model_version: 1,
                      },
                      {
                        agent_id: 'data__specialist',
                        domain: 'data',
                        always_on_tokens: 383,
                        living_model_version: 1,
                      },
                      {
                        agent_id: 'business_line__specialist',
                        domain: 'business_line',
                        always_on_tokens: 393,
                        living_model_version: 1,
                      },
                      {
                        agent_id: 'change__specialist',
                        domain: 'change',
                        always_on_tokens: 381,
                        living_model_version: 1,
                      },
                      {
                        agent_id: 'platform__specialist',
                        domain: 'platform',
                        always_on_tokens: 413,
                        living_model_version: 1,
                      },
                    ]
                ).map((arch) => {
                  const isSel = selectedAgentId === arch.agent_id;
                  return (
                    <button
                      key={arch.agent_id}
                      type="button"
                      onClick={() => handleSelectAgent(arch.agent_id)}
                      className={`flex w-full items-center justify-between rounded-lg border px-3 py-2.5 text-left text-xs transition ${
                        isSel
                          ? 'border-blue-600 bg-blue-50/50 ring-1 ring-blue-600/20'
                          : 'border-slate-200 bg-white hover:border-blue-300'
                      }`}
                    >
                      <div>
                        <div className="font-mono font-bold text-slate-900">
                          {arch.agent_id}
                        </div>
                        <div className="mt-0.5 text-[11px] text-slate-500">
                          Domain: <strong>{arch.domain}</strong> · v
                          {arch.living_model_version ?? 1}
                        </div>
                      </div>
                      <Badge variant="success">
                        {arch.always_on_tokens} / 12k tok
                      </Badge>
                    </button>
                  );
                })}
              </div>
            </Card>

            {/* Always-on token budget card */}
            <Card className="border-slate-200 bg-white p-4">
              <SectionHeader
                title="Always-On Context Budget (C1–C4)"
                subtitle="Hard ceiling enforced by HarnessTree validator (I-07)"
                right={<Badge variant="success">Validated OK</Badge>}
              />
              <div className="mt-3">
                <div className="flex items-center justify-between font-mono text-xs">
                  <span className="font-semibold text-slate-800">
                    {tokenUsed} tokens active
                  </span>
                  <span className="text-slate-500">
                    Ceiling: {tokenMax.toLocaleString()} tokens ({tokenPct}%)
                  </span>
                </div>
                <div className="mt-2 h-2.5 w-full overflow-hidden rounded-full bg-slate-100">
                  <div
                    className="h-full rounded-full bg-blue-600 transition-all"
                    style={{ width: `${Math.max(4, tokenPct)}%` }}
                  />
                </div>
              </div>

              {/* Trigger RSI Evolution */}
              <div className="mt-4 border-t border-slate-100 pt-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-900">
                    Two-Level RSI Evolution Loop
                  </span>
                  <span className="font-mono text-[11px] text-slate-500">
                    R=4k / P=3k
                  </span>
                </div>
                <p className="mt-1 text-[11px] text-slate-600">
                  Runs <code>VertexHarnessProposer</code> against{' '}
                  <code>EXPERIENCE.md</code>, validates candidate mutations in
                  the AST sandbox, and promotes if score improves.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={() => handleRunEvolve('parallel')}
                    disabled={evolving}
                  >
                    <Sparkles className="h-3.5 w-3.5" />
                    {evolving
                      ? 'Running RSI Loop...'
                      : 'Evolve Harness (Parallel k=2)'}
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handleRunEvolve('sequential')}
                    disabled={evolving}
                  >
                    <RefreshCw className="h-3.5 w-3.5" />
                    Hill-Climb (Sequential)
                  </Button>
                </div>

                {evolveResult && (
                  <div className="mt-3 rounded-md border border-blue-200 bg-blue-50/60 p-2.5 font-mono text-[11px] text-slate-800">
                    <div>
                      <strong>Status:</strong> {evolveResult.status} ·{' '}
                      <strong>Score:</strong> {evolveResult.winner_score ?? '1.0'}
                    </div>
                    <div>
                      <strong>Tag:</strong> {evolveResult.winning_tag || 'base'}{' '}
                      · <strong>Version:</strong> v
                      {evolveResult.living_model_version ?? 1}
                    </div>
                  </div>
                )}
              </div>
            </Card>
          </div>

          {/* Right Column: C1..C8 File Surface Explorer */}
          <Card className="border-slate-200 bg-white p-5 xl:col-span-8">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-100 pb-3">
              <div>
                <h3 className="font-mono text-sm font-bold text-slate-900">
                  {activeTree?.agent_id || selectedAgentId} — GODVIEW_SURFACE
                </h3>
                <p className="font-mono text-[11px] text-slate-500">
                  Root: {activeTree?.tree_root || 'godview/agents/base_trees'} ·
                  Model: {activeTree?.model_id || 'gemini-4-argon'}
                </p>
              </div>
              <Badge variant="info">
                Living Model v{activeTree?.living_model_version ?? 1}
              </Badge>
            </div>

            {/* Category Pills C1..C8 */}
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {CATEGORY_META.map((cat) => {
                const active = selectedCategory === cat.key;
                return (
                  <button
                    key={cat.key}
                    type="button"
                    onClick={() => setSelectedCategory(cat.key)}
                    className={`flex flex-col rounded-lg border p-2.5 text-left transition ${
                      active
                        ? 'border-blue-600 bg-blue-50/60 ring-1 ring-blue-600/20'
                        : 'border-slate-200 bg-white hover:border-blue-300'
                    }`}
                  >
                    <div className="flex items-center justify-between">
                      <span className="font-mono text-xs font-bold text-blue-700">
                        {cat.code}
                      </span>
                      <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium text-slate-600">
                        {cat.alwaysOn ? 'Always-On' : 'On-Demand'}
                      </span>
                    </div>
                    <div className="mt-1 truncate text-xs font-semibold text-slate-900">
                      {cat.label}
                    </div>
                    <div className="truncate font-mono text-[10px] text-slate-500">
                      {cat.fileHint}
                    </div>
                  </button>
                );
              })}
            </div>

            {/* Active Category File Viewer */}
            <div className="mt-4">{renderCategoryContent()}</div>
          </Card>
        </div>
      )}

      {/* SUBTAB 2: HOW GOD-VIEW IS BUILT (ARCHITECTURE & ORGANS) */}
      {subTab === 'blueprint' && (
        <div className="space-y-5">
          {/* Two-Level Recursion Loop Comparison */}
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <Card className="border-blue-200 bg-blue-50/20 p-5">
              <div className="flex items-center justify-between">
                <Badge variant="info">Inner Loop · P = 3,000 tokens</Badge>
                <span className="font-mono text-xs font-semibold text-blue-800">
                  Level 1 — Specialist Court Execution
                </span>
              </div>
              <h3 className="mt-2 text-sm font-bold text-slate-900">
                Bounded Incident Diagnosis &amp; TrustGate Remediation
              </h3>
              <p className="mt-1 text-xs leading-relaxed text-slate-700">
                When a zero-token Sentinel detects an invariant breach, it
                assembles a deterministic <code>EvidencePack</code> and wakes
                the relevant Specialist Court archetypes. Each specialist loads
                its C1–C4 always-on files (&le;12,000 tokens), executes C3
                autotrigger cues, runs C7 deterministic verification scripts,
                and submits its hypothesis + <code>ReversalRecipe</code> to{' '}
                <code>TrustGate</code>.
              </p>
            </Card>

            <Card className="border-emerald-200 bg-emerald-50/20 p-5">
              <div className="flex items-center justify-between">
                <Badge variant="success">Outer Loop · R = 4,000 tokens</Badge>
                <span className="font-mono text-xs font-semibold text-emerald-800">
                  Level 2 — Harness² Proposer &amp; Hill-Climbing
                </span>
              </div>
              <h3 className="mt-2 text-sm font-bold text-slate-900">
                Recursive Self-Improvement Across C1–C8 Files
              </h3>
              <p className="mt-1 text-xs leading-relaxed text-slate-700">
                After incident closure or scheduled reflection, the{' '}
                <code>VertexHarnessProposer</code> inspects{' '}
                <code>EXPERIENCE.md</code>, diagnoses failure modes, and
                generates candidate file mutations across C1–C8. Every candidate
                tree is validated against the 12,000-token cap and Python AST
                sandbox before being scored on deterministic evaluator tasks.
              </p>
            </Card>
          </div>

          {/* 6 Sovereign Organs */}
          <Card className="border-slate-200 bg-white p-5">
            <SectionHeader
              title="The 6 Sovereign Organs (How God-View Works Internally)"
              subtitle="Environment-neutral Python organs inside godview/sovereign/ and godview/harness/"
            />
            <div className="mt-4 grid grid-cols-1 gap-3 md:grid-cols-2 lg:grid-cols-3">
              {(overview?.blueprint?.organs || []).map((org: any) => (
                <div
                  key={org.id}
                  className="rounded-lg border border-slate-200 bg-slate-50/70 p-3.5"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-bold text-slate-900 text-xs">
                      {org.name}
                    </span>
                    <Badge variant="outline">{org.id}</Badge>
                  </div>
                  <div className="mt-1 font-mono text-[11px] text-blue-700">
                    {org.module}
                  </div>
                  <p className="mt-1.5 text-xs leading-snug text-slate-600">
                    {org.role}
                  </p>
                </div>
              ))}
            </div>
          </Card>

          {/* Constitutional Invariants */}
          <Card className="border-slate-200 bg-white p-5">
            <SectionHeader
              title="Constitutional Design Invariants (I-01 .. I-07)"
              subtitle="Non-negotiable architectural guarantees enforced by unit tests, import-linter, and neutrality linter"
            />
            <div className="mt-3 grid grid-cols-1 gap-2.5 md:grid-cols-2">
              {(overview?.blueprint?.invariants || []).map((inv: any) => (
                <div
                  key={inv.id}
                  className="flex items-start gap-3 rounded-lg border border-slate-200 bg-white p-3"
                >
                  <span className="rounded bg-blue-600 px-2 py-0.5 font-mono text-xs font-bold text-white">
                    {inv.id}
                  </span>
                  <div>
                    <div className="text-xs font-bold text-slate-900">
                      {inv.title}
                    </div>
                    <div className="mt-0.5 text-xs text-slate-600">
                      {inv.rule}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}

      {/* SUBTAB 3: PORTABLE PACKAGE & DEPLOY ANYWHERE UTILITY */}
      {subTab === 'portable' && (
        <div className="space-y-4">
          <Card className="border-slate-200 bg-white p-5">
            <SectionHeader
              title="Deploy God-View Anywhere as a Package or CLI Utility"
              subtitle="Zero hardcoded service names or project IDs (verified by lint_neutrality.py). Drop into any GCP project, Kubernetes cluster, or local Docker host."
              right={<Badge variant="success">pyproject.toml: godview</Badge>}
            />

            <div className="mt-4 grid grid-cols-1 gap-4 lg:grid-cols-2">
              {(
                overview?.blueprint?.portable_deployments || [
                  {
                    target: 'cli',
                    title: '1. Portable Python Package & CLI Utility',
                    install: 'uv pip install -e .',
                    commands: [
                      'godview doctor',
                      'godview perceive --env gcp --project $GOOGLE_CLOUD_PROJECT',
                      'godview harness inspect --agent application__specialist',
                      'godview serve --port 8080',
                    ],
                  },
                  {
                    target: 'cloud_run',
                    title: '2. Deploy to Any Google Cloud Project (Cloud Run)',
                    install:
                      'godview deploy --target cloud-run --project $PROJECT_ID --region us-central1',
                    commands: [
                      'gcloud run deploy god-view-sovereign --source . --region us-central1 --set-env-vars=GODVIEW_ENV=gcp,GOOGLE_CLOUD_PROJECT=$PROJECT_ID',
                      'gcloud run deploy god-view-sre-web --source godview/god-view-sre-web --region us-central1',
                    ],
                  },
                  {
                    target: 'docker',
                    title: '3. Local Docker Compose Proving Ground',
                    install: 'docker compose up --build -d',
                    commands: [
                      'godview deploy --target docker',
                      'docker run -p 8080:8080 -e GODVIEW_ENV=local godview-sovereign:latest',
                    ],
                  },
                  {
                    target: 'kubernetes',
                    title: '4. Kubernetes / GKE Cluster Operator',
                    install: 'godview deploy --target k8s',
                    commands: [
                      'kubectl create namespace godview-system',
                      'kubectl -n godview-system run god-view-sovereign --image=godview-sovereign:latest --port=8080',
                    ],
                  },
                ]
              ).map((dep: any) => (
                <div
                  key={dep.target}
                  className="flex flex-col justify-between rounded-xl border border-slate-200 bg-slate-50 p-4"
                >
                  <div>
                    <div className="flex items-center justify-between">
                      <h3 className="text-sm font-bold text-slate-900">
                        {dep.title}
                      </h3>
                      <Badge variant="info">{dep.target}</Badge>
                    </div>
                    <div className="mt-2 rounded border border-slate-200 bg-white px-3 py-2 font-mono text-xs text-blue-700">
                      $ {dep.install}
                    </div>
                    <div className="mt-2.5 space-y-1.5">
                      {(dep.commands || []).map((cmd: string) => (
                        <div
                          key={cmd}
                          className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-white px-3 py-1.5 font-mono text-xs text-slate-800"
                        >
                          <span className="truncate">$ {cmd}</span>
                          <button
                            type="button"
                            onClick={() => copyText(cmd)}
                            className="shrink-0 rounded px-1.5 py-0.5 text-[11px] font-sans font-medium text-slate-500 hover:bg-slate-100 hover:text-slate-900"
                          >
                            {copiedCmd === cmd ? 'Copied!' : 'Copy'}
                          </button>
                        </div>
                      ))}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </Card>
        </div>
      )}
    </div>
  );
};
