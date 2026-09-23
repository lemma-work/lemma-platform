'use client';

import { use } from 'react';
import { POD_DEFAULT_AGENT_SELECTOR } from 'lemma-sdk';

import { AgentHome } from '@/components/agents/agent-home';
import { TriggersRow } from '@/components/triggers/triggers-row';
import {
    ResourceHeader,
    ResourceDetailShell,
    ResourceDetailViewport,
} from '@/components/pod/resource-layout';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { DEFAULT_RESPONDER_DESCRIPTION, DEFAULT_RESPONDER_NAME } from '@/lib/utils/agents';
import { usePodAutomation } from '@/lib/hooks/use-pod-automation';

// Lem is a virtual, frontend-only agent: it has no row of its own. It stands in
// for the pod's default responder — the agent that answers on any surface not
// assigned to a specific agent. Its surfaces are exactly the surfaces with no
// explicit responder (`uses_default_agent`).
//
// The name is display copy and lives in one constant; the wire still calls this
// `pod_default` / `POD_DEFAULT`, and nothing here should spell it out inline.
//
// It is built from the same parts as an agent's page and should keep matching
// it: an identity card that states what it is and how it is reached, then the
// work. What it does not have is real — no instructions to edit, no tool set of
// its own, and nothing to share — so it has fewer rows rather than empty ones.
//
// Triggers used to be on that list, on the grounds that a trigger names its
// target and Lem has no name. It does have one: `POD_DEFAULT`, the same
// selector the conversation API has always taken. What was actually missing was
// somewhere to say what Lem should *do* when a trigger fires — a named agent is
// its own instruction and Lem's is empty — and a trigger now carries that
// sentence itself.
export default function PodAssistantPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const canUseSurfaces = podAccess.canAccessRoute('surfaces');

    const canUseSchedules = podAccess.canAny(['schedule.read', 'schedule.create']);
    const canCreateSchedule = podAccess.can('schedule.create');
    const canUpdateSchedule = podAccess.can('schedule.update');
    const canDeleteSchedule = podAccess.can('schedule.delete');

    // Pod-wide automation, grouped client-side — shares one cache entry with the
    // schedules page and agent detail pages instead of a per-view fetch.
    const automation = usePodAutomation(podId, {
        schedules: canUseSchedules,
        surfaces: canUseSurfaces,
    });
    const defaultSurfaces = automation.defaultSurfaces;
    // The wire selector, not the display name: `agent_name` on these rows is
    // `POD_DEFAULT`, which is what the API echoes for a target with no row.
    const schedules = automation.schedulesForAgent(POD_DEFAULT_AGENT_SELECTOR);
    const { data: conversationsPage } = useScopedConversations(
        { podId, agentName: POD_DEFAULT_AGENT_SELECTOR },
        { limit: 10, enabled: podAccess.can('conversation.read') },
    );
    const recentConversations = conversationsPage?.items ?? [];

    return (
        <ResourceDetailShell>
            {/* Same call as the agent pages: the tab strip above already names
                this, so the bar drops the title and the back link. `tab` rather
                than nothing, so a compact viewport with no strip takes the name
                back instead of leaving the page unnamed. */}
            <ResourceHeader
                title={DEFAULT_RESPONDER_NAME}
                titleOwner="tab"
                // Nothing left for the bar to hold, so it goes rather than
                // drawing an empty strip — the same call pod home makes.
                hideContextBar
                fullscreen={false}
            />

            {/* The same home the agent pages get. This was a stack of read-only
                cards — identity, wiring, a textarea that navigated away, and four
                recent conversations at the bottom — every one of them describing
                the assistant on a page where you could not talk to it. Then it
                was a live transcript, which had the opposite problem: sending
                consumed the page. Sending now opens the conversation as its own
                tab and this stays the assistant's home. */}
            <ResourceDetailViewport>
                <div className="resource-page-scroll agent-home-scroll">
                    <AgentHome
                        podId={podId}
                        agentName={DEFAULT_RESPONDER_NAME}
                        description={DEFAULT_RESPONDER_DESCRIPTION}
                        surfaces={canUseSurfaces ? defaultSurfaces : []}
                        conversations={recentConversations}
                        isAssistant
                    />

                    {/* The one wiring row Lem has. It is here rather than on a
                        schedules page for the same reason it is on an agent's
                        page: the thing being woken up is the context that makes
                        "what should start this?" answerable, and Lem's page is
                        the only place that context exists. `POD_DEFAULT` is
                        what the API takes; the modal shows the name.

                        `AgentHome` is deliberately not given the schedules as
                        well. It would draw its own read-only "Runs on its own"
                        list from them, which on an agent's page is fine because
                        the home and the editable rows are alternate modes of
                        one screen — Configure swaps between them. Lem has no
                        Configure mode, so both would render at once and the
                        same triggers would be listed twice, once uneditably. */}
                    {canUseSchedules ? (
                        <section className="agent-wiring">
                            <TriggersRow
                                podId={podId}
                                target={{ kind: 'agent', name: POD_DEFAULT_AGENT_SELECTOR }}
                                schedules={schedules}
                                canCreate={canCreateSchedule}
                                canUpdate={canUpdateSchedule}
                                canDelete={canDeleteSchedule}
                                emptyText="You ask it to."
                            />
                        </section>
                    ) : null}
                </div>
            </ResourceDetailViewport>
        </ResourceDetailShell>
    );
}
