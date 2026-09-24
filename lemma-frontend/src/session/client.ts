import { LemmaClient } from "lemma-sdk";
import { configuredApiUrl, requireApiUrl } from "./origins";
import { isLandingPreview } from "@/marketing/preview-mode";
import { siteRuntime } from "@/site/runtime";
import { isLocalDeployment } from "@/site/config";

/** One place a client is made, and one place the API origin is decided.
 *
 *  Auth works two ways, and which one you get depends on where this is served.
 *  Served from a host that shares the API's cookie domain — `pod.example.com`
 *  beside `api.example.com` — the session cookie arrives on its own and
 *  nothing below is needed. Served from `localhost`, that cookie is cross-site
 *  and never sent, so the SDK's bearer fallback is used instead: a token you
 *  paste in, kept in this browser only.
 *
 *  `client: "lemma-app"` names this app so its traffic is distinguishable from
 *  the web workspace later. Unnamed, everything lands under the generic SDK
 *  origin and "how much of this was a person?" stops being answerable. */

const API_KEY = "lemma_api_url";
const TOKEN_KEY = "lemma_token"; // the SDK's own bearer-token slot

let base: LemmaClient | null = null;
const scoped = new Map<string, LemmaClient>();

function read(key: string): string | null {
    if (isLandingPreview()) return null;
    try {
        return localStorage.getItem(key);
    } catch {
        return null;
    }
}

/** The origin this browser talks to: what `/connect` was pointed at, or what
 *  the deployment was built with. Refuses rather than inventing one — see
 *  `origins.ts` for why a default would be worse than an error. */
export function apiUrl(): string {
    if (isLandingPreview()) throw new Error("The product tour uses local sample data.");
    return read(API_KEY) ?? requireApiUrl();
}

/** Whether `apiUrl()` has an answer. The one question worth asking before
 *  building a client, because the alternative is a thrown render. */
export function hasApiUrl(): boolean {
    if (isLandingPreview()) return false;
    return Boolean(read(API_KEY) ?? configuredApiUrl());
}

/** The same origin, for the places that only display it — and empty rather
 *  than a refusal when there is none.
 *
 *  `/connect` is the screen somebody reaches *because* nothing is configured,
 *  so it is the one caller that must never be handed a throw: prefilling its
 *  input from `apiUrl()` crashes the render of the only way out. */
export function upstreamUrl(): string {
    return hasApiUrl() ? apiUrl() : "";
}

/** Blank is unset.
 *
 *  `.env.example` ships these as empty keys so the file lists what exists, and
 *  `??` does not catch an empty string — so an untouched copy of it would
 *  otherwise make `authUrl()` return `""`, which reaches `new URL("")` and a
 *  sign-in button that throws instead of going anywhere. */
function configuredAuthUrl(): string {
    return siteRuntime().authUrl.trim();
}

/** Where the platform UI lives, for the places this app deliberately hands
 *  off rather than rebuilding — connecting a channel, for one. */
export function siteUrl(): string {
    const auth = configuredAuthUrl();
    if (auth) return auth.replace(/\/auth\/?$/, "");
    /* Unconfigured returns empty rather than throwing: every caller here is
       building an `href` during a render, and a hand-off link that is missing
       is survivable in a way that a blank page is not. */
    return hasApiUrl() ? apiUrl().replace(/^https?:\/\/api\./, "https://") : "";
}

/** Where the desktop app is downloaded from.
 *
 *  A product page rather than a deployment's own, so unlike the API origin it
 *  has a default worth shipping: a fork that has not thought about this is
 *  better off pointing at the real installer than at nothing. Overridable
 *  because a self-hosted estate may distribute its own build, and a button
 *  labelled "Get the app" that fetches somebody else's is a worse answer than
 *  no button. Set it empty to drop the button and keep the explanation. */
export function downloadUrl(): string {
    /* Somebody looking at a desktop installation is already running the app,
       or is on a phone the installer is not for. */
    if (isLocalDeployment()) return "";
    const set = siteRuntime().desktopDownloadUrl;
    /* `??`, not `||`: blank is the way to say "no button", and a fallback that
       could not tell blank from absent would take that option away. */
    return (set ?? "https://lemma.work/download").trim();
}

export function authUrl(): string {
    return configuredAuthUrl() || apiUrl() + "/auth";
}

/** Whether this page is same-site with the API.
 *
 *  It decides more than it looks like. A widget is served from the API host
 *  and authenticates with the session cookie — the backend injects only
 *  `podId`, `apiUrl` and `authUrl` into it, never a token. Served from the
 *  API's own registrable domain, the widget's iframe is first-party to it and
 *  the cookie travels. Served from `localhost`, it is a third-party frame, the
 *  cookie is partitioned away, and the widget reports that it cannot read the
 *  pod — which looks like a broken widget and is not one. */
