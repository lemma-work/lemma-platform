import * as SecureStore from "expo-secure-store";

/** What the app keeps between launches, in the platform keystore: the
 *  session (per server, so switching to a dev stack does not sign out of the
 *  hosted one), which server, whether it is showing sample data, and a
 *  sign-in left open in the browser. */

const SERVER = "lemma.server";
const MODE = "lemma.mode";
const ORG = "lemma.org";
const SEEN = "lemma.home-seen";
const FOLDED = "lemma.folded";
const HANDOFF = "lemma.handoff";
const APPEARANCE = "lemma.appearance";
const LEMMAS_VIEW = "lemma.lemmas-view";

function sessionKey(serverId: string): string {
    /* SecureStore keys allow only [A-Za-z0-9._-]. */
    return "lemma.session." + serverId.replace(/[^A-Za-z0-9._-]/g, "_");
}

async function read(key: string): Promise<string | null> {
    try {
        return await SecureStore.getItemAsync(key);
    } catch {
        return null;
    }
}

async function write(key: string, value: string | null): Promise<void> {
    try {
        if (value === null) await SecureStore.deleteItemAsync(key);
        else await SecureStore.setItemAsync(key, value);
    } catch {
        /* Kept for this launch only. */
    }
}

export const store = {
    server: () => read(SERVER),
    setServer: (value: string) => write(SERVER, value),
    mode: async (): Promise<"live" | "sample" | null> => {
        const held = await read(MODE);
        return held === "live" || held === "sample" ? held : null;
    },
    setMode: (mode: "live" | "sample" | null) => write(MODE, mode),
    session: (serverId: string) => read(sessionKey(serverId)),
    setSession: (serverId: string, value: string | null) => write(sessionKey(serverId), value),
    org: () => read(ORG),
    setOrg: (id: string) => write(ORG, id),
    homeSeen: async (orgId: string): Promise<number | null> => {
        const held = await read(SEEN + "." + orgId.replace(/[^A-Za-z0-9._-]/g, "_"));
        const at = held ? Number(held) : Number.NaN;
        return Number.isFinite(at) ? at : null;
    },
    setHomeSeen: (orgId: string, at: number) => write(SEEN + "." + orgId.replace(/[^A-Za-z0-9._-]/g, "_"), String(at)),
    folded: async (): Promise<Map<string, boolean>> => {
        const held = await read(FOLDED);
        try {
            return new Map(held ? (Object.entries(JSON.parse(held)) as [string, boolean][]) : []);
        } catch {
            return new Map();
        }
    },
    setFolded: (chosen: ReadonlyMap<string, boolean>) => write(FOLDED, JSON.stringify(Object.fromEntries(chosen))),
    handoff: () => read(HANDOFF),
    setHandoff: (value: string | null) => write(HANDOFF, value),
    appearance: async (): Promise<"system" | "light" | "dark"> => {
        const held = await read(APPEARANCE);
        return held === "light" || held === "dark" ? held : "system";
    },
    setAppearance: (value: "system" | "light" | "dark") => write(APPEARANCE, value),
    lemmasView: async (): Promise<"grid" | "list"> => ((await read(LEMMAS_VIEW)) === "list" ? "list" : "grid"),
    setLemmasView: (value: "grid" | "list") => write(LEMMAS_VIEW, value),
};
