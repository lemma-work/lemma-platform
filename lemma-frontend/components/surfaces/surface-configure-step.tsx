'use client';

import { Plus, Trash2 } from '@/components/ui/icons';

import { SurfaceReachCard } from '@/components/surfaces/surface-reach-card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch, SwitchThumb, SwitchTrack } from '@/components/ui/switch';
import type { SurfacePlatformDefinition } from '@/lib/surfaces/registry';
import type { AssistantSurface } from '@/lib/types';
import { StepLoader } from '@/components/brand/loader';

export const DEFAULT_AGENT_VALUE = '__pod_default_agent__';

export interface ChannelDraft {
    channel_id: string;
    channel_name: string;
    agent_name: string | null;
}

export interface AvailableChannel {
    id: string;
    name?: string | null;
    is_member?: boolean | null;
}

export interface ConfigureDraft {
    agentName: string;
    channels: ChannelDraft[];
    allowedDomains: string;
    allowedEmails: string;
    allowSend: boolean;
}

/**
 * Settings for a surface that already exists and works.
 *
 * Deliberately *not* part of the connect journey: the responder is already
 * known (the agent whose page opened this), the name is derived, and asking
 * either up front is what made the old dialog read like a settings page. What
 * remains here is what genuinely varies after the fact — who answers where,
 * which senders count, and whether agents may speak first.
 */
