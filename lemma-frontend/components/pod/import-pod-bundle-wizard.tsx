'use client';

import { useMemo, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
    AlertTriangle,
    ArrowRight,
    Bot,
    CalendarClock,
    Check,
    ChevronRight,
    CircleAlert,
    Code2,
    Database,
    FileArchive,
    Globe,
    Loader2,
    type LucideIcon,
    PackagePlus,
    PanelsTopLeft,
    Plug,
    Radio,
    RotateCcw,
    Upload,
    User,
    Variable,
    Workflow,
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
            <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">
                {title}
            </p>
            {children}
        </div>
    );
}

/** "meal-log-from-telegram-12.zip" → "meal log from telegram 12" — repo slugs
 * and bundle filenames read like identifiers; the install hero should read
 * like an app name. Case is left alone (title-casing mangles acronyms). */
function prettifyBundleName(name: string): string {
    return name
        .replace(/\.(zip|tar\.gz|tgz|tar)$/i, '')
        .replace(/[-_]+/g, ' ')
        .trim();
}

/** The resource types the hero strip counts, in display order — grants are an
 * implementation detail of the plan, not something the user "gets". */
const HERO_COUNT_TYPES: [string, LucideIcon][] = [
    ['tables', Database],
    ['functions', Code2],
    ['agents', Bot],
    ['workflows', Workflow],
    ['schedules', CalendarClock],
    ['surfaces', Radio],
    ['apps', PanelsTopLeft],
];

/** The install sheet's inventory chips ("5 tables", "1 app"), computed off the
 * plan. */
function resourceCountChips(imp: PodImport): { label: string; icon: LucideIcon }[] {
    const counts = new Map<string, number>();
    for (const s of imp.plan) counts.set(s.resource_type, (counts.get(s.resource_type) ?? 0) + 1);
    return HERO_COUNT_TYPES.flatMap(([type, icon]) => {
        const n = counts.get(type) ?? 0;
        return n ? [{ label: `${n} ${n === 1 ? (SINGULAR[type] ?? type) : type}`, icon }] : [];
    });
}

/** App-store-style install header: app-icon tile + title + where it came from,
 * with the resource inventory as icon chips under it. */
function InstallHero({
    title,
    sourceLine,
    counts,
}: {
    title: string;
    sourceLine: string;
    counts?: { label: string; icon: LucideIcon }[];
}) {
    return (
        <div className="mb-5">
            <div className="flex items-center gap-3.5">
                <div className="flex h-14 w-14 shrink-0 items-center justify-center rounded-2xl border border-[var(--border-subtle)] bg-[var(--surface-2)] shadow-[var(--shadow-xs)]">
                    <PackagePlus className="h-7 w-7 text-[var(--accent)]" aria-hidden />
                </div>
                <div className="min-w-0">
                    <p className="break-words text-lg font-semibold leading-snug text-[var(--text-primary)]">
                        {title}
                    </p>
                    <p className="mt-0.5 truncate text-xs text-[var(--text-tertiary)]">{sourceLine}</p>
                </div>
            </div>
            {counts?.length ? (
                <div className="mt-3.5 flex flex-wrap gap-1.5">
                    {counts.map(({ label, icon: Icon }) => (
                        <span
                            key={label}
                            className="inline-flex items-center gap-1.5 rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] px-2 py-1 text-xs text-[var(--text-secondary)]"
                        >
                            <Icon className="h-3 w-3 text-[var(--text-tertiary)]" aria-hidden />
                            {label}
                        </span>
                    ))}
                </div>
            ) : null}
        </div>
    );
}

/** The small soft square every consent/setup row leads with. */
function IconChip({ icon: Icon, tone = 'default' }: { icon?: LucideIcon; tone?: 'default' | 'success' }) {
    return (
        <span
            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)]"
            aria-hidden
        >
            {Icon ? (
                <Icon
                    className={`h-3.5 w-3.5 ${
                        tone === 'success' ? 'text-[var(--state-success)]' : 'text-[var(--text-secondary)]'
                    }`}
                />
            ) : (
                <span className="h-1.5 w-1.5 rounded-full bg-[var(--text-tertiary)]" />
            )}
        </span>
    );
}

