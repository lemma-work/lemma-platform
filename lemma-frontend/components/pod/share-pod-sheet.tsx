'use client';

import Link from 'next/link';
import { useEffect, useRef, useState } from 'react';
import {
    Check,
    Download,
    ExternalLink,
    FileText,
    Github,
    Loader2,
    RotateCcw,
    Share2,
    Wand2,
} from 'lucide-react';
import { toast } from 'sonner';

import { ReadmeMarkdown } from '@/components/shared/readme-markdown';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Textarea } from '@/components/ui/textarea';
import { useAccounts } from '@/lib/hooks/use-connectors';
import { usePod } from '@/lib/hooks/use-pods';
import { formatResourceCounts } from '@/lib/pod-bundle';
import {
    type GithubPublishProgress,
    type GithubPublishResult,
    useExportPod,
    useGithubPublish,
    useGithubPublishPreview,
} from '@/lib/hooks/use-pod-imports';

/** The chip-track two-state control both toggles here share (visibility,
 * README preview/edit) — one segment per option, the active one lifted onto a
 * surface. */
function SegmentedToggle<T extends string | boolean>({
    options,
    value,
    onChange,
    size = 'md',
}: {
    options: readonly { label: string; value: T; disabled?: boolean }[];
    value: T;
    onChange: (value: T) => void;
    size?: 'sm' | 'md';
}) {
    return (
        <div className="inline-flex rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] p-1">
            {options.map((option) => (
                <button
                    key={option.label}
                    type="button"
                    aria-pressed={value === option.value}
                    disabled={option.disabled}
                    onClick={() => onChange(option.value)}
                    className={`rounded-sm font-medium transition-gentle disabled:cursor-not-allowed disabled:opacity-50 ${
                        size === 'sm' ? 'px-2.5 py-0.5 text-xs' : 'px-3 py-1 text-sm'
                    } ${
                        value === option.value
                            ? 'bg-[var(--surface-1)] text-[var(--text-primary)] shadow-[var(--shadow-xs)]'
                            : 'text-[var(--text-tertiary)]'
                    }`}
                >
                    {option.label}
                </button>
            ))}
        </div>
    );
}

/** A visibility switch, but readably labeled on both sides — "Private repo"
 * with a bare toggle next to it doesn't say what the *other* state is. */
function VisibilityToggle({
    isPrivate,
    onChange,
}: {
    isPrivate: boolean;
    onChange: (isPrivate: boolean) => void;
}) {
    return (
        <SegmentedToggle
            options={[
                { label: 'Public', value: false },
                { label: 'Private', value: true },
            ]}
            value={isPrivate}
            onChange={onChange}
        />
    );
}

function suggestRepoName(name: string | undefined): string {
    const slug = (name ?? '')
        .trim()
        .replace(/[^A-Za-z0-9._-]+/g, '-')
        .replace(/^-+|-+$/g, '');
    return slug || 'lemma-pod';
}

const PUBLISH_STAGES = [
    { key: 'export', label: 'Bundle pod' },
    { key: 'repo', label: 'Create repository' },
    { key: 'readme', label: 'Write README' },
    { key: 'upload', label: 'Upload files' },
] as const;

/** The four publish stages as a compact checklist driven by the NDJSON
 * progress stream — same visual language as the import wizard's StepDot, but
 * local because the semantics here are past/current/upcoming, not per-step
 * outcomes. Before the first event lands, "export" reads as current so the
 * list never shows four idle dots while work is happening. */
