'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowUpRight, Copy, Download, FileText, Github } from '@/components/ui/icons';
import { toast } from 'sonner';

import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from '@/components/ui/sheet';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Switch, SwitchThumb, SwitchTrack } from '@/components/ui/switch';
import { showResourceErrorToast } from '@/components/shared/resource-feedback';
import { BundleProgressBar } from '@/components/bundle/bundle-progress';
import {
    getPublish,
    pollExport,
    startExport,
    startPublish,
    toRepoSlug,
    trackBundleJob,
    triggerBundleDownload,
    type BundleProgressView,
    type PublishStatusResponse,
} from '@/lib/hooks/use-pod-bundle';

interface ShareSheetProps {
    podId: string;
    podName?: string | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}

/** The design-system Switch is headless — it needs a track + thumb to render. */
function Toggle({
    checked,
    onCheckedChange,
    disabled,
}: {
    checked: boolean;
    onCheckedChange: (next: boolean) => void;
    disabled?: boolean;
}) {
    return (
        <Switch checked={checked} onCheckedChange={onCheckedChange} disabled={disabled}>
            <SwitchTrack className={checked ? 'bg-[var(--action-primary)]' : undefined}>
                <SwitchThumb className={checked ? 'translate-x-4' : undefined} />
            </SwitchTrack>
        </Switch>
    );
}

function looksLikeNotConnected(message: string | null | undefined): boolean {
    const text = (message || '').toLowerCase();
    return text.includes('not_connected') || text.includes('connect') || text.includes('no github');
}

function publishPhaseLabel(status: string): string {
    if (status === 'EXPORTING') return 'Packaging pod…';
    if (status === 'PUBLISHING') return 'Pushing files to GitHub…';
    return 'Starting…';
}

