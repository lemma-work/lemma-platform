'use client';

import { use, useState } from 'react';
import type { AgentRuntimeConfig } from 'lemma-sdk';
import { Check, Info, Loader2 } from 'lucide-react';

import { toast } from 'sonner';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { AgentRuntimeSelector, resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-selector';
import { PodSettingsPanel, PodSettingsShell } from '@/components/pod/pod-settings-shell';
import {
    useAgentRuntimes,
    useAvailableAgentRuntimeHarnesses,
    useUpdatePodDefaultAgentRuntime,
} from '@/lib/hooks/use-agent-runtime';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePod, useUpdatePod } from '@/lib/hooks/use-pods';
import { PodJoinPolicy } from '@/lib/types';
import { cn } from '@/lib/utils';

export default function PodSettingsPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <PodSettingsPageContent params={params} />
        </ProtectedRoute>
    );
}

function PodSettingsPageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const { data: pod, isLoading: isLoadingPod } = usePod(podId);
    const {
        data: runtimeCatalog,
        isFetching: isFetchingRuntimeCatalog,
        isLoading: isLoadingRuntimeCatalog,
        refetch: refetchRuntimeCatalog,
    } = useAgentRuntimes(pod?.organization_id);
    const {
        data: availableHarnesses,
        isFetching: isFetchingAvailableHarnesses,
        isLoading: isLoadingAvailableHarnesses,
        refetch: refetchAvailableHarnesses,
    } = useAvailableAgentRuntimeHarnesses();
    const updatePodDefaultRuntime = useUpdatePodDefaultAgentRuntime();
    const [runtimeDraft, setRuntimeDraft] = useState<AgentRuntimeConfig | null>(null);

    const canUpdatePod = podAccess.can('pod.update');
    const podDefaultRuntime = resolveDefaultAgentRuntime(runtimeCatalog, pod?.config?.default_profile_id, availableHarnesses);
    const selectedRuntime = runtimeDraft ?? (pod?.config?.default_profile_id ? podDefaultRuntime : null);

    const handleRuntimeCommit = (runtime: AgentRuntimeConfig | null) => {
        setRuntimeDraft(runtime);
        updatePodDefaultRuntime.mutate({
            podId,
            agentRuntimeId: runtime?.profile_id ?? null,
        }, {
            onSuccess: () => setRuntimeDraft(null),
        });
    };

    if (isLoadingPod) {
        return (
            <div className="context-shell flex min-h-full items-center justify-center bg-transparent">
                <div className="surface-panel px-5 py-4">
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                </div>
            </div>
        );
    }

    return (
        <PodSettingsShell
            podId={podId}
            title="Pod Settings"
            description="Configure defaults that shape how this pod runs."
        >
            <div className="mx-auto flex w-full max-w-3xl flex-col gap-5">
            <PodSettingsPanel
                title="Default Agent Runtime"
                description="Agents without a pinned model and new conversations use this runtime."
            >
                <AgentRuntimeSelector
                    catalog={runtimeCatalog}
                    availableHarnesses={availableHarnesses}
                    organizationId={pod?.organization_id}
                    defaultRuntime={runtimeCatalog?.default_runtime ?? null}
                    value={selectedRuntime}
                    onChange={setRuntimeDraft}
                    onCommit={handleRuntimeCommit}
                    onRefresh={() => {
                        void refetchRuntimeCatalog();
                        void refetchAvailableHarnesses();
                    }}
                    commitLabel={selectedRuntime ? 'Save default' : 'Clear override'}
                    commitLoading={updatePodDefaultRuntime.isPending}
                    isRefreshing={isFetchingRuntimeCatalog || isFetchingAvailableHarnesses}
                    isLoading={isLoadingRuntimeCatalog || isLoadingAvailableHarnesses}
                    disabled={!canUpdatePod}
                    allowDefault
                    label="Pod default"
                    description={canUpdatePod
                        ? 'Choose the model this pod should use by default.'
                        : 'You can view this default, but your role cannot change pod settings.'}
                />
            </PodSettingsPanel>

            <PodJoinPolicyPanel
                podId={podId}
                currentPolicy={pod?.config?.join_policy ?? PodJoinPolicy.INVITE_ONLY}
                canUpdate={canUpdatePod}
            />
            </div>
        </PodSettingsShell>
    );
}

