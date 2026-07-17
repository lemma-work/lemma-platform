'use client';

import { use, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ArrowUp } from '@/components/ui/icons';

import { LemmaMark } from '@/components/brand/logo';
import { InlineChannelForm } from '@/components/pod/inline-channel-form';
import { RecentConversations, SurfaceConnectChip, SurfaceIdentityChip } from '@/components/pod/resource-automation';
import {
    ResourceDetailHeader,
    ResourceDetailShell,
    ResourceDetailViewport,
    ResourceTabPane,
} from '@/components/pod/resource-layout';
import { Button } from '@/components/ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet';
import { useScopedConversations } from '@/lib/hooks/use-assistants';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodAutomation } from '@/lib/hooks/use-pod-automation';
import { SURFACE_PLATFORM_META, getSurfacePlatformKey } from '@/lib/utils/surfaces';

// The "Pod Assistant" is a virtual, frontend-only agent: it has no row of its
// own. It stands in for the pod's default responder — the agent that answers on
// any surface not assigned to a specific agent. Its channels are exactly the
// surfaces with no explicit responder (`uses_default_agent`). This mirrors the
// agent detail page's overview exactly — it just has no edit mode, since there's
// no underlying resource to edit.
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
    // Omitting agent_name lists the default pod assistant's own conversations.
    const { data: conversationsPage } = useScopedConversations({ podId }, { limit: 4, enabled: canReadConversations });
    const recentConversations = conversationsPage?.items ?? [];

    const [message, setMessage] = useState('');
    const [channelSheetOpen, setChannelSheetOpen] = useState(false);

    // Platforms the default assistant doesn't already answer on — shown as
    // faded connect icons alongside its live channels.
    const reachedPlatforms = new Set(defaultSurfaces.map((surface) => getSurfacePlatformKey(surface)));
    const podConnectedPlatforms = new Set(automation.surfaces.map((surface) => getSurfacePlatformKey(surface)));
    const unreachedPlatforms = Object.keys(SURFACE_PLATFORM_META).filter((key) => !reachedPlatforms.has(key));

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
            <ResourceDetailHeader
                title="Pod Assistant"
                productIconKind="agents"
                backHref={`/pod/${podId}/ai`}
                backLabel="Agents"
                fullscreen={false}
            />

            <ResourceDetailViewport>
                <ResourceTabPane active>
                    <div className="h-full overflow-y-auto bg-[var(--pod-main-bg)]">
                        <div className="max-w-3xl px-5 py-8 sm:py-10">
                            <section className="flex items-start gap-4">
                                <span className="flex h-14 w-14 shrink-0 items-center justify-center rounded-lg bg-[var(--card-bg)] shadow-[var(--shadow-xs)]">
                                    <LemmaMark size="md" />
                                </span>
                                <div className="min-w-0 flex-1 pt-0.5">
                                    <h1 className="truncate font-display text-2xl font-semibold tracking-tight text-[var(--text-primary)]">Pod Assistant</h1>
                                    <p className="mt-1.5 text-sm leading-6 text-[var(--text-secondary)]">
                                        This pod&apos;s most capable agent. Ask it to add a table, build a workflow, spin up a
                                        new agent, connect a surface, or read and change your data, and it acts on the pod
                                        directly.
                                    </p>
                                </div>
                            </section>

                            {canUseSurfaces ? (
                                <div className="mt-6 flex flex-wrap items-center gap-2">
                                    <span className="text-sm text-[var(--text-secondary)]">Channels</span>
                                    {defaultSurfaces.map((surface) => (
                                        <SurfaceIdentityChip key={surface.id} surface={surface} reachFor={null} />
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
                                        placeholder="Message the pod assistant…"
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

                            <RecentConversations podId={podId} conversations={recentConversations} agentName={null} />
                        </div>

                        <Sheet open={channelSheetOpen} onOpenChange={setChannelSheetOpen}>
                            <SheetContent side="right" className="flex w-full flex-col gap-4 overflow-y-auto sm:max-w-md">
                                <SheetHeader>
                                    <SheetTitle>Add channel</SheetTitle>
                                    <SheetDescription>Route a connected surface to the pod default, or connect a new one.</SheetDescription>
                                </SheetHeader>
                                <InlineChannelForm
                                    podId={podId}
                                    agentName={null}
                                    allSurfaces={automation.surfaces}
                                    manageHref={`/pod/${podId}/surfaces`}
                                    onDone={() => setChannelSheetOpen(false)}
                                    onCancel={() => setChannelSheetOpen(false)}
                                />
                            </SheetContent>
                        </Sheet>
                    </div>
                </ResourceTabPane>
            </ResourceDetailViewport>
        </ResourceDetailShell>
    );
}
