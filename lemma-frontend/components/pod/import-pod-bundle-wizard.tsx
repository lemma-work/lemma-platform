'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
    AlertTriangle,
    ArrowRight,
    Bot,
    Check,
    CircleAlert,
    Code2,
    Database,
    FileArchive,
    Globe,
    Loader2,
    type LucideIcon,
    PackagePlus,
    RotateCcw,
    Upload,
} from 'lucide-react';
import { toast } from 'sonner';

import { RemixTakeover } from '@/components/pod/remix-takeover';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { useOrganizations } from '@/lib/hooks/use-organizations';
import { useAccessiblePods, usePod } from '@/lib/hooks/use-pods';
import {
    type Capability,
    type ImportStep,
    type PodImport,
    useApplyImport,
    useCreateImport,
    useImportFromGithub,
    useImportFromGithubIntoPod,
    useImportIntoNewPod,
    usePodImport,
} from '@/lib/hooks/use-pod-imports';

/** Where an import lands: a brand-new pod the importer owns, or merged into the
 * pod they're already in. */
type Target = 'new' | 'here';

type Phase = 'upload' | 'review' | 'result';

/** A non-upload origin for the wizard — drives the standalone entry points
 * (e.g. /import/github/<owner>/<repo>) that have no file to pick, only a
 * fetch to trigger once the new-vs-existing choice is made. */
type ExternalSource = { kind: 'github'; owner: string; repo: string };

const TIER_ICON: Record<string, LucideIcon> = {
    code: Code2,
    external: Globe,
    ai: Bot,
    data: Database,
};

const PLAN_TYPE_ORDER = [
    'tables',
    'functions',
    'agents',
    'workflows',
    'schedules',
    'surfaces',
    'apps',
    // Grants are applied in a final pass once every resource exists.
    'agent_grants',
    'function_grants',
];
const PLAN_TYPE_LABEL: Record<string, string> = {
    tables: 'Tables',
    functions: 'Functions',
    agents: 'Agents',
    workflows: 'Workflows',
    schedules: 'Schedules',
    surfaces: 'Surfaces',
    apps: 'Apps',
    agent_grants: 'Agent access',
    function_grants: 'Function access',
};

const SINGULAR: Record<string, string> = {
    tables: 'table',
    functions: 'function',
    agents: 'agent',
    workflows: 'workflow',
    schedules: 'schedule',
    surfaces: 'surface',
    apps: 'app',
    agent_grants: 'agent access',
    function_grants: 'function access',
};

/** A human hint for the most common failure causes; null falls back to the raw error. */
function errorHint(raw: string): string | null {
    if (/cannot connect to host|connect call failed|connection refused/i.test(raw)) {
        const port = raw.match(/:(\d{2,5})\b/)?.[1];
        const svc =
            port === '8711'
                ? 'The scheduler service'
                : port === '8721'
                  ? 'The agentbox sandbox service'
                  : port
                    ? `A backend service on port ${port}`
                    : 'A backend service';
        return `${svc} isn’t reachable — it may not be running in your stack.`;
    }
    if (/already exists/i.test(raw)) return 'It already exists in this pod.';
    if (/relation .* does not exist|does not exist/i.test(raw))
        return 'A resource it depends on hasn’t been created yet.';
    return null;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div className="mb-5">
            <p className="mb-2 text-sm font-medium text-[var(--text-secondary)]">{title}</p>
            {children}
        </div>
    );
}

function CapabilityList({ capabilities }: { capabilities: Capability[] }) {
    if (!capabilities.length) return null;
    return (
        <Section title="This pod will">
            <ul className="space-y-2">
                {capabilities.map((cap, i) => {
                    const Icon = TIER_ICON[cap.tier];
                    return (
                        <li key={i} className="flex items-center gap-2.5 text-sm text-[var(--text-primary)]">
                            {Icon ? (
                                <Icon className="h-4 w-4 shrink-0 text-[var(--text-tertiary)]" aria-hidden />
                            ) : (
                                <span className="h-1.5 w-1.5 rounded-full bg-[var(--text-tertiary)]" aria-hidden />
                            )}
                            {cap.summary}
                        </li>
                    );
                })}
            </ul>
        </Section>
    );
}

