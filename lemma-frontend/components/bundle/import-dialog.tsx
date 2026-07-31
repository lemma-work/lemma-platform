'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import {
    AlertTriangle,
    ArrowRight,
    Bot,
    CheckCircle2,
    Database,
    FileArchive,
    Files,
    Github,
    Loader2,
    PanelsTopLeft,
    Plug,
    Share2,
    Sparkles,
    Upload,
    Workflow,
    X,
} from '@/components/ui/icons';
import * as DialogPrimitive from '@radix-ui/react-dialog';

import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { showResourceErrorToast } from '@/components/shared/resource-feedback';
import { BundleProgressBar } from '@/components/bundle/bundle-progress';
import { AccountVariableField } from '@/components/bundle/account-variable-field';
import { ShareSheet } from '@/components/bundle/share-sheet';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { usePod } from '@/lib/hooks/use-pods';
import { cn } from '@/lib/utils';
import {
    applyImport,
    cancelImport,
    getImport,
    parseGithubRepo,
    startImport,
    trackBundleJob,
    uploadBundle,
    type BundleProgressView,
    type ImportPlan,
    type ImportStatusResponse,
    type PlanStep,
    type StepAction,
} from '@/lib/hooks/use-pod-bundle';

type Step = 'source' | 'planning' | 'review' | 'applying' | 'done' | 'error';
type SourceMode = 'upload' | 'github';

interface ImportDialogProps {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** Install into an existing pod. */
    podId?: string;
    podName?: string | null;
    /** Create a brand-new pod in this org, then import into it. */
    createNew?: { organizationId: string };
    /** Preset a GitHub source and skip the source step (e.g. from an import link). */
    presetGithub?: { owner: string; repo: string; ref?: string };
    /** Continue into the target pod after a successful import. */
    openPodOnComplete?: boolean;
    onCompleted?: (podId: string) => void;
}

type ImportSource =
    | { mode: 'upload'; file: File }
    | { mode: 'github'; owner: string; repo: string; ref?: string; accountId?: string };

const ACTION_STYLES: Record<StepAction, { label: string; className: string }> = {
    CREATE: { label: 'New', className: 'state-surface-success' },
    UPDATE: { label: 'Update', className: 'state-surface-warning' },
    SKIP: { label: 'Skip', className: 'text-[var(--text-tertiary)] bg-[var(--surface-2)]' },
};

function fileBaseName(name: string): string {
    return name.replace(/\.zip$/i, '').replace(/[_-]+/g, ' ').trim();
}

type ResourceGroupId = 'experience' | 'agents' | 'automation' | 'data' | 'connections' | 'other';

const RESOURCE_GROUPS: Array<{
    id: ResourceGroupId;
    title: string;
    kinds: string[];
    icon: typeof PanelsTopLeft;
}> = [
    {
        id: 'experience',
        title: 'Apps & surfaces',
        kinds: ['app', 'surface', 'agent_surface', 'desk', 'page'],
        icon: PanelsTopLeft,
    },
    {
        id: 'agents',
        title: 'Agents',
        kinds: ['agent', 'agent_grants', 'agent_grant'],
        icon: Bot,
    },
    {
        id: 'automation',
        title: 'Automations',
        kinds: ['function', 'workflow', 'schedule', 'trigger'],
        icon: Workflow,
    },
    {
        id: 'data',
        title: 'Data & knowledge',
        kinds: ['table', 'datastore', 'file', 'document'],
        icon: Database,
    },
    {
        id: 'connections',
        title: 'Connections',
        kinds: ['integration', 'connector', 'account', 'secret'],
        icon: Plug,
    },
];

