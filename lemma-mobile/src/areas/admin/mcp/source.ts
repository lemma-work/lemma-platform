import type { Grants } from "./model";

/** Using a Lemma from other AI apps over MCP: the address, the apps granted access (PARITY.md, F).
 *
 *  The web's `space/agent-access.tsx`, with its calls (`web/agent-access-model.ts`:
 *  `fetchMcpUrl`, `loadConnections`, `disconnectClient`, `setListening`).
 *  The live side is `live.ts`, the sample side `sample.ts`; screens reach it
 *  through `hooks.ts`. */
export interface McpSource {
    /** The Lemma's MCP address as the server states it, or null where this
     *  server does not offer MCP. A failed read throws: that is not MCP
     *  being off. */
    endpoint(podId: string): Promise<string | null>;
    /** The apps connected to the Lemma: everyone's for its admins, else the
     *  signed-in person's own. */
    grants(podId: string): Promise<Grants>;
    /** End a connection and every token it was given; the app has to ask
     *  again to come back. */
    disconnect(grantId: string): Promise<void>;
    /** Stop telling an app about an event (`on` false), or start again. The
     *  connection itself stays. */
    listen(grantId: string, subscriptionId: string, on: boolean): Promise<void>;
}
