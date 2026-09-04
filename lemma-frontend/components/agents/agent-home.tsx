'use client';

import Image from 'next/image';
import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useRef, useState } from 'react';

import { ArrowUp } from '@/components/ui/icons';
import { AgentTestPanel } from '@/components/agents/agent-test-panel';
import { Skeleton } from '@/components/shared/loading';
import { SurfaceModal, type SurfaceModalTarget } from '@/components/surfaces/surface-modal';
import type { SurfacePlatformValue } from '@/lib/hooks/use-pod-surfaces';
import { Button } from '@/components/ui/button';
import { ASSISTANT_MESSAGE_PARAM } from '@/lib/pods/composer-launch';

import { cn } from '@/lib/utils';
import { ResourceIcon } from '@/components/shared/resource-icon';
import { ResourceIdentity } from '@/components/shared/resource-identity';
import { getSurfaceDefinition, SURFACE_PLATFORM_ORDER } from '@/lib/surfaces/registry';
import {
    getSurfaceDeepLink,
    getSurfaceEmail,
    getSurfacePlatformKey,
    surfaceReachesAgent,
    surfaceReachesDefaultAgent,
} from '@/lib/utils/surfaces';
import type { AssistantSurface, Conversation, Schedule } from '@/lib/types';
import { describeScheduleConfig } from '@/lib/utils/schedules';
import { formatRelativeTime } from '@/lib/utils/relative-time';
import { formatAgentName } from '@/lib/utils/agents';
import { AgentContactShare } from '@/components/agents/agent-contact-share';
import { LEM_SEED, agentIdentitySeed } from '@/lib/identity/seeded-identity';

/**
 * One place someone can start talking to this agent, outside this app.
 *
 * A surface only becomes a button when it resolves to somewhere a person can
 * actually be sent. Telegram and WhatsApp carry an open-chat convention;
 * Resend carries an address. Slack and Teams reach the agent perfectly well but
 * have no link we can build from what the surface record holds, so they are
 * named without being clickable rather than handed a dead href — the row is
 * making a promise ("talk to me here") and a button that goes nowhere breaks it.
 */
interface AgentReachTarget {
    key: string;
    label: string;
    logoSrc?: string;
    href: string | null;
    connected: boolean;
}

/**
 * How many not-yet-connected platforms to advertise. The row's job is to say
 * "there are more ways to reach me" — every remaining platform, listed, would
 * bury the ones that actually work behind a wall of things that do not.
 */
const MAX_UNCONNECTED_REACH = 3;

/** A preview, not the archive — the full history is the conversations page. */
const MAX_HOME_RUNS = 6;

/** Same bargain for routines: the schedules page holds the rest. */
const MAX_HOME_ROUTINES = 4;

function reachTargets(surfaces: AssistantSurface[]): AgentReachTarget[] {
    const byPlatform = new Map<string, AgentReachTarget>();

    for (const surface of surfaces) {
        const key = getSurfacePlatformKey(surface);
        if (byPlatform.has(key)) continue;

        const definition = getSurfaceDefinition(key);
        const email = getSurfaceEmail(surface);
        byPlatform.set(key, {
            key,
            label: definition?.label || key,
            logoSrc: definition?.logoSrc,
            href: getSurfaceDeepLink(surface) ?? (email ? `mailto:${email}` : null),
            connected: true,
        });
    }

    /* The unconnected half. This row is the one place in the product that
       states the whole promise — you can reach this agent where you already
       talk — so listing only what is already wired makes it a status report for
       people who have finished setting up, which is nobody on the day they need
       it most. A platform not yet connected is the most useful thing the row can
       show: it is an offer, not an absence. */
    const unconnected: AgentReachTarget[] = [];
    for (const platform of SURFACE_PLATFORM_ORDER) {
        if (unconnected.length >= MAX_UNCONNECTED_REACH) break;
        if (byPlatform.has(platform)) continue;

        const definition = getSurfaceDefinition(platform);
        if (!definition) continue;
        unconnected.push({
            key: platform,
            label: definition.label,
            logoSrc: definition.logoSrc,
            href: null,
            connected: false,
        });
    }

    return [...byPlatform.values(), ...unconnected];
}