function RequirementsList({ requirements }: { requirements: Record<string, unknown> }) {
    const connectors = (requirements.connectors as { key: string; purpose?: string }[]) ?? [];
    const members = (requirements.members as { key: string }[]) ?? [];
    const variables = (requirements.variables as { key: string; purpose?: string }[]) ?? [];
    const data = requirements.data as { row_count?: number; tables_with_seed?: string[] } | undefined;

    if (!connectors.length && !members.length && !variables.length && !data) {
        return (
            <Section title="Needs from you">
                <p className="text-sm text-[var(--state-success)]">
                    Nothing to wire up — this bundle is self-contained.
                </p>
            </Section>
        );
    }
    return (
        <Section title="Needs from you">
            <ul className="space-y-1.5 text-sm">
                {connectors.map((c) => (
                    <li key={c.key} className="text-[var(--text-primary)]">
                        <span className="font-medium">connector</span> {c.key}
                        {c.purpose ? <span className="text-[var(--text-tertiary)]"> · {c.purpose}</span> : null}
                    </li>
                ))}
                {members.map((m) => (
                    <li key={m.key} className="text-[var(--text-primary)]">
                        <span className="font-medium">person</span> {m.key}
                        <span className="text-[var(--text-tertiary)]"> · defaults to you</span>
                    </li>
                ))}
                {variables.map((v) => (
                    <li key={v.key} className="text-[var(--text-primary)]">
                        <span className="font-medium">variable</span> {v.key}
                    </li>
                ))}
                {data ? (
                    <li className="text-[var(--text-primary)]">
                        <span className="font-medium">data</span> {data.row_count ?? 0} row(s) across{' '}
                        {(data.tables_with_seed ?? []).join(', ')}
                    </li>
                ) : null}
            </ul>
        </Section>
    );
}

/** Editable inputs for the variables a bundle needs resolved (connector
 * accounts + free variables). Members default to the importing user server-side,
 * so they're not asked for here. */
function ResolveInputs({
    requirements,
    values,
    onChange,
}: {
    requirements: Record<string, unknown>;
    values: Record<string, string>;
    onChange: (key: string, value: string) => void;
}) {
    const connectors = (requirements.connectors as { key: string; resolution?: { var?: string } }[]) ?? [];
    const variables = (requirements.variables as { key: string; purpose?: string }[]) ?? [];
    const fields = [
        ...connectors
            .map((c) => ({ key: c.resolution?.var, label: `connector · ${c.key}`, hint: 'account id' }))
            .filter((f): f is { key: string; label: string; hint: string } => !!f.key),
        ...variables.map((v) => ({ key: v.key, label: `variable · ${v.key}`, hint: v.purpose ?? '' })),
    ];
    if (!fields.length) return null;
    return (
        <Section title="Resolve">
            <div className="space-y-2">
                {fields.map((f) => (
                    <label key={f.key} className="flex items-center gap-3 text-sm">
                        <span className="w-44 shrink-0 text-[var(--text-secondary)]">{f.label}</span>
                        <Input
                            className="flex-1"
                            placeholder={f.hint}
                            value={values[f.key] ?? ''}
                            onChange={(e) => onChange(f.key, e.target.value)}
                        />
                    </label>
                ))}
            </div>
        </Section>
    );
}

function StepDot({ status }: { status: ImportStep['status'] }) {
    if (status === 'COMPLETED')
        return <Check className="h-3.5 w-3.5 shrink-0 text-[var(--state-success)]" aria-hidden />;
    if (status === 'FAILED')
        return <CircleAlert className="h-3.5 w-3.5 shrink-0 text-[var(--state-error)]" aria-hidden />;
    return (
        <span
            className={`h-1.5 w-1.5 shrink-0 rounded-full ${
                status === 'SKIPPED' ? 'bg-[var(--text-tertiary)]' : 'bg-[var(--border-strong)]'
            }`}
            aria-hidden
        />
    );
}

/** The plan, grouped by resource type — compact, and each type (incl. apps) is
 * its own clearly-labelled row instead of one long flat list. */
