import * as FileSystem from "expo-file-system/legacy";
import { freshAccess } from "../../../session/auth";
import type { LiveContext } from "../../context";
import type { ComputerSource, PendingSignIn, SignInOutcome, WebLogin } from "./source";
import type { BrowserState, Listing } from "@web/computer/machine";
import type { WorkspaceStatus } from "@web/computer/startup";

/** The computer, from the server: the web's own calls (`computer/queries.ts`),
 *  path for path. The person's own routes, keyed by the session rather than a
 *  Lemma, so every call goes through the plain client.
 *
 *  A file's bytes are read through the signed-in fetch (text) or downloaded
 *  by the platform with the token in a native header (an image, a save), as
 *  `data/io.ts` does: the SDK's byte reads answer a Blob, which React Native
 *  cannot always read. */
export function computerLive(context: LiveContext): ComputerSource {
    const api = context.server.api;
    const client = () => context.client();
    const contentUrl = (path: string, length: number) => `${api}/workspace/files:content?path=${encodeURIComponent(path)}&length=${length}`;

    return {
        machine: true,

        status: () => client().workspace.status() as Promise<WorkspaceStatus>,

        files: (path, wake, after) => client().request<Listing>("GET", "/workspace/files", {
            params: { path: path ?? undefined, wake: wake || undefined, after },
        }),

        async browser() {
            const found = await client().request<{ state?: string; detail?: string | null }>("GET", "/workspace/browser/status");
            return (found?.state ?? "unavailable") as BrowserState;
        },

        async chatFolder(podId, conversationId) {
            const found = await context.client(podId).conversations.get(conversationId, { pod_id: podId });
            const cwd = (found as { workspace_cwd?: unknown } | null)?.workspace_cwd;
            return typeof cwd === "string" && cwd.startsWith("/") ? cwd : null;
        },

        stat: (path) => client().request<{ size_bytes: number }>("GET", "/workspace/files:stat", { params: { path } }),

        async text(path, limit) {
            const response = await context.fetch(contentUrl(path, limit), { headers: { Accept: "*/*" } });
            if (!response.ok) throw await failureOf(response, "This file couldn’t be read.");
            const buffer = await response.arrayBuffer();
            return { text: new TextDecoder().decode(buffer), bytes: buffer.byteLength };
        },

        async download(path, limit) {
            const access = await freshAccess();
            if (!access) throw new Error("You’re signed out. Sign in again to read this file.");
            const name = path.split("/").pop() || "file";
            const result = await FileSystem.downloadAsync(contentUrl(path, limit), cachePath(name), {
                headers: { Authorization: `Bearer ${access}`, Accept: "*/*" },
            });
            if (result.status < 200 || result.status >= 300) {
                void FileSystem.deleteAsync(result.uri, { idempotent: true }).catch(() => undefined);
                throw new Error(result.status === 404 ? `${name} isn’t there any more.` : `${name} couldn’t be downloaded (${result.status}).`);
            }
            const info = await FileSystem.getInfoAsync(result.uri);
            return { uri: result.uri, bytes: info.exists ? info.size : 0 };
        },

        browserAccess: () => client().request<{ url: string; expires_at: string }>("POST", "/workspace/apps/browser/access", { body: { ttl_seconds: 1800 } }),

        async logins(wake) {
            const found = await client().request<{ items?: WebLogin[]; sleeping?: boolean }>("GET", "/web-logins", { params: { wake: wake || undefined } });
            return { items: found?.items ?? [], sleeping: found?.sleeping === true };
        },

        signOutOf: (origin) => client().request<{ site: string; forgotten: boolean }>("DELETE", "/web-logins", { params: { origin } }),

        signIn: (conversationId, toolCallId) => client().request<PendingSignIn>(
            "GET",
            "/web-logins/sign-ins/" + encodeURIComponent(conversationId) + "/" + encodeURIComponent(toolCallId),
        ),

        answerSignIn: (conversationId, toolCallId, signedIn) => client().request<SignInOutcome>(
            "POST",
            "/web-logins/sign-ins/" + encodeURIComponent(conversationId) + "/" + encodeURIComponent(toolCallId) + "/answer",
            { body: { signed_in: signedIn } },
        ),

        async currentPage(origin) {
            const found = await client().request<{ url: string | null }>("GET", "/workspace/browser/current-page-url", { params: { origin } });
            return found?.url ?? null;
        },

        fitDisplay: (size) => client().request<{ size: string | null }>("POST", "/workspace/browser/display-size", { body: size }),
    };
}

/** Somewhere in the cache for a file of this name, safe on every platform. */
function cachePath(name: string): string {
    const safe = name.replace(/[^\w.\- ]+/g, "_").slice(-120) || "file";
    return `${FileSystem.cacheDirectory}computer-${Date.now()}-${safe}`;
}

/** A failed read, in the server's words when it sent some. */
async function failureOf(response: Response, fallback: string): Promise<Error> {
    let detail = "";
    try {
        const body = (await response.json()) as { detail?: unknown };
        if (typeof body?.detail === "string") detail = body.detail;
    } catch {
        /* Not JSON: the fallback says it. */
    }
    return Object.assign(new Error(detail || fallback), { statusCode: response.status });
}