function displayResourceName(value: string): string {
    return value.replace(/[_-]+/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function naturalList(values: string[], limit = 3): string {
    const selected = values.slice(0, limit).map(displayResourceName);
    if (selected.length === 0) return '';
    if (selected.length === 1) return selected[0];
    if (selected.length === 2) return `${selected[0]} and ${selected[1]}`;
    return `${selected.slice(0, -1).join(', ')}, and ${selected.at(-1)}`;
}

function uniqueNames(steps: PlanStep[], kinds: string[]): string[] {
    return Array.from(
        new Set(
            steps
                .filter((item) => kinds.includes(item.kind.toLowerCase()))
                .map((item) => item.name),
        ),
    );
}

function describeResourceGroup(id: ResourceGroupId, steps: PlanStep[]): string {
    const namesFor = (...kinds: string[]) => uniqueNames(steps, kinds);
    if (id === 'experience') {
        const apps = namesFor('app', 'desk', 'page');
        const surfaces = namesFor('surface', 'agent_surface');
        return [
            apps.length ? `${naturalList(apps)} becomes the interface your team opens.` : '',
            surfaces.length ? `${naturalList(surfaces)} connects the pod to where people already work.` : '',
        ]
            .filter(Boolean)
            .join(' ');
    }
    if (id === 'agents') {
        const agents = namesFor('agent');
        const grants = namesFor('agent_grants', 'agent_grant');
        return [
            agents.length ? `${naturalList(agents)} can take work inside the pod.` : '',
            grants.length ? 'Their resource permissions are included in this installation.' : '',
        ]
            .filter(Boolean)
            .join(' ');
    }
    if (id === 'automation') {
        const workflows = namesFor('workflow');
        const schedules = namesFor('schedule');
        const functions = namesFor('function');
        return [
            workflows.length ? `${naturalList(workflows, 2)} coordinate multi-step work.` : '',
            schedules.length ? `${naturalList(schedules, 2)} keep recurring work moving.` : '',
            functions.length
                ? `${functions.length} callable action${functions.length === 1 ? '' : 's'} support the pod.`
                : '',
        ]
            .filter(Boolean)
            .join(' ');
    }
    if (id === 'data') {
        const tables = namesFor('table', 'datastore');
        const files = namesFor('file', 'document');
        return [
            tables.length ? `${naturalList(tables)} hold the pod’s working data.` : '',
            files.length ? `${naturalList(files)} add shared context and playbooks.` : '',
        ]
            .filter(Boolean)
            .join(' ');
    }
    if (id === 'connections') {
        const connections = namesFor('integration', 'connector', 'account');
        return connections.length
            ? `${naturalList(connections)} connects the pod to external services.`
            : 'Connection configuration is included in the installation.';
    }
    return 'Additional supporting resources are included in the installation.';
}

function ResourceGroupCard({
    id,
    title,
    icon: Icon,
    steps,
}: {
    id: ResourceGroupId;
    title: string;
    icon: typeof PanelsTopLeft;
    steps: PlanStep[];
}) {
    const names = Array.from(new Set(steps.map((item) => item.name)));
    const created = steps.filter((item) => item.action === 'CREATE').length;
    const updated = steps.filter((item) => item.action === 'UPDATE').length;

    return (
        <section className="bundle-import-resource-group" data-group={id}>
            <div className="bundle-import-resource-group-header">
                <span className="bundle-import-resource-group-icon">
                    <Icon className="h-4 w-4" />
                </span>
                <div>
                    <h3>{title}</h3>
                    <span>
                        {created ? `${created} new` : ''}
                        {created && updated ? ' · ' : ''}
                        {updated ? `${updated} update${updated === 1 ? '' : 's'}` : ''}
                    </span>
                </div>
            </div>
            <p>{describeResourceGroup(id, steps)}</p>
            <div className="bundle-import-resource-names">
                {names.slice(0, 4).map((name) => (
                    <span key={name}>{displayResourceName(name)}</span>
                ))}
                {names.length > 4 ? <span>+{names.length - 4} more</span> : null}
            </div>
        </section>
    );
}

function InstallGroupRow({
    id,
    title,
    icon: Icon,
    steps,
}: {
    id: ResourceGroupId;
    title: string;
    icon: typeof PanelsTopLeft;
    steps: PlanStep[];
}) {
    const failed = steps.some((item) => item.status === 'FAILED');
    const complete = steps.every((item) => item.status === 'DONE');
    const active = !failed && !complete && steps.some((item) => item.status === 'RUNNING' || item.status === 'DONE');
    const state = failed ? 'failed' : complete ? 'complete' : active ? 'active' : 'pending';
    const descriptions: Record<ResourceGroupId, string> = {
        experience: 'Preparing the app and its surfaces',
        agents: 'Adding agents and their permissions',
        automation: 'Connecting functions, workflows and schedules',
        data: 'Creating shared data and knowledge',
        connections: 'Connecting external services',
        other: 'Adding supporting resources',
    };

    return (
        <div className="bundle-import-install-group" data-group={id} data-state={state}>
            <span className="bundle-import-install-group-icon">
                <Icon className="h-4 w-4" />
            </span>
            <span>
                <strong>{title}</strong>
                <small>{descriptions[id]}</small>
            </span>
            <span className="bundle-import-install-group-state">
                {failed ? (
                    <AlertTriangle className="h-4 w-4" />
                ) : complete ? (
                    <CheckCircle2 className="h-4 w-4" />
                ) : active ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                    <span />
                )}
            </span>
        </div>
    );
}

function StepRow({ step }: { step: PlanStep }) {
    const action = ACTION_STYLES[step.action] ?? ACTION_STYLES.SKIP;
    const running = step.status === 'RUNNING';
    const done = step.status === 'DONE';
    const failed = step.status === 'FAILED';
    return (
        <div className="flex items-center gap-2 py-1.5 text-sm">
            <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                {running ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin text-[var(--action-primary)]" />
                ) : done ? (
                    <CheckCircle2 className="h-3.5 w-3.5 text-[var(--state-success)]" />
                ) : failed ? (
                    <AlertTriangle className="h-3.5 w-3.5 text-[var(--state-error)]" />
                ) : (
                    <span className="h-1.5 w-1.5 rounded-full bg-[var(--border-strong)]" />
                )}
            </span>
            <span className="min-w-0 flex-1 truncate text-[var(--text-secondary)]">
                <span className="text-[var(--text-tertiary)]">{step.kind.toLowerCase()}</span>{' '}
                <span className="text-[var(--text-primary)]">{step.name}</span>
            </span>
            {step.destructive ? (
                <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-[var(--state-error)]" aria-label="Destructive" />
            ) : null}
            <span className={cn('shrink-0 rounded px-1.5 py-0.5 text-xs font-medium uppercase', action.className)}>
                {action.label}
            </span>
        </div>
    );
}

