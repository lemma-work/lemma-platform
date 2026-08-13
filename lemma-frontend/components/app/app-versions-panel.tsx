'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { Check, ExternalLink, History, RotateCcw } from '@/components/ui/icons';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
} from '@/components/ui/sheet';
import { EmptyState } from '@/components/shared/empty-state';
import { StepLoader } from '@/components/brand/loader';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import {
    useAppReleases,
    usePromoteAppRelease,
    type AppRelease,
} from '@/lib/hooks/use-app-releases';

interface AppVersionsPanelProps {
    podId: string;
    appName: string | null;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** Show a release without promoting it. */
    onPreview?: (release: AppRelease) => void;
    /** The release currently being previewed, if any. */
    previewingReleaseNumber?: number | null;
    canPromote?: boolean;
}

function shortDigest(version: string) {
    return version.replace(/^sha256:/, '').slice(0, 7);
}

export function AppVersionsPanel({
    podId,
    appName,
    open,
    onOpenChange,
    onPreview,
    previewingReleaseNumber,
    canPromote = false,
}: AppVersionsPanelProps) {
    const { data: releases, isLoading } = useAppReleases(podId, appName, open);
    const promote = usePromoteAppRelease(podId, appName);
    const [pendingPromote, setPendingPromote] = useState<AppRelease | null>(null);

    const confirmPromote = async () => {
        if (!pendingPromote) return;
        try {
            await promote.mutateAsync(`v${pendingPromote.release_number}`);
            toast.success(`v${pendingPromote.release_number} is now live`);
            setPendingPromote(null);
        } catch {
            toast.error('Could not change the live version');
        }
    };

    return (
        <Sheet open={open} onOpenChange={onOpenChange}>
            <SheetContent side="right" className="w-full overflow-y-auto sm:max-w-md">
                <SheetHeader>
                    <SheetTitle className="flex items-center gap-2">
                        <History className="h-4 w-4" />
                        Versions
                    </SheetTitle>
                    <SheetDescription>
                        Every deploy of this app. Preview one to see it without changing
                        what visitors get.
                    </SheetDescription>
                </SheetHeader>

                {isLoading ? (
                    <div className="flex justify-center py-10">
                        <StepLoader size="sm" />
                    </div>
                ) : null}

                {!isLoading && !releases?.length ? (
                    <EmptyState
                        variant="region"
                        icon={<History className="h-5 w-5" />}
                        title="No versions yet"
                        description="Deploy this app and each build will show up here."
                    />
                ) : null}

                <ul className="mt-4 space-y-2">
                    {(releases ?? []).map((release) => {
                        const isPruned = Boolean(release.pruned_at);
                        const isPreviewing = previewingReleaseNumber === release.release_number;
                        return (
                            <li
                                key={release.id}
                                className={`rounded-lg border border-[var(--border-subtle)] p-3 ${
                                    isPruned ? 'opacity-60' : ''
                                }`}
                            >
                                <div className="flex items-center justify-between gap-2">
                                    <div className="flex min-w-0 items-center gap-2">
                                        <span className="font-medium text-[var(--text-primary)]">
                                            v{release.release_number}
                                        </span>
                                        <code className="text-xs text-[var(--text-tertiary)]">
                                            {shortDigest(release.version)}
                                        </code>
                                        {release.is_live ? (
                                            <Badge variant="success">Live</Badge>
                                        ) : null}
                                        {isPreviewing ? (
                                            <Badge variant="info">Previewing</Badge>
                                        ) : null}
                                    </div>
                                    <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                                        {formatRelativeTime(release.created_at) ?? ''}
                                    </span>
                                </div>

                                {isPruned ? (
                                    // The row stays so the history has no unexplained
                                    // gaps, but there are no bytes left to serve.
                                    <p className="mt-2 text-xs text-[var(--text-tertiary)]">
                                        Build removed to save space
                                        {formatRelativeTime(release.pruned_at)
                                            ? ` ${formatRelativeTime(release.pruned_at)}`
                                            : ''}
                                        .
                                    </p>
                                ) : (
                                    <div className="mt-2 flex flex-wrap items-center gap-2">
                                        {!release.is_live && onPreview ? (
                                            <Button
                                                type="button"
                                                variant="quiet"
                                                size="sm"
                                                className="h-7 gap-1.5 px-2 text-xs"
                                                onClick={() => onPreview(release)}
                                            >
                                                <ExternalLink className="h-3.5 w-3.5" />
                                                Preview
                                            </Button>
                                        ) : null}
                                        {!release.is_live && canPromote ? (
                                            <Button
                                                type="button"
                                                variant="quiet"
                                                size="sm"
                                                className="h-7 gap-1.5 px-2 text-xs"
                                                onClick={() => setPendingPromote(release)}
                                            >
                                                <RotateCcw className="h-3.5 w-3.5" />
                                                Set live
                                            </Button>
                                        ) : null}
                                        {release.is_live ? (
                                            <span className="flex items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                                                <Check className="h-3.5 w-3.5" />
                                                Serving now
                                            </span>
                                        ) : null}
                                        {!release.has_source ? (
                                            <span className="text-xs text-[var(--text-tertiary)]">
                                                No source
                                            </span>
                                        ) : null}
                                    </div>
                                )}

                                {pendingPromote?.id === release.id ? (
                                    <div className="mt-3 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] p-3">
                                        <p className="text-xs text-[var(--text-secondary)]">
                                            Make v{release.release_number} the version everyone
                                            gets? The current build stays in this list and can be
                                            set live again.
                                        </p>
                                        <div className="mt-2 flex gap-2">
                                            <Button
                                                type="button"
                                                variant="primary"
                                                size="sm"
                                                className="h-7 px-2 text-xs"
                                                disabled={promote.isPending}
                                                onClick={() => void confirmPromote()}
                                            >
                                                {promote.isPending ? 'Switching…' : 'Set live'}
                                            </Button>
                                            <Button
                                                type="button"
                                                variant="quiet"
                                                size="sm"
                                                className="h-7 px-2 text-xs"
                                                onClick={() => setPendingPromote(null)}
                                            >
                                                Cancel
                                            </Button>
                                        </div>
                                    </div>
                                ) : null}
                            </li>
                        );
                    })}
                </ul>
            </SheetContent>
        </Sheet>
    );
}
