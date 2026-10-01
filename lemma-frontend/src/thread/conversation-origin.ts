/** Where a conversation came from, read off what the backend recorded when
 *  it made it. Everything here is in the conversation's own `metadata` and
 *  `type` — nothing is fetched to work it out:
 *
 *  - a channel message:   `source: "agent_surfaces"`, `surface_platform`,
 *                         `conversation_kind` (DM | CHANNEL | EMAIL), `channel_name`
 *  - outsiders in a group: the same, plus `audience: "outsiders"` — one
 *                         conversation per group, where people outside the
 *                         space ask and its owner reads along
 *  - a notification:      `source: "notification"`, `surface_platform`
 *  - a schedule firing:   `source: "SCHEDULE"`, `schedule_name`, `schedule_type`
 *  - a workflow's agent:  `source: "WORKFLOW_RUN"`, `workflow_run_id`
 *  - a doc/table's chat:  `lemma_resource: "file:/path"` (type PROJECT)
 *  - anything else TASK:  a run nobody typed into
 */
export type OriginKind = "chat" | "channel" | "notification" | "schedule" | "workflow" | "resource" | "task";

export interface ConversationOrigin {
    kind: OriginKind;
    /** In a person's words: "Slack · #launch", "Schedule · daily-pulse". For
     *  outsiders, the group it came from: "Design partners". */
    label: string;
    /** SLACK | TEAMS | WHATSAPP | TELEGRAM | RESEND, for the channel's logo. */
    platform?: string;
    /** The workflow run a workflow's agent step belongs to. */
    runId?: string;
    /** People outside the space asked this, in a group that answers them. */
    outsiders?: boolean;
}

const PLATFORM: Record<string, string> = {
    SLACK: "Slack", TEAMS: "Teams", WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", RESEND: "Email",
};

/** "Telegram" for TELEGRAM; a platform nobody named here keeps its own word. */
export function platformName(platform: string): string {
    const key = platform.toUpperCase();
    return PLATFORM[key] ?? key.charAt(0) + key.slice(1).toLowerCase();
}

function text(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function originOf(metadata: Record<string, unknown> | null | undefined, type: string | null | undefined): ConversationOrigin {
    const meta = metadata ?? {};
    const source = text(meta.source);
    const platform = text(meta.surface_platform)?.toUpperCase();
    const named = platform ? platformName(platform) : null;

    /* Before the channel rule, which would call it "Telegram · #Design
       partners": the group is a name, not a #channel, and whose questions
       these are is the thing that sets the conversation apart. */
    if (text(meta.audience) === "outsiders") {
        const group = text(meta.channel_name);
        return { kind: "channel", label: group ?? (named ? named + " group" : "A group"), platform, outsiders: true };
    }
    if (source === "agent_surfaces") {
        const kind = text(meta.conversation_kind);
        const channel = text(meta.channel_name);
        const where = channel ? " · #" + channel.replace(/^#/, "") : kind === "DM" ? " · direct message" : kind === "EMAIL" ? "" : "";
        return { kind: "channel", label: (named ?? "Channel") + where, platform };
    }
    if (source === "notification") {
        return { kind: "notification", label: "Notification" + (named ? " · " + named : ""), platform };
    }
    if (source === "SCHEDULE") {
        const name = text(meta.schedule_name);
        /* The server keeps schedule names as slugs; a slug is not a label. */
        const said = name ? name.replace(/[-_]+/g, " ").replace(/^./, (one) => one.toUpperCase()) : null;
        return { kind: "schedule", label: "Schedule" + (said ? " · " + said : "") };
    }
    if (source === "WORKFLOW_RUN") {
        return { kind: "workflow", label: "Workflow run", runId: text(meta.workflow_run_id) ?? undefined };
    }
    const bound = text(meta.lemma_resource);
    if (bound) {
        const colon = bound.indexOf(":");
        const kind = colon > 0 ? bound.slice(0, colon) : "resource";
        const name = colon > 0 ? bound.slice(colon + 1) : bound;
        const short = name.split("/").filter(Boolean).pop() ?? name;
        const noun = kind === "file" ? "Doc" : kind.charAt(0).toUpperCase() + kind.slice(1);
        return { kind: "resource", label: noun + " · " + short };
    }
    if ((type ?? "").toUpperCase() === "TASK") return { kind: "task", label: "Task" };
    return { kind: "chat", label: "Chat" };
}

/** What the "Came from" column says. The space's name is the page's to give,
 *  so outsiders are named here rather than when the list is read. */
export function originLabel(origin: ConversationOrigin, space: string): string {
    return origin.outsiders ? "People outside " + space + " · " + origin.label : origin.label;
}
