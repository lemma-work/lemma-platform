'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import type { DatastoreDirectoryTreeNode } from 'lemma-sdk';
import { ArrowUpRight, Copy, Download, FileText, Github, Share2 } from '@/components/ui/icons';
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
import { AccountVariableField } from '@/components/bundle/account-variable-field';
import { SocialCardPanel } from '@/components/share/social-card-panel';
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
    type PublishMode,
} from '@/lib/hooks/use-pod-bundle';
import { usePod } from '@/lib/hooks/use-pods';
import { useTables } from '@/lib/hooks/use-datastores';
import { useQuery } from '@tanstack/react-query';
import { getLemmaClient } from '@/lib/sdk/lemma-client';

interface ShareSheetProps {
    podId: string;
    podName?: string | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    canPublish?: boolean;
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

/** Names the caller picked, as checkboxes. Nothing is selected by default:
 *  rows and files leave a pod only where someone asked for them. */
function NamePicker({
    label,
    hint,
    options,
    selected,
    onToggle,
    emptyText,
    disabled,
}: {
    label: string;
    hint: string;
    options: string[];
    selected: string[];
    onToggle: (name: string) => void;
    emptyText: string;
    disabled?: boolean;
}) {
    return (
        <div className="mt-4">
            <div className="text-sm text-[var(--text-secondary)]">
                {label}
                <span className="block text-xs text-[var(--text-tertiary)]">{hint}</span>
            </div>
            {options.length === 0 ? (
                <p className="mt-2 text-xs text-[var(--text-tertiary)]">{emptyText}</p>
            ) : (
                <div className="mt-2 max-h-36 overflow-y-auto rounded-md border border-[var(--border-subtle)] p-2">
                    {options.map((name) => (
                        <label
                            key={name}
                            className="flex cursor-pointer items-center gap-2 py-1 text-sm text-[var(--text-primary)]"
                        >
                            <input
                                type="checkbox"
                                className="accent-[var(--action-primary)]"
                                checked={selected.includes(name)}
                                onChange={() => onToggle(name)}
                                disabled={disabled}
                            />
                            <span className="truncate">{name}</span>
                        </label>
                    ))}
                </div>
            )}
        </div>
    );
}

function errorCode(error: unknown): string | null {
    if (error && typeof error === 'object' && 'code' in error) {
        const code = (error as { code?: unknown }).code;
        return typeof code === 'string' ? code : null;
    }
    return null;
}

function needsGithubAccount(code: string | null | undefined): boolean {
    return (
        code === 'ACCOUNT_NOT_FOUND' ||
        code === 'ACCOUNT_CREDENTIALS_NOT_FOUND' ||
        code === 'ACCOUNT_NOT_CONNECTED' ||
        code === 'CONNECTOR_UNAUTHORIZED' ||
        code === 'CONNECTOR_ACCESS_DENIED' ||
        code === 'OPERATION_EXECUTION_UNAUTHORIZED' ||
        code === 'OPERATION_EXECUTION_ACCESS_DENIED'
    );
}

function publishPhaseLabel(status: string): string {
    if (status === 'EXPORTING') return 'Packaging pod…';
    if (status === 'PUBLISHING') return 'Pushing files to GitHub…';
    return 'Starting…';
}

function githubImportPath(repoUrl: string): string | null {
    try {
        const url = new URL(repoUrl);
        if (url.hostname.toLowerCase() !== 'github.com') return null;
        const [owner, repoWithSuffix] = url.pathname.split('/').filter(Boolean);
        const repo = repoWithSuffix?.replace(/\.git$/i, '');
        if (!owner || !repo) return null;
        return `/import/github/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
    } catch {
        return null;
    }
}

export function ShareSheet({ podId, podName, open, onOpenChange, canPublish = true }: ShareSheetProps) {
    const { data: pod } = usePod(podId);
    const { data: tables } = useTables(podId, undefined, { enabled: open });
    const { data: folderTree } = useQuery({
        queryKey: ['bundle-export-folders', podId],
        queryFn: () => getLemmaClient(podId).files.tree({ rootPath: '/', filesPerDirectory: 0 }),
        enabled: open && Boolean(podId),
        staleTime: 60_000,
    });
    const defaultRepo = useMemo(() => toRepoSlug(podName || 'my-pod') || 'my-pod', [podName]);
    const tableNames = useMemo(
        () =>
            (tables?.items ?? [])
                .map((table) => table.name)
                .filter((name) => Boolean(name))
                .sort(),
        [tables?.items],
    );
    /** Every folder in the tree, flattened to full paths so a nested one can be
     *  picked on its own. The pod root is not offered: "everything" is exactly
     *  what naming folders exists to avoid. */
    const folderPaths = useMemo(() => {
        const out: string[] = [];
        const walk = (node: DatastoreDirectoryTreeNode) => {
            if (node.kind.toUpperCase() === 'FOLDER' && node.path && node.path !== '/') {
                out.push(node.path);
            }
            for (const child of node.children ?? []) walk(child);
        };
        if (folderTree) walk(folderTree.tree);
        return Array.from(new Set(out)).sort();
    }, [folderTree]);

    // Export. Both selections start empty — a bundle carries the pod's shape
    // unless rows/files are explicitly named.
    const [dataTables, setDataTables] = useState<string[]>([]);
    const [fileFolders, setFileFolders] = useState<string[]>([]);
    const [exporting, setExporting] = useState(false);
    const [exportView, setExportView] = useState<BundleProgressView | null>(null);

    // Publish
    const [repoName, setRepoName] = useState(defaultRepo);
    const [isPrivate, setIsPrivate] = useState(false);
    const [publishMode, setPublishMode] = useState<PublishMode>('CREATE');
    const [githubAccountId, setGithubAccountId] = useState('');
    const [aiReadme, setAiReadme] = useState(true);
    const [publishing, setPublishing] = useState(false);
    const [publishView, setPublishView] = useState<BundleProgressView | null>(null);
    const [published, setPublished] = useState<PublishStatusResponse | null>(null);
    const [needsGithub, setNeedsGithub] = useState(false);
    const publishedInstallUrl = useMemo(() => {
        const path = published?.repo_url && !published.private ? githubImportPath(published.repo_url) : null;
        if (!path || typeof window === 'undefined') return null;
        return new URL(path, window.location.origin).toString();
    }, [published]);

    async function handleExport() {
        if (exporting) return;
        setExporting(true);
        setExportView({ status: 'QUEUED', done: 0, total: 0 });
        try {
            const started = await startExport(podId, {
                data_tables: dataTables.length > 0 ? dataTables : null,
                file_folders: fileFolders.length > 0 ? fileFolders : null,
            });
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
        if (publishing || !name || !githubAccountId) return;
        setPublishing(true);
        setPublished(null);
        setNeedsGithub(false);
        setPublishView({ status: 'QUEUED', done: 0, total: 0 });
        try {
            const started = await startPublish(podId, {
                repo_name: name,
                mode: publishMode,
                private: isPrivate,
                account_id: githubAccountId,
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
            } else if (needsGithubAccount(final.error_code)) {
                setNeedsGithub(true);
            } else {
                throw new Error(final.error || 'Publish failed');
            }
        } catch (error) {
            if (needsGithubAccount(errorCode(error))) {
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

    async function sharePublishedPod() {
        if (!publishedInstallUrl) return;
        const title = `Run ${podName || 'this pod'} on Lemma`;
        const text = `Run ${podName || 'this pod'} on Lemma.`;
        if (navigator.share) {
            try {
                await navigator.share({ title, text, url: publishedInstallUrl });
                return;
            } catch (error) {
                if (error instanceof DOMException && error.name === 'AbortError') return;
            }
        }
        await copy(publishedInstallUrl, 'Run link');
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
                            A <code>.zip</code> of the pod — tables, agents, functions, workflows, apps and
                            surfaces. Rows and files come only from what you pick below. Anyone can import it
                            from Lemma.
                        </p>
                        <NamePicker
                            label="Include rows from these tables"
                            hint="Pick none to export table structure only."
                            options={tableNames}
                            selected={dataTables}
                            onToggle={(name) =>
                                setDataTables((current) =>
                                    current.includes(name)
                                        ? current.filter((n) => n !== name)
                                        : [...current, name],
                                )
                            }
                            emptyText="This pod has no tables."
                            disabled={exporting}
                        />
                        <NamePicker
                            label="Include these folders"
                            hint="Each folder travels with everything inside it."
                            options={folderPaths}
                            selected={fileFolders}
                            onToggle={(path) =>
                                setFileFolders((current) =>
                                    current.includes(path)
                                        ? current.filter((p) => p !== path)
                                        : [...current, path],
                                )
                            }
                            emptyText="This pod has no folders."
                            disabled={exporting}
                        />
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
                    {canPublish ? (
                    <section className="rounded-lg border border-[var(--border-subtle)] bg-[var(--surface-1)] p-4">
                        <div className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
                            <Github className="h-4 w-4 text-[var(--text-tertiary)]" />
                            Publish to GitHub
                        </div>
                        <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                            Creates a repo with a README and a <span className="font-medium">Run it on Lemma</span>{' '}
                            button — a durable, shareable install link.
                        </p>

                        {published ? (
                            <div className="mt-4 space-y-3">
                                {publishedInstallUrl ? (
                                    <SocialCardPanel
                                        variant="run"
                                        name={podName}
                                        url={publishedInstallUrl}
                                        label={published.repo_url?.replace(/^https?:\/\//, '')}
                                        // The install route is public and serves this
                                        // very card in its Open Graph tags.
                                        unfurls
                                    />
                                ) : null}
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
                                        onClick={() => publishedInstallUrl && copy(publishedInstallUrl, 'Run link')}
                                        disabled={!publishedInstallUrl}
                                    >
                                        <Copy className="mr-2 h-3.5 w-3.5" />
                                        Copy run link
                                    </Button>
                                    <Button
                                        variant="secondary"
                                        size="sm"
                                        className="flex-1"
                                        onClick={sharePublishedPod}
                                        disabled={!publishedInstallUrl}
                                    >
                                        <Share2 className="mr-2 h-3.5 w-3.5" />
                                        Share
                                    </Button>
                                </div>
                                <p className="text-xs text-[var(--text-tertiary)]">
                                    {published.private
                                        ? 'Private repositories can be imported by members who select an authorized GitHub account.'
                                        : 'This link opens the pod directly in Lemma. The GitHub repository remains its public, inspectable source.'}
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
                                <AccountVariableField
                                    organizationId={pod?.organization_id}
                                    podId={podId}
                                    connectorId="github"
                                    connectorKind="composio"
                                    label="GitHub account"
                                    description="The connected account that owns and publishes the repository."
                                    required
                                    value={githubAccountId}
                                    onChange={(value) => {
                                        setGithubAccountId(value);
                                        setNeedsGithub(false);
                                    }}
                                />
                                <div className="space-y-2">
                                    <Label className="text-xs">Publish mode</Label>
                                    <div className="grid grid-cols-2 gap-2">
                                        {(['CREATE', 'UPDATE'] as PublishMode[]).map((mode) => (
                                            <Button
                                                key={mode}
                                                type="button"
                                                variant={publishMode === mode ? 'primary' : 'secondary'}
                                                size="sm"
                                                onClick={() => setPublishMode(mode)}
                                            >
                                                {mode === 'CREATE' ? 'Create new' : 'Update existing'}
                                            </Button>
                                        ))}
                                    </div>
                                    {publishMode === 'UPDATE' ? (
                                        <p className="text-xs text-[var(--text-tertiary)]">
                                            Replaces README and Lemma-managed bundle files, removes stale managed
                                            files, and preserves unrelated repository content.
                                        </p>
                                    ) : (
                                        <p className="text-xs text-[var(--text-tertiary)]">
                                            Fails safely if this repository name already exists.
                                        </p>
                                    )}
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
                                {publishMode === 'CREATE' ? (
                                    <div className="flex items-center justify-between gap-3">
                                        <span className="text-sm text-[var(--text-secondary)]">Private repository</span>
                                        <Toggle checked={isPrivate} onCheckedChange={setIsPrivate} />
                                    </div>
                                ) : (
                                    <p className="text-xs text-[var(--text-tertiary)]">
                                        Update keeps the existing repository&apos;s visibility.
                                    </p>
                                )}

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

                                <Button variant="primary"
                                    className="w-full"
                                    onClick={handlePublish}
                                    disabled={!repoName.trim() || !githubAccountId}
                                >
                                    <Github className="mr-2 h-4 w-4" />
                                    Publish to GitHub
                                </Button>
                            </div>
                        )}
                    </section>
                    ) : null}
                </div>
            </SheetContent>
        </Sheet>
    );
}
