/**
 * Product analytics for the web client.
 *
 * The backend is the primary emitter (docs/design/product-analytics.md §5):
 * anything with a state change is captured off the domain event bus, which
 * covers every origin including the ones with no browser present. This file
 * therefore emits only what the server cannot see — navigation, funnel steps
 * abandoned before any API call, and client errors.
 *
 * Four settings are deliberate and must not be relaxed:
 *
 * - **autocapture off.** Lemma renders customer business data: table records,
 *   agent transcripts, file contents. Autocapture harvests DOM text into event
 *   properties, which is the fastest possible way to put a customer's records
 *   into a third-party analytics database.
 * - **session replay off.** The same reason, more so.
 * - **`before_send` is the export boundary.** Every event passes through it,
 *   whatever the call site, so the client gets the same default-deny posture the
 *   backend emitter has. `sanitize_properties` is deprecated and is not used.
 * - **cookieless until consent.** Persistence starts in memory and is upgraded
 *   only once someone accepts. See `consent.ts`.
 *
 * Consent governs what is stored in the browser, not whether a signed-in person
 * is counted. Inside the workspace, events carry the account's id whatever the
 * answer was, as `/privacy` says. Without consent, though, nothing may persist
 * an anonymous id between loads, so none is merged into the person
 * (`reuseAnonymousId`). Otherwise every page load would add one more id to
 * that person, until PostHog stops showing their events.
 */

import { isAnalyticsDocument } from "./document";

import type { BeforeSendFn, CaptureResult, PostHogConfig } from "posthog-js";

import { config, isLocalDeployment } from "@/site/config";
import {
    CLIENT_SPINE_PROPERTIES,
    allowedProperties,
    isCatalogued,
    type EventProperties,
    type WebAnalyticEvent,
} from "@/site/analytics/catalog";
import { hasAnalyticsConsent } from "@/site/analytics/consent";
import {
    toRouteTemplate,
    toTemplateUrl,
} from "@/site/analytics/route-template";

type PostHog = typeof import("posthog-js").posthog;

/** The loaded SDK, or null. `ph !== null` *is* "started" — one source of truth
 *  beats a separate boolean that can disagree with it. */
let ph: PostHog | null = null;
let starting: Promise<void> | null = null;

export interface AnalyticsIdentity {
    userId: string;
    organizationId?: string;
    podId?: string;
}

/** Who was last identified, so withdrawing consent — which has to `reset()` to
 *  clear the device — does not also stop counting the person still signed in. */
let identified: AnalyticsIdentity | null = null;

/**
 * A Desktop-local installation runs the whole backend on the user's machine,
 * and the README promises exactly that. It reports nothing, and the check is
 * the existing deployment helper rather than a new flag that could drift.
 */
function analyticsAllowed(): boolean {
    if (!isAnalyticsDocument()) return false;
    if (isLocalDeployment()) return false;
    return Boolean(config.ANALYTICS_KEY);
}

/**
 * Which properties are addresses, by the shape of their key.
 *
 * Not a list of keys, because posthog-js sets more of them than any list
 * written against its docs: `$session_entry_url` and `$session_entry_referrer`
 * ride on every event of a session, `$prev_pageview_pathname` on every pageview
 * after the first, and a release can add another. A list held only the six it
 * named and let those through with the full address, query string included.
 * `tests/analytics-export.test.ts` runs the real SDK to keep this true.
 */
const URL_KEY = /(url|referrer)$/i;
const PATH_KEY = /pathname$/i;

/** posthog-js's referrer for a visit that had none. Not an address. */
const DIRECT = "$direct";

/** A page here keeps its route template. A page on another site keeps only its
 *  origin: its path is not one of our routes, and its query is that site's,
 *  which for a mail client or a search engine is somebody's words. */
function scrubUrl(raw: string): string | null {
    if (raw === DIRECT) return raw;
    let url: URL;
    try {
        url = new URL(raw);
    } catch {
        return null;
    }
    if (url.origin !== window.location.origin) return url.origin;
    return toTemplateUrl(raw);
}

