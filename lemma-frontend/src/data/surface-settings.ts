import type { Surface } from "./types";
import { isPodDefaultAgent } from "./agent-names";
import type { AgentSurfaceResponse, SurfaceUpdateRequest } from "lemma-sdk";

export interface SurfaceDraft {
    agent: string;
    enabled: boolean;
    originallyEnabled: boolean;
    channels: { channel_id: string; channel_name: string | null }[];
    domains: string;
    emails: string;
    allowSend: boolean;
}

export const routesSupported = (platform: string) => ["SLACK", "TEAMS"].includes(platform);
export const filtersSupported = (platform: string) => ["RESEND", "EMAIL"].includes(platform);

export function surfaceDraft(surface: AgentSurfaceResponse): SurfaceDraft {
    return {
        agent: surface.agent_name ?? "pod_default",
        enabled: surface.status !== "INACTIVE",
        originallyEnabled: surface.status !== "INACTIVE",
        channels: (surface.config.channels ?? []).filter(route => route.channel_id).map(route => ({
            channel_id: route.channel_id!, channel_name: route.channel_name ?? null,
        })),
        domains: (surface.config.identity?.allowed_domains ?? []).join(", "),
        emails: (surface.config.identity?.allowed_email_addresses ?? []).join(", "),
        allowSend: surface.config.send_policy?.allow_send ?? false,
    };
}

const split = (value: string) => [...new Set(value.split(/[,\n]/).map(part => part.trim()).filter(Boolean))];

export function surfacePatch(platform: string, draft: SurfaceDraft): SurfaceUpdateRequest {
    // A partial config preserves provider settings and conversation policies the form does not own.
    return {
        default_agent_name: draft.agent === "pod_default" ? null : draft.agent,
        ...(draft.enabled !== draft.originallyEnabled ? { is_enabled: draft.enabled } : {}),
        config: {
            ...(routesSupported(platform) ? { channels: draft.channels } : {}),
            ...(filtersSupported(platform) ? { identity: { allowed_domains: split(draft.domains), allowed_email_addresses: split(draft.emails) } } : {}),
            send_policy: { allow_send: draft.allowSend },
        },
    };
}

export function surfaceStatus(status: string | undefined, active: boolean): string {
    switch (status) {
        case "PENDING_ADMIN_CONSENT": return "Needs administrator approval";
        case "NEEDS_SETUP": return "Finish setup";
        case "ERROR": return "Needs attention";
        case "INACTIVE": return "Disabled";
        default: return active ? "Connected" : "Needs attention";
    }
}

export function surfacesForAgent(surfaces: Surface[], agentName = "pod_default"): Surface[] {
    return surfaces.filter(surface => isPodDefaultAgent(agentName)
        ? surface.mine
        : !surface.mine && surface.agentKey === agentName);
}