function PlanList({ imp }: { imp: PodImport }) {
    const groups = PLAN_TYPE_ORDER.map((type) => ({
        type,
        steps: imp.plan.filter((s) => s.resource_type === type),
    })).filter((g) => g.steps.length);

    return (
        <Section title={`Plan · ${imp.progress_done}/${imp.progress_total}`}>
            <div className="overflow-hidden rounded-lg border border-[var(--border-subtle)]">
                {groups.map((g, gi) => {
                    const done = g.steps.filter(
                        (s) => s.status === 'COMPLETED' || s.status === 'SKIPPED',
                    ).length;
                    return (
                        <div
                            key={g.type}
                            className={`flex gap-3 px-3 py-2.5 ${gi ? 'border-t border-[var(--border-subtle)]' : ''}`}
                        >
                            <div className="w-24 shrink-0 pt-1">
                                <span className="text-sm font-medium text-[var(--text-primary)]">
                                    {PLAN_TYPE_LABEL[g.type] ?? g.type}
                                </span>
                                <span className="ml-1.5 text-xs text-[var(--text-tertiary)]">
                                    {done}/{g.steps.length}
                                </span>
                            </div>
                            <div className="flex flex-1 flex-wrap gap-1.5">
                                {g.steps.map((s) => (
                                    <span
                                        key={s.resource_name}
                                        title={s.error ?? undefined}
                                        className="inline-flex items-center gap-1.5 rounded-md bg-[var(--surface-2)] px-2 py-1 text-xs text-[var(--text-secondary)]"
                                    >
                                        <StepDot status={s.status} />
                                        {s.resource_name}
                                        {s.destructive ? (
                                            <AlertTriangle
                                                className="h-3 w-3 text-[var(--state-error)]"
                                                aria-hidden
                                            />
                                        ) : null}
                                    </span>
                                ))}
                            </div>
                        </div>
                    );
                })}
            </div>
        </Section>
    );
}

/** The new-pod vs install-here choice — two selectable cards. */
function TargetCard({
    active,
    icon: Icon,
    title,
    subtitle,
    onClick,
}: {
    active: boolean;
    icon: LucideIcon;
    title: string;
    subtitle: string;
    onClick: () => void;
}) {
    return (
        <button
            type="button"
            onClick={onClick}
            className={`flex items-start gap-2.5 rounded-lg border p-3 text-left ${
                active
                    ? 'border-[var(--accent)] bg-[var(--surface-2)]'
                    : 'border-[var(--border-subtle)] hover:bg-[var(--surface-2)]'
            }`}
        >
            <Icon
                className={`mt-0.5 h-4 w-4 shrink-0 ${active ? 'text-[var(--accent)]' : 'text-[var(--text-tertiary)]'}`}
            />
            <span>
                <span className="block text-sm font-medium text-[var(--text-primary)]">{title}</span>
                <span className="block text-xs text-[var(--text-tertiary)]">{subtitle}</span>
            </span>
        </button>
    );
}