function CapabilityList({ capabilities }: { capabilities: Capability[] }) {
    if (!capabilities.length) return null;
    return (
        <Section title="This pod will">
            <ul className="space-y-2">
                {capabilities.map((cap, i) => (
                    <li key={i} className="flex items-center gap-2.5">
                        <IconChip icon={TIER_ICON[cap.tier]} />
                        <span className="text-sm text-[var(--text-primary)]">{cap.summary}</span>
                    </li>
                ))}
            </ul>
        </Section>
    );
}

/** One "Setup required" row: icon chip + label/purpose, and — when the bundle
 * needs a value from the user — an input on the right (stacked below on small
 * screens). */
function SetupRow({
    icon,
    label,
    subtitle,
    input,
    values,
    onChange,
}: {
    icon: LucideIcon;
    label: string;
    subtitle?: string;
    input?: { key: string; hint: string };
    values?: Record<string, string>;
    onChange?: (key: string, value: string) => void;
}) {
    return (
        <li className="flex flex-col gap-2 sm:flex-row sm:items-center sm:gap-3">
            <div className="flex min-w-0 flex-1 items-center gap-2.5">
                <IconChip icon={icon} />
                <div className="min-w-0">
                    <p className="truncate text-sm text-[var(--text-primary)]">{label}</p>
                    {subtitle ? (
                        <p className="truncate text-xs text-[var(--text-tertiary)]">{subtitle}</p>
                    ) : null}
                </div>
            </div>
            {input && onChange ? (
                <Input
                    className="w-full sm:w-56 sm:shrink-0"
                    placeholder={input.hint}
                    aria-label={label}
                    value={values?.[input.key] ?? ''}
                    onChange={(e) => onChange(input.key, e.target.value)}
                />
            ) : null}
        </li>
    );
}

/** Everything the bundle needs from the importer, as one consent-style list —
 * requirement and (where applicable) the input that resolves it live on the
 * same row. Members default to the importing user server-side, so they get no
 * input. */
function SetupRequired({
    requirements,
    values,
    onChange,
}: {
    requirements: Record<string, unknown>;
    values: Record<string, string>;
    onChange: (key: string, value: string) => void;
}) {
    const connectors =
        (requirements.connectors as { key: string; purpose?: string; resolution?: { var?: string } }[]) ??
        [];
    const members = (requirements.members as { key: string; purpose?: string }[]) ?? [];
    const variables = (requirements.variables as { key: string; purpose?: string }[]) ?? [];
    const data = requirements.data as { row_count?: number; tables_with_seed?: string[] } | undefined;

    if (!connectors.length && !members.length && !variables.length && !data) {
        return (
            <Section title="Setup required">
                <div className="flex items-center gap-2.5">
                    <IconChip icon={Check} tone="success" />
                    <p className="text-sm text-[var(--state-success)]">
                        Self-contained — nothing to set up.
                    </p>
                </div>
            </Section>
        );
    }

    const rowCount = data?.row_count ?? 0;
    return (
        <Section title="Setup required">
            <ul className="space-y-2.5">
                {connectors.map((c) => (
                    <SetupRow
                        key={`connector-${c.key}`}
                        icon={Plug}
                        label={c.key}
                        subtitle={c.purpose ?? 'Connector'}
                        input={c.resolution?.var ? { key: c.resolution.var, hint: 'account id' } : undefined}
                        values={values}
                        onChange={onChange}
                    />
                ))}
                {members.map((m) => (
                    <SetupRow
                        key={`member-${m.key}`}
                        icon={User}
                        label={m.key}
                        subtitle="Pod member · defaults to you"
                    />
                ))}
                {variables.map((v) => (
                    <SetupRow
                        key={`variable-${v.key}`}
                        icon={Variable}
                        label={v.key}
                        subtitle={v.purpose ?? 'Variable'}
                        input={{ key: v.key, hint: v.purpose ?? 'value' }}
                        values={values}
                        onChange={onChange}
                    />
                ))}
                {data ? (
                    <SetupRow
                        icon={Database}
                        label="Seed data"
                        subtitle={`${rowCount} row${rowCount === 1 ? '' : 's'} across ${(
                            data.tables_with_seed ?? []
                        ).join(', ')}`}
                    />
                ) : null}
            </ul>
        </Section>
    );
}