export function ShareSheet({ podId, podName, open, onOpenChange }: ShareSheetProps) {
    const defaultRepo = useMemo(() => toRepoSlug(podName || 'my-pod') || 'my-pod', [podName]);

    // Export
    const [withData, setWithData] = useState(true);
    const [exporting, setExporting] = useState(false);
    const [exportView, setExportView] = useState<BundleProgressView | null>(null);

    // Publish
    const [repoName, setRepoName] = useState(defaultRepo);
    const [isPrivate, setIsPrivate] = useState(false);
    const [aiReadme, setAiReadme] = useState(true);
    const [publishing, setPublishing] = useState(false);
    const [publishView, setPublishView] = useState<BundleProgressView | null>(null);
    const [published, setPublished] = useState<PublishStatusResponse | null>(null);
    const [needsGithub, setNeedsGithub] = useState(false);

    async function handleExport() {
        if (exporting) return;
        setExporting(true);
        setExportView({ status: 'QUEUED', done: 0, total: 0 });
        try {
            const started = await startExport(podId, { with_data: withData });
            const final = await pollExport(podId, started.export_id, {
                onTick: (s) =>
                    setExportView({ status: s.status, done: s.progress.done, total: s.progress.total }),
            });
            if (final.status === 'READY' && final.download_url) {
                triggerBundleDownload(final.download_url, final.bundle_filename ?? undefined);
                if (final.warnings.length > 0) {
                    toast.warning('Bundle ready — with notes', {
                        description: final.warnings.slice(0, 3).join(' · '),
                    });
                } else {
                    toast.success('Bundle downloaded');
                }
            } else {
                throw new Error(final.error || 'Export failed');
            }
        } catch (error) {
            showResourceErrorToast(error, 'Export failed');
        } finally {
            setExporting(false);
            setExportView(null);
        }
    }

    async function handlePublish() {
        const name = repoName.trim();
        if (publishing || !name) return;
        setPublishing(true);
        setPublished(null);
        setNeedsGithub(false);
        setPublishView({ status: 'QUEUED', done: 0, total: 0 });
        try {
            const started = await startPublish(podId, {
                repo_name: name,
                private: isPrivate,
                ai_readme: aiReadme,
            });
            const final = await trackBundleJob({
                podId,
                eventsUrl: started.events_url,
                fetchStatus: () => getPublish(podId, started.publish_id),
                stopStatuses: ['COMPLETED', 'FAILED'],
                onProgress: setPublishView,
            });
            if (final.status === 'COMPLETED') {
                setPublished(final);
                toast.success('Published to GitHub');
            } else if (looksLikeNotConnected(final.error)) {
                setNeedsGithub(true);
            } else {
                throw new Error(final.error || 'Publish failed');
            }
        } catch (error) {
            const message = error instanceof Error ? error.message : '';
            if (looksLikeNotConnected(message)) {
                setNeedsGithub(true);
            } else {
                showResourceErrorToast(error, 'Publish failed');
            }
        } finally {
            setPublishing(false);
            setPublishView(null);
        }
    }

    async function copy(text: string, label: string) {
        try {
            await navigator.clipboard.writeText(text);
            toast.success(`${label} copied`);
        } catch {
            toast.error('Could not copy to clipboard');
        }
    }

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="right" className="flex w-full flex-col gap-0 sm:max-w-md">
                <SheetHeader>
                    <SheetTitle>Share this pod</SheetTitle>
                    <SheetDescription>
                        Package {podName ? <span className="font-medium">{podName}</span> : 'this pod'} as a
                        portable bundle — download it, or publish it to GitHub with a one-click install badge.
                    </SheetDescription>
                </SheetHeader>

                <div className="flex-1 space-y-6 overflow-y-auto px-1 py-6">
                    {/* Download */}
                    <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
                        <div className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
                            <Download className="h-4 w-4 text-[var(--text-tertiary)]" />
                            Download bundle
                        </div>
                        <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                            A <code>.zip</code> of the whole pod — tables, agents, functions, workflows, apps and
                            surfaces. Anyone can import it from Lemma.
                        </p>
                        <div className="mt-4 flex items-center justify-between gap-3">
                            <div className="text-sm text-[var(--text-secondary)]">
                                Include table data
                                <span className="block text-xs text-[var(--text-tertiary)]">
                                    Off exports the schema only.
                                </span>
                            </div>
                            <Toggle checked={withData} onCheckedChange={setWithData} disabled={exporting} />
                        </div>
                        {exporting && exportView ? (
                            <BundleProgressBar
                                className="mt-4"
                                done={exportView.done}
                                total={exportView.total}
                                label="Packaging…"
                            />
                        ) : (
                            <Button className="mt-4 w-full" variant="secondary" onClick={handleExport}>
                                <Download className="mr-2 h-4 w-4" />
                                Download .zip
                            </Button>
                        )}
                    </section>

                    {/* Publish to GitHub */}
                    <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
                        <div className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
                            <Github className="h-4 w-4 text-[var(--text-tertiary)]" />
                            Publish to GitHub
                        </div>
                        <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                            Creates a repo with a README and an <span className="font-medium">Import to Lemma</span>{' '}
                            badge — a durable, shareable install link.
                        </p>

                        {published ? (
                            <div className="mt-4 space-y-3">
                                <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] p-3">
                                    <div className="text-xs text-[var(--text-tertiary)]">Published repository</div>
                                    <a
                                        href={published.repo_url ?? '#'}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="mt-0.5 flex items-center gap-1 text-sm font-medium text-[var(--action-primary)] hover:underline"
                                    >
                                        {published.repo_url?.replace(/^https?:\/\//, '') ?? published.repo_name}
                                        <ArrowUpRight className="h-3.5 w-3.5" />
                                    </a>
                                </div>
                                <div className="flex gap-2">
                                    <Button
                                        variant="secondary"
                                        size="sm"
                                        className="flex-1"
                                        onClick={() => published.repo_url && copy(published.repo_url, 'Repo link')}
                                    >
                                        <Copy className="mr-2 h-3.5 w-3.5" />
                                        Copy link
                                    </Button>
                                    <Button variant="secondary" size="sm" className="flex-1" onClick={() => setPublished(null)}>
                                        Publish again
                                    </Button>
                                </div>
                                <p className="text-xs text-[var(--text-tertiary)]">
                                    Others import it from <span className="font-medium">Import → GitHub</span> by pasting
                                    this URL.
                                </p>
                            </div>
                        ) : publishing ? (
                            <BundleProgressBar
                                className="mt-4"
                                done={publishView?.done ?? 0}
                                total={publishView?.total ?? 0}
                                label={publishPhaseLabel(publishView?.status ?? 'QUEUED')}
                            />
                        ) : (
                            <div className="mt-4 space-y-4">
                                <div className="space-y-1.5">
                                    <Label htmlFor="bundle-repo-name" className="text-xs">
                                        Repository name
                                    </Label>
                                    <Input
                                        id="bundle-repo-name"
                                        value={repoName}
                                        onChange={(e) => setRepoName(e.target.value)}
                                        placeholder="my-pod"
                                    />
                                </div>
                                <div className="flex items-start justify-between gap-3 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] p-3">
                                    <div className="flex items-start gap-2">
                                        <FileText className="mt-0.5 h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                                        <div className="text-sm text-[var(--text-secondary)]">
                                            AI-written README
                                            <span className="block text-xs text-[var(--text-tertiary)]">
                                                Generates a README describing what the pod does.
                                            </span>
                                        </div>
                                    </div>
                                    <Toggle checked={aiReadme} onCheckedChange={setAiReadme} />
                                </div>
                                <div className="flex items-center justify-between gap-3">
                                    <span className="text-sm text-[var(--text-secondary)]">Private repository</span>
                                    <Toggle checked={isPrivate} onCheckedChange={setIsPrivate} />
                                </div>

                                {needsGithub ? (
                                    <div className="rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] p-3 text-xs text-[var(--text-secondary)]">
                                        Connect your GitHub account first, then publish.
                                        <Link
                                            href={`/pod/${podId}/connectors`}
                                            className="ml-1 font-medium text-[var(--action-primary)] hover:underline"
                                        >
                                            Open connectors
                                        </Link>
                                    </div>
                                ) : null}

                                <Button className="w-full" onClick={handlePublish} disabled={!repoName.trim()}>
                                    <Github className="mr-2 h-4 w-4" />
                                    Publish to GitHub
                                </Button>
                            </div>
                        )}
                    </section>
                </div>
            </SheetContent>
        </Sheet>
    );
}
