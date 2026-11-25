import type { LiveContext } from "../../context";
import type { McpSource } from "./source";
import { disconnectClient, fetchMcpUrl, loadConnections, setListening } from "@web/space/agent-access-model";

/** The web's own calls (`web/agent-access-model.ts`), through the signed-in
 *  fetch: it swaps the web's cookie for this app's bearer token on the
 *  API's own address. */
export function mcpLive(context: LiveContext): McpSource {
    const api = context.server.api;
    const fetcher = context.fetch as typeof fetch;
    return {
        endpoint: (podId) => fetchMcpUrl(api, podId, fetcher),
        grants: (podId) => loadConnections(api, podId, fetcher),
        disconnect: (grantId) => disconnectClient(api, grantId, fetcher),
        listen: (grantId, subscriptionId, on) => setListening(api, grantId, subscriptionId, on, fetcher),
    };
}
