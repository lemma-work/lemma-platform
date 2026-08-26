'use client';

import { useEffect, useState } from 'react';

import { AgentAvatarPicker } from '@/components/agents/agent-avatar-picker';
import { agentInitials } from '@/components/agents/agent-overview-panels';
import { RuntimeModelPicker } from '@/components/lemma/assistant/model-picker';
import { ResourceHeroTitle } from '@/components/pod/resource-layout';
import { ResourceIcon } from '@/components/shared/resource-icon';
import {
    ResourceShareButton,
    getResourceVisibilityCopy,
    type ResourceVisibilityValue,
} from '@/components/shared/resource-visibility';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { useAgentRuntimes } from '@/lib/hooks/use-agent-runtime';
import { usePod } from '@/lib/hooks/use-pods';
import { resolveDefaultAgentRuntime } from '@/components/agents/agent-runtime-helpers';
import { podModelsHref } from '@/lib/navigation/pod-settings';
import { formatAgentName } from '@/lib/utils/agents';
import type { Agent } from '@/lib/types';

/**
 * Who this agent is — stated once, at the top of its page.
 *
 * The old page said it three times: the workspace tab strip, an overview rail
 * header, and an editor "Profile" section. This is the one masthead, and it
 * hands the name to the context bar only when it scrolls out of view
 * (`ResourceHeroTitle`). Everything here edits in place; there is no separate
 * profile form to open.
 */
export function AgentIdentityHeader({
    podId,
    agent,
    onUpdate,
    canEdit,
    shareUrl,
    onShareVisibilityChange,
}: {
    podId: string;
    agent: Agent;
    onUpdate: (data: Partial<Agent>) => void;
    canEdit: boolean;
    shareUrl?: string;
    onShareVisibilityChange?: (visibility: ResourceVisibilityValue) => void | Promise<void>;
}) {
    const [description, setDescription] = useState(agent.description || '');
    const [isPictureOpen, setIsPictureOpen] = useState(false);
    const { data: pod } = usePod(podId);
    const { data: runtimeCatalog } = useAgentRuntimes(pod?.organization_id);
    const defaultRuntime = resolveDefaultAgentRuntime(
        runtimeCatalog,
        pod?.config?.default_profile_id,
    );

    useEffect(() => {
        // eslint-disable-next-line react-hooks/set-state-in-effect
        setDescription(agent.description || '');
    }, [agent.description]);

    const label = formatAgentName(agent.name);
    const visibility = getResourceVisibilityCopy(agent.visibility, 'agents');
    const VisibilityIcon = visibility.icon;

    const avatar = (
        <ResourceIcon
            iconUrl={agent.icon_url}
            alt=""
            label={label}
            imageClassName="object-contain p-1"
            className="h-full w-full rounded-xl"
            identitySeed={agent.id || agent.name}
            identitySize={32}
            fallback={(
                <span className="resource-monogram flex h-full w-full items-center justify-center rounded-xl text-sm font-semibold">
                    {agentInitials(label)}
                </span>
            )}
        />
    );

    return (
        <header className="agent-identity" data-edu="agent-identity">
            {canEdit ? (
                <button
                    type="button"
                    className="agent-identity-avatar"
                    onClick={() => setIsPictureOpen(true)}
                    aria-label="Change display picture"
                    title="Change display picture"
                >
                    {avatar}
                </button>
            ) : (
                <span className="agent-identity-avatar" aria-hidden>{avatar}</span>
            )}

            <div className="agent-identity-body">
                <div className="agent-identity-titles">
                    <ResourceHeroTitle className="agent-identity-name">{label}</ResourceHeroTitle>
                    {/* The stored name is what every tool call, surface route, and
                        bundle refers to. It is not editable here, so it reads as a
                        fact rather than a field. */}
                    <span className="agent-identity-slug" title="Identifier">{agent.name}</span>
                </div>

                {canEdit ? (
                    // A raw textarea, not the shared field: `form-field-control`
                    // paints a bordered box, and this line has to read as the
                    // sentence describing the agent until someone reaches for it.
                    //
                    // The hidden twin is what makes it grow. Both share one grid
                    // cell, so the sized-by-content span sets the height and the
                    // textarea fills it — no measuring, and nothing that can
                    // measure its own output and run away.
                    <div className="agent-identity-description">
                        <span aria-hidden>{description || 'What work does this agent own?'}&nbsp;</span>
                        <textarea
                            className="agent-identity-description-field"
                            value={description}
                            onChange={(event) => setDescription(event.target.value)}
                            onBlur={() => {
                                if (description !== (agent.description || '')) onUpdate({ description });
                            }}
                            placeholder="What work does this agent own?"
                            rows={1}
                        />
                    </div>
                ) : agent.description ? (
                    <p className="agent-identity-description-static">{agent.description}</p>
                ) : null}

                {/* No agent address here, deliberately: "Reached by" owns reach,
                    renders the address in full, and is what you click to change
                    it. Repeated here it was the same string twice on one card. */}
            </div>

            <div className="agent-identity-chips">
                <div className="agent-identity-chip-slot" data-edu="agent-runtime">
                    <RuntimeModelPicker
                        catalog={runtimeCatalog}
                        defaultRuntime={defaultRuntime}
                        value={agent.agent_runtime ?? null}
                        onChange={(agentRuntime) => onUpdate({ agent_runtime: agentRuntime })}
                        disabled={!canEdit}
                        compact
                        ariaLabel="Agent model"
                        scopeHint="Default for this agent"
                        manageHref={podModelsHref(podId)}
                    />
                </div>

                {/* Sharing is one control, not two: the chip states who can open
                    this agent and opens the dialog that changes it. The context
                    bar no longer carries a second share button. */}
                <div className="agent-identity-chip-slot" data-edu="agent-sharing">
                    <ResourceShareButton
                        value={agent.visibility}
                        podId={podId}
                        resourceType="agent"
                        resourceId={agent.id}
                        resourceLabel="agents"
                        resourceName={label}
                        shareUrl={shareUrl}
                        disabled={!canEdit}
                        onChange={async (next) => {
                            if (onShareVisibilityChange) await onShareVisibilityChange(next);
                            onUpdate({ visibility: next });
                        }}
                        trigger={({ openShare, disabled }) => (
                            <button
                                type="button"
                                className="agent-identity-chip"
                                onClick={openShare}
                                disabled={disabled}
                                title={visibility.description}
                            >
                                <VisibilityIcon className="h-3.5 w-3.5 shrink-0" />
                                <span className="truncate">{visibility.label}</span>
                            </button>
                        )}
                    />
                </div>
            </div>

            <Dialog open={isPictureOpen} onOpenChange={setIsPictureOpen}>
                <DialogContent className="max-h-[86vh] max-w-xl overflow-y-auto">
                    <DialogHeader>
                        <DialogTitle>Display picture</DialogTitle>
                        <DialogDescription>Choose a small visual marker for this agent.</DialogDescription>
                    </DialogHeader>
                    <AgentAvatarPicker
                        name={label}
                        seed={agent.id || agent.name}
                        value={agent.icon_url}
                        onChange={(iconUrl) => onUpdate({ icon_url: iconUrl || undefined })}
                    />
                </DialogContent>
            </Dialog>
        </header>
    );
}
