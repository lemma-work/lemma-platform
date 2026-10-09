import { toolKey } from "./tool-name";

export type DisplayResourceType =
    | "BROWSER"
    | "FILE"
    | "TABLE"
    | "AGENT"
    | "FUNCTION"
    | "WORKFLOW"
    | "APP"
    | "SCHEDULE"
    | "WIDGET";

export interface DisplayResource {
    type: DisplayResourceType;
    name?: string;
    path?: string;
    publicUrl?: string;
    /** Inline HTML, for a widget the agent wrote rather than linked. */
    content?: string;
    query?: string;
    /** A live browser's address, which only the tool's result carries: the
     *  sandbox mints it when the call runs, and it expires. */
    liveUrl?: string;
    /** When that address stops working (ISO), so an old card stops offering it. */
    liveUntil?: string;
}

const TYPES = new Set<DisplayResourceType>([
    "BROWSER",
    "FILE",
    "TABLE",
    "AGENT",
    "FUNCTION",
    "WORKFLOW",
    "APP",
    "SCHEDULE",
    "WIDGET",
]);

function record(value: unknown): Record<string, unknown> {
    return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>) : {};
}

function str(value: unknown): string | undefined {
    return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

/** Through the one tool-name reading, so a third-party `display_resource`
 *  is not drawn as one of Lemma's resource cards. */
export function isDisplayResourceTool(toolName: unknown, metadata?: Record<string, unknown> | null): boolean {
    return toolKey(toolName, metadata) === "display_resource";
}

export function parseDisplayResource(args: unknown, result?: unknown): DisplayResource | null {
    const outer = record(args);
    /* Some callers nest everything under `request`. */
    const request = Object.keys(record(outer.request)).length > 0 ? record(outer.request) : outer;

    const raw = str(request.type)?.toUpperCase();
    if (!raw || !TYPES.has(raw as DisplayResourceType)) return null;

    return {
        type: raw as DisplayResourceType,
        name: str(request.name),
        path: str(request.path),
        publicUrl: str(request.public_url ?? request.publicUrl),
        content: str(request.content),
        query: str(request.query),
        liveUrl: raw === "BROWSER" ? str(record(result).url) : undefined,
        liveUntil: raw === "BROWSER" ? str(record(result).expires_at) : undefined,
    };
}

export function resourceLabel(resource: DisplayResource): string {
    if (resource.name) return resource.name;
    if (resource.type === "BROWSER") return "Live browser";
    if (resource.path) return resource.path.split("/").filter(Boolean).pop() ?? resource.path;
    return resource.type.toLowerCase();
}

/** Where this resource lives in the workspace, for the kinds a card links to
 *  rather than draws. The same `/t/{pod}/…` routes the backend gives Slack and
 *  WhatsApp (`display_resource_renderer.build_display_resource_url`), so a
 *  link means the same place wherever it is followed from. */
export function resourceHref(site: string, podId: string, resource: DisplayResource, now = Date.now()): string | null {
    const base = site + "/t/" + encodeURIComponent(podId);
    const name = resource.name ? encodeURIComponent(resource.name) : null;
    switch (resource.type) {
        case "BROWSER":
            /* Opened in a new tab from a link: only a web address, whatever
               the deployment's sandbox URL setting says. */
            if (!resource.liveUrl || liveEnded(resource, now)) return null;
            try {
                const protocol = new URL(resource.liveUrl).protocol;
                return protocol === "http:" || protocol === "https:" ? resource.liveUrl : null;
            } catch {
                return null;
            }
        case "FILE": {
            const segments = (resource.path ?? "").split("/").filter(Boolean).map(encodeURIComponent);
            return segments.length ? base + "/file/" + segments.join("/") : base + "/files";
        }
        case "TABLE":
            return name ? base + "/table/" + name : base + "/tables";
        case "AGENT":
            return name ? base + "/profile/" + name : base + "/about";
        case "WORKFLOW":
            return name ? base + "/workflow/" + name : base + "/workflows";
        case "SCHEDULE":
            return base + "/about?section=schedules";
        case "FUNCTION":
        case "APP":
        case "WIDGET":
            return null;
    }
}

/** Whether a live browser's address has expired. One with no expiry given is
 *  taken at its word. */
export function liveEnded(resource: DisplayResource, now = Date.now()): boolean {
    if (resource.type !== "BROWSER" || !resource.liveUntil) return false;
    const until = Date.parse(resource.liveUntil);
    return Number.isFinite(until) && until <= now;
}