export function sameSiteWithApi(): boolean {
    try {
        const api = new URL(apiUrl(), window.location.origin).hostname;
        const here = window.location.hostname;
        if (api === here) return true;
        const root = api.split(".").slice(-2).join(".");
        return here === root || here.endsWith("." + root);
    } catch {
        return false;
    }
}

/** The bearer token this browser was connected with, or null for a cookie
 *  session.
 *
 *  Exported because one caller cannot use a header. A browser may not set one
 *  on a WebSocket handshake, so `computer/live.ts` has to put the token in the
 *  query string the way the API's own datastore socket does — and without it
 *  the screen was the single call in this app that could not authenticate the
 *  way the rest of it does. Nothing else should read this: `lemma()` and
 *  `askApi` already send it. */
export function sessionToken(): string | null {
    return read(TOKEN_KEY);
}

/** True when this browser has a bearer token. A cookie session is invisible
 *  from here, so this being false does not mean unauthenticated. */
export function hasToken(): boolean {
    return Boolean(sessionToken());
}

export function connect(nextApiUrl: string, token: string): void {
    try {
        localStorage.setItem(API_KEY, nextApiUrl.trim().replace(/\/$/, ""));
        if (token.trim()) localStorage.setItem(TOKEN_KEY, token.trim());
    } catch {
        /* a browser refusing storage can still use a cookie session */
    }
    base = null;
    scoped.clear();
}

export function disconnect(): void {
    try {
        localStorage.removeItem(TOKEN_KEY);
    } catch {
        /* nothing to clear */
    }
    base = null;
    scoped.clear();
}

/** One authenticated request to the API, for the endpoints the SDK generated
 *  and then did not surface.
 *
 *  `OrganizationsService` has `orgSuggested` and `orgJoinAutoJoin` — the two
 *  calls that let somebody whose company is already on Lemma in through the
 *  front door — but `OrganizationsNamespace` never wrapped them and the
 *  client's `_http` and `_generated` are private. So either this app waits for
 *  an SDK that exports them, or it makes the request itself.
 *
 *  It authenticates both ways the SDK does, and for the same reasons: the
 *  session cookie where the page is same-site with the API, and the bearer
 *  token where it is not. Getting only one of those right would make this work
 *  in production and fail on localhost, or the reverse.
 *
 *  Delete it the moment the namespace grows the two methods. */
export async function askApi<T>(path: string, init?: RequestInit): Promise<T> {
    const token = read(TOKEN_KEY);
    const response = await fetch(apiUrl() + path, {
        ...init,
        credentials: "include",
        headers: {
            accept: "application/json",
            ...(init?.body ? { "content-type": "application/json" } : {}),
            ...(token ? { authorization: "Bearer " + token } : {}),
            ...init?.headers,
        },
    });
    if (!response.ok) {
        /* The body, not the status, because the API says why in it and
           "Request failed with status 403" sends somebody to look in the
           network tab for something this line already has. */
        const said = await response.text().catch(() => "");
        throw new Error(said.slice(0, 300) || response.status + " " + response.statusText);
    }
    if (response.status === 204) return undefined as T;
    return (await response.json()) as T;
}

/** The origin a client is built against when there is none to build it
 *  against.
 *
 *  `lemma()` has to be *constructible* without configuration, because several
 *  components make their client during render — `thread/live-conversation.tsx`
 *  and `library/record-editor.tsx` among them — and sample mode mounts all of
 *  them. Sample mode never asks that client for anything, so a throw there
 *  takes down the one view that works with no backend in order to complain
 *  about not having one.
 *
 *  `.invalid` is reserved by RFC 2606 and never resolves, so a request that
 *  does escape fails immediately and names itself in the error rather than
 *  reaching some real host. Everything that genuinely makes a request by hand
 *  — `session.tsx`, `computer/live-screen.tsx` — goes through `apiUrl()` and
 *  still refuses outright. */
const NO_ORIGIN = "https://lemma-api-not-configured.invalid";

export function lemma(podId?: string): LemmaClient {
    const configured = hasApiUrl();
    const url = configured ? apiUrl() : NO_ORIGIN;
    const auth = configured ? authUrl() : NO_ORIGIN + "/auth";
    base ??= new LemmaClient({ apiUrl: url, authUrl: auth, client: "lemma-app" });
    if (!podId) return base;

    const existing = scoped.get(podId);
    if (existing) return existing;
    /* `withPod` rather than a second `new LemmaClient`, and the difference is
       the whole of whether this app notices a session ending.
     *
     *  A constructed client carries its own `AuthManager`. Since nearly every
     *  request in the app is pod-scoped, a 401 marked *that* manager
     *  unauthenticated — while the session gate watches the base client, which
     *  never heard. Observed rather than reasoned: a bearer token expired
     *  mid-session, every new request began failing, and the app sat there
     *  showing cached data with no door and no error.
     *
     *  `withPod` shares the auth state, so one 401 anywhere is one session
     *  ending everywhere. */
    const made = base.withPod(podId);
    scoped.set(podId, made);
    return made;
}
