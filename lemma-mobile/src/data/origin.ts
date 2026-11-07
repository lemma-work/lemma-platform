/** Where a conversation came from, read off its own `metadata` and `type`.
 *  A port of `lemma-frontend/src/thread/conversation-origin.ts`; keep the two
 *  in step until it moves into the SDK. */

export type OriginKind = "chat" | "channel" | "notification" | "schedule" | "workflow" | "resource" | "task" | "ask";

export interface ConversationOrigin {
    kind: OriginKind;
    label: string;
    platform?: string;
    runId?: string;
    outsiders?: boolean;
}

const PLATFORM: Record<string, string> = {
    SLACK: "Slack", TEAMS: "Teams", WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", RESEND: "Email", EMAIL: "Email",
};

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
    if (text(meta.audience) === "outsiders") {
        const group = text(meta.channel_name);
        return { kind: "channel", label: group ?? (named ? named + " group" : "A group"), platform, outsiders: true };
    }
    if (source === "agent_surfaces") {
        const kind = text(meta.conversation_kind);
        const channel = text(meta.channel_name);
        const where = channel ? " · #" + channel.replace(/^#/, "") : kind === "DM" ? " · direct message" : "";
        return { kind: "channel", label: (named ?? "Channel") + where, platform };
    }
    if (source === "notification") return { kind: "notification", label: "Notification" + (named ? " · " + named : ""), platform };
    if (source === "SCHEDULE") {
        const name = text(meta.schedule_name);
        const said = name ? name.replace(/[-_]+/g, " ").replace(/^./, (one) => one.toUpperCase()) : null;
        return { kind: "schedule", label: "Schedule" + (said ? " · " + said : "") };
    }
    if (source === "pod_ask") {
        const ask = meta.ask && typeof meta.ask === "object" ? (meta.ask as Record<string, unknown>) : {};
        return { kind: "ask", label: "From " + (text(ask.from_pod_name) ?? "another Lemma") };
    }
    if (source === "WORKFLOW_RUN") return { kind: "workflow", label: "Workflow run", runId: text(meta.workflow_run_id) ?? undefined };
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
