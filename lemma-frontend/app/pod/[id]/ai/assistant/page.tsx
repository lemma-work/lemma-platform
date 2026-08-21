'use client';

import { use } from 'react';
import { POD_DEFAULT_AGENT_SELECTOR } from 'lemma-sdk';

import { AgentHome } from '@/components/agents/agent-home';
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
// its own, no trigger that can name it, and nothing to share — so it has fewer
// rows rather than empty ones.
export default function PodAssistantPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const canUseSurfaces = podAccess.canAccessRoute('surfaces');

    // Pod-wide automation, grouped client-side — shares one cache entry with the
    // schedules page and agent detail pages instead of a per-view fetch. No
    // schedules: the default assistant isn't a named target a trigger can wake.
    const automation = usePodAutomation(podId, { schedules: false, surfaces: canUseSurfaces });
    const defaultSurfaces = automation.defaultSurfaces;
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
                </div>
            </ResourceDetailViewport>
        </ResourceDetailShell>
    );
}
