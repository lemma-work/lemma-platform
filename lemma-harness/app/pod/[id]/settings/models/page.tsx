'use client';

import { use, useState } from 'react';
import type { AgentRuntimeConfig } from 'lemma-sdk';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { findProfileByRuntime, resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { ModelsSettings } from '@/components/agents/models-settings';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { PodModelsFill } from '@/components/pod/route-skeletons';
import {
    useAgentRuntimes,
    useUpdatePodDefaultAgentRuntime,
} from '@/lib/hooks/use-agent-runtime';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePod } from '@/lib/hooks/use-pods';

export default function PodModelsPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <PodModelsPageContent params={params} />
        </ProtectedRoute>
    );
}

/**
 * What this pod runs on, and what it can run on — one page.
 *
 * The catalog below is organization-wide: a provider key is bought, billed and
 * rotated once, so it is stored against the organization and every pod reads
 * the same list. That is a storage boundary, not a navigation one, and giving
 * it its own page under a second noun meant four places in the pod linked out
 * to a different shell and carried a `returnTo` to find their way back. The
 * boundary still exists — it is printed on the rows that are shared — but you
 * never leave the pod to cross it.
 */
function PodModelsPageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const { data: pod, isLoading: isLoadingPod } = usePod(podId);
    const organizationId = pod?.organization_id;
    // ModelsSettings reads the management listing itself. This catalog query is
    // what the rest of the pod (every model picker) shares, so keep refreshing
    // it alongside — a rename here changes what those pickers offer.
    const { data: runtimeCatalog, refetch: refetchRuntimeCatalog } = useAgentRuntimes(organizationId);
    const updatePodDefaultRuntime = useUpdatePodDefaultAgentRuntime();
    const [runtimeDraft, setRuntimeDraft] = useState<AgentRuntimeConfig | null>(null);

    const canUpdatePod = podAccess.can('pod.update');
    // Prefer the full stored runtime (profile + model); fall back to the legacy
    // provider-only default, resolving its model from the profile for display.
    const storedRuntime = pod?.config?.default_runtime
        ?? (pod?.config?.default_profile_id
            ? resolveDefaultAgentRuntime(runtimeCatalog, pod.config.default_profile_id)
            : null);
    // A stored default can name a profile that has since been archived. The
    // picker sets allowAuto={false}, so there is no Auto row to fall back to and
    // every agent in the pod would silently inherit a dead default. Resolve the
    // modern path through the catalog too, and say so rather than degrade.
    const storedRuntimeIsMissing = Boolean(
        storedRuntime?.profile_id
        && runtimeCatalog
        && !findProfileByRuntime(runtimeCatalog, storedRuntime),
    );
    const selectedRuntime = runtimeDraft ?? (storedRuntimeIsMissing ? null : storedRuntime);

    const handleRuntimeCommit = (runtime: AgentRuntimeConfig | null) => {
        setRuntimeDraft(runtime);
        updatePodDefaultRuntime.mutate({
            podId,
            runtime,
        }, {
            onSuccess: () => setRuntimeDraft(null),
        });
    };

    return (
        // `form`, not the ledger width: these rows are a name, a fact and a
        // status, and stretched to the six-column measure the status ended up an
        // inch of empty page away from the model it belongs to.
        <PodSettingsShell podId={podId} title="Models" width="form">
            {isLoadingPod || !organizationId ? <PodModelsFill /> : (
                <ModelsSettings
                    organizationId={organizationId}
                    /*
                     * The contents of the pod default line, not its chrome.
                     * `ModelsSettings` owns the strip so it can seat Recheck on
                     * the same line — and in the body rather than the shell's
                     * action slot, which hands actions to the shared context bar
                     * and drops them when the bar runs out of room, hiding the
                     * one setting most people open this page to change.
                     */
                    defaultRow={(
                        <>
                            <div className="min-w-0">
                                <div className="text-sm font-medium text-[var(--text-primary)]">
                                    Pod default
                                </div>
                                <div className="mt-0.5 text-xs text-[var(--text-tertiary)]">
                                    {storedRuntimeIsMissing
                                        ? 'The model this pod used was archived. Pick another, or restore it below.'
                                        : 'Agents with no model of their own, and every new conversation.'}
                                </div>
                            </div>
                            <RuntimeModelPicker
                                catalog={runtimeCatalog}
                                defaultRuntime={runtimeCatalog?.default_runtime ?? null}
                                value={selectedRuntime}
                                onChange={handleRuntimeCommit}
                                disabled={!canUpdatePod}
                                ariaLabel="Pod default model"
                                allowAuto={false}
                                scopeHint="Pod default"
                            />
                        </>
                    )}
                    onRefresh={() => {
                        void refetchRuntimeCatalog();
                    }}
                />
            )}
        </PodSettingsShell>
    );
}