export function ImportDialog({
    open,
    onOpenChange,
    podId,
    podName,
    createNew,
    presetGithub,
    openPodOnComplete = false,
    onCompleted,
}: ImportDialogProps) {
    const router = useRouter();
    const queryClient = useQueryClient();

    const [step, setStep] = useState<Step>(presetGithub ? 'planning' : 'source');
    const [targetPodId, setTargetPodId] = useState<string | null>(podId ?? null);
    const [sourceMode, setSourceMode] = useState<SourceMode>('upload');
    const [file, setFile] = useState<File | null>(null);
    const [githubUrl, setGithubUrl] = useState('');
    const [githubAccountId, setGithubAccountId] = useState('');
    const [newPodName, setNewPodName] = useState('');
    const [plan, setPlan] = useState<ImportPlan | null>(null);
    const [variables, setVariables] = useState<Record<string, string>>({});
    const [confirmDestructive, setConfirmDestructive] = useState(false);
    const [busy, setBusy] = useState(false);
    const [progressLabel, setProgressLabel] = useState<string | null>(null);
    const [liveSteps, setLiveSteps] = useState<PlanStep[]>([]);
    const [applyView, setApplyView] = useState<BundleProgressView | null>(null);
    const [errorMessage, setErrorMessage] = useState<string | null>(null);
    const [shareOpen, setShareOpen] = useState(false);

    // Refs for cleanup of a create-new pod / staged import that never finished.
    const targetPodRef = useRef<string | null>(podId ?? null);
    const createdPodRef = useRef<string | null>(null);
    const importIdRef = useRef<string | null>(null);
    const eventsUrlRef = useRef<string | null>(null);
    const completedRef = useRef(false);
    const abortRef = useRef<AbortController | null>(null);
    const autoStartedRef = useRef(false);

    const isCreateNew = Boolean(createNew);

    const resetState = useCallback(() => {
        setStep(presetGithub ? 'planning' : 'source');
        setTargetPodId(podId ?? null);
        setSourceMode('upload');
        setFile(null);
        setGithubUrl('');
        setGithubAccountId('');
        setNewPodName('');
        setPlan(null);
        setVariables({});
        setConfirmDestructive(false);
        setBusy(false);
        setProgressLabel(null);
        setLiveSteps([]);
        setApplyView(null);
        setErrorMessage(null);
        targetPodRef.current = podId ?? null;
        createdPodRef.current = null;
        importIdRef.current = null;
        eventsUrlRef.current = null;
        completedRef.current = false;
        abortRef.current = null;
    }, [podId, presetGithub]);

    const cleanupUnfinished = useCallback(async () => {
        abortRef.current?.abort();
        const target = targetPodRef.current;
        const importId = importIdRef.current;
        if (target && importId && !completedRef.current) {
            try {
                await cancelImport(target, importId);
            } catch {
                /* best effort */
            }
        }
        // Delete the throwaway pod we created for a create-new import that failed.
        if (createdPodRef.current && !completedRef.current) {
            try {
                await getLemmaClient().pods.delete(createdPodRef.current);
                queryClient.invalidateQueries({ queryKey: ['pods'] });
            } catch {
                /* best effort */
            }
        }
    }, [queryClient]);

    const handleOpenChange = useCallback(
        (next: boolean) => {
            if (!next) {
                if (!completedRef.current) void cleanupUnfinished();
                onOpenChange(false);
                // Defer reset so the closing animation doesn't flash the source step.
                setTimeout(resetState, 200);
            } else {
                resetState();
                onOpenChange(true);
            }
        },
        [cleanupUnfinished, onOpenChange, resetState],
    );

    async function resolveTargetPod(source: ImportSource): Promise<string> {
        if (targetPodRef.current) return targetPodRef.current;
        if (podId) {
            targetPodRef.current = podId;
            return podId;
        }
        if (!createNew) throw new Error('No import target');
        const suggested =
            newPodName.trim() ||
            (source.mode === 'upload' ? fileBaseName(source.file.name) : source.repo) ||
            'Imported pod';
        const pod = (await getLemmaClient().pods.create({
            name: suggested,
            organization_id: createNew.organizationId,
        })) as { id: string };
        createdPodRef.current = pod.id;
        targetPodRef.current = pod.id;
        queryClient.invalidateQueries({ queryKey: ['pods'] });
        return pod.id;
    }

    async function beginImport(source: ImportSource) {
        if (busy) return;
        setErrorMessage(null);
        setBusy(true);
        setStep('planning');
        setProgressLabel(source.mode === 'github' ? 'Fetching repository…' : 'Uploading bundle…');
        const abort = new AbortController();
        abortRef.current = abort;
        try {
            const target = await resolveTargetPod(source);
            setTargetPodId(target);

            let started: ImportStatusResponse;
            if (source.mode === 'upload') {
                const uploaded = await uploadBundle(target, source.file);
                started = await startImport(target, { kind: 'URL', url: uploaded.url });
            } else {
                started = await startImport(target, {
                    kind: 'GITHUB',
                    owner: source.owner,
                    repo: source.repo,
                    ref: source.ref,
                    account_id: source.accountId || undefined,
                });
            }
            importIdRef.current = started.import_id;
            eventsUrlRef.current = started.events_url;

            setProgressLabel('Planning changes…');
            const planned = await trackBundleJob({
                podId: target,
                eventsUrl: started.events_url,
                fetchStatus: () => getImport(target, started.import_id),
                stopStatuses: ['AWAITING_CONFIRMATION', 'FAILED', 'CANCELLED', 'PARTIALLY_CANCELLED'],
                onProgress: (v) =>
                    setProgressLabel(v.status === 'FETCHING' ? 'Fetching bundle…' : 'Planning changes…'),
                signal: abort.signal,
            });

            if (
                source.mode === 'github' &&
                !source.accountId &&
                (planned.error_code === 'GITHUB_REPOSITORY_NOT_FOUND' ||
                    planned.error_code === 'GITHUB_IMPORT_UNAUTHORIZED')
            ) {
                setSourceMode('github');
                setGithubUrl(`github.com/${source.owner}/${source.repo}`);
                setErrorMessage('Select a GitHub account and retry. This may be a private repository.');
                setStep('source');
                return;
            }
            if (planned.status !== 'AWAITING_CONFIRMATION' || !planned.plan) {
                throw new Error(planned.error || 'Could not plan the import.');
            }

            // Seed variable defaults.
            const seeded: Record<string, string> = {};
            for (const v of planned.plan.variables) seeded[v.name] = v.default ?? '';
            setVariables(seeded);
            setPlan(planned.plan);
            setLiveSteps(planned.plan.steps);
            setStep('review');
        } catch (error) {
            if ((error as Error)?.name === 'AbortError') return;
            const message = error instanceof Error ? error.message : 'Import failed';
            setErrorMessage(message);
            setStep('error');
            showResourceErrorToast(error, 'Import failed');
        } finally {
            setBusy(false);
            setProgressLabel(null);
        }
    }

    function handleStart() {
        setErrorMessage(null);
        if (sourceMode === 'upload') {
            if (!file) {
                setErrorMessage('Choose a .zip bundle to import.');
                return;
            }
            void beginImport({ mode: 'upload', file });
            return;
        }
        const repo = parseGithubRepo(githubUrl);
        if (!repo) {
            setErrorMessage('Enter a GitHub repo, e.g. github.com/owner/repo.');
            return;
        }
        void beginImport({
            mode: 'github',
            owner: repo.owner,
            repo: repo.repo,
            accountId: githubAccountId || undefined,
        });
    }

    // Preset source (import link) → skip the picker and plan immediately on open.
    useEffect(() => {
        if (!open) {
            autoStartedRef.current = false;
            return;
        }
        if (presetGithub && !autoStartedRef.current) {
            autoStartedRef.current = true;
            void beginImport({
                mode: 'github',
                owner: presetGithub.owner,
                repo: presetGithub.repo,
                ref: presetGithub.ref,
            });
        }
        // beginImport is intentionally excluded — it closes over fresh state each
        // render; the autoStartedRef guard makes this fire exactly once per open.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, presetGithub]);

    async function handleApply() {
        const target = targetPodRef.current;
        const importId = importIdRef.current;
        const eventsUrl = eventsUrlRef.current;
        if (!target || !importId || !eventsUrl || busy || !plan) return;

        // Required variables must be filled.
        const missing = plan.variables.filter((v) => v.required && !variables[v.name]?.trim());
        if (missing.length > 0) {
            setErrorMessage(`Fill required values: ${missing.map((v) => v.name).join(', ')}`);
            return;
        }
        if (plan.has_destructive_steps && !confirmDestructive) {
            setErrorMessage('Confirm the destructive changes to continue.');
            return;
        }

        setBusy(true);
        setErrorMessage(null);
        setLiveSteps(plan.steps);
        setApplyView({ status: 'APPLYING', done: 0, total: plan.steps.length });
        const abort = new AbortController();
        abortRef.current = abort;
        try {
            await applyImport(target, importId, {
                variables,
                confirm_destructive: confirmDestructive,
            });
            setStep('applying');
            const final = await trackBundleJob({
                podId: target,
                eventsUrl,
                fetchStatus: () => getImport(target, importId),
                stopStatuses: ['COMPLETED', 'FAILED', 'CANCELLED', 'PARTIALLY_CANCELLED'],
                onProgress: setApplyView,
                onFrame: (frame) => {
                    if (frame.type === 'step' && typeof frame.step.index === 'number') {
                        setLiveSteps((prev) =>
                            prev.map((s) =>
                                s.index === frame.step.index
                                    ? { ...s, status: frame.step.status ?? s.status, error: frame.step.error ?? s.error }
                                    : s,
                            ),
                        );
                    }
                },
                signal: abort.signal,
            });

            if (final.status !== 'COMPLETED') {
                throw new Error(final.error || 'Apply failed');
            }
            if (final.plan) setLiveSteps(final.plan.steps);

            completedRef.current = true;
            createdPodRef.current = null; // keep the pod — it's real now
            queryClient.invalidateQueries({ queryKey: ['pods'] });
            queryClient.invalidateQueries({ queryKey: ['pods', target] });
            setStep('done');
        } catch (error) {
            if ((error as Error)?.name === 'AbortError') return;
            const message = error instanceof Error ? error.message : 'Apply failed';
            setErrorMessage(message);
            setStep('error');
            showResourceErrorToast(error, 'Apply failed');
        } finally {
            setBusy(false);
        }
    }

    function handleFinish() {
        const target = targetPodRef.current;
        completedRef.current = true;
        onOpenChange(false);
        setTimeout(resetState, 200);
        if (target) {
            onCompleted?.(target);
            if (isCreateNew || openPodOnComplete) router.push(`/pod/${target}`);
            else router.refresh();
        }
    }

    // Organization for connector-account variables (create-new knows it upfront;
    // install-here derives it from the target pod).
    const { data: targetPod } = usePod(createNew ? undefined : targetPodId ?? undefined);
    const organizationId = createNew?.organizationId ?? targetPod?.organization_id ?? undefined;

    // ---- render helpers ----
    const planSteps = liveSteps.length > 0 ? liveSteps : plan?.steps ?? [];
    const counts = planSteps.reduce(
        (acc, s) => {
            acc[s.action] = (acc[s.action] ?? 0) + 1;
            return acc;
        },
        {} as Record<string, number>,
    );
    const changingSteps = planSteps.filter((item) => item.action !== 'SKIP');
    const resourceCount = changingSteps.length;
    const groupedKinds = new Set(RESOURCE_GROUPS.flatMap((group) => group.kinds));
    const contextualGroups = RESOURCE_GROUPS.map((group) => ({
        ...group,
        steps: changingSteps.filter((item) => group.kinds.includes(item.kind.toLowerCase())),
    })).filter((group) => group.steps.length > 0);
    const otherSteps = changingSteps.filter((item) => !groupedKinds.has(item.kind.toLowerCase()));
    if (otherSteps.length > 0) {
        contextualGroups.push({
            id: 'other',
            title: 'Supporting resources',
            kinds: [],
            icon: Files,
            steps: otherSteps,
        });
    }
    const targetLabel = isCreateNew
        ? newPodName.trim() || plan?.bundle_name || presetGithub?.repo || 'a new pod'
        : podName || 'this pod';
    const isFetchingBundle = /fetching|uploading/i.test(progressLabel ?? '');
    const phase =
        step === 'source' || step === 'planning'
            ? 0
            : step === 'review'
              ? 1
              : step === 'applying'
                ? 2
                : step === 'done'
                  ? 3
                  : 1;
    const dialogTitle =
        step === 'review'
            ? 'Review installation'
            : step === 'applying'
              ? 'Installing pod'
              : step === 'done'
                ? 'Installation complete'
                : step === 'error'
                  ? 'Installation stopped'
                  : presetGithub
                    ? 'Preparing installation'
                    : isCreateNew
                      ? 'Import a pod'
                      : `Install into ${targetLabel}`;

    return (
        <>
        <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
            <DialogPrimitive.Portal>
                <DialogPrimitive.Overlay className="scrim-overlay fixed inset-0 z-50 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
                <DialogPrimitive.Content className="bundle-import-dialog text-[var(--text-primary)] outline-none">
                    <header className="bundle-import-dialog-header">
                        <div className="min-w-0">
                            <DialogPrimitive.Title className="truncate text-base font-semibold text-[var(--text-primary)]">
                                {dialogTitle}
                            </DialogPrimitive.Title>
                            <DialogPrimitive.Description className="mt-0.5 truncate text-xs text-[var(--text-tertiary)]">
                                {presetGithub
                                    ? `${presetGithub.owner}/${presetGithub.repo} → ${targetLabel}`
                                    : isCreateNew
                                      ? 'Create a new pod from a .zip bundle or GitHub repository.'
                                      : `Add resources to ${targetLabel}. Existing resources update in place.`}
                            </DialogPrimitive.Description>
                        </div>
                        <DialogPrimitive.Close
                            className="lemma-shell-icon-button custom-focus-ring h-9 w-9 shrink-0 text-[var(--text-tertiary)]"
                            aria-label="Close"
                        >
                            <X className="h-4 w-4" />
                        </DialogPrimitive.Close>
                    </header>

                    <div className="bundle-import-progress" aria-label="Installation progress">
                        {['Source', 'Review', 'Install'].map((label, index) => (
                            <div
                                key={label}
                                className={cn(
                                    'bundle-import-progress-step',
                                    index < phase && 'is-complete',
                                    index === phase && step !== 'done' && 'is-active',
                                    step === 'done' && 'is-complete',
                                )}
                            >
                                <span>{index < phase || step === 'done' ? <CheckCircle2 className="h-3.5 w-3.5" /> : index + 1}</span>
                                <strong>{label}</strong>
                            </div>
                        ))}
                    </div>

                    <div className="bundle-import-dialog-body">
                        <div className="bundle-import-stage">
                    {/* --- SOURCE --- */}
                    {step === 'source' ? (
                        <div className="space-y-4">
                            <div className="grid grid-cols-2 gap-2">
                                <button
                                    type="button"
                                    onClick={() => setSourceMode('upload')}
                                    className={cn(
                                        'flex items-center gap-2 rounded-lg border p-3 text-sm transition-colors',
                                        sourceMode === 'upload'
                                            ? 'border-[var(--action-primary)] bg-[var(--surface-2)] text-[var(--text-primary)]'
                                            : 'border-[var(--border-subtle)] text-[var(--text-secondary)] hover:bg-[var(--surface-2)]',
                                    )}
                                >
                                    <FileArchive className="h-4 w-4" />
                                    Upload .zip
                                </button>
                                <button
                                    type="button"
                                    onClick={() => setSourceMode('github')}
                                    className={cn(
                                        'flex items-center gap-2 rounded-lg border p-3 text-sm transition-colors',
                                        sourceMode === 'github'
                                            ? 'border-[var(--action-primary)] bg-[var(--surface-2)] text-[var(--text-primary)]'
                                            : 'border-[var(--border-subtle)] text-[var(--text-secondary)] hover:bg-[var(--surface-2)]',
                                    )}
                                >
                                    <Github className="h-4 w-4" />
                                    From GitHub
                                </button>
                            </div>

                            {sourceMode === 'upload' ? (
                                <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-lg border border-dashed border-[var(--border-strong)] bg-[var(--surface-1)] px-4 py-8 text-center transition-colors hover:bg-[var(--surface-2)]">
                                    <Upload className="h-6 w-6 text-[var(--text-tertiary)]" />
                                    <span className="text-sm text-[var(--text-secondary)]">
                                        {file ? file.name : 'Click to choose a .zip bundle'}
                                    </span>
                                    <input
                                        type="file"
                                        accept=".zip,application/zip"
                                        className="hidden"
                                        onChange={(e) => {
                                            const chosen = e.target.files?.[0] ?? null;
                                            setFile(chosen);
                                            if (chosen && !newPodName) setNewPodName(fileBaseName(chosen.name));
                                        }}
                                    />
                                </label>
                            ) : (
                                <div className="space-y-3">
                                    <div className="space-y-1.5">
                                        <Label htmlFor="import-github-url" className="text-xs">
                                            GitHub repository
                                        </Label>
                                        <Input
                                            id="import-github-url"
                                            value={githubUrl}
                                            onChange={(e) => {
                                                setGithubUrl(e.target.value);
                                                const repo = parseGithubRepo(e.target.value);
                                                if (repo && !newPodName) setNewPodName(repo.repo);
                                            }}
                                            placeholder="github.com/owner/repo"
                                        />
                                    </div>
                                    <AccountVariableField
                                        organizationId={organizationId}
                                        podId={targetPodId}
                                        connectorId="github"
                                        provider="COMPOSIO"
                                        label="GitHub account"
                                        description="Optional for public repositories; required for private repositories and higher rate limits."
                                        value={githubAccountId}
                                        onChange={setGithubAccountId}
                                    />
                                </div>
                            )}

                            {isCreateNew ? (
                                <div className="space-y-1.5">
                                    <Label htmlFor="import-pod-name" className="text-xs">
                                        New pod name
                                    </Label>
                                    <Input
                                        id="import-pod-name"
                                        value={newPodName}
                                        onChange={(e) => setNewPodName(e.target.value)}
                                        placeholder="Imported pod"
                                    />
                                </div>
                            ) : null}

                            {errorMessage ? (
                                <p className="text-sm text-[var(--state-error)]">{errorMessage}</p>
                            ) : null}

                            <Button className="w-full" onClick={handleStart} loading={busy} loadingLabel="Preparing…">
                                Continue
                                <ArrowRight className="ml-2 h-4 w-4" />
                            </Button>
                        </div>
                    ) : null}

                    {/* --- PLANNING --- */}
                    {step === 'planning' ? (
                        <div className="bundle-import-planning">
                            <div className="bundle-import-planning-sheet">
                                <span className="bundle-import-tape" aria-hidden="true" />
                                <div className="bundle-import-planning-sticker">
                                    <Sparkles className="h-5 w-5" />
                                </div>
                                <div>
                                    <p className="bundle-import-section-label">Preparing your review</p>
                                    <h2>Making sense of the bundle</h2>
                                    <p>
                                        Comparing what’s in the repository with <strong>{targetLabel}</strong>.
                                    </p>
                                </div>
                                <ol className="bundle-import-planning-list">
                                    <li data-state={isFetchingBundle ? 'active' : 'complete'}>
                                        <span>
                                            {isFetchingBundle ? (
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                            ) : (
                                                <CheckCircle2 className="h-4 w-4" />
                                            )}
                                        </span>
                                        <div>
                                            <strong>Read the bundle</strong>
                                            <small>{progressLabel ?? 'Repository ready'}</small>
                                        </div>
                                    </li>
                                    <li data-state={isFetchingBundle ? 'pending' : 'active'}>
                                        <span>
                                            {!isFetchingBundle ? (
                                                <Loader2 className="h-4 w-4 animate-spin" />
                                            ) : null}
                                        </span>
                                        <div>
                                            <strong>Compare with {targetLabel}</strong>
                                            <small>Find what will be created or updated</small>
                                        </div>
                                    </li>
                                    <li data-state="pending">
                                        <span />
                                        <div>
                                            <strong>Prepare a clear review</strong>
                                            <small>Group the app, agents, automations and data</small>
                                        </div>
                                    </li>
                                </ol>
                            </div>
                        </div>
                    ) : null}

                    {/* --- REVIEW --- */}
                    {step === 'review' && plan ? (
                        <div className="bundle-import-review-layout">
                            <div className="bundle-import-review-main">
                                <div className="bundle-import-review-summary">
                                    <p className="bundle-import-section-label">What this pod adds</p>
                                    <h2>Review the working parts, not just the files</h2>
                                    <p>
                                        This is how <strong>{plan.bundle_name || presetGithub?.repo || 'the pod'}</strong>{' '}
                                        will show up inside <strong>{targetLabel}</strong>.
                                    </p>
                                </div>

                                <div className="bundle-import-resource-groups">
                                    {contextualGroups.map((group) => (
                                        <ResourceGroupCard
                                            key={group.id}
                                            id={group.id}
                                            title={group.title}
                                            icon={group.icon}
                                            steps={group.steps}
                                        />
                                    ))}
                                </div>

                                <details className="bundle-import-plan-details">
                                    <summary>
                                        <span>View all technical changes</span>
                                        <span>{planSteps.length}</span>
                                    </summary>
                                    <div className="bundle-import-step-list">
                                        {planSteps.map((resourceStep) => (
                                            <StepRow
                                                key={`${resourceStep.kind}-${resourceStep.index}`}
                                                step={resourceStep}
                                            />
                                        ))}
                                    </div>
                                </details>

                                {plan.warnings.length > 0 ? (
                                    <div className="bundle-import-warnings">
                                        <AlertTriangle className="h-4 w-4 shrink-0" />
                                        <div>
                                            {plan.warnings.map((warning, index) => (
                                                <p key={index}>{warning}</p>
                                            ))}
                                        </div>
                                    </div>
                                ) : null}

                                {plan.variables.length > 0 ? (
                                    <div className="bundle-import-configuration">
                                        <div>
                                            <p className="bundle-import-section-label">Configuration</p>
                                            <p className="text-xs text-[var(--text-tertiary)]">
                                                Complete the values this pod needs before installation.
                                            </p>
                                        </div>
                                        {plan.variables.map((variable) => {
                                            const setValue = (value: string) =>
                                                setVariables((previous) => ({
                                                    ...previous,
                                                    [variable.name]: value,
                                                }));

                                            if (variable.kind === 'account' && variable.connector) {
                                                return (
                                                    <AccountVariableField
                                                        key={variable.name}
                                                        organizationId={organizationId}
                                                        podId={targetPodId}
                                                        connectorId={variable.connector}
                                                        provider={variable.provider}
                                                        label={variable.name}
                                                        description={variable.description}
                                                        required={variable.required}
                                                        value={variables[variable.name] ?? ''}
                                                        onChange={setValue}
                                                    />
                                                );
                                            }

                                            const secret = /secret|password|token|key/i.test(variable.kind);
                                            return (
                                                <div key={variable.name} className="space-y-1">
                                                    <Label htmlFor={`var-${variable.name}`} className="text-xs">
                                                        {variable.name}
                                                        {variable.required ? (
                                                            <span className="text-[var(--state-error)]"> *</span>
                                                        ) : null}
                                                    </Label>
                                                    {variable.description ? (
                                                        <p className="text-xs text-[var(--text-tertiary)]">
                                                            {variable.description}
                                                        </p>
                                                    ) : null}
                                                    <Input
                                                        id={`var-${variable.name}`}
                                                        type={secret ? 'password' : 'text'}
                                                        value={variables[variable.name] ?? ''}
                                                        onChange={(event) => setValue(event.target.value)}
                                                        placeholder={variable.default ?? ''}
                                                    />
                                                </div>
                                            );
                                        })}
                                    </div>
                                ) : null}

                                {plan.has_destructive_steps ? (
                                    <label className="bundle-import-destructive">
                                        <Checkbox
                                            checked={confirmDestructive}
                                            onCheckedChange={(value) => setConfirmDestructive(value === true)}
                                            className="mt-0.5"
                                        />
                                        <span className="text-xs text-[var(--text-secondary)]">
                                            Some steps remove columns or data that exist today. I understand and want
                                            to proceed.
                                        </span>
                                    </label>
                                ) : null}

                                {errorMessage ? (
                                    <p className="text-sm text-[var(--state-error)]">{errorMessage}</p>
                                ) : null}
                            </div>

                            <aside className="bundle-import-review-aside">
                                <div>
                                    <p className="bundle-import-section-label">Install into</p>
                                    <h3>{targetLabel}</h3>
                                </div>

                                <div className="bundle-import-review-route">
                                    <span>
                                        <Github className="h-4 w-4" />
                                        {plan.bundle_name || presetGithub?.repo || 'Bundle'}
                                    </span>
                                    <ArrowRight className="h-4 w-4" />
                                    <span>
                                        <Database className="h-4 w-4" />
                                        {targetLabel}
                                    </span>
                                </div>

                                <div className="bundle-import-review-impact" aria-label="Resource change summary">
                                    <span>
                                        <strong>{resourceCount}</strong>
                                        resources changing
                                    </span>
                                    {counts.CREATE ? (
                                        <span>
                                            <strong>{counts.CREATE}</strong>
                                            created
                                        </span>
                                    ) : null}
                                    {counts.UPDATE ? (
                                        <span>
                                            <strong>{counts.UPDATE}</strong>
                                            updated in place
                                        </span>
                                    ) : null}
                                </div>

                                <Button
                                    className="w-full"
                                    onClick={handleApply}
                                    loading={busy}
                                    loadingLabel="Installing…"
                                    disabled={plan.has_destructive_steps && !confirmDestructive}
                                >
                                    {isCreateNew ? 'Create and install' : `Install ${resourceCount} resources`}
                                    <ArrowRight className="ml-2 h-4 w-4" />
                                </Button>
                                <Button
                                    variant="secondary"
                                    className="w-full"
                                    onClick={() => handleOpenChange(false)}
                                >
                                    Cancel
                                </Button>
                                <p className="bundle-import-review-assurance">
                                    Nothing changes until you confirm. Updates modify matching resources in place.
                                </p>
                            </aside>
                        </div>
                    ) : null}

                    {/* --- APPLYING --- */}
                    {step === 'applying' ? (
                        <div className="bundle-import-applying">
                            <div className="bundle-import-applying-header">
                                <div>
                                    <p className="bundle-import-section-label">Installation in progress</p>
                                    <h2>Setting up {plan?.bundle_name || presetGithub?.repo || 'your pod'}</h2>
                                    <p>The working parts are being added to {targetLabel}.</p>
                                </div>
                                <span className="bundle-import-applying-stamp">
                                    <Sparkles className="h-4 w-4" />
                                    In progress
                                </span>
                            </div>
                            <BundleProgressBar
                                done={applyView?.done ?? 0}
                                total={applyView?.total ?? planSteps.length}
                                label="Installing resources…"
                            />
                            <div className="bundle-import-install-layout">
                                <div className="bundle-import-install-groups">
                                    {contextualGroups.map((group) => (
                                        <InstallGroupRow
                                            key={group.id}
                                            id={group.id}
                                            title={group.title}
                                            icon={group.icon}
                                            steps={group.steps}
                                        />
                                    ))}
                                </div>
                                <details className="bundle-import-plan-details bundle-import-install-details">
                                    <summary>
                                        <span>View technical progress</span>
                                        <span>{planSteps.length}</span>
                                    </summary>
                                    <div className="bundle-import-step-list">
                                        {planSteps.map((resourceStep) => (
                                            <StepRow
                                                key={`${resourceStep.kind}-${resourceStep.index}`}
                                                step={resourceStep}
                                            />
                                        ))}
                                    </div>
                                </details>
                            </div>
                        </div>
                    ) : null}

                    {/* --- DONE --- */}
                    {step === 'done' ? (
                        <div className="bundle-import-done">
                            <div className="bundle-import-done-paper">
                                <span className="bundle-import-tape" aria-hidden="true" />
                                <div className="bundle-import-done-icon">
                                    <CheckCircle2 className="h-7 w-7" />
                                </div>
                                <div>
                                    <p className="bundle-import-section-label">Ready to use</p>
                                    <h2>{plan?.bundle_name || presetGithub?.repo || 'Pod'} is ready</h2>
                                    <p>
                                        Installed {resourceCount} resource{resourceCount === 1 ? '' : 's'} in{' '}
                                        <strong>{targetLabel}</strong>.
                                    </p>
                                </div>
                                <div className="bundle-import-next-step">
                                    <div>
                                        <strong>Next</strong>
                                        <span>Open the pod, confirm the app works, then invite someone into it.</span>
                                    </div>
                                    <Button onClick={handleFinish}>
                                        {isCreateNew || openPodOnComplete ? 'Open pod' : 'Done'}
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </div>
                                <Button variant="secondary" onClick={() => setShareOpen(true)}>
                                    <Share2 className="mr-2 h-4 w-4" />
                                    Share this pod
                                </Button>
                            </div>
                        </div>
                    ) : null}

                    {/* --- ERROR --- */}
                    {step === 'error' ? (
                        <div className="flex flex-col items-center gap-3 py-8 text-center">
                            <div className="state-surface-error flex h-12 w-12 items-center justify-center rounded-full">
                                <AlertTriangle className="h-6 w-6 text-[var(--state-error)]" />
                            </div>
                            <p className="text-sm text-[var(--text-secondary)]">
                                {errorMessage ?? 'Something went wrong.'}
                            </p>
                            <div className="flex w-full gap-2">
                                <Button variant="secondary" className="flex-1" onClick={() => handleOpenChange(false)}>
                                    Close
                                </Button>
                                <Button
                                    className="flex-1"
                                    onClick={() => {
                                        setErrorMessage(null);
                                        setStep('source');
                                    }}
                                >
                                    Try again
                                </Button>
                            </div>
                        </div>
                    ) : null}
                        </div>
                    </div>
                </DialogPrimitive.Content>
            </DialogPrimitive.Portal>
        </DialogPrimitive.Root>
        {targetPodId ? (
            <ShareSheet
                podId={targetPodId}
                podName={plan?.bundle_name ?? podName}
                open={shareOpen}
                onOpenChange={setShareOpen}
            />
        ) : null}
        </>
    );
}
