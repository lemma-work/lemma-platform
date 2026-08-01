'use client';

import { use, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { MessageSquare, Save } from '@/components/ui/icons';
import { toast } from 'sonner';

import { AgentDetailSkeleton } from '@/components/agents/agent-detail-skeleton';
import { AgentIdentityHeader } from '@/components/agents/agent-identity-header';
import { AgentInstructions } from '@/components/agents/agent-instructions';
import { AgentTestPanel } from '@/components/agents/agent-test-panel';
import { AgentWiringRows } from '@/components/agents/agent-wiring-rows';
import { TourLayer } from '@/components/education/coachmark';
import {
    ResourceHeader,
    ResourceDetailShell,
    ResourceDetailViewport,
    ResourceWorkSplit,
} from '@/components/pod/resource-layout';
import { ResourceArrivalNotice } from '@/components/shared/resource-feedback';
import type { ResourceVisibilityValue } from '@/components/shared/resource-visibility';
import { Button } from '@/components/ui/button';
import { getAgentOverviewState } from '@/lib/agents/overview-state';
import { resourceAllows } from '@/lib/authz/resource-actions';
import { useAgent, useUpdateAgent } from '@/lib/hooks/use-agents';
import { useConversations } from '@/lib/hooks/use-assistants';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodAutomation } from '@/lib/hooks/use-pod-automation';
import { Agent, UpdateAgentData } from '@/lib/types';
import { formatAgentName } from '@/lib/utils/agents';
import { playSoundFeedback } from '@/lib/feedback/sound-feedback';

/**
 * One agent, one page.
 *
 * There used to be an Overview/Edit switch here, which split one job — making
 * the agent good — across two screens: what it can reach lived in the editor,
 * who can reach it lived in the overview, and the identity was stated in both.
 * Now the page reads top to bottom as who it is, how it is wired, and how it
 * behaves, with a dock on the right for actually running it.
 */
