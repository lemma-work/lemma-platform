'use client';

import { useState } from 'react';
import { toast } from 'sonner';
import { AlertTriangle, Check, Play, RotateCcw } from '@/components/ui/icons';

import { Badge } from '@/components/ui/badge';
import { Button } from '@/components/ui/button';
import { EmptyState } from '@/components/shared/empty-state';
import { StepLoader } from '@/components/brand/loader';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import {
    useFunctionRevision,
    useFunctionRevisions,
    usePromoteFunctionRevision,
    type FunctionRevision,
} from '@/lib/hooks/use-function-revisions';

interface FunctionRevisionsTabProps {
    podId: string;
    functionName: string;
    canUpdate: boolean;
    /** Run this revision without promoting it. */
    onRunRevision?: (revision: FunctionRevision) => void;
}

function shortHash(hash: string) {
    return hash.replace(/^sha256:/, '').slice(0, 7);
}

export function FunctionRevisionsTab({
    podId,
    functionName,
    canUpdate,
    onRunRevision,
}: FunctionRevisionsTabProps) {
    const { data: revisions, isLoading, isError, refetch } = useFunctionRevisions(podId, functionName);
    const promote = usePromoteFunctionRevision(podId, functionName);
    const [expandedRef, setExpandedRef] = useState<string | null>(null);
    const [pendingPromote, setPendingPromote] = useState<FunctionRevision | null>(null);
    const { data: expanded, isLoading: isLoadingCode, isError: codeError, refetch: retryCode } = useFunctionRevision(podId, functionName, expandedRef);

    const confirmPromote = async () => {
        if (!pendingPromote) return;
        try {
            const result = await promote.mutateAsync(`r${pendingPromote.revision_number}`);
            if (result?.schema_changed) {
                // The schemas move with the revision, so anything bound to the
                // previous contract can break — say so rather than letting it
                // surface later as a confusing runtime failure.
                toast.warning(
                    `r${pendingPromote.revision_number} is live. Its input or output ` +
                    'shape differs from the previous version — check agents and ' +
                    'workflows that call it.',
                );
            } else {
                toast.success(`r${pendingPromote.revision_number} is now live`);
            }
            setPendingPromote(null);
        } catch {
            toast.error('Could not change the live revision');
        }
    };

    if (isLoading) {
        return (
            <div className="flex justify-center py-10">
                <StepLoader size="sm" />
            </div>
        );
    }

    if (isError) {
        return <EmptyState title="Could not load versions" description="Try again to retrieve this function's history."
            action={<Button variant="quiet" onClick={() => void refetch()}>Retry</Button>} />;
    }

    if (!revisions?.length) {
        return (
            <EmptyState
                variant="region"
                icon={<RotateCcw className="h-5 w-5" />}
                title="No revisions yet"
                description="Save this function's code and each build will show up here."
            />
        );
    }

    return (
        <ul className="space-y-2">
            {revisions.map((revision) => {
                const isPruned = Boolean(revision.pruned_at);
                const ref = `r${revision.revision_number}`;
                const isExpanded = expandedRef === ref;
                return (
                    <li
                        key={revision.id}
                        className={`rounded-lg border border-[var(--border-subtle)] p-3 ${
                            isPruned ? 'opacity-60' : ''
                        }`}
                    >
                        <div className="flex items-center justify-between gap-2">
                            <div className="flex min-w-0 items-center gap-2">
                                <span className="text-sm font-medium text-[var(--text-primary)]">
                                    {ref}
                                </span>
                                <code className="text-xs text-[var(--text-tertiary)]">
                                    {shortHash(revision.revision_hash)}
                                </code>
                                {revision.is_live ? <Badge variant="success">Live</Badge> : null}
                            </div>
                            <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
                                {formatRelativeTime(revision.created_at) ?? ''}
                            </span>
                        </div>

                        {isPruned ? (
                            <p className="mt-2 text-xs text-[var(--text-tertiary)]">
                                Build removed to save space. Runs that used it still show
                                this version.
                            </p>
                        ) : (
                            <div className="mt-2 flex flex-wrap items-center gap-2">
                                <Button
                                    type="button"
                                    variant="quiet"
                                    size="sm"
                                    className="h-7 px-2 text-xs"
                                    onClick={() => setExpandedRef(isExpanded ? null : ref)}
                                >
                                    {isExpanded ? 'Hide code' : 'View code'}
                                </Button>
                                {/* Gated on canUpdate like "Set live" beside it:
                                    pinning a run to a superseded build requires
                                    function.update, so an execute-only user was
                                    being shown a button that 403s. */}
                                {!revision.is_live && onRunRevision && canUpdate ? (
                                    <Button
                                        type="button"
                                        variant="quiet"
                                        size="sm"
                                        className="h-7 gap-1.5 px-2 text-xs"
                                        onClick={() => onRunRevision(revision)}
                                    >
                                        <Play className="h-3.5 w-3.5" />
                                        Run this
                                    </Button>
                                ) : null}
                                {!revision.is_live && canUpdate ? (
                                    <Button
                                        type="button"
                                        variant="quiet"
                                        size="sm"
                                        className="h-7 gap-1.5 px-2 text-xs"
                                        onClick={() => setPendingPromote(revision)}
                                    >
                                        <RotateCcw className="h-3.5 w-3.5" />
                                        Set live
                                    </Button>
                                ) : null}
                                {revision.is_live ? (
                                    <span className="flex items-center gap-1.5 text-xs text-[var(--text-tertiary)]">
                                        <Check className="h-3.5 w-3.5" />
                                        Running now
                                    </span>
                                ) : null}
                            </div>
                        )}

                        {isExpanded && codeError ? (
                            <div role="alert" className="mt-3 text-sm text-[var(--text-secondary)]">
                                Could not load this version&apos;s code.
                                <Button variant="quiet" onClick={() => void retryCode()}>Retry</Button>
                            </div>
                        ) : isExpanded ? (
                            <pre className="mt-3 max-h-64 overflow-auto rounded-md bg-[var(--bg-subtle)] p-3 text-xs text-[var(--text-secondary)]">
                                {isLoadingCode ? 'Loading…' : (expanded?.code ?? 'Source is unavailable for this version.')}
                            </pre>
                        ) : null}

                        {pendingPromote?.id === revision.id ? (
                            <div className="mt-3 rounded-md border border-[var(--border-subtle)] bg-[var(--surface-2)] p-3">
                                <p className="flex items-start gap-2 text-xs text-[var(--text-secondary)]">
                                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                                    <span>
                                        Make {ref} the version this function runs? Its input and
                                        output shapes are restored with it, so callers built
                                        against the current version may need updating.
                                    </span>
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
    );
}
