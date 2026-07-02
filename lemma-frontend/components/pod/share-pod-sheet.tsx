'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
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
import {
    type GithubPublishProgress,
    type GithubPublishResult,
    useExportPod,
    useGithubPublish,
    useGithubPublishPreview,
} from '@/lib/hooks/use-pod-imports';

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
        <div className="inline-flex rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] p-1">
            {([
                { label: 'Public', value: false },
                { label: 'Private', value: true },
            ] as const).map((option) => (
                <button
                    key={option.label}
                    type="button"
                    aria-pressed={isPrivate === option.value}
                    onClick={() => onChange(option.value)}
                    className={`rounded-sm px-3 py-1 text-sm font-medium transition-gentle ${
                        isPrivate === option.value
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

function suggestRepoName(name: string | undefined): string {
    const slug = (name ?? '')
        .trim()
        .replace(/[^A-Za-z0-9._-]+/g, '-')
        .replace(/^-+|-+$/g, '');
    return slug || 'lemma-pod';
}

/** Bundle resource kinds in display order, with singular forms for count = 1. */
const RESOURCE_KINDS = [
    ['tables', 'table'],
    ['functions', 'function'],
    ['agents', 'agent'],
    ['workflows', 'workflow'],
    ['schedules', 'schedule'],
    ['surfaces', 'surface'],
    ['apps', 'app'],
] as const;

/** "5 tables · 4 functions · 1 agent · 1 app" from the preview's non-zero
 * resource counts; empty string when there's nothing to say. */
function formatResourceCounts(counts: Record<string, number>): string {
    return RESOURCE_KINDS.filter(([plural]) => (counts[plural] ?? 0) > 0)
        .map(([plural, singular]) => `${counts[plural]} ${counts[plural] === 1 ? singular : plural}`)
        .join(' · ');
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
    const [repoName, setRepoName] = useState(() => suggestRepoName(podName));
    const [debouncedRepoName, setDebouncedRepoName] = useState(() => suggestRepoName(podName));
    const [isPrivate, setIsPrivate] = useState(false);
    const [githubResult, setGithubResult] = useState<GithubPublishResult | null>(null);
    const [publishProgress, setPublishProgress] = useState<GithubPublishProgress | null>(null);
    const [readmeText, setReadmeText] = useState('');
    const [readmeEdited, setReadmeEdited] = useState(false);
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

    useEffect(() => {
        setRepoName(suggestRepoName(effectivePodName));
    }, [effectivePodName]);

    // Debounce the repo name before it drives the README preview fetch, so
    // typing doesn't fire a request per keystroke.
    useEffect(() => {
        const id = setTimeout(() => setDebouncedRepoName(suggestRepoName(repoName)), 400);
        return () => clearTimeout(id);
    }, [repoName]);

    const preview = useGithubPublishPreview(podId, debouncedRepoName, showGithubForm);
    const generatedReadme = preview.data?.readme;
    const published = githubResult?.status === 'published';

    // The editor tracks the generated draft as it (re)loads — until the user's
    // first edit, which pins the text (a repo rename must not clobber edits).
    // After a publish, the panel holds what actually landed on GitHub, so the
    // stale generated draft must not overwrite it either.
    useEffect(() => {
        if (readmeEdited || published) return;
        if (generatedReadme !== undefined) setReadmeText(generatedReadme);
    }, [generatedReadme, readmeEdited, published]);

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
            setReadmeText('');
            setReadmeEdited(false);
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
                        // import-URL rewrite), not the local draft.
                        if (result.readme != null) {
                            setReadmeText(result.readme);
                            setReadmeEdited(false);
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
                    <div className="inline-flex rounded-md border border-[color:var(--chip-border)] bg-[var(--chip-bg)] p-1">
                        {([
                            { label: 'Preview', value: 'preview' },
                            { label: 'Edit', value: 'edit' },
                        ] as const).map((option) => (
                            <button
                                key={option.value}
                                type="button"
                                aria-pressed={activeReadmeTab === option.value}
                                disabled={readmeLocked && option.value === 'edit'}
                                onClick={() => setReadmeTab(option.value)}
                                className={`rounded-sm px-2.5 py-0.5 text-xs font-medium transition-gentle disabled:cursor-not-allowed disabled:opacity-50 ${
                                    activeReadmeTab === option.value
                                        ? 'bg-[var(--surface-1)] text-[var(--text-primary)] shadow-[var(--shadow-xs)]'
                                        : 'text-[var(--text-tertiary)]'
                                }`}
                            >
                                {option.label}
                            </button>
                        ))}
                    </div>
                    {readmeEdited && !readmeLocked ? (
                        <Button
                            variant="ghost"
                            size="xs"
                            className="px-1.5"
                            title="Reset to generated"
                            aria-label="Reset to generated"
                            onClick={() => {
                                setReadmeText(generatedReadme ?? '');
                                setReadmeEdited(false);
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
                            setReadmeText(e.target.value);
                            setReadmeEdited(true);
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
                            <button
                                type="button"
                                onClick={() => {
                                    // Reconnected in another tab? Back to the
                                    // form — no close-and-reopen dance.
                                    setGithubResult(null);
                                    setShowGithubForm(true);
                                }}
                                className="text-xs font-medium text-[var(--text-secondary)] hover:underline"
                            >
                                Try again
                            </button>
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
                                onChange={(e) => setRepoName(e.target.value)}
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