function PublishStageChecklist({
    progress,
    aiPolish,
}: {
    progress: GithubPublishProgress | null;
    aiPolish?: boolean;
}) {
    const currentIndex = Math.max(
        0,
        PUBLISH_STAGES.findIndex((s) => s.key === (progress?.stage ?? 'export')),
    );
    const uploading = progress?.stage === 'upload';
    const uploadPct =
        uploading && progress.total
            ? Math.min(100, Math.round(((progress.done ?? 0) / progress.total) * 100))
            : 0;

    return (
        <div className="flex flex-col gap-1.5">
            {PUBLISH_STAGES.map((stage, i) => {
                const isPast = i < currentIndex;
                const isCurrent = i === currentIndex;
                // The stream's own label is authoritative for the README row
                // (the backend knows whether it's polishing); the preview's
                // ai_polish flag is only the pre-arrival hint — the preview
                // query can fail while publishing still works.
                const label =
                    stage.key === 'readme'
                        ? (progress?.stage === 'readme' && progress.label) ||
                          (aiPolish ? 'Write README · AI polish' : stage.label)
                        : stage.label;
                return (
                    <div key={stage.key} className="text-xs">
                        <div className="flex items-center gap-2">
                            <span className="flex w-3.5 shrink-0 items-center justify-center">
                                {isPast ? (
                                    <Check
                                        className="h-3.5 w-3.5 text-[var(--state-success)]"
                                        aria-hidden
                                    />
                                ) : isCurrent ? (
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden />
                                ) : (
                                    <span
                                        className="h-1.5 w-1.5 rounded-full bg-[var(--border-strong)]"
                                        aria-hidden
                                    />
                                )}
                            </span>
                            <span
                                className={
                                    isPast || isCurrent
                                        ? 'text-[var(--text-primary)]'
                                        : 'text-[var(--text-tertiary)]'
                                }
                            >
                                {label}
                            </span>
                            {stage.key === 'upload' && uploading && progress.total ? (
                                <span className="ml-auto tabular-nums text-[var(--text-tertiary)]">
                                    {progress.done ?? 0}/{progress.total}
                                </span>
                            ) : null}
                        </div>
                        {stage.key === 'upload' && uploading ? (
                            // 14px icon slot + 8px gap keeps the bar flush with the label.
                            <div className="ml-[22px] mt-1">
                                <div className="h-[1.5px] overflow-hidden rounded-full bg-[var(--border-subtle)]">
                                    <div
                                        className="h-full rounded-full bg-[var(--accent)] transition-all"
                                        /* eslint-disable-next-line no-restricted-syntax -- Upload progress width is data-driven geometry. */
                                        style={{ width: `${uploadPct}%` }}
                                    />
                                </div>
                                {progress.path ? (
                                    <p className="mt-1 truncate text-xs text-[var(--text-tertiary)]">
                                        {progress.path}
                                        {/* Chunked files upload one piece per
                                            commit — show which piece, or a big
                                            file reads as a stalled row. */}
                                        {progress.part && progress.parts
                                            ? ` · part ${progress.part}/${progress.parts}`
                                            : ''}
                                    </p>
                                ) : null}
                            </div>
                        ) : null}
                    </div>
                );
            })}
        </div>
    );
}

/** The pod's outbound surface: download the bundle or publish to GitHub —
 * beat 1 of the share loop. (Direct pod-id share links are intentionally not
 * offered for now; GitHub's import badge is the durable channel.) The GitHub
 * path expands the dialog into a two-panel publish studio: details + progress
 * on the left, an editable live README preview on the right showing exactly
 * what gets written (and, after publish, exactly what landed). */