export function ImportPodBundleWizard({
    podId,
    initialImport,
    source,
}: {
    // The pod you're already inside (e.g. /pod/<id>/import) — omit for a
    // standalone entry point that has no "current pod" (see `source`).
    podId?: string;
    // A pre-planned import (e.g. from a shared /import/p/<id> link) — the wizard
    // skips upload and opens straight at review.
    initialImport?: PodImport;
    // A non-upload origin (e.g. a GitHub repo) for a standalone entry point —
    // the "upload" phase asks new-vs-existing-pod and an org/pod picker
    // instead of a file drop.
    source?: ExternalSource;
}) {
    const router = useRouter();
    const standalone = !podId;
    const [phase, setPhase] = useState<Phase>(initialImport ? 'review' : 'upload');
    const [target, setTarget] = useState<Target>('new');
    const [file, setFile] = useState<File | null>(null);
    const [imp, setImp] = useState<PodImport | null>(initialImport ?? null);
    const [vars, setVars] = useState<Record<string, string>>({});
    const [selectedOrgId, setSelectedOrgId] = useState('');
    const [selectedExistingPodId, setSelectedExistingPodId] = useState('');

    const pod = usePod(podId);
    const createImport = useCreateImport();
    const importIntoNewPod = useImportIntoNewPod();
    const importFromGithub = useImportFromGithub();
    const importFromGithubIntoPod = useImportFromGithubIntoPod();
    const applyImport = useApplyImport();
    const uploading = createImport.isPending || importIntoNewPod.isPending;
    const continuing = importFromGithub.isPending || importFromGithubIntoPod.isPending;

    const organizations = useOrganizations({ enabled: standalone }).data?.items ?? [];
    const effectiveOrgId = selectedOrgId || organizations[0]?.id || '';
    const accessiblePods = useAccessiblePods({ enabled: standalone }).data;

    // The import always carries the pod it targets (imp.pod_id). In pod-scoped
    // mode that's "did it differ from the pod we started in"; standalone has
    // no starting pod, so the choice the user made is the source of truth.
    const createdNewPod = podId ? !!imp && imp.pod_id !== podId : target === 'new';
    const newPod = usePod(createdNewPod ? imp?.pod_id : undefined);
    // Live progress: the apply POST blocks until done, so a concurrent poll
    // (enabled here for the exact duration of that call) is what actually
    // shows per-step progress as it happens.
    const polled = usePodImport(imp?.pod_id, imp?.id, { forcePoll: applyImport.isPending });
    const liveImp = polled.data ?? imp;

    const destructiveCount = useMemo(
        () => imp?.plan.filter((s) => s.destructive).length ?? 0,
        [imp],
    );

    const onUpload = async () => {
        if (!file) return;
        try {
            const result =
                target === 'new'
                    ? await importIntoNewPod.mutateAsync({
                          file,
                          organizationId: pod.data!.organization_id,
                          sourceRef: file.name,
                      })
                    : await createImport.mutateAsync({ podId: podId!, file, sourceName: file.name });
            setImp(result);
            setPhase('review');
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Upload failed');
        }
    };

    const onContinueFromSource = async () => {
        if (!source) return;
        try {
            const result =
                target === 'new'
                    ? await importFromGithub.mutateAsync({
                          owner: source.owner,
                          repo: source.repo,
                          organizationId: effectiveOrgId,
                      })
                    : await importFromGithubIntoPod.mutateAsync({
                          podId: selectedExistingPodId,
                          owner: source.owner,
                          repo: source.repo,
                      });
            setImp(result);
            setPhase('review');
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Could not import this repo');
        }
    };

    const onApply = async () => {
        if (!imp) return;
        try {
            const result = await applyImport.mutateAsync({
                podId: imp.pod_id,
                importId: imp.id,
                variables: vars,
            });
            setImp(result);
            setPhase('result');
            if (result.status === 'COMPLETED') toast.success('Import complete');
            else if (result.status === 'FAILED') {
                const failed = result.plan.find((s) => s.status === 'FAILED');
                toast.error(
                    failed
                        ? `Couldn’t create ${SINGULAR[failed.resource_type] ?? failed.resource_type} “${failed.resource_name}”`
                        : 'Import stopped',
                );
            }
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Apply failed');
        }
    };

    const reset = () => {
        setFile(null);
        setImp(null);
        setSelectedOrgId('');
        setSelectedExistingPodId('');
        setPhase('upload');
    };

    return (
        <div className="mx-auto w-full max-w-2xl">
            <div className="surface-panel p-6">
                {phase === 'upload' && (
                    <>
                        {!standalone && (
                            <>
                                <p className="mb-4 text-sm text-[var(--text-secondary)]">
                                    Upload a pod bundle archive (.zip or .tar.gz). We&apos;ll show you
                                    exactly what it does and what it needs before anything is applied.
                                </p>
                                <label className="flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed border-[var(--border-strong)] px-4 py-8 text-center hover:bg-[var(--surface-2)]">
                                    <Upload className="h-5 w-5 text-[var(--text-tertiary)]" />
                                    <span className="text-sm text-[var(--text-secondary)]">
                                        {file ? file.name : 'Choose a bundle archive'}
                                    </span>
                                    <input
                                        type="file"
                                        accept=".zip,.tar.gz,.tgz,.tar"
                                        className="hidden"
                                        onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                                    />
                                </label>
                            </>
                        )}
                        <div className={`grid grid-cols-2 gap-2 ${standalone ? '' : 'mt-4'}`}>
                            <TargetCard
                                active={target === 'new'}
                                icon={PackagePlus}
                                title="Create a new pod"
                                subtitle="A fresh pod you fully own"
                                onClick={() => setTarget('new')}
                            />
                            <TargetCard
                                active={target === 'here'}
                                icon={ArrowRight}
                                title={standalone ? 'Install into an existing pod' : 'Install into this pod'}
                                subtitle={standalone ? 'Add its resources to a pod you have' : 'Add its resources here'}
                                onClick={() => setTarget('here')}
                            />
                        </div>
                        {standalone && target === 'new' && (
                            <div className="mt-4">
                                <label className="mb-1.5 block text-sm text-[var(--text-secondary)]">
                                    Workspace
                                </label>
                                <select
                                    value={effectiveOrgId}
                                    onChange={(e) => setSelectedOrgId(e.target.value)}
                                    className="form-field-control flex h-11 w-full items-center px-3 py-2 text-sm text-[var(--text-primary)] outline-none"
                                >
                                    <option value="" disabled>
                                        Select a workspace
                                    </option>
                                    {organizations.map((org) => (
                                        <option key={org.id} value={org.id}>
                                            {org.name}
                                        </option>
                                    ))}
                                </select>
                            </div>
                        )}
                        {standalone && target === 'here' && (
                            <div className="mt-4">
                                <label className="mb-1.5 block text-sm text-[var(--text-secondary)]">
                                    Pod
                                </label>
                                <select
                                    value={selectedExistingPodId}
                                    onChange={(e) => setSelectedExistingPodId(e.target.value)}
                                    className="form-field-control flex h-11 w-full items-center px-3 py-2 text-sm text-[var(--text-primary)] outline-none"
                                >
                                    <option value="" disabled>
                                        Select a pod
                                    </option>
                                    {accessiblePods?.groups.map((group) => (
                                        <optgroup key={group.organization.id} label={group.organization.name}>
                                            {group.pods.map((p) => (
                                                <option key={p.id} value={p.id}>
                                                    {p.name}
                                                </option>
                                            ))}
                                        </optgroup>
                                    ))}
                                </select>
                            </div>
                        )}
                        <div className="mt-5 flex justify-end gap-2">
                            {standalone ? (
                                <Button
                                    disabled={target === 'new' ? !effectiveOrgId : !selectedExistingPodId}
                                    loading={continuing}
                                    onClick={onContinueFromSource}
                                >
                                    Continue <ArrowRight className="ml-1.5 h-4 w-4" />
                                </Button>
                            ) : (
                                <Button
                                    disabled={!file || (target === 'new' && !pod.data)}
                                    loading={uploading}
                                    onClick={onUpload}
                                >
                                    <FileArchive className="mr-1.5 h-4 w-4" /> Analyze bundle
                                </Button>
                            )}
                        </div>
                    </>
                )}

                {phase === 'review' && imp && (
                    <>
                        <CapabilityList capabilities={imp.capabilities} />
                        <RequirementsList requirements={imp.requirements} />
                        <ResolveInputs
                            requirements={imp.requirements}
                            values={vars}
                            onChange={(key, value) => setVars((prev) => ({ ...prev, [key]: value }))}
                        />
                        <PlanList imp={applyImport.isPending ? (liveImp ?? imp) : imp} />
                        {applyImport.isPending && (
                            <p className="mb-5 flex items-center gap-2 text-sm text-[var(--text-tertiary)]">
                                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Applying — this can take
                                up to a minute for larger pods.
                            </p>
                        )}
                        {destructiveCount > 0 && (
                            <div className="mb-5 flex items-start gap-2 rounded-lg border border-[var(--state-error)] px-3 py-2.5">
                                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--state-error)]" />
                                <p className="text-sm text-[var(--text-primary)]">
                                    {destructiveCount} table change(s) will drop or rebuild columns —
                                    existing data in those columns is lost.
                                </p>
                            </div>
                        )}
                        <div className="flex justify-between">
                            <Button variant="ghost" onClick={reset}>
                                Back
                            </Button>
                            <Button
                                variant={destructiveCount > 0 ? 'destructive' : 'primary'}
                                loading={applyImport.isPending}
                                onClick={onApply}
                            >
                                {destructiveCount > 0 ? 'Apply (data loss)' : 'Apply import'}
                            </Button>
                        </div>
                    </>
                )}

                {phase === 'result' && imp && createdNewPod && imp.status === 'COMPLETED' && (
                    <>
                        <RemixTakeover podId={imp.pod_id} podName={newPod.data?.name} />
                        <div className="mt-5">
                            <PlanList imp={imp} />
                        </div>
                        <div className="mt-4 flex justify-end">
                            <Button variant="ghost" onClick={reset}>
                                Import another
                            </Button>
                        </div>
                    </>
                )}

                {phase === 'result' && imp && !(createdNewPod && imp.status === 'COMPLETED') && (
                    <>
                        <div className="mb-4 flex items-center gap-2">
                            {imp.status === 'COMPLETED' ? (
                                <>
                                    <Check className="h-5 w-5 text-[var(--state-success)]" />
                                    <p className="text-base font-medium text-[var(--text-primary)]">
                                        Imported · {imp.progress_done}/{imp.progress_total}
                                    </p>
                                </>
                            ) : (
                                <>
                                    <CircleAlert className="h-5 w-5 text-[var(--state-error)]" />
                                    <p className="text-base font-medium text-[var(--text-primary)]">
                                        Stopped at {imp.progress_done}/{imp.progress_total}
                                    </p>
                                </>
                            )}
                        </div>
                        {imp.status === 'FAILED'
                            ? (() => {
                                  const failed = imp.plan.find((s) => s.status === 'FAILED');
                                  const raw = (failed?.error || imp.error || '').trim();
                                  const hint = errorHint(raw);
                                  return (
                                      <div className="mb-4 rounded-lg border border-[var(--state-error)] p-3">
                                          <p className="text-sm font-medium text-[var(--text-primary)]">
                                              {failed
                                                  ? `Couldn’t create ${SINGULAR[failed.resource_type] ?? failed.resource_type} “${failed.resource_name}”`
                                                  : 'Import stopped'}
                                          </p>
                                          {hint ? (
                                              <p className="mt-1 text-sm text-[var(--text-secondary)]">{hint}</p>
                                          ) : null}
                                          {raw ? (
                                              <details className="mt-2">
                                                  <summary className="cursor-pointer text-xs text-[var(--text-tertiary)]">
                                                      Error details
                                                  </summary>
                                                  <pre className="mt-1 whitespace-pre-wrap text-xs text-[var(--text-secondary)]">
                                                      {raw}
                                                  </pre>
                                              </details>
                                          ) : null}
                                          <p className="mt-2 text-xs text-[var(--text-tertiary)]">
                                              Fix the cause, then Resume — the {imp.progress_done} step(s)
                                              already imported are skipped.
                                          </p>
                                      </div>
                                  );
                              })()
                            : null}
                        <PlanList imp={imp} />
                        <div className="flex justify-between">
                            <Button variant="ghost" onClick={reset}>
                                Import another
                            </Button>
                            {imp.status === 'FAILED' ? (
                                <Button loading={applyImport.isPending} onClick={onApply}>
                                    <RotateCcw className="mr-1.5 h-4 w-4" /> Resume
                                </Button>
                            ) : createdNewPod ? (
                                <Button
                                    variant="primary"
                                    onClick={() => router.push(`/pod/${imp.pod_id}`)}
                                >
                                    Open your new pod <ArrowRight className="ml-1.5 h-4 w-4" />
                                </Button>
                            ) : imp.status === 'COMPLETED' ? (
                                <Button
                                    variant="primary"
                                    onClick={() => router.push(`/pod/${imp.pod_id}`)}
                                >
                                    Open pod <ArrowRight className="ml-1.5 h-4 w-4" />
                                </Button>
                            ) : null}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
