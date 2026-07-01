'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { Check, Copy, Download, ExternalLink, FileText, Github, Link2, Loader2, Share2 } from 'lucide-react';
import { toast } from 'sonner';

import { ReadmeMarkdown } from '@/components/shared/readme-markdown';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogTrigger } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { useAccounts } from '@/lib/hooks/use-connectors';
import { usePod } from '@/lib/hooks/use-pods';
import {
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

/** The pod's outbound surface: copy a shareable import link, download the
 * bundle, or publish to GitHub (with a live README preview of exactly what
 * gets written) — beat 1 of the share loop. A full dialog, not a cramped
 * popover, since this is the primary way a pod leaves Lemma. */
export function SharePodSheet({ podId, podName }: { podId: string; podName?: string }) {
    const [open, setOpen] = useState(false);
    const [copied, setCopied] = useState(false);
    const [showGithubForm, setShowGithubForm] = useState(false);
    const [repoName, setRepoName] = useState(() => suggestRepoName(podName));
    const [debouncedRepoName, setDebouncedRepoName] = useState(() => suggestRepoName(podName));
    const [isPrivate, setIsPrivate] = useState(false);
    const [githubResult, setGithubResult] = useState<GithubPublishResult | null>(null);
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

    const onOpenChange = (next: boolean) => {
        setOpen(next);
        if (!next) {
            // Reopening starts from a clean slate rather than a stale result/form.
            setShowGithubForm(false);
            setGithubResult(null);
        }
    };

    const shareLink =
        typeof window !== 'undefined' ? `${window.location.origin}/import/p/${podId}` : '';

    const copyLink = async () => {
        try {
            await navigator.clipboard.writeText(shareLink);
            setCopied(true);
            setTimeout(() => setCopied(false), 1500);
        } catch {
            toast.error('Couldn’t copy the link');
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

    const onPublishToGithub = () => {
        githubPublish.mutate(
            { podId, repoName: suggestRepoName(repoName), isPrivate },
            {
                onSuccess: (result) => {
                    setGithubResult(result);
                    // On success the result box replaces the form; on failure
                    // (e.g. a name collision) keep it open with the same
                    // values so retrying is one edit + click, not a restart.
                    if (result.status === 'published') setShowGithubForm(false);
                    if (result.status === 'failed') toast.error(result.message ?? 'Publish failed');
                },
                onError: (e) => toast.error(e instanceof Error ? e.message : 'Publish failed'),
            },
        );
    };

    const githubAccountLabel =
        githubAccount?.email || githubAccount?.provider_account_id || 'your GitHub account';

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogTrigger asChild>
                <Button variant="secondary">
                    <Share2 className="mr-1.5 h-4 w-4" /> Share
                </Button>
            </DialogTrigger>
            <DialogContent className="max-w-2xl">
                <DialogHeader>
                    <DialogTitle>Share {effectivePodName ?? 'this pod'}</DialogTitle>
                </DialogHeader>
                <p className="-mt-3 text-sm text-[var(--text-tertiary)]">
                    Anyone in your org can import it as their own pod.
                </p>

                <div className="flex items-center gap-2">
                    <div className="flex flex-1 items-center gap-1.5 overflow-hidden rounded-lg border border-[var(--border-subtle)] px-2.5 py-1.5 text-xs text-[var(--text-secondary)]">
                        <Link2 className="h-3.5 w-3.5 shrink-0" />
                        <span className="truncate">{shareLink.replace(/^https?:\/\//, '')}</span>
                    </div>
                    <Button variant="secondary" onClick={copyLink} aria-label="Copy link">
                        {copied ? <Check className="h-4 w-4" /> : <Copy className="h-4 w-4" />}
                    </Button>
                </div>

                <Button
                    variant="secondary"
                    className="w-full justify-start"
                    loading={exportPod.isPending}
                    onClick={onDownload}
                >
                    <Download className="mr-2 h-4 w-4" /> Download bundle (.zip)
                </Button>

                {githubResult?.status === 'published' ? (
                    <div className="rounded-lg border border-[var(--border-subtle)] p-3">
                        <p className="flex items-center gap-1.5 text-sm font-medium text-[var(--state-success)]">
                            <Check className="h-4 w-4" /> Published
                        </p>
                        <a
                            href={githubResult.repo_url ?? '#'}
                            target="_blank"
                            rel="noreferrer"
                            className="mt-1 flex items-center gap-1 text-xs text-[var(--text-accent)] hover:underline"
                        >
                            {githubResult.repo_url} <ExternalLink className="h-3 w-3" />
                        </a>
                        {githubResult.message ? (
                            <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                                {githubResult.message}
                            </p>
                        ) : null}
                    </div>
                ) : githubResult?.status === 'not_connected' ? (
                    <div className="rounded-lg border border-[var(--border-subtle)] p-3">
                        <p className="text-sm text-[var(--text-primary)]">
                            {githubResult.message ?? 'Connect GitHub first.'}
                        </p>
                        <Link
                            href={`/pod/${podId}/connectors`}
                            className="mt-1 inline-flex items-center gap-1 text-xs text-[var(--text-accent)] hover:underline"
                        >
                            Open Connectors <ExternalLink className="h-3 w-3" />
                        </Link>
                    </div>
                ) : !showGithubForm ? (
                    <Button
                        variant="secondary"
                        className="w-full justify-start"
                        onClick={() => setShowGithubForm(true)}
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
                    <div className="rounded-lg border border-[var(--border-subtle)] p-3">
                        <p className="flex items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                            <Github className="h-3.5 w-3.5" /> Publishing as {githubAccountLabel}
                        </p>
                        <div className="mt-2 flex items-center gap-2">
                            <Input
                                className="flex-1"
                                value={repoName}
                                onChange={(e) => setRepoName(e.target.value)}
                                placeholder="repo-name"
                            />
                            <VisibilityToggle isPrivate={isPrivate} onChange={setIsPrivate} />
                        </div>

                        <p className="mt-4 flex items-center gap-1.5 text-xs font-medium text-[var(--text-tertiary)]">
                            <FileText className="h-3.5 w-3.5" /> README preview
                        </p>
                        <div className="mt-1.5 max-h-64 overflow-y-auto rounded-lg border border-[var(--border-subtle)] p-3">
                            {preview.isLoading ? (
                                <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                                    <Loader2 className="h-3.5 w-3.5 animate-spin" /> Rendering…
                                </div>
                            ) : preview.data ? (
                                <ReadmeMarkdown markdown={preview.data.readme} />
                            ) : (
                                <p className="text-xs text-[var(--text-tertiary)]">
                                    Couldn’t load a preview — publishing still works.
                                </p>
                            )}
                        </div>

                        <div className="mt-3 flex justify-end gap-2">
                            <Button variant="ghost" onClick={() => setShowGithubForm(false)}>
                                Cancel
                            </Button>
                            <Button loading={githubPublish.isPending} onClick={onPublishToGithub}>
                                Publish
                            </Button>
                        </div>
                    </div>
                )}
            </DialogContent>
        </Dialog>
    );
}