/**
 * The agent's home, and it stays its home.
 *
 * It was briefly the empty state of a live conversation, so typing here turned
 * this page into a transcript and the agent's front door was gone until you
 * asked for a new run. That fought the workspace: conversations in this product
 * are *tabs*, and the working set is persistent — so the honest behaviour is the
 * one pod home already has. Sending navigates to the conversation route, which
 * opens as its own tab beside this one, and the agent's page is still the
 * agent's page when you come back to it.
 *
 * Which frees the page to be worth visiting: who this is, where else it can be
 * reached, the box that starts something, and what it has been doing.
 */
export function AgentHome({
    podId,
    agentId,
    agentSlug,
    agentName,
    description,
    iconUrl,
    surfaces,
    schedules = [],
    conversations = [],
    canConverse = true,
    isAssistant,
}: {
    podId: string;
    /**
     * The agent's id, which is what its face is seeded from.
     *
     * Separate from `agentName` because the name arriving here is already a
     * *display* name — so seeding on it drew this page a different creature from
     * the one in the sidebar, which seeds on the stored name, and a third from
     * the header, which seeds on the id. See `agentIdentitySeed`.
     */
    agentId?: string | null;
    /** The stored name, which is what `/pod/…/agents/…` routes on. */
    agentSlug?: string | null;
    agentName: string;
    description?: string | null;
    iconUrl?: string | null;
    surfaces: AssistantSurface[];
    schedules?: Schedule[];
    conversations?: Conversation[];
    /** False for an agent with declared inputs: it is called, not talked to. */
    canConverse?: boolean;
    /** Lem answers as the pod, so its reach is the pod's default one. */
    isAssistant?: boolean;
}) {
    const router = useRouter();
    const composerRef = useRef<HTMLTextAreaElement>(null);
    const [draft, setDraft] = useState('');
    const [connectTarget, setConnectTarget] = useState<SurfaceModalTarget | null>(null);
    const label = formatAgentName(agentName);

    const submit = () => {
        const text = draft.trim();
        if (!text) return;

        const params = new URLSearchParams();
        if (!isAssistant) params.set('agent', agentName);
        params.set(ASSISTANT_MESSAGE_PARAM, text);
        setDraft('');
        router.push(`/pod/${encodeURIComponent(podId)}/conversations/new?${params.toString()}`);
    };

    /* Matched on the *stored* name, not the display one. `surfaceDefaultAgent`
       compares against `surface.agent_name`, which is what the pod typed — so
       an agent named `support_triage` was asking whether a surface answers for
       "Support Triage" and getting no for every chip on the row. */
    const reachableSurfaces = surfaces.filter((surface) => (isAssistant
        ? surfaceReachesDefaultAgent(surface)
        : surfaceReachesAgent(surface, agentSlug || agentName)));
    const reach = reachTargets(reachableSurfaces);

    return (
        <div className="agent-home">
            {isAssistant ? (
                /* Lem draws from the reserved seed, not a hashed one — it is the
                   responder every conversation already knows, so a generated
                   face would introduce a stranger, and a different stranger in
                   every pod. It used to wear the Lemma trademark on a tinted
                   tile, which drew the pod's most capable agent in the treatment
                   reserved for inert things: no eyes, no state, no motion, on the
                   one screen whose whole job is to introduce it. Same 64px and
                   the same renderer as an agent's face below. */
                <ResourceIdentity
                    seed={LEM_SEED}
                    label={label}
                    kind="being"
                    size={64}
                    className="agent-home-face h-16 w-16"
                />
            ) : (
                /* 64px: well past the 32px where a being's rich motion turns on,
                   so the face is awake when you arrive — this is the one place
                   on the page whose whole job is to introduce it. */
                <ResourceIcon
                    iconUrl={iconUrl}
                    alt=""
                    label={label}
                    identityKind="being"
                    identitySeed={agentIdentitySeed({ id: agentId, name: agentName })}
                    identitySize={64}
                    className="agent-home-face h-16 w-16 rounded-2xl"
                />
            )}
            {/* First person, because the identity system already draws this
                thing with a face and eyes that answer the pointer. Naming it in
                the third person on the one screen where it introduces itself
                would be the copy disagreeing with the artwork. */}
            <h1 className="agent-home-greeting">Hey, I&apos;m {label}</h1>
            {description ? <p className="agent-home-description">{description}</p> : null}

            {/* The composer, and it launches rather than hosts. `assistantMessage`
                is the convention for arriving somewhere with something already
                said — the loud half of `composer-launch`, which is right here
                because the person has written the sentence and pressed send. The
                conversation opens in its own tab; this page keeps its own. */}
            {canConverse ? (
            <form
                className="agent-home-composer form-field-control"
                /* The whole box is the target, not just the textarea inside it.
                   A composer is chrome wrapped around a field, and clicking the
                   chrome — the padding, the bar with the send button — did
                   nothing, so the box looked focusable in the places it was
                   least obviously not. `mousedown` rather than `click` so the
                   caret lands before the browser's own selection settles. */
                onMouseDown={(event) => {
                    if (event.target === composerRef.current) return;
                    if ((event.target as HTMLElement).closest('button')) return;
                    event.preventDefault();
                    composerRef.current?.focus();
                }}
                onSubmit={(event) => {
                    event.preventDefault();
                    submit();
                }}
            >
                <textarea
                    ref={composerRef}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    onKeyDown={(event) => {
                        if (event.key === 'Enter' && !event.shiftKey) {
                            event.preventDefault();
                            submit();
                        }
                    }}
                    placeholder={`Ask ${label} to do something…`}
                    rows={2}
                    className="inline-edit-field min-h-14 w-full resize-none px-3 py-2.5 text-sm leading-6"
                />
                <div className="agent-home-composer-bar">
                    <span className="truncate text-xs text-[var(--text-tertiary)]">
                        Opens in a new tab
                    </span>
                    <Button
                        variant="primary"
                        type="submit"
                        size="icon"
                        className="h-8 w-8 shrink-0 rounded-full"
                        disabled={!draft.trim()}
                        aria-label={`Start a conversation with ${label}`}
                    >
                        <ArrowUp className="h-4 w-4" />
                    </Button>
                </div>
            </form>
            ) : (
                /* An agent with declared inputs is *called* with arguments, so
                   the box that starts it is a form, not a message. It renders
                   here rather than one click away in the editor: this is the
                   agent's home, and "how do I run this" is the question a home
                   exists to answer. The renderer is the panel's own — the same
                   typed fields, required-key handling and JSON inputs the dock
                   draws — minus its header, because the page named the agent in
                   the greeting directly above. */
                <div className="agent-home-run-form">
                    <p className="agent-home-called">Runs with typed inputs.</p>
                    <div className="agent-home-run-form-body">
                        <AgentTestPanel
                            podId={podId}
                            agentName={agentName}
                            view="conversation"
                            showHeader={false}
                        />
                    </div>
                </div>
            )}

            {reach.length > 0 ? (
                <div className="agent-home-reach">
                    {reach.map((target) => {
                        const body = (
                            <>
                                {target.logoSrc ? (
                                    <Image
                                        src={target.logoSrc}
                                        alt=""
                                        width={16}
                                        height={16}
                                        className={cn('object-contain', !target.connected && 'opacity-60')}
                                        aria-hidden="true"
                                    />
                                ) : null}
                                <span>
                                    {target.connected
                                        ? `Talk to me on ${target.label}`
                                        : `Connect ${target.label}`}
                                </span>
                            </>
                        );

                        /* Connecting happens here, not somewhere else. This
                           used to link to `/pod/:id/surfaces`, which is a
                           *legacy redirect* to the agents index — so "Connect
                           WhatsApp" bounced you to a list of agents, having
                           forgotten both the platform you picked and the agent
                           you picked it for. Surfaces are configured from the
                           agent that answers on them, and this is that agent's
                           page: the modal opens over it with both already
                           known. */
                        if (!target.connected) {
                            return (
                                <button
                                    key={target.key}
                                    type="button"
                                    onClick={() => setConnectTarget({ platform: target.key as SurfacePlatformValue })}
                                    className="agent-home-reach-chip custom-focus-ring"
                                >
                                    {body}
                                </button>
                            );
                        }

                        if (!target.href) {
                            return (
                                <span key={target.key} className="agent-home-reach-chip" data-static="true">
                                    {body}
                                </span>
                            );
                        }

                        return (
                            <Link
                                key={target.key}
                                href={target.href}
                                target="_blank"
                                rel="noreferrer"
                                className="agent-home-reach-chip custom-focus-ring"
                                data-connected="true"
                            >
                                {body}
                            </Link>
                        );
                    })}
                    {/* Last in the row. Lem is included: it answers on the pod's
                        own surfaces, which is an address worth handing out — and
                        the card says which pod, since Lem's name does not. */}
                    {isAssistant || agentSlug ? (
                        <AgentContactShare
                            podId={podId}
                            workspacePath={
                                isAssistant
                                    ? `/pod/${encodeURIComponent(podId)}/ai/assistant`
                                    : `/pod/${encodeURIComponent(podId)}/agents/${encodeURIComponent(agentSlug!)}`
                            }
                            agentName={label}
                            seed={isAssistant ? LEM_SEED : agentIdentitySeed({ id: agentId, name: agentSlug })}
                            iconUrl={iconUrl}
                            description={description}
                            isAssistant={isAssistant}
                            surfaces={reachableSurfaces}
                        />
                    ) : null}
                </div>
            ) : null}

            {/* What it does without you. Routines are the part of an agent that
                is easiest to forget you set up and most surprising to rediscover
                from a run you did not start, so the home states them plainly —
                `describeScheduleConfig` already turns a cron into a sentence. It
                is not the schedule editor: this says *that* it runs and when,
                and Configure is where you change it. */}
            {schedules.length > 0 ? (
                <div className="agent-home-runs">
                    <p className="agent-home-runs-heading">Runs on its own</p>
                    {schedules.slice(0, MAX_HOME_ROUTINES).map((schedule) => (
                        <Link
                            key={schedule.id}
                            href={`/pod/${encodeURIComponent(podId)}/schedules`}
                            className="agent-home-run custom-focus-ring"
                        >
                            <span className="min-w-0 flex-1 truncate">{schedule.name}</span>
                            <span className="agent-home-run-time">
                                {describeScheduleConfig(schedule)}
                            </span>
                        </Link>
                    ))}
                </div>
            ) : null}

            {/* What it has been doing. This is the page answering "where are this
                agent's conversations" itself, rather than sending you to the
                shell for it — and it is a section of a home, not a rail flush
                against the sidebar, which is the difference between a list and a
                second navigation column. Each row opens in its own tab. */}
            {conversations.length > 0 ? (
                <div className="agent-home-runs">
                    <p className="agent-home-runs-heading">Recent conversations</p>
                    {conversations.slice(0, MAX_HOME_RUNS).map((conversation) => (
                        <Link
                            key={conversation.id}
                            href={`/pod/${encodeURIComponent(podId)}/conversations/${encodeURIComponent(conversation.id)}`}
                            className="agent-home-run custom-focus-ring"
                        >
                            <span className="min-w-0 flex-1 truncate">
                                {conversation.title || 'Untitled conversation'}
                            </span>
                            <span className="agent-home-run-time">
                                {formatRelativeTime(conversation.updated_at || conversation.created_at)}
                            </span>
                        </Link>
                    ))}
                    {conversations.length > MAX_HOME_RUNS ? (
                        <Link
                            href={`/pod/${encodeURIComponent(podId)}/conversations`}
                            className="agent-home-run agent-home-run-all custom-focus-ring"
                        >
                            All conversations
                        </Link>
                    ) : null}
                </div>
            ) : null}

            <SurfaceModal
                podId={podId}
                target={connectTarget}
                agentName={isAssistant ? null : agentName}
                onClose={() => setConnectTarget(null)}
            />
        </div>
    );
}

/**
 * The home, waiting.
 *
 * Shaped like what arrives — a face, a name, a line of description, the
 * composer — rather than like the editor's stack of cards, which is what used to
 * stand here and made every arrival flash the wrong screen. Nothing about the
 * frame is unknown while the agent loads: the name is in the URL and the blocks
 * land in the same places whoever it turns out to be.
 */
export function AgentHomeSkeleton() {
    return (
        <div className="agent-home" role="status" aria-label="Loading agent">
            <Skeleton shape="block" className="h-16 w-16 rounded-2xl" />
            <Skeleton className="h-8 w-56" />
            <Skeleton className="h-4 w-80" />
            <Skeleton shape="block" className="agent-home-composer h-24 w-full rounded-xl" />
        </div>
    );
}