function StepDot({ status, running }: { status: ImportStep['status']; running?: boolean }) {
    if (running)
        return <Loader2 className="h-3 w-3 shrink-0 animate-spin text-[var(--accent)]" aria-hidden />;
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
 * its own clearly-labelled row instead of one long flat list. `runningKey`
 * (resource_type:resource_name) marks the step the apply is on right now;
 * `showProgress` adds a thin overall bar under the title row. `bare` drops the
 * Section chrome for use inside the "What's inside" disclosure. */
function PlanList({
    imp,
    runningKey,
    showProgress = false,
    bare = false,
}: {
    imp: PodImport;
    runningKey?: string | null;
    showProgress?: boolean;
    bare?: boolean;
}) {
    const groups = PLAN_TYPE_ORDER.map((type) => ({
        type,
        steps: imp.plan.filter((s) => s.resource_type === type),
    })).filter((g) => g.steps.length);

    const body = (
        <>
            {showProgress && imp.progress_total > 0 ? (
                <div className="mb-2 h-0.5 w-full rounded-full bg-[var(--border-subtle)]">
                    <div
                        className="h-full rounded-full bg-[var(--accent)] transition-all"
                        /* eslint-disable-next-line no-restricted-syntax -- Apply progress width is data-driven geometry. */
                        style={{ width: `${(imp.progress_done / imp.progress_total) * 100}%` }}
                    />
                </div>
            ) : null}
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
                                {g.steps.map((s) => {
                                    const running =
                                        runningKey === `${s.resource_type}:${s.resource_name}`;
                                    return (
                                        <span
                                            key={s.resource_name}
                                            title={s.error ?? undefined}
                                            className={`inline-flex items-center gap-1.5 rounded-md bg-[var(--surface-2)] px-2 py-1 text-xs text-[var(--text-secondary)] ${
                                                running ? 'ring-1 ring-inset ring-[var(--accent)]' : ''
                                            }`}
                                        >
                                            <StepDot status={s.status} running={running} />
                                            {s.resource_name}
                                            {s.destructive ? (
                                                <AlertTriangle
                                                    className="h-3 w-3 text-[var(--state-error)]"
                                                    aria-hidden
                                                />
                                            ) : null}
                                        </span>
                                    );
                                })}
                            </div>
                        </div>
                    );
                })}
            </div>
        </>
    );

    if (bare) return body;
    return <Section title={`Plan · ${imp.progress_done}/${imp.progress_total}`}>{body}</Section>;
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
    fullWidth = false,
}: {
    // The pod you're already inside (e.g. /pod/<id>/import) — omit for a
    // standalone entry point that has no "current pod" (see `source`).
    podId?: string;
    // A pre-planned import — the wizard skips upload and opens straight at
    // review. No entry point passes this today (direct pod-id share links were
    // pulled), but the capability stays for the next shared-link flow.
    initialImport?: PodImport;
    // A non-upload origin (e.g. a GitHub repo) for a standalone entry point —
    // the "upload" phase asks new-vs-existing-pod and an org/pod picker
    // instead of a file drop.
    source?: ExternalSource;
    // Let a parent grid own the width (e.g. the two-column GitHub import page)
    // instead of the wizard's default centered max-w-2xl.
    fullWidth?: boolean;
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
    // "What's inside" starts folded; installing forces it open (and it stays
    // open) so the live per-step progress is never hidden.
    const [planOpen, setPlanOpen] = useState(false);

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
    // The pod the import lands in — a fresh pod or an existing one — named in
    // the post-import takeover.
    const targetPod = usePod(imp?.pod_id);
    // Live progress: the apply POST blocks until done, so a concurrent poll
    // (enabled here for the exact duration of that call) is what actually
    // shows per-step progress as it happens.
    const polled = usePodImport(imp?.pod_id, imp?.id, { forcePoll: applyImport.isPending });
    const liveImp = polled.data ?? imp;
    // The step the backend is on right now — the first still-PENDING one in
    // the polled plan, but only while an apply is actually in flight.
    const runningStep = applyImport.isPending
        ? (liveImp?.plan.find((s) => s.status === 'PENDING') ?? null)
        : null;
    const runningKey = runningStep
        ? `${runningStep.resource_type}:${runningStep.resource_name}`
        : null;

    const destructiveCount = useMemo(
        () => imp?.plan.filter((s) => s.destructive).length ?? 0,
        [imp],
    );
    const hasApp = useMemo(() => !!imp?.plan.some((s) => s.resource_type === 'apps'), [imp]);
    // A pre-planned import (shared link) has no upload phase to go "Back" to —
    // reset() would land on an upload form scoped to the SHARER's pod, where
    // "install into this pod" mutates the pod that was shared with you.
    const preplanned = !!initialImport;

    // Where this bundle came from, for the install hero's source line.
    const sourceLine =
        source?.kind === 'github'
            ? `From GitHub · ${source.owner}/${source.repo}`
            : initialImport
              ? 'Shared from another Lemma pod'
              : 'From uploaded bundle';
    const countChips = useMemo(() => (imp ? resourceCountChips(imp) : []), [imp]);

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
        // Force the plan disclosure open so live per-step progress is visible
        // for the whole install (and after it lands).
        setPlanOpen(true);
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
        setPlanOpen(false);
        setPhase('upload');
    };

    return (
        <div className={fullWidth ? 'w-full' : 'mx-auto w-full max-w-2xl'}>
            <div className="surface-panel p-6">
                {phase === 'upload' && (
                    <>
                        {standalone && source && (
                            <InstallHero
                                title={prettifyBundleName(source.repo)}
                                sourceLine={`From GitHub · ${source.owner}/${source.repo}`}
                            />
                        )}
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
                        {standalone && source && (
                            <p className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-[var(--text-tertiary)]">
                                Install to
                            </p>
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
                                    className="w-full"
                                    disabled={target === 'new' ? !effectiveOrgId : !selectedExistingPodId}
                                    loading={continuing}
                                    onClick={onContinueFromSource}
                                >
                                    Prepare install <ArrowRight className="ml-1.5 h-4 w-4" />
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
                        <InstallHero
                            title={imp.source_name ? prettifyBundleName(imp.source_name) : 'Pod bundle'}
                            sourceLine={sourceLine}
                            counts={countChips}
                        />
                        <CapabilityList capabilities={imp.capabilities} />
                        <SetupRequired
                            requirements={imp.requirements}
                            values={vars}
                            onChange={(key, value) => setVars((prev) => ({ ...prev, [key]: value }))}
                        />
                        <div className="mb-5">
                            <button
                                type="button"
                                disabled={applyImport.isPending}
                                onClick={() => setPlanOpen((open) => !open)}
                                aria-expanded={planOpen || applyImport.isPending}
                                className="flex w-full items-center gap-1.5 text-sm font-medium text-[var(--text-secondary)] hover:text-[var(--text-primary)] disabled:cursor-default disabled:hover:text-[var(--text-secondary)]"
                            >
                                <ChevronRight
                                    className={`h-4 w-4 shrink-0 transition-transform ${
                                        planOpen || applyImport.isPending ? 'rotate-90' : ''
                                    }`}
                                    aria-hidden
                                />
                                What’s inside · {imp.plan.length} step{imp.plan.length === 1 ? '' : 's'}
                            </button>
                            {(planOpen || applyImport.isPending) && (
                                <div className="mt-2">
                                    <PlanList
                                        bare
                                        imp={applyImport.isPending ? (liveImp ?? imp) : imp}
                                        runningKey={runningKey}
                                        showProgress={applyImport.isPending}
                                    />
                                </div>
                            )}
                        </div>
                        {destructiveCount > 0 && (
                            <div className="mb-5 flex items-start gap-2 rounded-lg border border-[var(--state-error)] px-3 py-2.5">
                                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--state-error)]" />
                                <p className="text-sm text-[var(--text-primary)]">
                                    {destructiveCount} table change(s) will drop or rebuild columns —
                                    existing data in those columns is lost.
                                </p>
                            </div>
                        )}
                        {applyImport.isPending && (
                            <p className="mb-4 flex items-center gap-2 text-sm text-[var(--text-tertiary)]">
                                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                                {runningStep ? (
                                    <span>
                                        Applying {(liveImp ?? imp).progress_done + 1} of{' '}
                                        {(liveImp ?? imp).progress_total} —{' '}
                                        {runningStep.action === 'UPDATE' ? 'updating' : 'creating'}{' '}
                                        {SINGULAR[runningStep.resource_type] ?? runningStep.resource_type}{' '}
                                        “{runningStep.resource_name}”… This can take up to a minute
                                        for larger pods.
                                    </span>
                                ) : (
                                    <span>Applying — this can take up to a minute for larger pods.</span>
                                )}
                            </p>
                        )}
                        <div className="flex items-center gap-2">
                            {!preplanned && (
                                <Button variant="ghost" onClick={reset}>
                                    Back
                                </Button>
                            )}
                            {/* The install CTA owns the row, app-store style —
                                this is the one button the whole sheet leads to. */}
                            <Button
                                className="flex-1"
                                variant={destructiveCount > 0 ? 'destructive' : 'primary'}
                                loading={applyImport.isPending}
                                onClick={onApply}
                            >
                                {destructiveCount > 0 ? 'Install (data loss)' : 'Install pod'}
                            </Button>
                        </div>
                    </>
                )}

                {phase === 'result' && imp && createdNewPod && imp.status === 'COMPLETED' && (
                    <>
                        <RemixTakeover
                            podId={imp.pod_id}
                            podName={targetPod.data?.name}
                            hasApp={hasApp}
                        />
                        <div className="mt-5">
                            <PlanList imp={imp} showProgress />
                        </div>
                        <div className={`mt-4 flex ${preplanned ? 'justify-end' : 'justify-between'}`}>
                            {!preplanned && (
                                <Button variant="ghost" onClick={reset}>
                                    Import another
                                </Button>
                            )}
                            <Button
                                variant="primary"
                                onClick={() => router.push(`/pod/${imp.pod_id}`)}
                            >
                                Open your new pod <ArrowRight className="ml-1.5 h-4 w-4" />
                            </Button>
                        </div>
                    </>
                )}

                {phase === 'result' && imp && !(createdNewPod && imp.status === 'COMPLETED') && (
                    <>
                        {imp.status === 'COMPLETED' ? (
                            <div className="mb-5">
                                <RemixTakeover
                                    podId={imp.pod_id}
                                    podName={targetPod.data?.name}
                                    context="existing"
                                    hasApp={hasApp}
                                />
                            </div>
                        ) : applyImport.isPending ? (
                            // A resume in flight is an apply like any other —
                            // show the live step instead of the stale failure.
                            <p className="mb-4 flex items-center gap-2 text-sm text-[var(--text-tertiary)]">
                                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin" />
                                {runningStep ? (
                                    <span>
                                        Resuming {(liveImp ?? imp).progress_done + 1} of{' '}
                                        {(liveImp ?? imp).progress_total} —{' '}
                                        {runningStep.action === 'UPDATE' ? 'updating' : 'creating'}{' '}
                                        {SINGULAR[runningStep.resource_type] ?? runningStep.resource_type}{' '}
                                        “{runningStep.resource_name}”…
                                    </span>
                                ) : (
                                    <span>Resuming — already-imported steps are skipped.</span>
                                )}
                            </p>
                        ) : (
                            <div className="mb-4 flex items-center gap-2">
                                <CircleAlert className="h-5 w-5 text-[var(--state-error)]" />
                                <p className="text-base font-medium text-[var(--text-primary)]">
                                    Stopped at {imp.progress_done}/{imp.progress_total}
                                </p>
                            </div>
                        )}
                        {imp.status === 'FAILED' && !applyImport.isPending
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
                        <PlanList
                            imp={applyImport.isPending ? (liveImp ?? imp) : imp}
                            runningKey={runningKey}
                            showProgress
                        />
                        <div className={`flex ${preplanned ? 'justify-end' : 'justify-between'}`}>
                            {!preplanned && (
                                <Button variant="ghost" onClick={reset}>
                                    Import another
                                </Button>
                            )}
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
