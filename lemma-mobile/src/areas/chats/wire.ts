import type { LemmaClient, MentionPart } from "lemma-sdk";
import { items, record, words } from "../../data/wire";
import { displayAgentName, isPodDefaultAgent } from "@web/data/agent-names";
import type { AgentRef, ChannelChange, MineChange, PostBody } from "./source";
import type { Surface } from "@web/data/types";

/** What the chats area sends the API and reads back from it that the web's
 *  copied modules do not cover: the bodies of a post, a change, one's own
 *  settings, and the Lemma's agents, people and bots as the web reads them.
 *  Pure, for the tests. */

type Channels = LemmaClient["channels"];

export function must<T>(value: T | null, what: string): T {
    if (value === null) throw new Error("The " + what + " came back without an id.");
    return value;
}

/** The post as the API takes it: mentions and files as typed parts (the
 *  web's `postRequest`). */
export function postRequest(body: PostBody): Parameters<Channels["post"]>[2] {
    return {
        text: body.text,
        members_only: body.membersOnly ?? false,
        source_id: body.sourceId ?? null,
        thread_root_id: body.threadRootId ?? null,
        parts: [
            ...(body.mentions ?? []).map((mention) => ({ type: "mention", id: mention.id, kind: mention.kind as MentionPart.kind })),
            ...(body.files ?? []).map((file) => ({
                type: "file", path: file.path, name: file.name,
                ...(file.mimeType ? { mime_type: file.mimeType } : {}),
                ...(file.size !== null ? { size: file.size } : {}),
            })),
        ],
        client_message_id: body.clientMessageId ?? null,
    } as Parameters<Channels["post"]>[2];
}

/** A channel change as the API takes it: only the fields present (the web's
 *  `updateRequest`). */
export function updateRequest(change: ChannelChange): Record<string, unknown> {
    const body: Record<string, unknown> = {};
    if (change.title !== undefined) body.title = change.title;
    if (change.topic !== undefined) body.topic = change.topic;
    if (change.archived !== undefined) body.archived = change.archived;
    if (change.visibility !== undefined) body.visibility = change.visibility;
    if (change.answersShared !== undefined) body.answers_shared = change.answersShared;
    if (change.agentsAnswer !== undefined) body.agents_answer = change.agentsAnswer;
    if (change.handBackAfterHours !== undefined) body.hand_back_after_hours = change.handBackAfterHours;
    if (change.assignee !== undefined) body.assignee = change.assignee;
    if (change.status !== undefined) body.status = change.status;
    return body;
}

/** A pod's roles as the roster says them (the web's `readPodRoles`). */
export function roleOf(roles: unknown): string {
    const held = Array.isArray(roles) ? roles.filter((one): one is string => typeof one === "string") : [];
    const known = [["POD_ADMIN", "Admin"], ["POD_EDITOR", "Editor"], ["POD_USER", "User"], ["POD_VIEWER", "Viewer"]].find(([value]) => held.includes(value));
    if (known) return known[1];
    const custom = held[0]?.replace(/^POD_/, "").replaceAll("_", " ").toLowerCase();
    return custom ? custom.charAt(0).toUpperCase() + custom.slice(1) : "Member";
}

/** The Lemma's agents, the Lemma itself first and named as it is: the agent
 *  behind it answers under its name (the web's `agentRows` + `answeringAs`). */
export function readAgents(listed: unknown, lemmaName: string | null): AgentRef[] {
    return items(listed).flatMap((raw): AgentRef[] => {
        const row = record(raw);
        const name = words(row.name);
        const id = words(row.id);
        if (!name || !id) return [];
        const kind = words(row.kind);
        const front = isPodDefaultAgent(name, kind);
        const description = words(row.description) ?? words(row.instruction) ?? "";
        const blurb = description.split(/(?<=[.!?])\s/)[0] ?? "";
        return [{ id, name, label: front && lemmaName ? lemmaName : displayAgentName(name, kind), front, blurb }];
    }).sort((a, b) => Number(b.front) - Number(a.front) || a.label.localeCompare(b.label));
}

/** The Lemma's bots, as the web lists them (`listSurfaces`). */
export function readSurfaces(listed: unknown, lemmaName: string | null): Surface[] {
    return items(listed).flatMap((raw): Surface[] => {
        const surface = record(raw);
        const id = words(surface.id);
        const platform = words(surface.platform);
        if (!id || !platform) return [];
        const mine = surface.uses_default_agent === true || isPodDefaultAgent(words(surface.agent_name));
        const reach = record(surface.reach);
        const status = words(surface.status) ?? "ACTIVE";
        return [{
            id,
            platform,
            name: words(surface.name) ?? platform,
            mine,
            agentName: mine && lemmaName ? lemmaName : displayAgentName(words(surface.agent_name) ?? ""),
            agentKey: words(surface.agent_name) ?? undefined,
            handle: words(reach.handle) ?? words(surface.surface_identity_username) ?? words(surface.surface_identity_email) ?? "",
            email: words(reach.email) ?? words(surface.surface_identity_email) ?? undefined,
            active: status === "ACTIVE",
            status,
        }];
    });
}

/** One's own settings in a chat as the API takes them: only the fields
 *  present (the web's `setMine`). */
export function mineRequest(change: MineChange): Record<string, unknown> {
    const body: Record<string, unknown> = {};
    if (change.notify !== undefined) body.notify = change.notify;
    if (change.notifyVia !== undefined) body.notify_via = change.notifyVia;
    if (change.mutedUntil !== undefined) body.muted_until = change.mutedUntil;
    return body;
}
