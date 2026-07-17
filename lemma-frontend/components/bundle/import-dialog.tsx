'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useQueryClient } from '@tanstack/react-query';
import {
    AlertTriangle,
    ArrowRight,
    CheckCircle2,
    FileArchive,
    Github,
    Loader2,
    MessageCircle,
    PanelsTopLeft,
    Share2,
    Sparkles,
    Upload,
    Wrench,
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
    onCompleted?: (podId: string) => void;
}

type ImportSource =
    | { mode: 'upload'; file: File }
    | { mode: 'github'; owner: string; repo: string; ref?: string };

const ACTION_STYLES: Record<StepAction, { label: string; className: string }> = {
    CREATE: { label: 'New', className: 'state-surface-success' },
    UPDATE: { label: 'Update', className: 'state-surface-warning' },
    SKIP: { label: 'Skip', className: 'text-[var(--text-tertiary)] bg-[var(--surface-2)]' },
};

function fileBaseName(name: string): string {
    return name.replace(/\.zip$/i, '').replace(/[_-]+/g, ' ').trim();
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
    onCompleted,
}: ImportDialogProps) {
    const router = useRouter();
    const queryClient = useQueryClient();

    const [step, setStep] = useState<Step>(presetGithub ? 'planning' : 'source');
    const [targetPodId, setTargetPodId] = useState<string | null>(podId ?? null);
    const [sourceMode, setSourceMode] = useState<SourceMode>('upload');
    const [file, setFile] = useState<File | null>(null);
    const [githubUrl, setGithubUrl] = useState('');
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
                });
            }
            importIdRef.current = started.import_id;
            eventsUrlRef.current = started.events_url;

            setProgressLabel('Planning changes…');
            const planned = await trackBundleJob({
                podId: target,
                eventsUrl: started.events_url,
                fetchStatus: () => getImport(target, started.import_id),
                stopStatuses: ['AWAITING_CONFIRMATION', 'FAILED', 'CANCELLED'],
                onProgress: (v) =>
                    setProgressLabel(v.status === 'FETCHING' ? 'Fetching bundle…' : 'Planning changes…'),
                signal: abort.signal,
            });

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
        void beginImport({ mode: 'github', owner: repo.owner, repo: repo.repo });
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
                stopStatuses: ['COMPLETED', 'FAILED', 'CANCELLED'],
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
            if (isCreateNew) router.push(`/pod/${target}`);
            else router.refresh();
        }
    }

    // Navigate somewhere inside the freshly-imported pod, closing the wizard.
    function navigateTo(path: string) {
        const target = targetPodRef.current;
        completedRef.current = true;
        onOpenChange(false);
        setTimeout(resetState, 200);
        if (target) {
            onCompleted?.(target);
            router.push(path);
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

    return (
        <>
        <DialogPrimitive.Root open={open} onOpenChange={handleOpenChange}>
            <DialogPrimitive.Portal>
                <DialogPrimitive.Overlay className="scrim-overlay fixed inset-0 z-50 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0" />
                <DialogPrimitive.Content className="fixed inset-0 z-50 flex flex-col bg-[var(--surface-1)] text-[var(--text-primary)] outline-none data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0">
                    <header className="flex h-14 shrink-0 items-center justify-between gap-4 border-b border-[var(--border-subtle)] px-4 sm:px-6">
                        <div className="min-w-0">
                            <DialogPrimitive.Title className="truncate text-sm font-medium text-[var(--text-primary)]">
                                {isCreateNew ? 'Import a pod' : `Install into ${podName || 'this pod'}`}
                            </DialogPrimitive.Title>
                            <DialogPrimitive.Description className="truncate text-xs text-[var(--text-tertiary)]">
                                {isCreateNew
                                    ? 'Create a new pod from a bundle — a .zip or a GitHub repo.'
                                    : 'Add resources from a bundle into this pod. Existing resources update in place.'}
                            </DialogPrimitive.Description>
                        </div>
                        <DialogPrimitive.Close
                            className="lemma-shell-icon-button custom-focus-ring h-9 w-9 shrink-0 text-[var(--text-tertiary)]"
                            aria-label="Close"
                        >
                            <X className="h-4 w-4" />
                        </DialogPrimitive.Close>
                    </header>

                    <div className="flex-1 overflow-y-auto">
                        <div className="mx-auto w-full max-w-2xl px-4 py-8 sm:px-6">
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
                                <div className="space-y-1.5">
                                    <Label htmlFor="import-github-url" className="text-xs">
                                        Public GitHub repository
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
                        <div className="flex flex-col items-center justify-center gap-3 py-12 text-center">
                            <Loader2 className="h-6 w-6 animate-spin text-[var(--action-primary)]" />
                            <p className="text-sm text-[var(--text-secondary)]">{progressLabel ?? 'Planning…'}</p>
                        </div>
                    ) : null}

                    {/* --- REVIEW --- */}
                    {step === 'review' && plan ? (
                        <div className="space-y-4">
                            <div className="flex flex-wrap gap-2 text-xs">
                                {(['CREATE', 'UPDATE', 'SKIP'] as StepAction[]).map((a) =>
                                    counts[a] ? (
                                        <span
                                            key={a}
                                            className={cn('rounded px-2 py-0.5 font-medium', ACTION_STYLES[a].className)}
                                        >
                                            {counts[a]} {ACTION_STYLES[a].label.toLowerCase()}
                                        </span>
                                    ) : null,
                                )}
                            </div>

                            <div className="max-h-52 divide-y divide-[var(--border-subtle)] overflow-y-auto rounded-lg border border-[var(--border-subtle)] px-3">
                                {planSteps.map((s) => (
                                    <StepRow key={`${s.kind}-${s.index}`} step={s} />
                                ))}
                            </div>

                            {plan.warnings.length > 0 ? (
                                <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] p-3 text-xs text-[var(--text-secondary)]">
                                    {plan.warnings.map((w, i) => (
                                        <p key={i}>{w}</p>
                                    ))}
                                </div>
                            ) : null}

                            {plan.variables.length > 0 ? (
                                <div className="space-y-3">
                                    <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-tertiary)]">
                                        Configuration
                                    </p>
                                    {plan.variables.map((v) => {
                                        const setValue = (val: string) =>
                                            setVariables((prev) => ({ ...prev, [v.name]: val }));

                                        if (v.kind === 'account' && v.connector) {
                                            return (
                                                <AccountVariableField
                                                    key={v.name}
                                                    organizationId={organizationId}
                                                    podId={targetPodId}
                                                    connectorId={v.connector}
                                                    provider={v.provider}
                                                    label={v.name}
                                                    description={v.description}
                                                    required={v.required}
                                                    value={variables[v.name] ?? ''}
                                                    onChange={setValue}
                                                />
                                            );
                                        }

                                        const secret = /secret|password|token|key/i.test(v.kind);
                                        return (
                                            <div key={v.name} className="space-y-1">
                                                <Label htmlFor={`var-${v.name}`} className="text-xs">
                                                    {v.name}
                                                    {v.required ? <span className="text-[var(--state-error)]"> *</span> : null}
                                                </Label>
                                                {v.description ? (
                                                    <p className="text-xs text-[var(--text-tertiary)]">{v.description}</p>
                                                ) : null}
                                                <Input
                                                    id={`var-${v.name}`}
                                                    type={secret ? 'password' : 'text'}
                                                    value={variables[v.name] ?? ''}
                                                    onChange={(e) => setValue(e.target.value)}
                                                    placeholder={v.default ?? ''}
                                                />
                                            </div>
                                        );
                                    })}
                                </div>
                            ) : null}

                            {plan.has_destructive_steps ? (
                                <label className="state-surface-error flex items-start gap-2 rounded-md p-3">
                                    <Checkbox
                                        checked={confirmDestructive}
                                        onCheckedChange={(v) => setConfirmDestructive(v === true)}
                                        className="mt-0.5"
                                    />
                                    <span className="text-xs text-[var(--text-secondary)]">
                                        Some steps remove columns or data that exist today. I understand and want to
                                        proceed.
                                    </span>
                                </label>
                            ) : null}

                            {errorMessage ? (
                                <p className="text-sm text-[var(--state-error)]">{errorMessage}</p>
                            ) : null}

                            <div className="flex gap-2">
                                <Button variant="secondary" className="flex-1" onClick={() => handleOpenChange(false)}>
                                    Cancel
                                </Button>
                                <Button
                                    className="flex-1"
                                    onClick={handleApply}
                                    loading={busy}
                                    loadingLabel="Applying…"
                                    disabled={plan.has_destructive_steps && !confirmDestructive}
                                >
                                    {isCreateNew ? 'Create pod' : 'Apply'}
                                </Button>
                            </div>
                        </div>
                    ) : null}

                    {/* --- APPLYING --- */}
                    {step === 'applying' ? (
                        <div className="space-y-4">
                            <BundleProgressBar
                                done={applyView?.done ?? 0}
                                total={applyView?.total ?? planSteps.length}
                                label="Applying resources…"
                            />
                            <div className="max-h-64 divide-y divide-[var(--border-subtle)] overflow-y-auto rounded-lg border border-[var(--border-subtle)] px-3">
                                {planSteps.map((s) => (
                                    <StepRow key={`${s.kind}-${s.index}`} step={s} />
                                ))}
                            </div>
                        </div>
                    ) : null}

                    {/* --- DONE (the "it's yours" takeover) --- */}
                    {step === 'done' ? (
                        <div className="flex flex-col items-center gap-6 py-6 text-center">
                            <div className="flex flex-col items-center gap-3">
                                <div className="state-surface-success flex h-14 w-14 items-center justify-center rounded-full">
                                    <Sparkles className="h-7 w-7 text-[var(--state-success)]" />
                                </div>
                                <div>
                                    <p className="text-xl font-medium text-[var(--text-primary)]">
                                        {isCreateNew
                                            ? `${plan?.bundle_name || podName || 'Your pod'} is yours`
                                            : 'Installed'}
                                    </p>
                                    <p className="mt-1 text-sm text-[var(--text-tertiary)]">
                                        {isCreateNew
                                            ? 'Open it, make it your own, then pass it on.'
                                            : 'The bundle was applied to this pod.'}
                                    </p>
                                </div>
                            </div>

                            {isCreateNew ? (
                                <div className="grid w-full grid-cols-2 gap-3">
                                    {[
                                        {
                                            icon: PanelsTopLeft,
                                            title: 'Open the app',
                                            hint: 'See what it does',
                                            onClick: () => navigateTo(`/pod/${targetPodId}/app/pages`),
                                        },
                                        {
                                            icon: MessageCircle,
                                            title: 'Activate a surface',
                                            hint: 'Slack, Telegram, more',
                                            onClick: () => navigateTo(`/pod/${targetPodId}/surfaces`),
                                        },
                                        {
                                            icon: Share2,
                                            title: 'Share with a friend',
                                            hint: 'Pass the pod on',
                                            onClick: () => setShareOpen(true),
                                        },
                                        {
                                            icon: Wrench,
                                            title: 'Customize',
                                            hint: 'Tweak it in chat',
                                            onClick: () => navigateTo(`/pod/${targetPodId}`),
                                        },
                                    ].map((action) => (
                                        <button
                                            key={action.title}
                                            type="button"
                                            onClick={action.onClick}
                                            className="flex flex-col items-start gap-2 rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-4 text-left transition-colors hover:border-[var(--action-primary)] hover:bg-[var(--surface-2)]"
                                        >
                                            <action.icon className="h-5 w-5 text-[var(--action-primary)]" />
                                            <div>
                                                <div className="text-sm font-medium text-[var(--text-primary)]">
                                                    {action.title}
                                                </div>
                                                <div className="text-xs text-[var(--text-tertiary)]">{action.hint}</div>
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            ) : (
                                <div className="flex w-full gap-2">
                                    <Button variant="secondary" className="flex-1" onClick={() => setShareOpen(true)}>
                                        <Share2 className="mr-2 h-4 w-4" />
                                        Share
                                    </Button>
                                    <Button className="flex-1" onClick={handleFinish}>
                                        Done
                                        <ArrowRight className="ml-2 h-4 w-4" />
                                    </Button>
                                </div>
                            )}
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
