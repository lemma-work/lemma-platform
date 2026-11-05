import type { SettingsSection } from "@/settings/settings-modal";
export const SETTINGS: readonly SettingsSection[] = [
    "account",
    "appearance",
    "usage",
    "plan",
    "people",
    "connectors",
    "models",
    "org-usage",
    "team-billing",
    "this-mac",
    "this-mac-setup",
    "this-mac-agents",
    "this-mac-sharing",
    "this-mac-updates",
    "this-mac-advanced",
];
export function settingsFromQuery(
    value: string | null,
): SettingsSection | null {
    return SETTINGS.find((s) => s === value) ?? null;
}
export function legacyAddress(
    path: string[],
    query: URLSearchParams,
): string | null {
    const [root, id, area, item] = path;
    const e = encodeURIComponent;
    const setting = (name: string, org?: string) =>
        "/t?settings=" + name + (org ? "&org=" + e(org) : "");
    if (root === "profile")
        return setting(
            id === "billing" ? "plan" : id === "usage" ? "usage" : "account",
        );
    if (root === "connectors") return setting("connectors");
    if (root === "organizations")
        return id === "new"
            ? "/organizations/new"
            : setting(
                  (
                      {
                          members: "people",
                          billing: "team-billing",
                          usage: "org-usage",
                          "agent-runtimes": "models",
                      } as Record<string, string>
                  )[item] || "people",
                  id,
              );
    /* Where the server sends a person to fix a connector -- every typed
       refusal's `connect_url` is `/pods/{pod}/connections?connector={slug}`.
       It is the teammate's Connections part, opened on that connector. */
    if (root === "pods" && id && area === "connections" && !item) {
        const slug = query.get("connector");
        return "/t/" + e(id) + "/about/connections" + (slug ? "?connector=" + e(slug) : "");
    }
    if (root === "pod" && id) {
        const base = "/t/" + e(id);
        switch (area) {
            case undefined:
                return base;
            case "conversations":
                return base + "/conversation" + (item ? "/" + e(item) : "");
            case "agents":
            case "assistants":
                return (
                    base +
                    "/profile" +
                    (item && item !== "new" ? "/" + e(item) : "")
                );
            case "data":
                return query.get("tab")
                    ? base + "/table/" + e(query.get("tab")!)
                    : base + "/library";
            case "files":
                return query.get("file")
                    ? base +
                          "/file/" +
                          query
                              .get("file")!
                              .split("/")
                              .filter(Boolean)
                              .map(e)
                              .join("/")
                    : base + "/library";
            case "app":
                return query.get("page")
                    ? base + "/app/" + e(query.get("page")!)
                    : base + "/library";
            case "computer":
                return base + "/computer";
            case "ai":
                return base + "/conversation";
            case "settings":
                return item === "usage"
                    ? setting("org-usage")
                    : item === "models"
                      ? setting("models")
                      : base +
                        "/profile?section=" +
                        (item === "members"
                            ? "people%20with%20access"
                            : "about");
            case "flows":
                return base + "/profile?section=workflows";
            case "functions":
                return base + "/profile?section=what%20it%20can%20reach%20for";
            case "schedules":
                return base + "/profile?section=schedules";
            /* Notifications became the channels' inbox. */
            case "notifications":
                return base + "/channels";
            case "surfaces":
            case "channels":
                return base + "/conversation?reach=1";
            case "connectors":
                return base + "/about/connections";
            default:
                return base + "/profile";
        }
    }
    return null;
}