export default function AgentDetailPage({
    params,
}: {
    params: Promise<{ id: string; agentId: string }>;
}) {
    const { id: podId, agentId: agentNameParam } = use(params);
    const agentName = agentNameParam;
    const searchParams = useSearchParams();
    const podAccess = usePodAccess(podId);
    const canUpdateAgent = podAccess.can('agent.update');
    const canUseSchedules = podAccess.canAny(['schedule.read', 'schedule.create']);
    const canCreateSchedule = podAccess.can('schedule.create');
    const canUpdateSchedule = podAccess.can('schedule.update');
    const canDeleteSchedule = podAccess.can('schedule.delete');
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
    const [isDockOpen, setIsDockOpen] = useState<boolean | null>(null);
    const [dockView, setDockView] = useState<'conversation' | 'history'>('conversation');
    const [layoutWidth, setLayoutWidth] = useState(0);
    const layoutObserverRef = useRef<ResizeObserver | null>(null);
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

    /**
     * A callback ref, not an effect, and it now attaches to the skeleton shell
     * on mount — so the split has a real width before the agent lands, and the
     * stacked/side-by-side decision is made once instead of being corrected a
     * frame after the content appears.
     */
    const measureLayout = useCallback((node: HTMLDivElement | null) => {
        layoutObserverRef.current?.disconnect();
        layoutObserverRef.current = null;
        if (!node) return;

        const syncWidth = () => setLayoutWidth(node.getBoundingClientRect().width);
        syncWidth();

        const observer = new ResizeObserver(syncWidth);
        observer.observe(node);
        layoutObserverRef.current = observer;
    }, []);

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
    const openConversationId = searchParams.get('conversation');

    // This page used to return a bare centred spinner while the agent loaded —
    // no header, no cards, no dock — and then snap the whole two-pane layout
    // into place. Nothing about the *frame* was ever unknown: the name is in the
    // URL, and the cards are in the same places whatever the agent turns out to
    // be. So the shell renders from the first frame and only the fields wait.
    const isReady = Boolean(localAgent) && !isLoading;
    const displayName = localAgent?.name || agentName;
    const label = formatAgentName(displayName);
    const agentShareUrl = typeof window === 'undefined'
        ? undefined
        : `${window.location.origin}/pod/${podId}/agents/${encodeURIComponent(displayName)}`;

    // A brand-new agent has nothing to read and everything to try, so the dock
    // starts open on it. One that already runs opens quiet — you came to change
    // something, and its real conversations live on the conversations page.
    const isDraft = getAgentOverviewState({
        surfaceCount: agentSurfaces.length,
        scheduleCount: agentSchedules.length,
        conversationCount: recentConversations.length,
        canUseSurfaces,
        canUseSchedules,
        canCreateSchedule,
    }) === 'draft';
    // A conversation id in the URL is a request to look at that run, so it opens
    // the dock too — until the reader closes it themselves. Closed while the
    // agent loads: the dock runs the *saved* agent, and there isn't one yet.
    const dockOpen = isReady && (isDockOpen ?? (isDraft || Boolean(openConversationId)));
    const isStackedLayout = dockOpen && layoutWidth > 0 && layoutWidth < 1040;

    return (
        <ResourceDetailShell>
            <TourLayer tour="agent-editor" />
            <ResourceHeader
                title={label}
                // The identity block below owns the name; the bar takes it back
                // only once that block scrolls out of the pane.
                titleOwner="page"
                backHref={`/pod/${podId}/ai`}
                backLabel="Agents"
                fullscreen={false}
                actions={(
                    <>
                        {canUpdateCurrentAgent && (hasUnsavedChanges || updateAgent.isPending) ? (
                            <Button variant="primary"
                                type="button"
                                size="sm"
                                className="h-8 gap-1.5 px-3 text-xs font-medium"
                                onClick={() => void handleSave()}
                                loading={updateAgent.isPending}
                                loadingLabel="Saving…"
                                disabled={!hasUnsavedChanges}
                            >
                                <Save className="h-3.5 w-3.5" />
                                Save changes
                            </Button>
                        ) : null}
                        <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            className="h-8 gap-1.5 px-2.5 text-xs font-medium"
                            onClick={() => setIsDockOpen(!dockOpen)}
                            aria-pressed={dockOpen}
                            disabled={!isReady}
                        >
                            <MessageSquare className="h-3.5 w-3.5" />
                            Try it
                        </Button>
                    </>
                )}
            />

            <ResourceDetailViewport>
                <div ref={measureLayout} className="h-full min-h-0">
                <ResourceWorkSplit
                    // Measured off the split itself, not the window: a pod
                    // sidebar or a docked assistant narrows this long before the
                    // viewport says anything. Side by side needs room for a
                    // readable prompt *and* the dock; below that they stack.
                    isStacked={isStackedLayout}
                    main={(
                        <div className="resource-page-scroll">
                            <div className="resource-page-column">
                                {/* Who it is and how it is wired are one card: both
                                    answer "what is this thing", and neither is the
                                    work. The prompt gets its own. */}
                                {localAgent ? (
                                    <>
                                        <section className="resource-card">
                                            <AgentIdentityHeader
                                                podId={podId}
                                                agent={localAgent}
                                                onUpdate={handleUpdate}
                                                canEdit={canUpdateCurrentAgent}
                                                shareUrl={agentShareUrl}
                                                onShareVisibilityChange={handleShareVisibilityChange}
                                            />

                                            <AgentWiringRows
                                                podId={podId}
                                                agent={localAgent}
                                                onUpdate={handleUpdate}
                                                canEdit={canUpdateCurrentAgent}
                                                surfaces={agentSurfaces}
                                                schedules={agentSchedules}
                                                canUseSurfaces={canUseSurfaces}
                                                canUseSchedules={canUseSchedules}
                                                canCreateSchedule={canCreateSchedule}
                                                canUpdateSchedule={canUpdateSchedule}
                                                canDeleteSchedule={canDeleteSchedule}
                                            />
                                        </section>

                                        <AgentInstructions
                                            agent={localAgent}
                                            onUpdate={handleUpdate}
                                            canEdit={canUpdateCurrentAgent}
                                        />
                                    </>
                                ) : (
                                    <AgentDetailSkeleton />
                                )}
                            </div>
                        </div>
                    )}
                    aside={dockOpen ? (
                        <div className="agent-dock">
                            <div className="agent-dock-bar">
                                <div className="segmented-control">
                                    <button
                                        type="button"
                                        className="segmented-control-item"
                                        data-active={dockView === 'conversation'}
                                        onClick={() => setDockView('conversation')}
                                    >
                                        Run
                                    </button>
                                    <button
                                        type="button"
                                        className="segmented-control-item"
                                        data-active={dockView === 'history'}
                                        onClick={() => setDockView('history')}
                                    >
                                        History
                                    </button>
                                </div>

                                {/* Runs go through the server, which only knows the
                                    saved agent — so an unsaved draft would be tested
                                    as the old version. Say so rather than let the
                                    result quietly disagree with the editor. */}
                                {hasUnsavedChanges ? (
                                    <button
                                        type="button"
                                        className="agent-dock-stale"
                                        onClick={() => void handleSave()}
                                        disabled={updateAgent.isPending}
                                    >
                                        Testing the saved version — save first
                                    </button>
                                ) : null}
                            </div>

                            <div className="agent-dock-body">
                                <AgentTestPanel
                                    podId={podId}
                                    agentName={displayName}
                                    agentOverride={localAgent}
                                    view={dockView}
                                    openConversationId={openConversationId}
                                    onClose={() => setIsDockOpen(false)}
                                />
                            </div>
                        </div>
                    ) : null}
                    /* `border-l-0`/`border-t-0`: the dock carries its own card
                       border, and the split's edge rule would double it. */
                    asideClassName={isStackedLayout
                        ? 'agent-dock-shell agent-dock-shell-stacked w-full border-t-0'
                        : 'agent-dock-shell w-[min(30rem,42%)] border-l-0'}
                />
                </div>
            </ResourceDetailViewport>

            <ResourceArrivalNotice
                resource="agent"
                title="Agent created"
                description="Write its instructions, wire up what it can use, then try it in the panel on the right."
                celebrate
                className="mx-4 mt-3"
            />
        </ResourceDetailShell>
    );
}
