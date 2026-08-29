'use client';

import { use, useCallback, useEffect, useRef, useState } from 'react';
import { useSearchParams } from 'next/navigation';
import { MessageSquare, Save, Settings2 } from '@/components/ui/icons';
import { toast } from 'sonner';

import { AgentDetailSkeleton } from '@/components/agents/agent-detail-skeleton';
import { AgentHome, AgentHomeSkeleton } from '@/components/agents/agent-home';
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
import { agentTakesInput, formatAgentName } from '@/lib/utils/agents';

/**
 * One agent, one page, with two modes and a clear default.
 *
 * The page used to open in its editor: config in the main column, a dock on the
 * right for actually running the thing. That optimised for the rarer act. You
 * tune an agent while you are building it and seldom after; you talk to it every
 * day — and since the sidebar rail put every agent one click from every route,
 * this is somewhere people land constantly rather than visit to make changes.
 *
 * So talking is the default and editing is a button, and hitting it *swaps*
 * which pane is big: config takes the main column and the conversation moves
 * into the dock. The thing you came to work on is always the large one, and
 * "tune it while testing it" — the genuinely good part of the old layout —
 * survives intact.
 *
 * An agent with declared inputs is excluded from all of this: it is called with
 * arguments rather than talked to (see `takes_input`), so there is no
 * conversation to make the default and it opens in the editor as before.
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
    // Four answers the draft check below ("has this agent ever run"); home wants
    // enough to be a preview of what it has been doing.
    const { data: conversationsPage } = useConversations(podId, agentName, { limit: 10 });
    const recentConversations = conversationsPage?.items ?? [];
    const homeConversations = recentConversations;
    const updateAgent = useUpdateAgent();
    const { mutateAsync: updateAgentAsync } = updateAgent;

    const [localAgent, setLocalAgent] = useState<Agent | null>(null);
    const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false);
    const [isDockOpen, setIsDockOpen] = useState<boolean | null>(null);
    const [dockView, setDockView] = useState<'conversation' | 'history'>('conversation');
    const [isEditing, setIsEditing] = useState(false);
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

    // The same line the sidebar's agents rail draws. Every agent gets a home;
    // what differs is how you invoke it. A declared input schema means this one
    // is called with arguments rather than talked to, so its home offers no
    // message box and its run form lives in the dock.
    const canConverse = Boolean(localAgent) && !agentTakesInput(localAgent);
    const editing = isEditing;

    // Picking a run from the rail opens it in the dock — the messenger move:
    // the list is on the left, the conversation is on the right, and the URL
    // carries the pick so a refresh (or a shared link) lands on the same run.

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
    // The dock is the *run form*, and nothing else now. Its chat half is gone:
    // a conversational agent is started from the home's composer and the run
    // opens as its own tab, so a test-chat panel bolted to the editor was a
    // third place to talk to one agent — with its own history list, its own
    // header and its own idea of which conversation you were in.
    const dockOpen = editing && isReady && !canConverse
        && (isDockOpen ?? (isDraft || Boolean(openConversationId)));
    const isStackedLayout = dockOpen && layoutWidth > 0 && layoutWidth < 1040;

    return (
        <ResourceDetailShell>
            <TourLayer tour="agent-editor" />
            {/* No title, no back link — only the action. The tab strip above
                already names this agent and carries the control that leaves it,
                and the front door says the name a third time in its greeting. A
                bar repeating both was the shell arguing with itself; §7's rule
                is that the shell owns the title, and here it already does. */}
            <ResourceHeader
                title={label}
                // The tab strip directly above already names this agent, and the
                // front door says it a third time in its greeting, so the bar
                // drops to just the action. `tab` rather than dropping the title
                // outright because it self-corrects: on a compact viewport, where
                // the strip is hidden, the bar takes the name back instead of
                // leaving nothing on screen naming the thing.
                titleOwner="tab"
                // No back link either. The tab carries its own close, and a bar
                // whose whole content was "← Agents" sat between the strip and
                // the page saying nothing the strip did not.
                //
                // In talk mode nothing is left for it to hold — Configure lives
                // in the page — so the bar goes rather than drawing 48px of
                // empty strip. Pod home has no context bar for the same reason,
                // which is most of why this page did not feel like it.
                hideContextBar={!editing}
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
                        {/* One control, because there are two modes and you are
                            always in exactly one of them. The old "Try it"
                            toggled a dock beside an editor you could not leave;
                            this says which half of the page is the work. It is
                            hidden for an agent with declared inputs — that page
                            has only the editor, so a toggle would offer a mode
                            that does not exist. */}
                        {isEditing ? (
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                className="h-8 gap-1.5 px-2.5 text-xs font-medium"
                                onClick={() => setIsEditing(false)}
                                disabled={!isReady}
                            >
                                <MessageSquare className="h-3.5 w-3.5" />
                                Done
                            </Button>
                        ) : null}
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
                        <div className="flex h-full min-h-0">
                            {/* No rail. This page grew its own conversation list
                                on the left, which put two lists of conversations
                                side by side — the pod's history in the shell
                                sidebar and this agent's here, same rows, same
                                width, one border apart. Two lists of the same
                                kind of thing read as two sidebars, not as a
                                hierarchy. The shell's is the one that survives,
                                because it is present on every route. Agent-scoped
                                history keeps a home in the dock's History tab
                                while editing. */}
                            {!editing ? (
                                localAgent ? (
                                /* The agent's home, and it stays home. Sending
                                   from here opens the conversation as its own
                                   workspace tab — the tabs are a persistent
                                   working set, so this page is still here when
                                   you come back to it. Hosting the transcript in
                                   place instead meant the agent's page became a
                                   transcript and its front door was gone until
                                   you asked for a new run. */
                                <div className="resource-page-scroll agent-home-scroll min-w-0 flex-1">
                                    {/* Anchored to the pane's corner, not the
                                        column's. Inside a 44rem block centred in
                                        a very wide pane, "top-right" is not a
                                        corner at all — the button floated in
                                        space above the face with no edge to
                                        belong to. A page action wants the place
                                        the eye already checks for one, which is
                                        exactly where the context bar used to put
                                        it. */}
                                    {canUpdateCurrentAgent ? (
                                        <div className="agent-home-page-actions">
                                            <Button
                                                type="button"
                                                variant="secondary"
                                                size="sm"
                                                onClick={() => setIsEditing(true)}
                                                className="h-8 gap-1.5 px-2.5 text-xs font-medium"
                                            >
                                                <Settings2 className="h-3.5 w-3.5" />
                                                Configure
                                            </Button>
                                        </div>
                                    ) : null}
                                    <AgentHome
                                        podId={podId}
                                        agentId={localAgent.id}
                                        agentSlug={localAgent.name}
                                        agentName={displayName}
                                        description={localAgent.description}
                                        iconUrl={localAgent.icon_url}
                                        surfaces={agentSurfaces}
                                        schedules={agentSchedules}
                                        conversations={homeConversations}
                                        canConverse={canConverse}
                                    />
                                </div>
                                ) : (
                                    /* The home's own skeleton. The editor's was
                                       standing in here, so every arrival flashed
                                       a stack of cards before resolving into a
                                       page that has none — a loading state
                                       promising the wrong screen. */
                                    <div className="resource-page-scroll agent-home-scroll min-w-0 flex-1">
                                        <AgentHomeSkeleton />
                                    </div>
                                )
                            ) : (
                            <div className="resource-page-scroll min-w-0 flex-1">
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
                // What actually comes next now: the agent is already usable, and
                // everything creation no longer asks for is one button away.
                description="Say something below to try it, or open Configure to give it instructions, access, and a schedule."
                celebrate
                className="mx-4 mt-3"
            />
        </ResourceDetailShell>
    );
}