export function SurfaceConfigureStep({
    definition,
    surface,
    assistants,
    draft,
    onDraftChange,
    availableChannels,
    isLoadingChannels,
    defaultRouteAgent = null,
}: {
    definition: SurfacePlatformDefinition;
    surface: AssistantSurface;
    assistants: Array<{ id?: string | null; name: string }>;
    draft: ConfigureDraft;
    onDraftChange: (patch: Partial<ConfigureDraft>) => void;
    availableChannels: AvailableChannel[];
    isLoadingChannels: boolean;
    /** Agent a newly added route answers as — the one whose page opened this.
     * `null` falls to the pod default, which is right for the pod assistant. */
    defaultRouteAgent?: string | null;
}) {
    const { channelRoutes, senderFilters } = definition.capabilities;

    const usedChannelIds = new Set(draft.channels.map((route) => route.channel_id).filter(Boolean));
    const remainingChannels = availableChannels.filter((channel) => !usedChannelIds.has(channel.id));

    const updateRoute = (index: number, patch: Partial<ChannelDraft>) =>
        onDraftChange({
            channels: draft.channels.map((route, i) => (i === index ? { ...route, ...patch } : route)),
        });

    return (
        <div className="grid gap-4">
            <SurfaceReachCard surface={surface} />

            <div className="grid gap-2">
                <label className="type-eyebrow-medium">Who answers here</label>
                <Select value={draft.agentName} onValueChange={(value) => onDraftChange({ agentName: value })}>
                    <SelectTrigger className="h-10 bg-[var(--field-bg)]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={DEFAULT_AGENT_VALUE}>Pod default agent</SelectItem>
                        {assistants.map((assistant) => (
                            <SelectItem key={assistant.id || assistant.name} value={assistant.name}>
                                {assistant.name}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                    {channelRoutes
                        ? 'Takes direct messages, plus any channel without a route of its own below.'
                        : 'Takes everything that arrives here.'}
                </p>
            </div>

            {senderFilters ? (
                <div className="surface-panel-muted grid gap-3 p-3">
                    <div>
                        <p className="text-sm font-medium text-[var(--text-primary)]">Whose mail becomes work?</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                            A busy mailbox carries a lot that isn’t work. Anything that doesn’t match
                            is left alone.
                        </p>
                    </div>
                    <div className="grid gap-1.5">
                        <label className="type-eyebrow-medium">Allowed domains</label>
                        <Input
                            value={draft.allowedDomains}
                            onChange={(event) => onDraftChange({ allowedDomains: event.target.value })}
                            placeholder="acme.com, partner.org"
                        />
                    </div>
                    <div className="grid gap-1.5">
                        <label className="type-eyebrow-medium">Allowed addresses</label>
                        <Input
                            value={draft.allowedEmails}
                            onChange={(event) => onDraftChange({ allowedEmails: event.target.value })}
                            placeholder="vip@acme.com, support@partner.org"
                        />
                    </div>
                    {!draft.allowedDomains.trim() && !draft.allowedEmails.trim() ? (
                        <p className="text-xs leading-5 text-[var(--state-warning)]">
                            Nothing set — every message in this mailbox becomes work.
                        </p>
                    ) : null}
                </div>
            ) : null}

            {channelRoutes ? (
                <div className="surface-panel-muted grid gap-3 p-3">
                    <div>
                        <p className="text-sm font-medium text-[var(--text-primary)]">Channel routing</p>
                        <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                            Point a channel at its own agent — several agents can answer in one
                            workspace. In channels an agent speaks only when mentioned, or in a
                            thread it already joined.
                        </p>
                        {/* The precondition belongs here, not in the empty state: someone
                            looking at a short list needs it just as much as someone
                            looking at none, and by then it reads as an explanation
                            rather than an instruction. */}
                        <p className="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
                            Only channels the {definition.label} bot has been invited to appear here.
                        </p>
                    </div>

                    {isLoadingChannels ? (
                        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                            <StepLoader size="xs" /> Loading channels…
                        </div>
                    ) : availableChannels.length === 0 && draft.channels.length === 0 ? (
                        <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                            Invite it to a channel in {definition.label}, then reopen this.
                        </p>
                    ) : (
                        <>
                            {draft.channels.map((route, index) => {
                                const otherUsed = new Set(
                                    draft.channels
                                        .filter((_, i) => i !== index)
                                        .map((other) => other.channel_id)
                                        .filter(Boolean),
                                );
                                const options = availableChannels.filter((channel) => !otherUsed.has(channel.id));
                                return (
                                    <ChannelRouteRow
                                        key={index}
                                        route={route}
                                        options={options}
                                        assistants={assistants}
                                        onChange={(patch) => updateRoute(index, patch)}
                                        onRemove={() =>
                                            onDraftChange({
                                                channels: draft.channels.filter((_, i) => i !== index),
                                            })
                                        }
                                    />
                                );
                            })}
                            <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                className="w-fit"
                                disabled={remainingChannels.length === 0}
                                onClick={() => {
                                    const next = remainingChannels[0];
                                    onDraftChange({
                                        channels: [
                                            ...draft.channels,
                                            {
                                                channel_id: next?.id ?? '',
                                                channel_name: next?.name ?? '',
                                                agent_name: defaultRouteAgent,
                                            },
                                        ],
                                    });
                                }}
                            >
                                <Plus className="mr-1.5 h-3.5 w-3.5" />
                                Add channel
                            </Button>
                        </>
                    )}
                </div>
            ) : null}

            <div className="surface-panel-muted flex items-center justify-between gap-3 p-3">
                <div className="min-w-0">
                    <p className="text-sm font-medium text-[var(--text-primary)]">Let agents speak first</p>
                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                        Only in a thread that already exists. Nobody gets messaged out of the blue.
                    </p>
                </div>
                <Switch
                    checked={draft.allowSend}
                    onCheckedChange={(value) => onDraftChange({ allowSend: value })}
                    aria-label="Let agents speak first"
                    className="surface-platform-switch"
                >
                    <SwitchTrack className={draft.allowSend ? 'bg-[var(--action-primary)]' : undefined}>
                        <SwitchThumb className={draft.allowSend ? 'translate-x-4' : undefined} />
                    </SwitchTrack>
                </Switch>
            </div>
        </div>
    );
}

function ChannelRouteRow({
    route,
    options,
    assistants,
    onChange,
    onRemove,
}: {
    route: ChannelDraft;
    options: AvailableChannel[];
    assistants: Array<{ id?: string | null; name: string }>;
    onChange: (patch: Partial<ChannelDraft>) => void;
    onRemove: () => void;
}) {
    // A route whose channel the API no longer lists (the bot left it) still has
    // to render its current selection, or saving would silently drop it.
    const missingSelected = Boolean(route.channel_id) && !options.some((channel) => channel.id === route.channel_id);

    return (
        <div className="grid items-end gap-2 sm:grid-cols-[1fr_1fr_auto]">
            <div className="grid gap-1">
                <label className="type-eyebrow-medium">Channel</label>
                <Select
                    value={route.channel_id}
                    onValueChange={(id) => {
                        const picked = options.find((channel) => channel.id === id);
                        onChange({ channel_id: id, channel_name: picked?.name ?? '' });
                    }}
                >
                    <SelectTrigger className="h-9 bg-[var(--field-bg)]">
                        <SelectValue placeholder="Select channel" />
                    </SelectTrigger>
                    <SelectContent>
                        {missingSelected ? (
                            <SelectItem value={route.channel_id}>
                                {route.channel_name ? `#${route.channel_name}` : route.channel_id}
                            </SelectItem>
                        ) : null}
                        {options.map((channel) => (
                            <SelectItem key={channel.id} value={channel.id}>
                                {channel.name ? `#${channel.name}` : channel.id}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <div className="grid gap-1">
                <label className="type-eyebrow-medium">Agent</label>
                <Select
                    value={route.agent_name ?? DEFAULT_AGENT_VALUE}
                    onValueChange={(value) =>
                        onChange({ agent_name: value === DEFAULT_AGENT_VALUE ? null : value })
                    }
                >
                    <SelectTrigger className="h-9 bg-[var(--field-bg)]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        <SelectItem value={DEFAULT_AGENT_VALUE}>Pod default agent</SelectItem>
                        {assistants.map((assistant) => (
                            <SelectItem key={assistant.id || assistant.name} value={assistant.name}>
                                {assistant.name}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            </div>
            <Button
                type="button"
                variant="quiet"
                size="icon"
                onClick={onRemove}
                aria-label="Remove channel route"
                className="h-9 w-9"
            >
                <Trash2 className="h-4 w-4" />
            </Button>
        </div>
    );
}
