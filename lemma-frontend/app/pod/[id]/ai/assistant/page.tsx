'use client';

import { use, useState } from 'react';
import { useRouter } from 'next/navigation';
import { POD_DEFAULT_AGENT_SELECTOR } from 'lemma-sdk';
import { ArrowUp } from '@/components/ui/icons';

import { Nothing, WiringRow } from '@/components/pod/wiring-row';
import { LemmaMark } from '@/components/brand/logo';
import { RecentConversations } from '@/components/pod/recent-conversations';
import {
    ResourceHeader,
    ResourceDetailShell,
    ResourceDetailViewport,
    ResourceHeroTitle,
} from '@/components/pod/resource-layout';
import { AgentSurfacesRow } from '@/components/surfaces/agent-surfaces-row';
import { Button } from '@/components/ui/button';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodAutomation } from '@/lib/hooks/use-pod-automation';

// The "Pod Assistant" is a virtual, frontend-only agent: it has no row of its
// own. It stands in for the pod's default responder — the agent that answers on
// any surface not assigned to a specific agent. Its channels are exactly the
// surfaces with no explicit responder (`uses_default_agent`).
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
    const router = useRouter();
    const podAccess = usePodAccess(podId);
    const canUseSurfaces = podAccess.canAccessRoute('surfaces');
    const canReadConversations = podAccess.can('conversation.read');

    // Pod-wide automation, grouped client-side — shares one cache entry with the
    // schedules page and agent detail pages instead of a per-view fetch. No
    // schedules: the default assistant isn't a named target a trigger can wake.
    const automation = usePodAutomation(podId, { schedules: false, surfaces: canUseSurfaces });
    const defaultSurfaces = automation.defaultSurfaces;
    const { data: conversationsPage } = useScopedConversations(
        { podId, agentName: POD_DEFAULT_AGENT_SELECTOR },
        { limit: 4, enabled: canReadConversations },
    );
    const recentConversations = conversationsPage?.items ?? [];

    const [message, setMessage] = useState('');

    // Hand off to the pod's new-conversation flow with no `?agent=` — the pod
    // default assistant answers, carrying the first message so it sends on arrival.
    const startConversation = () => {
        const text = message.trim();
        const params = new URLSearchParams();
        if (text) params.set('assistantMessage', text);
        const query = params.toString();
        router.push(`/pod/${podId}/conversations/new${query ? `?${query}` : ''}`);
    };

    return (
        <ResourceDetailShell>
            <ResourceHeader
                title="Pod Assistant"
                titleOwner="page"
                backHref={`/pod/${podId}/ai`}
                backLabel="Agents"
                fullscreen={false}
            />

            <ResourceDetailViewport>
                <div className="resource-page-scroll">
                    <div className="resource-page-column">
                        <section className="resource-card">
                            <header className="agent-identity">
                                <span className="agent-identity-avatar" aria-hidden>
                                    <LemmaMark size="sm" />
                                </span>
                                <div className="agent-identity-body">
                                    <div className="agent-identity-titles">
                                        <ResourceHeroTitle className="agent-identity-name">Pod Assistant</ResourceHeroTitle>
                                    </div>
                                    <p className="agent-identity-description-static">
                                        This pod&rsquo;s most capable agent. Ask it to add a table, build a workflow, spin up a
                                        new agent, connect a surface, or read and change your data, and it acts on the pod directly.
                                    </p>
                                </div>
                            </header>

                            {canUseSurfaces ? (
                                <div className="agent-wiring">
                                    <WiringRow label="Reached by">
                                        <div className="agent-wiring-chips">
                                            {defaultSurfaces.length === 0 ? <Nothing>Only you, here.</Nothing> : null}
                                            <AgentSurfacesRow
                                                podId={podId}
                                                agentName={null}
                                                surfaces={defaultSurfaces}
                                                label={null}
                                            />
                                        </div>
                                    </WiringRow>
                                </div>
                            ) : null}
                        </section>

                        <section className="resource-card">
                            <p className="resource-card-eyebrow">Ask it something</p>
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
                                    placeholder="Message the pod assistant…"
                                    rows={3}
                                    className="inline-edit-field min-h-20 w-full resize-none px-2.5 py-2 text-sm leading-6"
                                />
                                <div className="flex items-center justify-between gap-3 px-1.5 pb-1">
                                    <span className="truncate text-xs text-[var(--text-tertiary)]">
                                        Enter to send · Shift + Enter for a new line
                                    </span>
                                    <Button variant="primary"
                                        type="button"
                                        size="icon"
                                        className="h-8 w-8 shrink-0 rounded-full"
                                        onClick={startConversation}
                                        aria-label="Start conversation"
                                    >
                                        <ArrowUp className="h-4 w-4" />
                                    </Button>
                                </div>
                            </div>

                            {/* Brings its own top margin, which is why it is not in a
                                `space-y` stack. */}
                            <RecentConversations podId={podId} conversations={recentConversations} agentName={null} />
                        </section>
                    </div>
                </div>
            </ResourceDetailViewport>
        </ResourceDetailShell>
    );
}