function scrubBag(bag: Record<string, unknown> | undefined): void {
    if (!bag) return;
    // posthog-js copies `document.title` onto every pageview, and a page's title
    // is the name of what it shows — a shared page, a contact, a file.
    delete bag.title;
    for (const [key, value] of Object.entries(bag)) {
        if (typeof value !== "string") continue;
        if (URL_KEY.test(key)) {
            const kept = scrubUrl(value);
            if (kept === null) delete bag[key];
            else bag[key] = kept;
        } else if (PATH_KEY.test(key)) {
            bag[key] = toRouteTemplate(value);
        }
    }
}

/**
 * Reduce every address on an event to its route template, and drop the page
 * title, including inside `$set` and `$set_once`.
 *
 * `$set_once` is the important half: `$initial_current_url` is written to the
 * *person* on identify, so an un-scrubbed first-touch URL persists against a
 * named human and is far harder to clean up than an event. posthog-js puts both
 * beside `properties` on the event, not inside it — reading only the inner
 * copies is how the person's first URL went out whole.
 */
function scrubAddresses(event: CaptureResult | null): CaptureResult | null {
    if (!event) return event;
    const properties = event.properties as Record<string, unknown> | undefined;
    scrubBag(properties);
    scrubBag(event.$set as Record<string, unknown> | undefined);
    scrubBag(event.$set_once as Record<string, unknown> | undefined);
    scrubBag(properties?.$set as Record<string, unknown> | undefined);
    scrubBag(properties?.$set_once as Record<string, unknown> | undefined);
    return event;
}

/** Errors whose message is fixed text this app wrote, and so says nothing about
 *  anybody. Every other message is the error's type. */
const FIXED_MESSAGES = new Set(["SignInLoopError"]);

interface ExceptionFrame { filename?: unknown; abs_path?: unknown }
interface ExceptionEntry { type?: unknown; value?: unknown; stacktrace?: { frames?: ExceptionFrame[] } }

/** A stack frame's file, which is our own code — keep it, it is how an error
 *  is found — unless it is a page: an inline script's frame names the page it
 *  ran on, address and all. */
function scrubFrameFile(raw: unknown): unknown {
    if (typeof raw !== "string") return raw;
    let url: URL;
    try {
        url = new URL(raw);
    } catch {
        return raw;
    }
    if (url.origin === window.location.origin && url.pathname.startsWith("/_next/")) return url.origin + url.pathname;
    return scrubUrl(raw);
}

/**
 * Keep an exception's type and where it was thrown, and drop what it said.
 *
 * A message is written wherever the error is raised, and is routinely made of
 * whatever was in hand: `Cannot read "Q3 layoffs" of undefined`, a failed
 * request's body, a file name. The type and the frames are enough to find the
 * bug; the message is the part that can carry somebody's work out with it.
 */
function scrubExceptions(event: CaptureResult | null): CaptureResult | null {
    if (!event || event.event !== "$exception") return event;
    const list = event.properties?.$exception_list as ExceptionEntry[] | undefined;
    for (const entry of list ?? []) {
        const type = typeof entry.type === "string" ? entry.type : "Error";
        if (!FIXED_MESSAGES.has(type)) entry.value = type;
        for (const frame of entry.stacktrace?.frames ?? []) {
            if ("filename" in frame) frame.filename = scrubFrameFile(frame.filename);
            if ("abs_path" in frame) frame.abs_path = scrubFrameFile(frame.abs_path);
        }
    }
    delete event.properties?.$exception_message;
    return event;
}

/**
 * Default-deny on event names and property keys.
 *
 * PostHog's own `$`-prefixed events pass through — they are the pageviews,
 * identifies and group identifies this integration is built on. Everything else
 * must be in the catalog, and carries only what its entry declares.
 */
function dropUnknownEvents(event: CaptureResult | null): CaptureResult | null {
    if (!event) return event;
    if (event.event.startsWith("$")) return event;
    if (!isCatalogued(event.event)) return null;
    if (event.properties) {
        const kept = allowedProperties(event.event, event.properties);
        // Preserve everything posthog-js manages itself; the allowlist governs
        // what *call sites* may add.
        for (const key of Object.keys(event.properties)) {
            if (
                key.startsWith("$") ||
                SDK_KEYS.has(key) ||
                key in kept ||
                CLIENT_SPINE_PROPERTIES.includes(key)
            ) {
                continue;
            }
            delete event.properties[key];
        }
    }
    return event;
}

