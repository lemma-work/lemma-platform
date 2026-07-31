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
 * Short reach description for a surface from one perspective. `reachFor` is the
 * agent name whose perspective we render; `null` means the pod default assistant.
 */
export function describeReach(surface: AssistantSurface, reachFor: string | null): string {
    const parts: string[] = [];
    const isDefaultResponder = reachFor === null
        ? surfaceUsesDefaultAgent(surface)
        : surface.agent_name === reachFor;
    if (isDefaultResponder) parts.push('Direct messages');

    const routes = (surface.config?.channels ?? []).filter((route) =>
        reachFor === null ? !route.agent_name : route.agent_name === reachFor,
    );
    for (const route of routes) {
        const name = route.channel_name || route.channel_id;
        if (name) parts.push(name.startsWith('#') ? name : `#${name}`);
    }

    return parts.join(' · ') || 'Routed here';
}