// Auto-joiners always receive the base pod role ("User"). Elevated roles
// (Editor / Admin) are granted only via invite or by approving a join request —
// see the note rendered below the selector and lemma-work/lemma-platform#30.
const POD_JOIN_POLICY_OPTIONS: {
    value: PodJoinPolicy;
    label: string;
    description: string;
    /** Role a person receives when they add themselves under this policy. */
    selfJoinRole?: string;
}[] = [
    {
        value: PodJoinPolicy.INVITE_ONLY,
        label: 'Invite only',
        description:
            'Nobody can add themselves. People join by invitation or an approved join request — the only way to grant Editor or Admin access.',
    },
    {
        value: PodJoinPolicy.ORG_MEMBERS,
        label: 'Organization members',
        description: 'Any member of this pod’s organization can add themselves to it.',
        selfJoinRole: 'User',
    },
    {
        value: PodJoinPolicy.PUBLIC,
        label: 'Anyone',
        description: 'Any Lemma user can add themselves, and is added to the organization as a member.',
        selfJoinRole: 'User',
    },
];

function PodJoinPolicyPanel({
    podId,
    currentPolicy,
    canUpdate,
}: {
    podId: string;
    currentPolicy: PodJoinPolicy;
    canUpdate: boolean;
}) {
    const updatePod = useUpdatePod();
    const [policy, setPolicy] = useState<PodJoinPolicy>(currentPolicy);

    const handleChange = (next: PodJoinPolicy) => {
        if (next === policy) return;
        const previous = policy;
        setPolicy(next);
        updatePod.mutate(
            { id: podId, data: { config: { join_policy: next } } },
            {
                onSuccess: () => toast.success('Pod access updated'),
                onError: (error) => {
                    setPolicy(previous);
                    toast.error(`Failed to update access: ${error.message}`);
                },
            },
        );
    };

    const disabled = !canUpdate || updatePod.isPending;

    return (
        <PodSettingsPanel
            title="Who can join"
            description="Decide whether people can add themselves to this pod or need an invite."
        >
            <div className="settings-list" role="radiogroup" aria-label="Who can join this pod">
                {POD_JOIN_POLICY_OPTIONS.map((option) => {
                    const selected = option.value === policy;
                    return (
                        <button
                            key={option.value}
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            disabled={disabled}
                            onClick={() => handleChange(option.value)}
                            data-selected={selected}
                            className="settings-choice-row items-start disabled:cursor-not-allowed disabled:opacity-60"
                        >
                            <span className="flex min-w-0 flex-col gap-1">
                                <span className="text-sm font-medium text-[var(--text-primary)]">{option.label}</span>
                                <span className="text-xs leading-5 text-[var(--text-tertiary)]">{option.description}</span>
                                {option.selfJoinRole ? (
                                    <span className="mt-0.5 inline-flex w-fit items-center gap-1 rounded-full border border-[var(--chip-border)] bg-[var(--chip-bg)] px-2 py-0.5 text-xs font-medium text-[var(--chip-fg)]">
                                        Joins as {option.selfJoinRole}
                                    </span>
                                ) : null}
                            </span>
                            <span
                                aria-hidden
                                className={cn(
                                    'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-gentle',
                                    selected
                                        ? 'border-[var(--state-success)] bg-[var(--state-success)] text-[var(--text-on-brand)]'
                                        : 'border-[var(--field-border)] text-transparent',
                                )}
                            >
                                <Check className="h-3 w-3" strokeWidth={3} />
                            </span>
                        </button>
                    );
                })}
            </div>
            {canUpdate ? (
                <p className="settings-help-text mt-3 flex items-start gap-1.5">
                    <Info className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
                    <span>
                        People who add themselves always get the base <strong>User</strong> role. To grant
                        Editor or Admin access, invite them or approve their join request from the Members tab.
                    </span>
                </p>
            ) : (
                <p className="settings-help-text mt-3">Your role cannot change pod settings.</p>
            )}
        </PodSettingsPanel>
    );
}
