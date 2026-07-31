'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import { ArrowRight, Plus, Search } from '@/components/ui/icons';
import { toast } from 'sonner';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { Button } from '@/components/ui/button';
import { useDeletePod, type AccessiblePod } from '@/lib/hooks/use-pods';

export function HomeWorkspaceOverview({
    pods,
    showOrganizationName,
    showCreateAction,
    isLoading,
    error,
}: {
    pods: AccessiblePod[];
    showOrganizationName?: boolean;
    showCreateAction?: boolean;
    isLoading?: boolean;
    error?: unknown;
}) {
    const { mutate: deletePod, isPending: isDeletingPod } = useDeletePod();
    const [podPendingDelete, setPodPendingDelete] = useState<AccessiblePod | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const showSearch = pods.length > 4;
    const filteredPods = useMemo(() => {
        const query = searchQuery.trim().toLowerCase();
        if (!query) return pods;

        return pods.filter((pod) => (
            pod.name.toLowerCase().includes(query) ||
            (pod.description || '').toLowerCase().includes(query) ||
            (pod.organization_name || '').toLowerCase().includes(query)
        ));
    }, [pods, searchQuery]);

    const handleDeletePod = () => {
        if (!podPendingDelete) return;

        deletePod(podPendingDelete.id, {
            onSuccess: () => {
                toast.success('Pod deleted');
                setPodPendingDelete(null);
            },
            onError: () => toast.error('Failed to delete pod'),
        });
    };

    return (
        <div className="mx-auto w-full max-w-6xl">
            <section>
                <div>
                    {showCreateAction || showSearch ? (
                        <div className="mb-3 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                            {showSearch ? (
                                <div className="relative w-full sm:max-w-xs">
                                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-tertiary)]" />
                                    <input
                                        type="search"
                                        value={searchQuery}
                                        onChange={(event) => setSearchQuery(event.target.value)}
                                        placeholder="Search pods"
                                        className="form-field-control h-10 w-full pl-9 pr-3 text-sm text-[var(--text-primary)] outline-none placeholder:text-[var(--text-soft)] focus-ring"
                                    />
                                </div>
                            ) : (
                                <span />
                            )}
                            {showCreateAction ? (
                                <Button asChild size="sm" className="w-full gap-2 px-4 sm:w-auto">
                                    <Link href="/create-pod">
                                        <Plus className="h-4 w-4" />
                                        New Pod
                                    </Link>
                                </Button>
                            ) : null}
                        </div>
                    ) : null}

                    {error ? (
                        <div className="surface-panel-muted px-4 py-4 text-sm text-[var(--state-error)]">
                            Failed to load pods.
                        </div>
                    ) : isLoading ? (
                        <div className="overflow-hidden border-b border-[color:color-mix(in_srgb,var(--row-border)_48%,transparent)]">
                            {Array.from({ length: 4 }).map((_, index) => (
                                <div
                                    key={index}
                                    className="flex min-h-[6.25rem] animate-pulse items-center gap-4 border-b border-[color:color-mix(in_srgb,var(--row-border)_48%,transparent)] px-4 py-5 last:border-b-0 sm:px-5"
                                >
                                    <div className="h-12 w-12 rounded-lg bg-[var(--bg-subtle)]" />
                                    <div className="min-w-0 flex-1 space-y-2">
                                        <div className="h-4 w-56 max-w-full rounded bg-[var(--bg-subtle)]" />
                                        <div className="h-3 w-80 max-w-full rounded bg-[var(--bg-subtle)]" />
                                        <div className="h-3 w-64 max-w-full rounded bg-[var(--bg-subtle)]" />
                                    </div>
                                </div>
                            ))}
                        </div>
                    ) : filteredPods.length > 0 ? (
                        <div className="overflow-hidden border-b border-[color:color-mix(in_srgb,var(--row-border)_48%,transparent)]">
                            {filteredPods.map((pod) => (
                                <article
                                    key={pod.id}
                                    className="group flex min-h-[6.25rem] min-w-0 items-center gap-4 border-b border-[color:color-mix(in_srgb,var(--row-border)_48%,transparent)] px-4 py-5 last:border-b-0 hover:bg-[color:color-mix(in_srgb,var(--surface-2)_28%,transparent)] sm:px-5"
                                >
                                    <Link
                                        href={`/pod/${pod.id}`}
                                        className="custom-focus-ring rounded-lg"
                                        aria-label={`Open ${pod.name}`}
                                    >
                                        <ResourceIcon
                                            iconUrl={pod.icon_url}
                                            alt={`${pod.name} icon`}
                                            label={pod.name}
                                            className="h-12 w-12 rounded-lg bg-[var(--delight-soft)] text-[var(--delight)]"
                                        />
                                    </Link>
                                    <Link
                                        href={`/pod/${pod.id}`}
                                        className="custom-focus-ring flex min-w-0 flex-1 items-center gap-4 rounded py-1"
                                    >
                                        <span className="min-w-0 flex-1">
                                            <span className="block truncate text-sm font-medium text-[var(--text-primary)]">{pod.name}</span>
                                            {showOrganizationName && pod.organization_name ? (
                                                <span className="mt-1 block truncate text-xs text-[var(--text-tertiary)]">
                                                    {pod.organization_name}
                                                </span>
                                            ) : null}
                                            <span className="mt-1 line-clamp-2 block text-sm leading-6 text-[var(--text-secondary)]">
                                                {pod.description || 'Open the workspace and continue where the pod left off.'}
                                            </span>
                                        </span>
                                    </Link>
                                    <div className="flex shrink-0 items-center gap-2">
                                        <ArrowRight className="h-4 w-4 text-[var(--text-tertiary)] transition-transform group-hover:translate-x-0.5 group-hover:text-[var(--text-primary)]" />
                                        <ResourceActionsMenu ariaLabel={`Open actions for ${pod.name}`} triggerClassName="h-7 w-7">
                                            <DestructiveResourceActionItem onSelect={() => setPodPendingDelete(pod)}>
                                                Delete pod
                                            </DestructiveResourceActionItem>
                                        </ResourceActionsMenu>
                                    </div>
                                </article>
                            ))}
                        </div>
                    ) : pods.length > 0 ? (
                        <div className="surface-panel-muted px-4 py-4 text-sm text-[var(--text-secondary)]">
                            No pods match that search.
                        </div>
                    ) : (
                        <div className="surface-panel-muted flex flex-col gap-4 px-4 py-4 sm:flex-row sm:items-center sm:justify-between">
                            <div className="min-w-0">
                                <p className="text-sm font-medium text-[var(--text-primary)]">No pods yet</p>
                                <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">
                                    Start with the work loop you want Lemma to help operate.
                                </p>
                            </div>
                            <div className="shrink-0">
                                <Link
                                    href="/create-pod"
                                    className="inline-flex items-center gap-2 rounded-md border border-[var(--row-border)] bg-[var(--card-bg)] px-3 py-2 text-sm font-medium text-[var(--text-primary)] transition-colors hover:bg-[var(--card-bg-hover)]"
                                >
                                    <Plus className="h-4 w-4" />
                                    New Pod
                                </Link>
                            </div>
                        </div>
                    )}
                </div>
            </section>
            <DestructiveConfirmationDialog
                open={Boolean(podPendingDelete)}
                onOpenChange={(open) => {
                    if (!open) setPodPendingDelete(null);
                }}
                title="Delete pod"
                description={`Delete "${podPendingDelete?.name ?? 'this pod'}"? This removes the workspace and its operating surfaces.`}
                resourceName={podPendingDelete?.name ?? ''}
                consequences={[
                    'Apps, agents, workflows, schedules, tables, docs, and pod context inside this pod will be removed.',
                    'People with access will no longer be able to open this workspace.',
                    'This action cannot be undone.',
                ]}
                confirmLabel="Delete pod"
                pendingLabel="Deleting pod..."
                isPending={isDeletingPod}
                onConfirm={handleDeletePod}
            />
        </div>
    );
}
