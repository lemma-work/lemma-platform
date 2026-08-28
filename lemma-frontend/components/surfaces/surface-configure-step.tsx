'use client';

import Link from 'next/link';
import { Plus, Trash2 } from '@/components/ui/icons';

import { SurfaceConnectionRow } from '@/components/surfaces/surface-connection-row';
import { SurfaceReachCard } from '@/components/surfaces/surface-reach-card';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { Switch, SwitchThumb, SwitchTrack } from '@/components/ui/switch';
import type { SurfacePlatformDefinition } from '@/lib/surfaces/registry';
import type { AssistantSurface } from '@/lib/types';
import { StepLoader } from '@/components/brand/loader';
import { DEFAULT_RESPONDER_NAME } from '@/lib/utils/agents';

export const DEFAULT_AGENT_VALUE = '__pod_default_agent__';

/**
 * "The pod's own assistant answers here" — an explicit choice, and not the same
 * as leaving a route unset.
 *
 * Unset means nobody has said, which falls to whoever answers the surface's
 * DMs. Collapsing the two is how an explicit pick made inside Slack came back
 * as a different agent; the value matches Slack's own picker so a route set in
 * either place reads the same in both.
 */
export const POD_ASSISTANT_VALUE = '__pod_assistant__';

export interface ChannelDraft {
    channel_id: string;
    channel_name: string;
    agent_name: string | null;
    use_pod_assistant: boolean;
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
    /** Slack only: this app is one agent's own bot, not the workspace's shared one. */
    dedicatedToAgent: boolean;
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
    customAppHref,
    onOpenReference,
    onRebind,
}: {
    definition: SurfacePlatformDefinition;
    surface: AssistantSurface;
    assistants: Array<{ id?: string | null; name: string }>;
    draft: ConfigureDraft;
    onDraftChange: (patch: Partial<ConfigureDraft>) => void;
    availableChannels: AvailableChannel[];
    isLoadingChannels: boolean;
    /** Re-runs the connect journey to bind this surface to another account. */
    onRebind: () => void;
    /** Agent a newly added route answers as — the one whose page opened this.
     * `null` is an explicit pod-assistant choice. */
    defaultRouteAgent?: string | null;
    /** Where an org sets up its own app for this platform. Passed only when it
     * hasn't already — otherwise the offer is stale. */
    customAppHref?: string;
    /** Opens the reference card (delivery URL, what to check). Absent when
     * there is nothing to reference. */
    onOpenReference?: () => void;
}) {
    const { channelRoutes, senderFilters } = definition.capabilities;

    const usedChannelIds = new Set(draft.channels.map((route) => route.channel_id).filter(Boolean));
    const remainingChannels = availableChannels.filter((channel) => !usedChannelIds.has(channel.id));
    // A route only delivers where the bot is actually a member, and the API
    // tells us — it returns every public channel, not only the joined ones.
    // Adding a route defaults to one that will work rather than the first in
    // the list, which was as likely as not a channel it has never been in.
    const firstJoinable = remainingChannels.find((channel) => channel.is_member) ?? remainingChannels[0];
    const anyJoined = availableChannels.some((channel) => channel.is_member);

    // What to call this surface's responder in copy. The draft holds the
    // sentinel for "the pod assistant", which is not a name anyone would read.
    const responderLabel =
        draft.agentName === DEFAULT_AGENT_VALUE ? DEFAULT_RESPONDER_NAME : draft.agentName;

    const updateRoute = (index: number, patch: Partial<ChannelDraft>) =>
        onDraftChange({
            channels: draft.channels.map((route, i) => (i === index ? { ...route, ...patch } : route)),
        });

    return (
        <div className="grid gap-4">
            <SurfaceReachCard surface={surface} />

            <SurfaceConnectionRow surface={surface} onRebind={onRebind} />

            <div className="grid gap-2">
                <label className="type-eyebrow-medium">Who answers here</label>
                <Select value={draft.agentName} onValueChange={(value) => onDraftChange({ agentName: value })}>
                    <SelectTrigger className="h-10 bg-[var(--field-bg)]">
                        <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                        {/* Named the same as the route picker below, because it
                            means the same thing to a person. */}
                        <SelectItem value={DEFAULT_AGENT_VALUE}>{DEFAULT_RESPONDER_NAME}</SelectItem>
                        {assistants.map((assistant) => (
                            <SelectItem key={assistant.id || assistant.name} value={assistant.name}>
                                {assistant.name}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
                <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                    {/* Slack is the one platform where this is not "answers every
                        DM" — each person picks their own from the App Home, so
                        this one answers whoever hasn't. Saying otherwise made it
                        look like a setting that overrides everybody. */}
                    {definition.platform === 'SLACK'
                        ? draft.dedicatedToAgent
                            ? 'Answers everyone here — this bot is theirs alone, so nobody picks anyone else.'
                            : 'Answers anyone who hasn’t picked their own, plus any channel you haven’t set separately.'
                        : channelRoutes
                            ? 'Answers direct messages, plus any channel you haven’t set separately.'
                            : 'Answers everything that arrives here.'}
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
                            When you invite Lemma to a channel, it asks who should answer. This is
                            the same question, if you want to change your mind. In channels it
                            only speaks when you mention it, or in a thread it already joined.
                        </p>
                        {/* The precondition belongs here, not in the empty state: someone
                            looking at a short list needs it just as much as someone
                            looking at none, and by then it reads as an explanation
                            rather than an instruction. */}
                        <p className="mt-1 text-xs leading-5 text-[var(--text-tertiary)]">
                            Every channel is listed here, but Lemma only answers in the ones it’s
                            been invited to.
                        </p>
                    </div>

                    {isLoadingChannels ? (
                        <div className="flex items-center gap-2 text-xs text-[var(--text-tertiary)]">
                            <StepLoader size="xs" /> Loading channels…
                        </div>
                    ) : !anyJoined && draft.channels.length === 0 ? (
                        <div className="grid gap-1">
                            <p className="text-xs leading-5 text-[var(--text-secondary)]">
                                Lemma isn’t in any channel yet. In {definition.label}, type{' '}
                                <code className="rounded bg-[var(--field-bg)] px-1 py-0.5">/invite @Lemma</code>{' '}
                                in the channel you want it to answer in.
                            </p>
                            <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                                It’ll ask who should answer, and the channel turns up here. Nothing to
                                set up first.
                            </p>
                        </div>
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
                                    onDraftChange({
                                        channels: [
                                            ...draft.channels,
                                            {
                                                channel_id: firstJoinable?.id ?? '',
                                                channel_name: firstJoinable?.name ?? '',
                                                agent_name: defaultRouteAgent,
                                                use_pod_assistant: defaultRouteAgent === null,
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

            {/* Slack only, because the picker it turns off is Slack's alone.
                Stated rather than inferred: a surface bound to an agent looks
                identical whether that agent is the only responder or merely the
                default, and only the person who made the app knows which they
                meant. */}
            {definition.platform === 'SLACK' ? (
                <div className="surface-panel-muted flex items-center justify-between gap-3 p-3">
                    <div className="min-w-0">
                        <p className="text-sm font-medium text-[var(--text-primary)]">
                            {responderLabel} answers here, and only {responderLabel}
                        </p>
                        <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                            For a bot made for one agent. Turns off the “who answers my
                            messages?” choice in Slack — this app can only be {responderLabel}.
                        </p>
                    </div>
                    <Switch
                        checked={draft.dedicatedToAgent}
                        onCheckedChange={(value) => onDraftChange({ dedicatedToAgent: value })}
                        aria-label={`Only ${responderLabel} answers here`}
                        className="surface-platform-switch"
                    >
                        <SwitchTrack
                            className={draft.dedicatedToAgent ? 'bg-[var(--action-primary)]' : undefined}
                        >
                            <SwitchThumb
                                className={draft.dedicatedToAgent ? 'translate-x-4' : undefined}
                            />
                        </SwitchTrack>
                    </Switch>
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

            {/* One quiet line, and only when it says something true.
                Almost nobody needs their own Slack app, so a full panel gave it
                the weight of a setting — and it kept offering the switch to
                workspaces that had already made it, which reads as an
                invitation to do something you have done. `customAppHref` is
                passed only when the workspace is still on Lemma's app; once
                it's on its own, the useful thing is where messages arrive. */}
            {customAppHref || onOpenReference ? (
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-1">
                    {customAppHref ? (
                        <Link
                            href={customAppHref}
                            className="lemma-quiet-text-button custom-focus-ring text-xs font-medium text-[var(--text-secondary)] underline-offset-2 hover:underline"
                        >
                            Use your own Slack app
                        </Link>
                    ) : null}
                    {onOpenReference ? (
                        <button
                            type="button"
                            onClick={onOpenReference}
                            className="lemma-quiet-text-button custom-focus-ring text-xs font-medium text-[var(--text-secondary)] underline-offset-2 hover:underline"
                        >
                            Where Slack sends messages
                        </button>
                    ) : null}
                </div>
            ) : null}
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
    const selected = options.find((channel) => channel.id === route.channel_id);
    // `is_member === false` is a real answer; `undefined` is a platform that
    // doesn't report membership, and warning there would be a guess.
    const notInvited = selected?.is_member === false;

    return (
        <div className="grid gap-1">
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
                                    <span className="flex w-full items-center justify-between gap-2">
                                        <span className="truncate">
                                            {channel.name ? `#${channel.name}` : channel.id}
                                        </span>
                                        {channel.is_member === false ? (
                                            <span className="shrink-0 text-[var(--text-tertiary)]">
                                                not invited
                                            </span>
                                        ) : null}
                                    </span>
                                </SelectItem>
                            ))}
                        </SelectContent>
                    </Select>
                </div>
                <div className="grid gap-1">
                    <label className="type-eyebrow-medium">Agent</label>
                    <Select
                        value={
                            route.use_pod_assistant
                                ? POD_ASSISTANT_VALUE
                                : route.agent_name ?? DEFAULT_AGENT_VALUE
                        }
                        onValueChange={(value) =>
                            onChange({
                                agent_name:
                                    value === DEFAULT_AGENT_VALUE || value === POD_ASSISTANT_VALUE
                                        ? null
                                        : value,
                                use_pod_assistant: value === POD_ASSISTANT_VALUE,
                            })
                        }
                    >
                        <SelectTrigger className="h-9 bg-[var(--field-bg)]">
                            <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                            {/* Three answers, in the order they narrow: nobody has said,
                                the pod's own assistant, one named agent. The middle one
                                is a choice, not a fallback — see POD_ASSISTANT_VALUE. */}
                            <SelectItem value={DEFAULT_AGENT_VALUE}>Whoever answers here by default</SelectItem>
                            <SelectItem value={POD_ASSISTANT_VALUE}>{DEFAULT_RESPONDER_NAME}</SelectItem>
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
            {notInvited ? (
                <p className="text-xs leading-5 text-[var(--state-warning)]">
                    Lemma isn’t in {route.channel_name ? `#${route.channel_name}` : 'this channel'} yet.
                    You can save this, but nothing will arrive until you invite it.
                </p>
            ) : null}
        </div>
    );
}
