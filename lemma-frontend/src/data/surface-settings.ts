import type { Surface } from "./types";
import { isPodDefaultAgent } from "./agent-names";
import { isGroupPlatform } from "@/channels/sources";
import type { AgentSurfaceResponse, SurfaceContactsConfig, SurfaceUpdateRequest } from "lemma-sdk";

export interface SurfaceDraft {
    agent: string;
    enabled: boolean;
    originallyEnabled: boolean;
    channels: { channel_id: string; channel_name: string | null }[];
    domains: string;
    emails: string;
    allowSend: boolean;
    /** The bot answers people outside the space, from what the space made
     *  Public, in every group it is in. On unless it was switched off. */
    answersOutsiders: boolean;
    /** Whom the bot answers in private chats beyond the space's people. */
    contactsAnswer: "off" | "known" | "anyone";
    originalContactsAnswer: "off" | "known" | "anyone";
}

/** Platforms whose bot can answer a contact in a private chat. */
export const contactsSupported = (platform: string) => ["WHATSAPP", "TELEGRAM", "RESEND"].includes(platform);

/** Whether this bot is the space's own -- its own token, number or address --
 *  which is the only kind that answers contacts. Lemma's shared bot is every
 *  space's at once, and a stranger writing to it is writing to Lemma. */
export function isOwnBot(surface: AgentSurfaceResponse): boolean {
    return surface.platform === "RESEND" || Boolean(surface.pod_connector) || surface.credential_mode === "CUSTOM";
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
        answersOutsiders: surface.config.groups?.answers_outsiders !== false,
        contactsAnswer: contactsAnswerOf(surface),
        originalContactsAnswer: contactsAnswerOf(surface),
    };
}

function contactsAnswerOf(surface: AgentSurfaceResponse): "off" | "known" | "anyone" {
    const answer = surface.config.contacts?.answer;
    return answer === "known" || answer === "anyone" ? answer : "off";
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
            /* Only where the bot can be in a group; elsewhere the section is
               left as it is. */
            ...(isGroupPlatform(platform) ? { groups: { answers_outsiders: draft.answersOutsiders } } : {}),
            /* Only when it changed: the API keeps who looks after contacts,
               and makes it whoever turned them on. */
            ...(contactsSupported(platform) && draft.contactsAnswer !== draft.originalContactsAnswer
                ? { contacts: { answer: draft.contactsAnswer as SurfaceContactsConfig["answer"] } }
                : {}),
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

/** Which pod connector a surface runs as, and how that account stands. */
export interface SurfaceConnectionView {
    podConnector: string;
    connectorId: string;
    /** The account's name at the provider. */
    label: string;
    /** Whose account it is, by name or email; "you" when it is yours. */
    owner: string;
    /** The owner has left the pod. Their token works until it expires. */
    ownerGone: boolean;
    /** CONNECTED, REAUTH_REQUIRED, DISCONNECTED or MISSING. */
    status: string;
}

export function readConnection(surface: Pick<AgentSurfaceResponse, "connection" | "pod_connector">): SurfaceConnectionView | null {
    const connection = surface.connection;
    const slug = connection?.pod_connector ?? surface.pod_connector ?? "";
    if (!slug) return null;
    const owner = connection?.connected_by;
    return {
        podConnector: slug,
        connectorId: connection?.connector_id ?? "",
        label: connection?.display_name ?? "",
        owner: owner ? (owner.is_you ? "you" : owner.name || owner.email || "") : "",
        ownerGone: owner ? owner.is_pod_member === false : false,
        status: String(connection?.status ?? "").toUpperCase(),
    };
}

/** One line for the account a surface runs as: "Runs as support-inbox ·
 *  support@acme.com · by Alice". */
export function connectionLine(view: SurfaceConnectionView): string {
    return ["Runs as " + view.podConnector, view.label, view.owner && "by " + view.owner].filter(Boolean).join(" · ");
}

/** Why a surface's connector cannot run, said for whoever can fix it, or
 *  empty when it can. `connector` is the pod connector it names, when the
 *  pod's list has it. */
export function connectionTrouble(view: SurfaceConnectionView, connector: { status: string } | undefined): string {
    if (!connector && view.status !== "CONNECTED") return view.podConnector + " is no longer one of this teammate's connectors.";
    if (connector?.status === "UNAVAILABLE") return "The installation behind " + view.podConnector + " is turned off for the organization.";
    if (connector?.status === "NEEDS_ACCOUNT" || view.status === "MISSING") return view.podConnector + " has no account shared with it right now.";
    if (view.status === "REAUTH_REQUIRED") return "The account behind " + view.podConnector + " needs " + (view.owner === "you" ? "you" : view.owner || "its owner") + " to sign in again.";
    if (view.status === "DISCONNECTED") return "The account behind " + view.podConnector + " was disconnected.";
    return "";
}

/** Whether to offer moving the surface to another connector: its own one
 *  runs as nobody, or names a connector that is gone. */
export function needsRebind(view: SurfaceConnectionView, connector: { status: string } | undefined): boolean {
    return !connector || connector.status === "NEEDS_ACCOUNT" || view.status === "MISSING";
}

/** The app a surface is on, as a catalog connector id: what its connection
 *  says, else the connector it names, else its platform's connector. Empty
 *  when none of them says — and then nothing is offered in its place. */
export function surfaceApp(view: SurfaceConnectionView, current: { connectorId: string } | undefined, platformConnector: string): string {
    return view.connectorId || current?.connectorId || platformConnector;
}

/** The SHARED connectors a surface could run as instead: on the surface's own
 *  app (`app`, from `surfaceApp`), ready, and not the one it has. A
 *  connector whose app this page cannot read is not offered — a Slack
 *  channel moved onto a Gmail account answers nobody. */
export function rebindChoices<T extends { slug: string; connectorId: string; mode: string; status: string }>(view: SurfaceConnectionView, connectors: T[], app: string): T[] {
    if (!app) return [];
    return connectors.filter((one) => one.mode === "SHARED" && one.status === "READY" && one.slug !== view.podConnector && one.connectorId === app);
}