/** posthog-js's own properties without a `$`. It drops an event whose
 *  `before_send` removed `token`, and an event without `distinct_id` belongs
 *  to nobody. Deleting both, as this gate once did, lost every catalogued
 *  event without a word. */
const SDK_KEYS = new Set(["token", "distinct_id"]);

/** What posthog-js is started with. Exported so the export-boundary test runs
 *  the real SDK on exactly this, rather than on a copy of it that could drift. */
export function analyticsOptions(): Partial<PostHogConfig> & { before_send: BeforeSendFn[] } {
    return {
        // Same-origin, so an ad blocker does not silently eat a share of
        // the data — and the share it eats is not random, it skews toward
        // exactly the technical users Lemma sells to.
        api_host: "/ingest",
        ui_host: config.ANALYTICS_HOST,
        autocapture: false,
        disable_session_recording: true,
        capture_pageview: false, // App Router navigations are captured by hand.
        capture_pageleave: true,
        // Anonymous traffic must not mint a person record for every bot
        // that hits the landing page.
        person_profiles: "identified_only",
        // Upgraded to `localStorage+cookie` by `applyAnalyticsPersistence`.
        ...storage(hasAnalyticsConsent()),
        mask_personal_data_properties: true,
        before_send: [dropUnknownEvents, scrubAddresses, scrubExceptions],
    };
}

/** Where the client keeps its ids, by the answer given. With nothing stored,
 *  every load starts a new anonymous id, and that id is not merged into the
 *  person it identifies as — there is no earlier visit it could join up. */
function storage(granted: boolean): Pick<PostHogConfig, "persistence" | "reuseAnonymousId"> {
    return granted
        ? { persistence: "localStorage+cookie", reuseAnonymousId: false }
        : { persistence: "memory", reuseAnonymousId: true };
}

/** Properties on every event, posthog-js's own pageviews included. `reset()`
 *  clears them, so this runs after every reset too. */
function registerSpine(client: PostHog): void {
    client.register({ deployment: config.DEPLOYMENT });
}

/** Errors nothing caught. A render error is caught by the app's error pages
 *  (`app/error.tsx`, `app/global-error.tsx`), which report it themselves. */
function reportUncaught(): void {
    window.addEventListener("error", (event) => captureClientError(event.error ?? event.message));
    window.addEventListener("unhandledrejection", (event) => captureClientError(event.reason));
}

export function startAnalytics(): Promise<void> {
    if (starting) return starting;
    if (!analyticsAllowed()) return Promise.resolve();

    starting = import("posthog-js")
        .then((mod) => {
            /* The named export rather than `default`: it is the client in a
               bundle and under Node alike, where `default` is the CommonJS
               module object and the export test could not start the real one. */
            const client = mod.posthog;
            client.init(config.ANALYTICS_KEY, analyticsOptions());
            registerSpine(client);
            ph = client;
            reportUncaught();
            // Flags arrive after init, and again after identify reloads them.
            client.onFeatureFlags(notifyFlagListeners);
            notifyFlagListeners();
        })
        .catch(() => {
            // A blocked or failed chunk load must not take the app with it.
            ph = null;
        });

    return starting;
}

/**
 * Called by the consent banner and by the switch on the privacy page.
 *
 * Granting upgrades an already-running client in place, so the anonymous id
 * established before consent survives the transition — a landing→signup funnel
 * is not broken by the act of consenting.
 *
 * Withdrawing has to *remove* what consent wrote, not merely stop writing more.
 * Order carries that: changing the persistence type makes posthog-js empty the
 * store it was using, so the identifier actually leaves localStorage and the
 * cookie jar, and only then does `reset()` mint a fresh anonymous id — into
 * memory, where it stays. Resetting first would write that fresh id straight
 * back to the device we were trying to clear. A person still signed in is then
 * identified again: what they withdrew was the identifier in this browser, not
 * their account.
 */
export function applyAnalyticsPersistence(granted: boolean): void {
    if (!ph) return;
    ph.set_config(storage(granted));
    if (granted) return;
    ph.reset();
    registerSpine(ph);
    if (identified) setAnalyticsIdentity(identified);
}

