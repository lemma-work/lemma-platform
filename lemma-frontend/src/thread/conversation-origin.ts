/** Where a conversation came from, read off what the backend recorded when
 *  it made it. Everything here is in the conversation's own `metadata` and
 *  `type` — nothing is fetched to work it out:
 *
 *  - a channel message:   `source: "agent_surfaces"`, `surface_platform`,
 *                         `conversation_kind` (DM | CHANNEL | EMAIL), `channel_name`
 *  - a notification:      `source: "notification"`, `surface_platform`
 *  - a schedule firing:   `source: "SCHEDULE"`, `schedule_name`, `schedule_type`
 *  - a workflow's agent:  `source: "WORKFLOW_RUN"`, `workflow_run_id`
 *  - a doc/table's chat:  `lemma_resource: "file:/path"` (type PROJECT)
 *  - anything else TASK:  a run nobody typed into
 */
export type OriginKind = "chat" | "channel" | "notification" | "schedule" | "workflow" | "resource" | "task";

export interface ConversationOrigin {
    kind: OriginKind;
    /** In a person's words: "Slack · #launch", "Schedule · daily-pulse". */
    label: string;
    /** SLACK | TEAMS | WHATSAPP | TELEGRAM | RESEND, for the channel's logo. */
    platform?: string;
    /** The workflow run a workflow's agent step belongs to. */
    runId?: string;
}

const PLATFORM: Record<string, string> = {
    SLACK: "Slack", TEAMS: "Teams", WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", RESEND: "Email",
};

function text(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

export function originOf(metadata: Record<string, unknown> | null | undefined, type: string | null | undefined): ConversationOrigin {
    const meta = metadata ?? {};
    const source = text(meta.source);
    const platform = text(meta.surface_platform)?.toUpperCase();
    const platformName = platform ? PLATFORM[platform] ?? platform.charAt(0) + platform.slice(1).toLowerCase() : null;

    if (source === "agent_surfaces") {
        const kind = text(meta.conversation_kind);
        const channel = text(meta.channel_name);
        const where = channel ? " · #" + channel.replace(/^#/, "") : kind === "DM" ? " · direct message" : kind === "EMAIL" ? "" : "";
        return { kind: "channel", label: (platformName ?? "Channel") + where, platform };
    }
    if (source === "notification") {
        return { kind: "notification", label: "Notification" + (platformName ? " · " + platformName : ""), platform };
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
