import type { AssistantSurface } from '@/lib/types';

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

/** The agent this surface belongs to. `null` = Lem. */
function surfaceDefaultAgent(surface: AssistantSurface): string | null {
    return surfaceUsesDefaultAgent(surface) ? null : surface.agent_name ?? null;
}

/**
 * A surface "reaches" an agent when it belongs to them -- which is every place
 * the surface is allowed, because a surface answers as exactly one agent.
 */
export function surfaceReachesAgent(surface: AssistantSurface, agentName: string): boolean {
    return surfaceReaches(surface, agentName).length > 0;
}

/**
 * Whether the pod's own assistant is the agent this surface answers as.
 *
 * The assistant is a real agent row now rather than a stand-in for the absence
 * of one, so the backend decides this and sends `uses_default_agent`. The
 * `agent_name` fallback is only for payloads minted before it did.
 */
export function surfaceUsesDefaultAgent(surface: AssistantSurface): boolean {
    return surface.uses_default_agent ?? !surface.agent_name;
}

/**
 * Whether the pod's assistant is reachable through this surface at all.
 *
 * This once had to consider channels separately, because a workspace whose DMs
 * belonged to one agent could still route `#general` to Lem. One bot is one
 * agent now, so the surface either belongs to the assistant — DMs and every
 * allowed channel with it — or the assistant is not here.
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
 * The email address this surface *is*, or null when it merely has one.
 *
 * Only `RESEND`. Gmail and Outlook also carry a `surface_identity_email`, but
 * that is the mailbox someone connected — the address is theirs, and it was
 * theirs before Lemma saw it. A Resend address is the agent's own, minted for it
 * at creation, and it is the only one worth putting in front of a person as
 * "this is how you write to it".
 */
export function getSurfaceEmail(surface: AssistantSurface): string | null {
    if (getSurfacePlatformKey(surface) !== 'RESEND') return null;
    const address = (surface.reach?.email || surface.surface_identity_email || '').trim();
    return address || null;
}

/** The address an agent answers on, given every surface that reaches it. */
export function agentEmailAddress(surfaces: AssistantSurface[]): string | null {
    for (const surface of surfaces) {
        const address = getSurfaceEmail(surface);
        if (address) return address;
    }
    return null;
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
    /** Why this reach exists, when the label alone doesn't say — "3 people
     * chose this agent" reads very differently from "answers everyone here". */
    detail?: string;
}

/** Display form of a route's channel, or null when it names neither id nor name. */
function channelLabel(route: { channel_id?: string | null; channel_name?: string | null }): string | null {
    const name = route.channel_name || route.channel_id;
    if (!name) return null;
    return name.startsWith('#') ? name : `#${name}`;
}

/** Platforms where the surface's own reach is an inbox, not a chat. */
const MAIL_PLATFORMS = new Set(['RESEND']);

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
    // One surface, one agent: it either belongs to this one or it does not.
    const isTheirs = surfaceDefaultAgent(surface) === reachFor;
    // Nobody DMs a mailbox. "Direct messages" is Slack, Telegram and WhatsApp —
    // a person opening a chat with the bot — and an email surface wore the label
    // anyway, so the agent page's tooltip read "Live · Direct messages" over an
    // address and the agents list called an inbox a place you send DMs.
    const isMail = MAIL_PLATFORMS.has(getSurfacePlatformKey(surface));
    if (isTheirs) {
        reaches.push({
            key: 'dm',
            kind: 'dm',
            label: isMail ? 'Email' : 'Direct messages',
            detail: isMail ? 'Mail sent here becomes work' : 'Answers here',
        });
    }

    // Channels are an allow-list: every one of them is answered by the
    // surface's agent, so if the surface is theirs, all of them are.
    for (const route of isTheirs ? surface.config?.channels ?? [] : []) {
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

/**
 * Whether this agent answers the surface's direct messages.
 *
 * One answer now. Slack used to let each person pick their own agent from the
 * App Home, so several agents could hold DMs on one bot at once.
 */
export function surfaceAnswersDirectMessages(
    surface: AssistantSurface,
    reachFor: string | null,
): boolean {
    return surfaceDefaultAgent(surface) === reachFor;
}

/**
 * Short reach description for a surface from one perspective. `reachFor` is the
 * agent name whose perspective we render; `null` means the pod default assistant.
 */
export function describeReach(surface: AssistantSurface, reachFor: string | null): string {
    const parts = surfaceReaches(surface, reachFor).map((reach) => reach.label);
    return parts.join(' · ') || 'Reaches this agent';
}

/**
 * What a pod member can say about the account a surface runs on.
 *
 * Connected accounts are personal, so for everyone except their owner the
 * account id resolves to nothing — this is the only place a teammate learns who
 * to ask. `problem` states what stops it working (or will), and `canRebind` is
 * true when pointing the surface at your own account is the repair.
 */
export interface SurfaceConnectionSummary {
    /** The account's own label — `@acme_ops_bot`, a mailbox, a workspace. */
    label: string | null;
    attribution: string;
    problem: string | null;
    canRebind: boolean;
}

function connectionOwnerName(surface: AssistantSurface): string | null {
    const owner = surface.connection?.connected_by;
    if (!owner) return null;
    return owner.name?.trim() || owner.email?.trim() || null;
}

export function describeConnection(surface: AssistantSurface): SurfaceConnectionSummary | null {
    const connection = surface.connection;
    // No connection means no account: the surface runs on Lemma's own bot, and
    // there is nobody to name.
    if (!connection) return null;

    const owner = connection.connected_by;
    const name = connectionOwnerName(surface);
    const isYou = Boolean(owner?.is_you);
    const ownerLeft = Boolean(owner) && !owner?.is_pod_member;

    const attribution = isYou
        ? 'Connected by you'
        : name
            ? `Connected by ${name}`
            : 'Connected by someone outside this pod';

    let problem: string | null = null;
    if (connection.status === 'MISSING') {
        problem = 'The account this ran on no longer exists.';
    } else if (connection.status === 'REAUTH_REQUIRED' || connection.status === 'DISCONNECTED') {
        problem = isYou
            ? 'Your account needs reconnecting — nothing arrives until it does.'
            : ownerLeft
                ? `${name || 'Its owner'} has left this pod, so nobody here can reconnect it.`
                : `Only ${name || 'its owner'} can reconnect it.`;
    } else if (ownerLeft) {
        // Not an outage: the credential still resolves off the account row, so
        // it keeps working right up until it expires — and then nobody is left
        // who can renew it. Say that now, while it is still cheap to fix.
        problem = `${name || 'Its owner'} has left this pod. It works until the account expires.`;
    }

    return {
        label: connection.display_name?.trim() || null,
        attribution,
        problem,
        canRebind: problem !== null,
    };
}
