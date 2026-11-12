import type { LemmaClient } from "lemma-sdk";
import { authedFetch } from "../session/auth";
import { lemma } from "../session/client";
import type { Server } from "../session/server";

/** What an area's live side is handed: the server, the SDK client (one per
 *  Lemma through `client(podId)`), and the signed-in fetch for what the SDK
 *  cannot do on a phone, such as reading bytes or uploading (`data/io.ts`). */
export interface LiveContext {
    server: Server;
    client: (podId?: string) => LemmaClient;
    fetch: typeof authedFetch;
}

export function liveContext(server: Server): LiveContext {
    return { server, client: (podId) => lemma(server, podId), fetch: authedFetch };
}
