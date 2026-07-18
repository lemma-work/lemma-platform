'use client';

import { use, useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { ArrowUp, Boxes, Loader2, Plus, Save, Share2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { AgentEditor } from '@/components/agents/agent-editor';
import { InlineChannelForm } from '@/components/pod/inline-channel-form';
import { InlineTriggerForm } from '@/components/pod/inline-trigger-form';
import { RecentConversations, SurfaceConnectChip, SurfaceIdentityChip, TriggerIdentityChip } from '@/components/pod/resource-automation';
import {
    ResourceDetailHeader,
    ResourceDetailShell,
    ResourceDetailViewport,
    ResourceTabPane,
} from '@/components/pod/resource-layout';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { ResourceArrivalNotice } from '@/components/shared/resource-feedback';
import { ResourceShareButton, ResourceVisibilityBadge, type ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { useAgent, useUpdateAgent } from '@/lib/hooks/use-agents';
import { useConversations } from '@/lib/hooks/use-assistants';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodAutomation } from '@/lib/hooks/use-pod-automation';
import { Agent, UpdateAgentData } from '@/lib/types';
import { formatAgentName } from '@/lib/utils/agents';
import { SURFACE_PLATFORM_META, getSurfacePlatformKey } from '@/lib/utils/surfaces';
import { playSoundFeedback } from '@/lib/feedback/sound-feedback';
import { requestConversationStageNavigation } from '@/lib/assistant/conversation-presentation';

type AgentDetailMode = 'overview' | 'edit';

function agentInitials(name: string): string {
    const tokens = name.trim().split(/[\s\-_]+/).filter(Boolean);
    if (tokens.length >= 2) return `${tokens[0][0]}${tokens[1][0]}`.toUpperCase();
    return (tokens[0] || name).slice(0, 2).toUpperCase();
}

export default function AgentDetailPage({
    params,
}: {
    params: Promise<{ id: string; agentId: string }>;
}) {
    const { id: podId, agentId: agentNameParam } = use(params);
    const agentName = agentNameParam;
    const pathname = usePathname();
    const router = useRouter();
    const searchParams = useSearchParams();
    const podAccess = usePodAccess(podId);
    const canUpdateAgent = podAccess.can('agent.update');
    const canUseSchedules = podAccess.canAny(['schedule.read', 'schedule.create']);
    const canCreateSchedule = podAccess.can('schedule.create');
    const canUseSurfaces = podAccess.canAccessRoute('surfaces');

    const { data: agentData, isLoading } = useAgent(podId, agentName);
    // Pod-wide automation, grouped client-side — shares one cache entry with the
    // schedules page and agents list instead of a per-agent filtered fetch.
    const automation = usePodAutomation(podId, {
        schedules: canUseSchedules,
        surfaces: canUseSurfaces,
    });
    const agentSchedules = automation.schedulesForAgent(agentName);
    const agentSurfaces = automation.surfacesForAgent(agentName);
    const { data: conversationsPage } = useConversations(podId, agentName, { limit: 4 });
    const recentConversations = conversationsPage?.items ?? [];
    const updateAgent = useUpdateAgent();
    const { mutateAsync: updateAgentAsync } = updateAgent;

    const [localAgent, setLocalAgent] = useState<Agent | null>(null);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
    const [message, setMessage] = useState('');
    const [channelSheetOpen, setChannelSheetOpen] = useState(false);
    const [triggerSheetOpen, setTriggerSheetOpen] = useState(false);
    const lastSavedHashRef = useRef('');

    const buildUpdatePayload = useCallback((agent: Agent) => ({
        description: agent.description,
        icon_url: agent.icon_url,
        agent_runtime: agent.agent_runtime ?? null,
        instruction: agent.instruction,
        input_schema: agent.input_schema,
        output_schema: agent.output_schema,
        tool_sets: agent.tool_sets,
        accessible_tables: agent.accessible_tables,
        accessible_folders: agent.accessible_folders,
        accessible_connectors: agent.accessible_connectors,
        accessible_functions: agent.function_names ?? undefined,
        accessible_agents: agent.agent_names ?? undefined,
        visibility: agent.visibility as UpdateAgentData['visibility'],
    }), []);

    useEffect(() => {
        if (agentData && !hasUnsavedChanges) {
            // eslint-disable-next-line react-hooks/set-state-in-effect
            setLocalAgent(agentData);
            lastSavedHashRef.current = JSON.stringify(buildUpdatePayload(agentData));
        }
    }, [agentData, buildUpdatePayload, hasUnsavedChanges]);

    const isEqualValue = (currentValue: unknown, nextValue: unknown): boolean => {
        if (Object.is(currentValue, nextValue)) return true;
        if (typeof currentValue === 'object' && currentValue !== null && typeof nextValue === 'object' && nextValue !== null) {
            try {
                return JSON.stringify(currentValue) === JSON.stringify(nextValue);
            } catch {
                return false;
            }
        }
        return false;
    };

    const handleUpdate = useCallback((updates: Partial<Agent>) => {
        setLocalAgent((prev) => {
            if (!prev) return prev;
            if (!resourceAllows(prev, 'agent.update', canUpdateAgent)) return prev;

            const changed = Object.entries(updates).some(([key, value]) => {
                const currentValue = prev[key as keyof Agent];
                return !isEqualValue(currentValue, value);
            });

            if (!changed) return prev;
            setHasUnsavedChanges(true);
            return { ...prev, ...updates };
        });
    }, [canUpdateAgent]);

    const handleSave = useCallback(async () => {
        const currentAgent = localAgent;
        if (!currentAgent) return;
        if (!resourceAllows(currentAgent, 'agent.update', canUpdateAgent)) return;

        const payload = buildUpdatePayload(currentAgent);
        const payloadHash = JSON.stringify(payload);

        if (payloadHash === lastSavedHashRef.current) {
            setHasUnsavedChanges(false);
            return;
        }

        try {
            await updateAgentAsync({ podId, agentName, data: payload });
            lastSavedHashRef.current = payloadHash;
            setHasUnsavedChanges(false);
            playSoundFeedback('action-success');
        } catch (error) {
            console.error('Failed to save agent:', error);
            toast.error(error instanceof Error ? error.message : 'Failed to save agent. Please try again.');
        }
    }, [agentName, buildUpdatePayload, canUpdateAgent, localAgent, podId, updateAgentAsync]);

    const handleShareVisibilityChange = useCallback(async (visibility: ResourceVisibilityValue) => {
        const currentAgent = localAgent;
        if (!currentAgent) return;
        if (!resourceAllows(currentAgent, 'agent.update', canUpdateAgent)) return;

        try {
            await updateAgentAsync({ podId, agentName, data: { visibility: visibility as UpdateAgentData['visibility'] } });
        } catch (error) {
            console.error('Failed to update agent visibility:', error);
            toast.error(error instanceof Error ? error.message : 'Failed to update visibility. Please try again.');
            return;
        }

        const nextAgent = { ...currentAgent, visibility };
        setLocalAgent((prev) => prev ? { ...prev, visibility } : prev);
        if (!hasUnsavedChanges) {
            lastSavedHashRef.current = JSON.stringify(buildUpdatePayload(nextAgent));
        }
    }, [agentName, buildUpdatePayload, canUpdateAgent, hasUnsavedChanges, localAgent, podId, updateAgentAsync]);

    const canUpdateCurrentAgent = resourceAllows(localAgent, 'agent.update', canUpdateAgent);
    const activeMode: AgentDetailMode = canUpdateCurrentAgent && searchParams.get('mode') === 'edit' ? 'edit' : 'overview';

    const setActiveMode = useCallback((nextMode: AgentDetailMode) => {
        if (nextMode === 'edit' && !canUpdateCurrentAgent) return;
        const nextParams = new URLSearchParams(searchParams.toString());
        if (nextMode === 'edit') {
            nextParams.set('mode', 'edit');
        } else {
            nextParams.delete('mode');
        }
        const nextQuery = nextParams.toString();
        router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
    }, [canUpdateCurrentAgent, pathname, router, searchParams]);

    if (isLoading) {
        return (
            <div className="flex h-full items-center justify-center bg-transparent">
                <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
            </div>
        );
    }

    if (!localAgent) {
        return (
            <div className="flex h-full items-center justify-center bg-transparent">
                <div className="text-center">
                    <h2 className="font-display text-2xl font-semibold text-[var(--text-primary)]">Agent not found</h2>
                </div>
            </div>
        );
    }

    const displayName = localAgent.name || agentName;
    const agentShareUrl = typeof window === 'undefined'
        ? undefined
        : `${window.location.origin}/pod/${podId}/agents/${encodeURIComponent(displayName)}`;

    const toolCount = (localAgent.tool_sets?.length ?? localAgent.toolsets?.length ?? 0)
        + (localAgent.accessible_tables?.length ?? 0)
        + (localAgent.accessible_folders?.length ?? 0)
        + (localAgent.accessible_connectors?.length ?? 0);

    // Platforms this agent doesn't already answer on — shown as faded connect
    // icons alongside its live channels. `podConnectedPlatforms` distinguishes
    // "already connected, just route it here" from "not connected at all".
    const reachedPlatforms = new Set(agentSurfaces.map((surface) => getSurfacePlatformKey(surface)));
    const podConnectedPlatforms = new Set(automation.surfaces.map((surface) => getSurfacePlatformKey(surface)));
    const unreachedPlatforms = Object.keys(SURFACE_PLATFORM_META).filter((key) => !reachedPlatforms.has(key));

    // Hand off to the pod's new-conversation flow, scoped to this agent (`?agent=`)
    // and carrying the first message (`assistantMessage`) so it sends on arrival.
    const startConversation = () => {
        const text = message.trim();
        const params = new URLSearchParams({ agent: displayName });
        if (text) params.set('assistantMessage', text);
        const href = `/pod/${podId}/conversations/new?${params.toString()}`;
        if (!requestConversationStageNavigation(href)) router.push(href);
    };

    return (
        <ResourceDetailShell>
            <ResourceDetailHeader
                title={formatAgentName(displayName)}
                productIconKind="agents"
                backHref={`/pod/${podId}/ai`}
                backLabel="Agents"
                meta={<ResourceVisibilityBadge visibility={localAgent.visibility} resourceLabel="agents" />}
                // NB: do not drive `fullscreen` from state here. On a non-workflow route
                // it toggles the layout's fullscreen branch (PodShell), which re-parents
                // and remounts this page, and the header's setTopbar cleanup/setup then
                // ping-pongs fullscreen → infinite render loop. The editor renders fine in
                // the normal shell (as the original edit mode did).
                fullscreen={false}
                tabs={(
                    <AgentModeSwitch value={activeMode} onChange={setActiveMode} canEdit={canUpdateCurrentAgent} />
                )}
                actions={(
                    <TooltipProvider>
                    <>
                        {activeMode === 'edit' && canUpdateCurrentAgent && (hasUnsavedChanges || updateAgent.isPending) ? (
                            <Button
                                type="button"
                                size="sm"
                                className="h-8 gap-1.5 px-3 text-xs font-medium"
                                onClick={() => void handleSave()}
                                disabled={updateAgent.isPending || !hasUnsavedChanges}
                            >
                                {updateAgent.isPending ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}
                                {updateAgent.isPending ? 'Saving...' : 'Save changes'}
                            </Button>
                        ) : null}
                        {canUpdateCurrentAgent ? (
                            <ResourceShareButton
                                value={localAgent.visibility}
                                podId={podId}
                                resourceType="agent"
                                resourceId={localAgent.id}
                                resourceLabel="agents"
                                resourceName={formatAgentName(displayName)}
                                shareUrl={agentShareUrl}
                                onChange={handleShareVisibilityChange}
                                trigger={({ openShare, disabled }) => (
                                    <Tooltip>
                                        <TooltipTrigger asChild>
                                            <Button type="button" variant="ghost" size="icon" className="h-8 w-8 rounded" onClick={openShare} disabled={disabled} aria-label="Share">
                                                <Share2 className="h-4 w-4" />
                                            </Button>
                                        </TooltipTrigger>
                                        <TooltipContent>Share</TooltipContent>
                                    </Tooltip>
                                )}
                            />
                        ) : null}
                    </>
                    </TooltipProvider>
                )}
            />

            <ResourceDetailViewport>
                <ResourceTabPane active={activeMode === 'overview'}>
                    {activeMode === 'overview' ? (
                        <div className="h-full overflow-y-auto bg-[var(--pod-main-bg)]">
                            <div className="max-w-3xl px-5 py-8 sm:py-10">
                                <section className="flex items-start gap-4">
                                    <ResourceIcon
                                        iconUrl={localAgent.icon_url}
                                        alt={`${formatAgentName(displayName)} icon`}
                                        label={displayName}
                                        imageClassName="object-contain p-1.5"
                                        className="h-14 w-14 shrink-0 rounded-lg bg-[var(--card-bg)] shadow-[var(--shadow-xs)]"
                                        fallback={(
                                            <span className="resource-monogram flex h-full w-full items-center justify-center rounded-lg text-lg font-semibold">
                                                {agentInitials(displayName)}
                                            </span>
                                        )}
                                    />
                                    <div className="min-w-0 flex-1 pt-0.5">
                                        <h1 className="truncate font-display text-2xl font-semibold tracking-tight text-[var(--text-primary)]">{formatAgentName(displayName)}</h1>
                                        {localAgent.description?.trim() ? (
                                            <p className="mt-1.5 text-sm leading-6 text-[var(--text-secondary)]">{localAgent.description.trim()}</p>
                                        ) : null}
                                        <div className="mt-3 flex flex-wrap items-center gap-2">
                                            <MetaChip
                                                icon={<Boxes className="h-3.5 w-3.5" aria-hidden />}
                                                onClick={canUpdateCurrentAgent ? () => setActiveMode('edit') : undefined}
                                            >
                                                {toolCount} tool{toolCount === 1 ? '' : 's'}
                                            </MetaChip>
                                        </div>
                                    </div>
                                </section>

                                {canUseSurfaces ? (
                                    <div className="mt-6 flex flex-wrap items-center gap-2">
                                        <span className="text-sm text-[var(--text-secondary)]">Channels</span>
                                        {agentSurfaces.map((surface) => (
                                            <SurfaceIdentityChip key={surface.id} surface={surface} reachFor={displayName} />
                                        ))}
                                        {unreachedPlatforms.map((platformKey) => (
                                            <SurfaceConnectChip
                                                key={platformKey}
                                                platformKey={platformKey}
                                                connectedInPod={podConnectedPlatforms.has(platformKey)}
                                                manageHref={`/pod/${podId}/surfaces`}
                                                onRoute={() => setChannelSheetOpen(true)}
                                            />
                                        ))}
                                    </div>
                                ) : null}

                                {canUseSchedules ? (
                                    <div className="mt-3 flex flex-wrap items-center gap-2">
                                        <span className="text-sm text-[var(--text-secondary)]">Triggers</span>
                                        {agentSchedules.map((schedule) => (
                                            <TriggerIdentityChip key={schedule.id} schedule={schedule} />
                                        ))}
                                        {canCreateSchedule ? (
                                            <Button
                                                type="button"
                                                variant="outline"
                                                size="icon"
                                                className="h-8 w-8 shrink-0 rounded-lg border-dashed text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                                                onClick={() => setTriggerSheetOpen(true)}
                                                aria-label="New trigger"
                                            >
                                                <Plus className="h-4 w-4" />
                                            </Button>
                                        ) : null}
                                    </div>
                                ) : null}

                                <section className="mt-7">
                                    <div className="form-field-control p-2.5">
                                        <textarea
                                            value={message}
                                            onChange={(event) => setMessage(event.target.value)}
                                            onKeyDown={(event) => {
                                                if (event.key === 'Enter' && !event.shiftKey) {
                                                    event.preventDefault();
                                                    startConversation();
                                                }
                                            }}
                                            placeholder={`Message ${formatAgentName(displayName)}…`}
                                            rows={3}
                                            className="inline-edit-field min-h-20 w-full resize-none px-2.5 py-2 text-sm leading-6"
                                        />
                                        <div className="flex items-center justify-between gap-3 px-1.5 pb-1">
                                            <span className="truncate text-xs text-[var(--text-tertiary)]">
                                                Enter to send · Shift + Enter for a new line
                                            </span>
                                            <Button type="button" size="icon" className="h-8 w-8 shrink-0 rounded-full" onClick={startConversation} aria-label="Start conversation">
                                                <ArrowUp className="h-4 w-4" />
                                            </Button>
                                        </div>
                                    </div>
                                </section>

                                <RecentConversations podId={podId} conversations={recentConversations} agentName={displayName} />
                            </div>

                            <Sheet open={channelSheetOpen} onOpenChange={setChannelSheetOpen}>
                                <SheetContent side="right" className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
                                    <SheetHeader>
                                        <SheetTitle>Add channel</SheetTitle>
                                        <SheetDescription>Route a connected surface to this agent, or connect a new one.</SheetDescription>
                                    </SheetHeader>
                                    <InlineChannelForm
                                        podId={podId}
                                        agentName={displayName}
                                        allSurfaces={automation.surfaces}
                                        manageHref={`/pod/${podId}/surfaces`}
                                        onDone={() => setChannelSheetOpen(false)}
                                        onCancel={() => setChannelSheetOpen(false)}
                                    />
                                </SheetContent>
                            </Sheet>

                            <Sheet open={triggerSheetOpen} onOpenChange={setTriggerSheetOpen}>
                                <SheetContent side="right" className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
                                    <SheetHeader>
                                        <SheetTitle>New trigger</SheetTitle>
                                        <SheetDescription>What wakes this agent up on its own.</SheetDescription>
                                    </SheetHeader>
                                    <InlineTriggerForm
                                        podId={podId}
                                        target={{ kind: 'agent', name: displayName }}
                                        moreOptionsHref={`/pod/${podId}/schedules/new?agent=${encodeURIComponent(displayName)}`}
                                        onCreated={() => setTriggerSheetOpen(false)}
                                        onCancel={() => setTriggerSheetOpen(false)}
                                    />
                                </SheetContent>
                            </Sheet>
                        </div>
                    ) : null}
                </ResourceTabPane>

                <ResourceTabPane active={activeMode === 'edit'}>
                    {activeMode === 'edit' && canUpdateCurrentAgent ? (
                        <AgentEditor
                            podId={podId}
                            agent={localAgent}
                            onUpdate={handleUpdate}
                            isNameEditable={false}
                            shareUrl={agentShareUrl}
                            onShareVisibilityChange={handleShareVisibilityChange}
                        />
                    ) : null}
                </ResourceTabPane>
            </ResourceDetailViewport>

            <ResourceArrivalNotice
                resource="agent"
                title="Agent created"
                description="Start a conversation to try it, connect a channel so people can reach it, or add a trigger to run it on its own."
                celebrate
                actions={[
                    ...(canUpdateCurrentAgent ? [{ label: 'Set up', onClick: () => setActiveMode('edit'), variant: 'primary' as const }] : []),
                ]}
                className="mx-4 mt-3"
            />
        </ResourceDetailShell>
    );
}

function MetaChip({
    icon,
    onClick,
    children,
}: {
    icon: ReactNode;
    onClick?: () => void;
    children: ReactNode;
}) {
    if (!onClick) {
        return (
            <span className="chip chip-sm chip-muted">
                {icon}{children}
            </span>
        );
    }
    return (
        <button
            type="button"
            onClick={onClick}
            className="chip chip-sm chip-muted custom-focus-ring cursor-pointer transition-colors hover:border-[var(--border-strong)] hover:text-[var(--text-primary)]"
        >
            {icon}{children}
        </button>
    );
}

function AgentModeSwitch({
    value,
    onChange,
    canEdit,
}: {
    value: AgentDetailMode;
    onChange: (value: AgentDetailMode) => void;
    canEdit: boolean;
}) {
    if (!canEdit) return null;
    const items: Array<{ value: AgentDetailMode; label: string }> = [
        { value: 'overview', label: 'Overview' },
        { value: 'edit', label: 'Edit' },
    ];
    return (
        <div className="segmented-control">
            {items.map((item) => (
                <button
                    key={item.value}
                    type="button"
                    onClick={() => onChange(item.value)}
                    className="segmented-control-item custom-focus-ring"
                    data-active={value === item.value}
                    aria-pressed={value === item.value}
                >
                    {item.label}
                </button>
            ))}
        </div>
    );
}