/** A catalogued event. Waits for the SDK rather than dropping what happens
 *  before its chunk has loaded: the first screen a person sees is often the
 *  one that fires first. */
export function captureEvent<E extends WebAnalyticEvent>(
    name: E,
    ...[properties]: keyof EventProperties<E> extends never ? [] : [EventProperties<E>]
): void {
    void startAnalytics().then(() => {
        ph?.capture(name, properties ?? {});
    });
}

/**
 * An error, for error tracking: its type and frames, never its message
 * (`scrubExceptions`). Anything thrown may arrive here — a rejected promise's
 * reason is as often a string or a plain object as an `Error` — and what is not
 * an error is reported as one with nothing in it.
 */
export function captureClientError(thrown: unknown): void {
    void startAnalytics().then(() => {
        ph?.captureException(thrown instanceof Error ? thrown : new Error("Non-error thrown"));
    });
}

/** Named so the error-tracking issue it becomes says what happened. */
class SignInLoopError extends Error {
    override name = "SignInLoopError";
}

/**
 * A pod app that would not take a sign-in the portal could see.
 *
 * Sent as an exception rather than a catalogued event, because what the team
 * watches is error tracking: every request in such a loop succeeds, so nothing
 * else would ever raise it. Fixed text and no properties of its own. The app it
 * came from is in `$referrer`, which `scrubAddresses` reduces to an origin, as
 * it does for every other site's address.
 */
export function captureSignInLoop(): void {
    void startAnalytics().then(() => {
        ph?.captureException(
            new SignInLoopError("A pod app kept sending a signed-in person back to sign in"),
        );
    });
}

/** The page in front, as a pageview. Which navigations count is the caller's
 *  (`isNewView`); `$current_url` is set by posthog-js and rewritten in
 *  `before_send`, so there is exactly one place that decides what a URL may say. */
export function capturePageview(): void {
    ph?.capture("$pageview");
}

/**
 * Establish who this is, and which org and pod they are working in.
 *
 * Order is load-bearing. With `person_profiles: "identified_only"`, `group()`
 * also triggers person processing — calling it before `identify()` creates a
 * person keyed to the *anonymous* id, which is the pollution `identified_only`
 * exists to prevent. Exposed as one function for that reason: two would be two
 * things a caller can put in the wrong order.
 *
 * No group properties are sent. Org and pod *names* are exactly what the design
 * doc forbids in analytics; names live in Postgres and join on the id there.
 */
export function setAnalyticsIdentity(identity: AnalyticsIdentity): void {
    identified = identity;
    if (!ph) return;
    ph.identify(identity.userId);

    // `resetGroups` first, then re-apply: posthog-js has no "leave this group",
    // so without it every event fired after navigating out of a pod is still
    // attributed to the last pod visited.
    ph.resetGroups();
    if (identity.organizationId)
        ph.group("organization", identity.organizationId);
    if (identity.podId) ph.group("pod", identity.podId);
}

/** Signing out: the next person at this browser is somebody else. */
export function resetAnalyticsIdentity(): void {
    identified = null;
    if (!ph) return;
    ph.reset();
    registerSpine(ph);
}

/* ── Feature flags ─────────────────────────────────────────────────────
 * Off unless PostHog says on. Where PostHog never starts — Desktop, a
 * deployment with no key, a blocked chunk — every flag stays off, unless the
 * deployment names it in `NEXT_PUBLIC_LEMMA_FEATURES`. */

const flagListeners = new Set<() => void>();

function notifyFlagListeners(): void {
    for (const listener of flagListeners) listener();
}

export function subscribeToFlags(listener: () => void): () => void {
    flagListeners.add(listener);
    return () => flagListeners.delete(listener);
}

export function isFeatureOn(flag: FeatureFlag): boolean {
    if (forcedFeatures().has(flag)) return true;
    return ph?.isFeatureEnabled(flag) === true;
}

function forcedFeatures(): Set<string> {
    return new Set(config.FEATURES.split(",").map((name) => name.trim()).filter(Boolean));
}

/** Every flag the client reads, by its PostHog key. */
export type FeatureFlag = "contacts";