export function SharePodSheet({ podId, podName }: { podId: string; podName?: string }) {
    const [open, setOpen] = useState(false);
    const [showGithubForm, setShowGithubForm] = useState(false);
    // The user's repo name once they type; null means the suggestion tracks
    // the pod name (usePod can resolve after mount) — a late query must not
    // clobber input.
    const [repoNameOverride, setRepoNameOverride] = useState<string | null>(null);
    const [debouncedRepoName, setDebouncedRepoName] = useState(() => suggestRepoName(podName));
    const [isPrivate, setIsPrivate] = useState(false);
    const [githubResult, setGithubResult] = useState<GithubPublishResult | null>(null);
    const [publishProgress, setPublishProgress] = useState<GithubPublishProgress | null>(null);
    // The user's pinned README edit; null means the panel tracks the generated
    // draft (and, after a publish, what actually landed on GitHub).
    const [readmeOverride, setReadmeOverride] = useState<string | null>(null);
    // The preview repo slug whose badge URL is embedded in the pinned edit —
    // sent with publish so a repo rename after editing still gets the badge
    // rewritten to the final slug.
    const readmeSourceSlug = useRef<string | null>(null);
    const [readmeTab, setReadmeTab] = useState<'preview' | 'edit'>('preview');
    const exportPod = useExportPod();
    const githubPublish = useGithubPublish();
    const pod = usePod(podId);
    const effectivePodName = podName ?? pod.data?.name;
    const githubAccounts = useAccounts({
        organizationId: pod.data?.organization_id,
        connectorId: 'github',
        enabled: showGithubForm,
    });
    const githubAccount = githubAccounts.data?.[0];

    const repoName = repoNameOverride ?? suggestRepoName(effectivePodName);

    // Debounce the repo name before it drives the README preview fetch, so
    // typing doesn't fire a request per keystroke.
    useEffect(() => {
        const id = setTimeout(() => setDebouncedRepoName(suggestRepoName(repoName)), 400);
        return () => clearTimeout(id);
    }, [repoName]);

    const preview = useGithubPublishPreview(podId, debouncedRepoName, showGithubForm);
    const generatedReadme = preview.data?.readme;
    const published = githubResult?.status === 'published';
    const readmeEdited = readmeOverride !== null;

    // The panel shows the user's pinned edit if there is one (a repo rename or
    // preview reload must not clobber it); after a publish, what actually
    // landed on GitHub; otherwise the generated draft as it (re)loads.
    const readmeText =
        readmeOverride ?? (published ? (githubResult.readme ?? null) : null) ?? generatedReadme ?? '';

    const onOpenChange = (next: boolean) => {
        // A publish stream can't be cancelled — closing now would wipe the
        // progress/result state while the stream keeps writing into it, so the
        // dialog stays open until the result lands.
        if (!next && githubPublish.isPending) return;
        setOpen(next);
        if (!next) {
            // Reopening starts from a clean slate rather than a stale result/
            // form — README edits are intentionally discarded on close.
            setShowGithubForm(false);
            setGithubResult(null);
            setPublishProgress(null);
            setReadmeOverride(null);
            readmeSourceSlug.current = null;
            setReadmeTab('preview');
        }
    };

    const onDownload = () => {
        exportPod.mutate(
            { podId, withData: true },
            {
                onSuccess: (filename) => toast.success(`Downloaded ${filename}`),
                onError: (e) => toast.error(e instanceof Error ? e.message : 'Export failed'),
            },
        );
    };

    // Edits only count while there's actual content: an edited-to-blank
    // README would be silently replaced by the generated draft server-side,
    // so the UI must not claim it publishes "exactly as written".
    const effectiveEdited = readmeEdited && readmeText.trim().length > 0;

    const onPublishToGithub = () => {
        setPublishProgress(null);
        setGithubResult(null);
        githubPublish.mutate(
            {
                podId,
                repoName: suggestRepoName(repoName),
                isPrivate,
                // An edited README publishes verbatim; otherwise the backend
                // renders (and possibly AI-polishes) its own draft.
                readme: effectiveEdited ? readmeText : undefined,
                readmeSourceSlug: effectiveEdited
                    ? (readmeSourceSlug.current ?? undefined)
                    : undefined,
                onProgress: setPublishProgress,
            },
            {
                onSuccess: (result) => {
                    setGithubResult(result);
                    // On success the result box replaces the form; on failure
                    // (e.g. a name collision) keep it open with the same
                    // values so retrying is one edit + click, not a restart.
                    if (result.status === 'published') {
                        setShowGithubForm(false);
                        // Show exactly what landed on GitHub (post AI-polish /
                        // import-URL rewrite), not the local draft — drop the
                        // pin so the published text takes over.
                        if (result.readme != null) {
                            setReadmeOverride(null);
                            readmeSourceSlug.current = null;
                        }
                    }
                    if (result.status === 'failed') toast.error(result.message ?? 'Publish failed');
                },
                onError: (e) => toast.error(e instanceof Error ? e.message : 'Publish failed'),
            },
        );
    };

    const githubAccountLabel =
        githubAccount?.email || githubAccount?.provider_account_id || 'your GitHub account';

    const studioOpen = showGithubForm || published;
    // While publishing (and once published) the README is a record, not a
    // draft — the panel pins to Preview and edits are off.
    const readmeLocked = githubPublish.isPending || published;
    const activeReadmeTab: 'preview' | 'edit' = readmeLocked ? 'preview' : readmeTab;
    const resourceSummary = preview.data?.resource_counts
        ? formatResourceCounts(preview.data.resource_counts)
        : '';

    const readmePanel = (
        <div className="flex min-w-0 flex-col">
            <div className="flex items-center justify-between gap-2">
                <p className="flex items-center gap-1.5 text-xs font-medium text-[var(--text-tertiary)]">
                    <FileText className="h-3.5 w-3.5" /> README
                </p>
                <div className="flex items-center gap-1">
                    <SegmentedToggle
                        size="sm"
                        options={[
                            { label: 'Preview', value: 'preview' as const },
                            { label: 'Edit', value: 'edit' as const, disabled: readmeLocked },
                        ]}
                        value={activeReadmeTab}
                        onChange={setReadmeTab}
                    />
                    {readmeEdited && !readmeLocked ? (
                        <Button
                            variant="ghost"
                            size="xs"
                            className="px-1.5"
                            title="Reset to generated"
                            aria-label="Reset to generated"
                            onClick={() => {
                                setReadmeOverride(null);
                                readmeSourceSlug.current = null;
                            }}
                        >
                            <RotateCcw className="h-3.5 w-3.5" />
                        </Button>
                    ) : null}
                </div>
            </div>
            <div className="mt-1.5">
                {activeReadmeTab === 'edit' ? (
                    <Textarea
                        value={readmeText}
                        onChange={(e) => {
                            // The draft being pinned embeds the current
                            // preview slug in its badge URL — remember it so
                            // publish can still rewrite the badge if the repo
                            // is renamed after this edit.
                            if (readmeOverride === null)
                                readmeSourceSlug.current = preview.data?.repo_name ?? null;
                            setReadmeOverride(e.target.value);
                        }}
                        className="h-[420px] w-full resize-none font-mono text-xs leading-5"
                        aria-label="README markdown"
                        placeholder="# Write your README…"
                    />
                ) : (
                    <div className="h-[420px] overflow-y-auto rounded-lg border border-[var(--border-subtle)] p-4">
                        {readmeText ? (
                            <ReadmeMarkdown markdown={readmeText} />
                        ) : preview.isLoading ? (
                            <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                                <Loader2 className="h-3.5 w-3.5 animate-spin" /> Rendering…
                            </div>
                        ) : (
                            <p className="text-xs text-[var(--text-tertiary)]">
                                Couldn’t load a preview — publishing still works.
                            </p>
                        )}
                    </div>
                )}
            </div>
        </div>
    );

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogTrigger asChild>
                <Button variant="secondary">
                    <Share2 className="mr-1.5 h-4 w-4" /> Share
                </Button>
            </DialogTrigger>
            <DialogContent className={studioOpen ? 'max-w-5xl' : 'max-w-2xl'}>
                <DialogHeader>
                    <DialogTitle>Share {effectivePodName ?? 'this pod'}</DialogTitle>
                </DialogHeader>
                <p className="-mt-3 text-sm text-[var(--text-tertiary)]">
                    Publish it to GitHub with an import badge, or download the bundle — anyone can
                    install it as their own pod.
                </p>

                <Button
                    variant="secondary"
                    className="w-full justify-start"
                    loading={exportPod.isPending}
                    loadingLabel="Preparing bundle…"
                    onClick={onDownload}
                >
                    <Download className="mr-2 h-4 w-4" /> Download bundle (.zip)
                </Button>

                {githubResult?.status === 'published' ? (
                    <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
                        <div className="flex flex-col rounded-lg border border-[var(--border-subtle)] p-3">
                            <p className="flex items-center gap-1.5 text-sm font-medium text-[var(--state-success)]">
                                <Check className="h-4 w-4" /> Published
                            </p>
                            <a
                                href={githubResult.repo_url ?? '#'}
                                target="_blank"
                                rel="noreferrer"
                                className="mt-1 flex items-center gap-1 text-xs text-[var(--text-accent)] hover:underline"
                            >
                                <span className="truncate">{githubResult.repo_url}</span>
                                <ExternalLink className="h-3 w-3 shrink-0" />
                            </a>
                            {githubResult.message ? (
                                <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                                    {githubResult.message}
                                </p>
                            ) : null}
                            <div className="mt-auto flex justify-end pt-4">
                                <Button variant="ghost" onClick={() => onOpenChange(false)}>
                                    Done
                                </Button>
                            </div>
                        </div>
                        {readmePanel}
                    </div>
                ) : githubResult?.status === 'not_connected' ? (
                    <div className="rounded-lg border border-[var(--border-subtle)] p-3">
                        <p className="text-sm text-[var(--text-primary)]">
                            {githubResult.message ?? 'Connect GitHub first.'}
                        </p>
                        <div className="mt-2 flex items-center gap-3">
                            <Link
                                href={`/pod/${podId}/connectors`}
                                className="inline-flex items-center gap-1 text-xs text-[var(--text-accent)] hover:underline"
                            >
                                Open Connectors <ExternalLink className="h-3 w-3" />
                            </Link>
                            <Button
                                variant="ghost"
                                size="xs"
                                onClick={() => {
                                    // Reconnected in another tab? Back to the
                                    // form — no close-and-reopen dance.
                                    setGithubResult(null);
                                    setShowGithubForm(true);
                                }}
                            >
                                Try again
                            </Button>
                        </div>
                    </div>
                ) : !showGithubForm ? (
                    <Button
                        variant="secondary"
                        className="w-full justify-start"
                        onClick={() => {
                            setShowGithubForm(true);
                            setPublishProgress(null);
                        }}
                    >
                        <Github className="mr-2 h-4 w-4" /> Publish to GitHub
                    </Button>
                ) : githubAccounts.isLoading ? (
                    <div className="flex items-center gap-2 rounded-lg border border-[var(--border-subtle)] p-3 text-xs text-[var(--text-tertiary)]">
                        <Loader2 className="h-3.5 w-3.5 animate-spin" /> Checking your GitHub
                        connection…
                    </div>
                ) : !githubAccount ? (
                    <div className="rounded-lg border border-[var(--border-subtle)] p-3">
                        <p className="text-sm text-[var(--text-primary)]">
                            Connect GitHub in this pod&apos;s Connectors settings, then try again.
                        </p>
                        <Link
                            href={`/pod/${podId}/connectors`}
                            className="mt-1 inline-flex items-center gap-1 text-xs text-[var(--text-accent)] hover:underline"
                        >
                            Open Connectors <ExternalLink className="h-3 w-3" />
                        </Link>
                    </div>
                ) : (
                    <div className="flex flex-col gap-4 lg:grid lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
                        <div className="flex flex-col rounded-lg border border-[var(--border-subtle)] p-3">
                            <p className="flex items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                                <Github className="h-3.5 w-3.5" /> Publishing as {githubAccountLabel}
                            </p>
                            <Input
                                className="mt-2"
                                value={repoName}
                                onChange={(e) => setRepoNameOverride(e.target.value)}
                                placeholder="repo-name"
                            />
                            <div className="mt-2">
                                <VisibilityToggle isPrivate={isPrivate} onChange={setIsPrivate} />
                            </div>

                            {resourceSummary ? (
                                <div className="mt-4">
                                    <p className="text-xs font-medium text-[var(--text-tertiary)]">
                                        What gets published
                                    </p>
                                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                                        {resourceSummary}
                                    </p>
                                </div>
                            ) : null}

                            {effectiveEdited ? (
                                <p className="mt-3 flex items-start gap-1.5 text-xs text-[var(--text-tertiary)]">
                                    <FileText className="mt-0.5 h-3.5 w-3.5 shrink-0" /> Your edited
                                    README is published exactly as written.
                                </p>
                            ) : preview.data?.ai_polish ? (
                                <p className="mt-3 flex items-start gap-1.5 text-xs text-[var(--text-tertiary)]">
                                    <Wand2 className="mt-0.5 h-3.5 w-3.5 shrink-0" /> An AI pass
                                    polishes this draft during publish.
                                </p>
                            ) : null}

                            {githubPublish.isPending ? (
                                <div className="mt-4">
                                    <PublishStageChecklist
                                        progress={publishProgress}
                                        aiPolish={!effectiveEdited && preview.data?.ai_polish}
                                    />
                                </div>
                            ) : null}

                            {!githubPublish.isPending && githubResult?.status === 'failed' ? (
                                // A publish can take a minute — the failure has
                                // to survive the toast, or a user who tabbed
                                // away returns to a form that looks untouched.
                                <div className="mt-4 rounded-lg border border-[var(--state-error)] px-3 py-2.5">
                                    <p className="text-sm font-medium text-[var(--text-primary)]">
                                        Publish failed
                                    </p>
                                    <p className="mt-1 text-xs text-[var(--text-secondary)]">
                                        {githubResult.message ?? 'Something went wrong on the way to GitHub.'}
                                    </p>
                                    <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                                        Your settings and README are kept — adjust and publish again.
                                    </p>
                                </div>
                            ) : null}

                            <div className="mt-auto flex justify-end gap-2 pt-4">
                                {githubPublish.isPending ? (
                                    // The stream can't be safely abandoned
                                    // mid-publish — no Cancel here.
                                    <Button loading disabled>
                                        Publishing…
                                    </Button>
                                ) : (
                                    <>
                                        <Button
                                            variant="ghost"
                                            onClick={() => {
                                                setShowGithubForm(false);
                                                setPublishProgress(null);
                                            }}
                                        >
                                            Cancel
                                        </Button>
                                        <Button onClick={onPublishToGithub}>Publish</Button>
                                    </>
                                )}
                            </div>
                        </div>
                        {readmePanel}
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}
