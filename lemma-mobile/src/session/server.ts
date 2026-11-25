import { Platform } from "react-native";

/** Which Lemma the app talks to: lemma.work, a server the build names, or a
 *  custom address -- another deployment, or a dev stack on this machine.
 *  Each server keeps its own session, so switching does not sign out of the
 *  one left. */

export interface Server {
    id: string;
    label: string;
    /** The API, e.g. https://api.lemma.work */
    api: string;
    /** The web app, where people sign in: https://lemma.work */
    web: string;
}

/** `id` stays "hosted": a session saved before there was a choice is kept. */
export const HOSTED: Server = { id: "hosted", label: "lemma.work", api: "https://api.lemma.work", web: "https://lemma.work" };

/** The servers a build names in `EXPO_PUBLIC_LEMMA_SERVERS`: a JSON list of
 *  `{ id, label, api, web }`, set in a git-ignored `.env.local` (README,
 *  "Which server"). One that will not read is left out. */
export function serversFrom(raw: string | undefined): Server[] {
    if (!raw) return [];
    let list: unknown;
    try {
        list = JSON.parse(raw);
    } catch {
        return [];
    }
    if (!Array.isArray(list)) return [];
    return list.flatMap((one: Partial<Server> | null): Server[] => {
        if (!one || typeof one.id !== "string" || typeof one.api !== "string" || typeof one.web !== "string" || one.id === HOSTED.id) return [];
        const api = one.api.replace(/\/+$/, "");
        return [{ id: one.id, label: typeof one.label === "string" ? one.label : api.replace(/^https?:\/\//, ""), api, web: one.web.replace(/\/+$/, "") }];
    });
}

/** The servers offered by name, in the order the switcher lists them. */
export const PRESETS: readonly Server[] = [HOSTED, ...serversFrom(process.env.EXPO_PUBLIC_LEMMA_SERVERS)];

export function serverFrom(raw: string | null): Server {
    if (!raw) return HOSTED;
    try {
        const held = JSON.parse(raw) as Partial<Server>;
        const preset = PRESETS.find((one) => one.id === held.id);
        if (preset) return preset;
        if (typeof held.api === "string" && typeof held.web === "string" && typeof held.id === "string") {
            return { id: held.id, label: held.label ?? held.api, api: held.api.replace(/\/+$/, ""), web: held.web.replace(/\/+$/, "") };
        }
    } catch {
        /* A server that will not read is the hosted one. */
    }
    return HOSTED;
}

/** The machine running the emulator or simulator, as the device sees it:
 *  10.0.2.2 from the Android emulator, localhost from the iOS simulator. */
export const THIS_COMPUTER = Platform.OS === "android" ? "10.0.2.2" : "localhost";

const LOCAL = /^(localhost|127\.\d+\.\d+\.\d+|10\.\d+\.\d+\.\d+|192\.168\.\d+\.\d+|172\.(1[6-9]|2\d|3[01])\.\d+\.\d+|[^.]+\.local|[^.]+\.localhost|\[::1\])$/i;

/** An origin from what a person typed: "8000" (a port on this computer),
 *  "api.example.com", "localhost:8761" or a whole URL. A local address
 *  defaults to http, anything else to https. Null for anything that is not
 *  an address. */
export function addressFrom(typed: string): string | null {
    const said = typed.trim().replace(/\/+$/, "");
    if (!said) return null;
    if (/^\d{2,5}$/.test(said)) return `http://${THIS_COMPUTER}:${said}`;
    const match = /^(?:(https?):\/\/)?([^/\s:]+|\[[^\]]+\])(?::(\d{1,5}))?(?:\/.*)?$/i.exec(said);
    if (!match) return null;
    const [, scheme, host, port] = match;
    if (!/^[a-z0-9.[\]:-]+$/i.test(host) || (!host.includes(".") && !LOCAL.test(host))) return null;
    const protocol = scheme ? scheme.toLowerCase() : LOCAL.test(host) ? "http" : "https";
    return `${protocol}://${host.toLowerCase()}${port ? `:${port}` : ""}`;
}

/** A custom server from its API address and, optionally, its web app's. A
 *  hosted API at `api.example.com` has its web app at `example.com`, so the
 *  web address can be left out for one. */
export function customServer(apiTyped: string, webTyped: string): Server | null {
    const api = addressFrom(apiTyped);
    if (!api) return null;
    let web = webTyped.trim() ? addressFrom(webTyped) : null;
    if (!web) {
        const derived = /^(https?:\/\/)api\.(.+)$/.exec(api);
        if (!derived) return null;
        web = derived[1] + derived[2];
    }
    const preset = PRESETS.find((one) => one.api === api && one.web === web);
    if (preset) return preset;
    return { id: `custom:${api}|${web}`, label: api.replace(/^https?:\/\//, ""), api, web };
}
