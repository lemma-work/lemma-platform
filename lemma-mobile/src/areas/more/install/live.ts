import * as SecureStore from "expo-secure-store";
import type { LiveContext } from "../../context";
import { key } from "../../../web-shims/session-storage";
import { heldStore } from "./model";
import type { InstallSource, Readme } from "./source";
import type { ImportStatusResponse } from "@web/site/import-types";
import { forgetInstall, pendingInstall, rememberInstall } from "@web/site/import/pending-install";

const README_TIMEOUT_MS = 6000;

/** A public README, read the way anybody could read it: plain `fetch`, no
 *  credentials (the app's fetch signs only its own API's calls). */
async function publicReadme(owner: string, repo: string): Promise<Readme | null> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), README_TIMEOUT_MS);
    try {
        const response = await fetch(`https://raw.githubusercontent.com/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}/HEAD/README.md`, { signal: controller.signal });
        if (!response.ok) return null;
        const markdown = await response.text();
        return markdown.trim() ? { markdown, branch: "HEAD" } : null;
    } catch {
        return null;
    } finally {
        clearTimeout(timer);
    }
}

const PENDING = key("install-pending");

/** Writes to the keystore one after another, so a forget straight after a
 *  remember lands in that order. */
let writes: Promise<void> = Promise.resolve();

function persist(name: string, value: string | null): void {
    writes = writes.then(async () => {
        try {
            if (value === null) await SecureStore.deleteItemAsync(name);
            else await SecureStore.setItemAsync(name, value);
        } catch {
            /* Kept for this launch only. */
        }
    });
}

/** The keystore's pending install, as a store the web's rules can read at once. */
async function pendingStore() {
    await writes;
    let raw: string | null = null;
    try {
        raw = await SecureStore.getItemAsync(PENDING);
    } catch {
        raw = null;
    }
    return heldStore(raw ? { [PENDING]: raw } : {}, persist);
}

function record(raw: unknown): Record<string, unknown> {
    return raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {};
}

function words(value: unknown): string | null {
    return typeof value === "string" && value.trim() ? value.trim() : null;
}

/** Templates, installing from GitHub and remixing, from the server: the
 *  same calls the web's install panel makes. */
export function installLive(context: LiveContext): InstallSource {
    const imports = (podId: string) => "/pods/" + encodeURIComponent(podId) + "/bundle/imports";
    const job = (podId: string, importId: string) => imports(podId) + "/" + encodeURIComponent(importId);
    return {
        readme: publicReadme,

        async createOrg(name, derived) {
            const made = record(await context.client().organizations.create({ name, resolve_name_conflicts: derived }));
            const id = words(made.id);
            if (!id) throw new Error("The organization was made but came back without an id.");
            return { id, name: words(made.name) ?? name };
        },

        async createLemma(orgId, name) {
            const pod = record(await context.client().pods.create({ name, organization_id: orgId }));
            const id = words(pod.id);
            if (!id) throw new Error("The Lemma was made but came back without an id.");
            return { id, name: words(pod.name) ?? name, iconUrl: words(pod.icon_url), description: words(pod.description) };
        },

        startImport: (podId, input) => context.client(podId).request<ImportStatusResponse>("POST", imports(podId), {
            body: { kind: "GITHUB", owner: input.owner, repo: input.repo },
        }),
        readImport: (podId, importId) => context.client(podId).request<ImportStatusResponse>("GET", job(podId, importId)),
        applyImport: (podId, importId, input) => context.client(podId).request<ImportStatusResponse>("POST", job(podId, importId) + "/apply", {
            body: { variables: input.variables, connectors: input.connectors, confirm_destructive: input.confirmDestructive },
        }),
        async cancelImport(podId, importId) {
            await context.client(podId).request("DELETE", job(podId, importId));
        },

        pending: async () => pendingInstall(await pendingStore()),
        async rememberPending(install) {
            rememberInstall(await pendingStore(), install);
            await writes;
        },
        async forgetPending() {
            forgetInstall(await pendingStore());
            await writes;
        },
    };
}
