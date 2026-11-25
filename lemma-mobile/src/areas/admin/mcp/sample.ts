import type { ConnectedClient } from "@web/space/agent-access-model";
import type { McpSource } from "./source";

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function ago(ms: number): string {
    return new Date(Date.now() - ms).toISOString();
}

/** Support has three apps connected: Ada's Claude, Sam's ChatGPT, and an
 *  app Lena connected that named itself and is told about new tickets. The
 *  other sample Lemmas have none. Addresses are on `.example`, never real. */
function seed(): Map<string, ConnectedClient[]> {
    return new Map([
        ["sample-support-1140", [
            { grant_id: "grant-claude", user_id: "me", client_id: "https://claude.example/oauth/metadata.json", client_name: "Claude", scopes: ["pod:read", "pod:write"], connected_at: ago(12 * DAY), last_used_at: ago(2 * HOUR), listens_to: [] },
            { grant_id: "grant-chatgpt", user_id: "user-sam", client_id: "https://chatgpt.example/oauth/metadata.json", client_name: "ChatGPT", scopes: ["pod:read"], connected_at: ago(3 * DAY), last_used_at: null, listens_to: [] },
            {
                grant_id: "grant-notes", user_id: "user-lena", client_id: "client-7f3a", client_name: "Ticket notes", scopes: ["pod:read", "pod:events"], connected_at: ago(20 * DAY), last_used_at: ago(20 * MINUTE),
                listens_to: [
                    { id: "sub-tickets", name: "record.created", arguments: { table: "tickets" }, last_delivery_at: ago(20 * MINUTE), last_error: null, stopped_at: null, paused_at: null },
                    { id: "sub-refunds", name: "record.created", arguments: { table: "refunds" }, last_delivery_at: ago(4 * DAY), last_error: null, stopped_at: ago(2 * DAY), paused_at: null },
                ],
            },
        ]],
    ]);
}

export function mcpSample(): McpSource {
    const grants = seed();
    const find = (grantId: string) => [...grants.values()].flat().find((one) => one.grant_id === grantId);
    return {
        endpoint: async (podId) => `https://api.acme.example/mcp/${podId}`,
        grants: async (podId) => ({ items: grants.get(podId) ?? [], everyone: true }),
        disconnect: async (grantId) => {
            for (const [podId, list] of grants) grants.set(podId, list.filter((one) => one.grant_id !== grantId));
        },
        listen: async (grantId, subscriptionId, on) => {
            const subscription = find(grantId)?.listens_to?.find((one) => one.id === subscriptionId);
            if (!subscription) throw new Error("That subscription is gone, or is not yours to change.");
            subscription.stopped_at = on ? null : new Date().toISOString();
        },
    };
}
