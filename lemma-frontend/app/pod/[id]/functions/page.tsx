'use client';

import { use, useState } from 'react';
import Link from 'next/link';
import { ChevronRight, Edit2, MoreHorizontal, Play, Plus, Trash2, Workflow, Zap } from '@/components/ui/icons';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import { ConceptHint } from '@/components/education/concept-hint';
import { SectionPrimer } from '@/components/education/section-primer';
import { ResourceHeader, ResourceIndexShell, ResourceMetric, ResourceMetricStrip } from '@/components/pod/resource-layout';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { EmptyState } from '@/components/shared/empty-state';
import { AsyncRegion, ListSkeleton } from '@/components/shared/loading';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { ResourceVisibilityBadge } from '@/components/shared/resource-visibility';
import { useFunctions, useDeleteFunction } from '@/lib/hooks/use-functions';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import type { Function as FunctionType } from '@/lib/types';

export default function FunctionsIndexPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const canCreateFunction = podAccess.can('function.create');
    const canUpdateFunction = podAccess.can('function.update');
    const canExecuteFunction = podAccess.can('function.execute');
    const canDeleteFunction = podAccess.can('function.delete');
    const { data: functionsData, isLoading } = useFunctions(podId);
    const { mutate: deleteFunction, isPending: isDeletingFunction } = useDeleteFunction();
    const [functionPendingDelete, setFunctionPendingDelete] = useState<FunctionType | null>(null);

    const functions = functionsData?.items || [];
    const filteredFunctions = functions;

    const handleDelete = () => {
        if (!functionPendingDelete) return;
        if (!resourceAllows(functionPendingDelete, 'function.delete', canDeleteFunction)) return;
        deleteFunction(
            { podId, name: functionPendingDelete.name },
            {
                onSuccess: () => {
                    toast.success('Function deleted');
                    setFunctionPendingDelete(null);
                },
                onError: () => toast.error('Failed to delete function'),
            }
        );
    };

    return (
        <ResourceIndexShell>
            <ResourceHeader
                title="Functions"
                meta={<ConceptHint concept="function" />}
                backHref={`/pod/${podId}/flows`}
                backLabel="Workflows"
                actions={(
                    <>
                        <Link href={`/pod/${podId}/flows`}>
                            <Button variant="quiet" className="functions-index-peer-button gap-2 bg-[var(--bg-subtle)] text-[var(--text-secondary)] hover:bg-[var(--bg-canvas)] hover:text-[var(--text-primary)]" size="sm">
                                <Workflow className="h-4 w-4" />
                                Workflows
                            </Button>
                        </Link>
                        {canCreateFunction ? (
                            <Link href={`/pod/${podId}/functions/new`}>
                                <Button variant="secondary" className="gap-2" size="sm">
                                    <Plus className="h-4 w-4" />
                                    New Function
                                </Button>
                            </Link>
                        ) : null}
                    </>
                )}
            />

            <SectionPrimer concept="function" className="mb-4" />

            {/* The strip stays put across all three states — its label is known
                before the query is, and the count says `—` until it isn't. */}
            <ResourceMetricStrip className="lemma-index-tabs-left">
                <ResourceMetric label="Functions" value={isLoading ? undefined : functions.length} active />
            </ResourceMetricStrip>

            <AsyncRegion
                isLoading={isLoading}
                isEmpty={functions.length === 0}
                label="Loading functions"
                skeleton={<ListSkeleton rows={5} />}
                empty={(
                    <EmptyState
                        variant="region"
                        icon={<Zap className="h-5 w-5" />}
                        title="No functions yet"
                        description={canCreateFunction
                            ? "Add a reusable capability that agents and workflows can call when the pod needs to act."
                            : "Functions created for this pod will appear here when you have access to them."}
                        action={canCreateFunction ? (
                            <Link href={`/pod/${podId}/functions/new`}>
                                <Button variant="primary" size="sm" className="gap-2">
                                    <Plus className="h-4 w-4" />
                                    New function
                                </Button>
                            </Link>
                        ) : null}
                    />
                )}
            >
                <div className="lemma-index-list">
                    {filteredFunctions.map((fn) => (
                        <FunctionRow
                            key={fn.id}
                            func={fn}
                            podId={podId}
                            canUpdate={resourceAllows(fn, 'function.update', canUpdateFunction)}
                            canExecute={resourceAllows(fn, 'function.execute', canExecuteFunction)}
                            canDelete={resourceAllows(fn, 'function.delete', canDeleteFunction)}
                            onDelete={setFunctionPendingDelete}
                        />
                    ))}
                </div>
            </AsyncRegion>
            <DestructiveConfirmationDialog
                open={Boolean(functionPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setFunctionPendingDelete(null);
                }}
                title="Delete function"
                description={`Delete "${functionPendingDelete?.name ?? ''}"? This removes the callable code from this pod.`}
                resourceName={functionPendingDelete?.name ?? ''}
                consequences={[
                    'Workflows and agents using this function may fail until they are updated.',
                    'Function run history may no longer point to an editable function definition.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete function"
                pendingLabel="Deleting function..."
                isPending={isDeletingFunction}
                onConfirm={handleDelete}
            />
        </ResourceIndexShell>
    );
}

function FunctionRow({
    func,
    podId,
    canUpdate,
    canExecute,
    canDelete,
    onDelete,
}: {
    func: FunctionType;
    podId: string;
    canUpdate: boolean;
    canExecute: boolean;
    canDelete: boolean;
    onDelete: (func: FunctionType) => void;
}) {
    const hasMenuActions = canUpdate || canExecute || canDelete;

    return (
        <div className="lemma-index-row group flex items-center gap-2.5">
            <span className="state-badge-brand flex h-6 w-6 shrink-0 items-center justify-center rounded-md">
                <Zap className="h-3.5 w-3.5" />
            </span>

            <Link
                href={`/pod/${podId}/functions/${encodeURIComponent(func.name)}`}
                className="custom-focus-ring flex min-w-0 flex-1 items-baseline gap-2 rounded"
            >
                <h3 className="truncate font-display text-base font-medium text-[var(--text-primary)]">{func.name}</h3>
                <p className="hidden truncate text-xs text-[var(--text-secondary)] md:block">
                    {func.description || 'No description yet'}
                </p>
            </Link>

            <ResourceVisibilityBadge visibility={func.visibility} resourceLabel="functions" compact />
            <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] opacity-0 transition-opacity group-hover:opacity-100 md:inline">Function</span>
            <div className="flex items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                {hasMenuActions ? (
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button
                                type="button"
                                aria-label={`Open function menu for ${func.name}`}
                                className="functions-index-menu-button custom-focus-ring flex h-8 w-8 items-center justify-center rounded-md text-[var(--text-tertiary)] transition-gentle hover:bg-[var(--bg-subtle)] hover:text-[var(--text-secondary)]"
                            >
                                <MoreHorizontal className="h-4 w-4" />
                            </button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                            {canUpdate ? (
                                <DropdownMenuItem onSelect={(event) => { event.preventDefault(); event.stopPropagation(); }}>
                                    <Edit2 className="mr-2 h-4 w-4" />
                                    Edit
                                </DropdownMenuItem>
                            ) : null}
                            {canExecute ? (
                                <DropdownMenuItem onSelect={(event) => { event.preventDefault(); event.stopPropagation(); }}>
                                    <Play className="mr-2 h-4 w-4" />
                                    Test
                                </DropdownMenuItem>
                            ) : null}
                            {canDelete ? (
                                <>
                                    {(canUpdate || canExecute) ? <DropdownMenuSeparator /> : null}
                                    <DropdownMenuItem
                                        className="text-[var(--state-error)]"
                                        onSelect={(event) => {
                                            event.preventDefault();
                                            event.stopPropagation();
                                            onDelete(func);
                                        }}
                                    >
                                        <Trash2 className="mr-2 h-4 w-4" />
                                        Delete
                                    </DropdownMenuItem>
                                </>
                            ) : null}
                        </DropdownMenuContent>
                    </DropdownMenu>
                ) : null}

                <Link
                    href={`/pod/${podId}/functions/${encodeURIComponent(func.name)}`}
                    className="custom-focus-ring rounded"
                    aria-label={`Open function ${func.name}`}
                >
                    <ChevronRight className="h-4 w-4 text-[var(--text-tertiary)] transition-gentle group-hover:text-[var(--text-secondary)]" />
                </Link>
            </div>
        </div>
    );
}
