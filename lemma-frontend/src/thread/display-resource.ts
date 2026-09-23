export type DisplayResourceType =
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
}

const TYPES = new Set<DisplayResourceType>([
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

/** An agent may namespace the tool, so `mcp_display_resource` counts too. */
export function isDisplayResourceTool(toolName: unknown): boolean {
    if (typeof toolName !== "string") return false;
    const normalized = toolName.toLowerCase().replace(/[.:\-\s]/g, "_").replace(/^.*__/, "");
    return normalized === "display_resource" || normalized === "mcp_display_resource";
}

export function parseDisplayResource(args: unknown): DisplayResource | null {
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
    };
}

export function resourceLabel(resource: DisplayResource): string {
    if (resource.name) return resource.name;
    if (resource.path) return resource.path.split("/").filter(Boolean).pop() ?? resource.path;
    return resource.type.toLowerCase();
}

/** Where this resource lives in the platform, for the kinds this app does not
 *  render itself. */
export function resourceHref(site: string, podId: string, resource: DisplayResource): string | null {
    const base = site + "/pod/" + encodeURIComponent(podId);
    const name = resource.name ? encodeURIComponent(resource.name) : null;
    switch (resource.type) {
        case "FILE":
            return resource.path ? base + "/files?path=" + encodeURIComponent(resource.path) : base + "/files";
        case "TABLE":
            return name ? base + "/data/" + name : base + "/data";
        case "AGENT":
            return name ? base + "/agents/" + name : base + "/agents";
        case "FUNCTION":
            return name ? base + "/functions/" + name : base + "/functions";
        case "WORKFLOW":
            return name ? base + "/flows/" + name : base + "/flows";
        case "SCHEDULE":
            return base + "/schedules";
        case "APP":
        case "WIDGET":
            return null;
    }
}
