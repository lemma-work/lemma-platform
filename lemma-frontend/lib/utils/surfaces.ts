import type { AssistantSurface } from '@/lib/types';

export const SURFACE_PLATFORM_META: Record<string, { label: string; logoSrc: string }> = {
    SLACK: { label: 'Slack', logoSrc: '/surfaces/slack.png' },
    TEAMS: { label: 'Teams', logoSrc: '/surfaces/teams.png' },
    GMAIL: { label: 'Gmail', logoSrc: '/surfaces/gmail.png' },
    OUTLOOK: { label: 'Outlook', logoSrc: '/surfaces/outlook.png' },
    TELEGRAM: { label: 'Telegram', logoSrc: '/surfaces/telegram.png' },
    WHATSAPP: { label: 'WhatsApp', logoSrc: '/surfaces/whatsapp.png' },
};

export function getSurfacePlatformKey(surface: AssistantSurface): string {
    const config = (surface.config ?? {}) as Record<string, unknown>;
    const raw = typeof surface.platform === 'string' && surface.platform
        ? surface.platform
        : typeof config.type === 'string'
            ? config.type
            : 'SLACK';
    return raw.toUpperCase();
}

export type SurfaceStatusTone = 'success' | 'warning' | 'danger' | 'muted';

export function getSurfaceStatus(surface: AssistantSurface): { label: string; tone: SurfaceStatusTone } {
    const status = String(surface.status || '').toUpperCase();
    if (status === 'ACTIVE') return { label: 'Live', tone: 'success' };
    if (status === 'PENDING_ADMIN_CONSENT') return { label: 'Needs consent', tone: 'warning' };
    if (status === 'ERROR') return { label: 'Error', tone: 'danger' };
    return { label: 'Paused', tone: 'muted' };
}

/** Agent names a surface routes to via its per-channel routes (Slack/Teams). */
export function surfaceChannelAgents(surface: AssistantSurface): Array<string | null> {
    return (surface.config?.channels ?? []).map((route) => route.agent_name ?? null);
}

/**
 * A surface "reaches" an agent when that agent is the surface's default DM
 * responder or the explicit target of one of its channel routes.
 */
export function surfaceReachesAgent(surface: AssistantSurface, agentName: string): boolean {
    if (surface.agent_name === agentName) return true;
    return surfaceChannelAgents(surface).some((name) => name === agentName);
}

/**
 * A surface falls to the pod's default assistant (the virtual "Pod Super Agent")
 * when it has no explicit DM responder. The backend exposes this as
 * `uses_default_agent`; we fall back to an empty agent_name for older payloads.
 */
export function surfaceUsesDefaultAgent(surface: AssistantSurface): boolean {
    return surface.uses_default_agent ?? !surface.agent_name;
}

/**
 * A surface reaches the pod default assistant when it answers its direct
 * messages *or* routes a channel with no agent of its own.
 *
 * The channel half matters on Slack and Teams, where a workspace whose DMs
 * belong to one agent can still route `#general` to the pod assistant — which
 * `surfaceUsesDefaultAgent` alone would read as "not reached here".
 */
export function surfaceReachesDefaultAgent(surface: AssistantSurface): boolean {
    return surfaceReaches(surface, null).length > 0;
}

/** The surface's own address — a phone number, bot handle, workspace name, etc. */
export function getSurfaceIdentity(surface: AssistantSurface): string | null {
    const identity = surface.surface_identity_username?.trim();
    return identity || null;
}

/**
 * A direct link to message the surface itself (not this app) — e.g. a `wa.me`
 * chat link or a `t.me` bot link. Returns null for platforms with no such
 * direct-open convention (Slack, Teams, Gmail, Outlook) or a missing identity.
 */
export function getSurfaceDeepLink(surface: AssistantSurface): string | null {
    const identity = getSurfaceIdentity(surface);
    if (!identity) return null;

    switch (getSurfacePlatformKey(surface)) {
        case 'WHATSAPP': {
            const digits = identity.replace(/\D/g, '');
            return digits ? `https://wa.me/${digits}` : null;
        }
        case 'TELEGRAM': {
            const handle = identity.replace(/^@/, '');
            return handle ? `https://t.me/${handle}` : null;
        }
        default:
            return null;
    }
}

/**
 * One distinct place a surface reaches an agent.
 *
 * On Slack and Teams a single workspace install carries many of these — the
 * bot's DMs plus every channel routed by name — and they are what a person
 * thinks in ("#sales"), not the install. Identity platforms have exactly one.
 */
export interface SurfaceReach {
    /** Stable across renders; also what the modal targets. */
    key: string;
    kind: 'dm' | 'channel';
    /** Chip text. Channels arrive already prefixed with `#`. */
    label: string;
    /** Set when `kind` is `channel`. */
    channelId?: string;
}

/** Display form of a route's channel, or null when it names neither id nor name. */
function channelLabel(route: { channel_id?: string | null; channel_name?: string | null }): string | null {
    const name = route.channel_name || route.channel_id;
    if (!name) return null;
    return name.startsWith('#') ? name : `#${name}`;
}

/**
 * Every place this surface reaches one agent. `reachFor` is the agent name whose
 * perspective we render; `null` means the pod default assistant.
 *
 * DMs come first because they are the surface's own address — the thing that
 * exists before anyone routes anything.
 */
export function surfaceReaches(
    surface: AssistantSurface,
    reachFor: string | null,
): SurfaceReach[] {
    const reaches: SurfaceReach[] = [];
    if (surfaceAnswersDirectMessages(surface, reachFor)) {
        reaches.push({ key: 'dm', kind: 'dm', label: 'Direct messages' });
    }

    for (const route of surface.config?.channels ?? []) {
        const routed = reachFor === null ? !route.agent_name : route.agent_name === reachFor;
        if (!routed) continue;
        const label = channelLabel(route);
        if (!label) continue;
        reaches.push({
            key: `channel:${route.channel_id || label}`,
            kind: 'channel',
            label,
            channelId: route.channel_id || undefined,
        });
    }

    return reaches;
}

/** Whether this agent is the one that answers the surface's direct messages. */
export function surfaceAnswersDirectMessages(
    surface: AssistantSurface,
    reachFor: string | null,
): boolean {
    return reachFor === null
        ? surfaceUsesDefaultAgent(surface)
        : surface.agent_name === reachFor;
}

/**
 * The agent that answers this surface's DMs, for naming the holder to everyone
 * else. `null` means the pod default assistant.
 *
 * A surface has exactly one — DMs carry no channel to route on, so they always
 * fall to `surface.agent_name`. That is the one hard limit on a Slack workspace,
 * and naming the holder is how it stops being invisible.
 */
export function surfaceDirectMessageAgent(surface: AssistantSurface): string | null {
    return surfaceUsesDefaultAgent(surface) ? null : surface.agent_name ?? null;
}

/**
 * Short reach description for a surface from one perspective. `reachFor` is the
 * agent name whose perspective we render; `null` means the pod default assistant.
 */
export function describeReach(surface: AssistantSurface, reachFor: string | null): string {
    const parts = surfaceReaches(surface, reachFor).map((reach) => reach.label);
    return parts.join(' · ') || 'Routed here';
}
