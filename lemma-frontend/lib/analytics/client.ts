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
 */

import type { CaptureResult } from "posthog-js";

import { config, isLocalDeployment } from "@/lib/config";
import {
    CLIENT_SPINE_PROPERTIES,
    allowedProperties,
    isCatalogued,
    type WebAnalyticEvent,
} from "@/lib/analytics/catalog";
import { hasAnalyticsConsent } from "@/lib/analytics/consent";
import { toRouteTemplate, toTemplateUrl } from "@/lib/analytics/route-template";

type PostHog = typeof import("posthog-js").default;
type PropertyValue = string | number | boolean;

/** The loaded SDK, or null. `ph !== null` *is* "started" — one source of truth
 *  beats a separate boolean that can disagree with it. */
let ph: PostHog | null = null;
let starting: Promise<void> | null = null;

/** A pageview that arrived before the dynamic import finished. At most one: the
 *  first paint. Without it the very first navigation of a session — the one that
 *  tells you where people land — is lost to the async gap. */
let pendingPathname: string | null = null;

/**
 * A Desktop-local installation runs the whole backend on the user's machine,
 * and the README promises exactly that. It reports nothing, and the check is
 * the existing deployment helper rather than a new flag that could drift.
 */
function analyticsAllowed(): boolean {
    if (typeof window === "undefined") return false;
    if (isLocalDeployment()) return false;
    return Boolean(config.ANALYTICS_KEY);
}

/** URL-shaped properties posthog-js sets itself, on every event. */
const URL_PROPERTIES = ["$current_url", "$referrer", "$initial_current_url", "$initial_referrer"];
const PATH_PROPERTIES = ["$pathname", "$initial_pathname"];

function scrubUrlBag(bag: Record<string, unknown> | undefined): void {
    if (!bag) return;
    for (const key of URL_PROPERTIES) {
        if (typeof bag[key] !== "string") continue;
        const templated = toTemplateUrl(bag[key] as string);
        if (templated === null) delete bag[key];
        else bag[key] = templated;
    }
    for (const key of PATH_PROPERTIES) {
        if (typeof bag[key] !== "string") continue;
        bag[key] = toRouteTemplate(bag[key] as string);
    }
}

/**
 * Strip ids out of every URL-shaped property, including the ones inside `$set`
 * and `$set_once`.
 *
 * `$set_once` is the important half: `$initial_current_url` is written to the
 * *person* on identify, so an un-scrubbed first-touch URL persists against a
 * named human and is far harder to clean up than an event.
 */
function scrubUrls(event: CaptureResult | null): CaptureResult | null {
    const properties = event?.properties;
    if (!properties) return event;
    scrubUrlBag(properties);
    scrubUrlBag(properties.$set as Record<string, unknown> | undefined);
    scrubUrlBag(properties.$set_once as Record<string, unknown> | undefined);
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
            if (key.startsWith("$") || key in kept || CLIENT_SPINE_PROPERTIES.includes(key)) {
                continue;
            }
            delete event.properties[key];
        }
    }
    return event;
}

export function startAnalytics(): Promise<void> {
    if (starting) return starting;
    if (!analyticsAllowed()) return Promise.resolve();

    starting = import("posthog-js")
        .then((mod) => {
            const client = mod.default;
            client.init(config.ANALYTICS_KEY, {
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
                // Upgraded to `localStorage+cookie` by `grantAnalyticsConsent`.
                persistence: hasAnalyticsConsent() ? "localStorage+cookie" : "memory",
                mask_personal_data_properties: true,
                before_send: [dropUnknownEvents, scrubUrls],
            });
            ph = client;
            if (pendingPathname) {
                capturePageview(pendingPathname);
                pendingPathname = null;
            }
        })
        .catch(() => {
            // A blocked or failed chunk load must not take the app with it.
            ph = null;
        });

    return starting;
}

/** Called by the consent banner. Upgrades an already-running client in place so
 *  the anonymous id established before consent survives the transition. */
export function applyAnalyticsPersistence(granted: boolean): void {
    ph?.set_config({ persistence: granted ? "localStorage+cookie" : "memory" });
}

export function captureEvent(
    name: WebAnalyticEvent,
    properties: Record<string, PropertyValue> = {},
): void {
    ph?.capture(name, { ...properties, deployment: config.DEPLOYMENT });
}

export function capturePageview(pathname: string): void {
    if (!ph) {
        if (analyticsAllowed()) pendingPathname = pathname;
        return;
    }
    // `$current_url` is set by posthog-js and rewritten in `before_send`, so
    // there is exactly one place that decides what a URL may say.
    ph.capture("$pageview");
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
export function setAnalyticsIdentity(identity: {
    userId: string;
    organizationId?: string;
    podId?: string;
}): void {
    if (!ph) return;
    ph.identify(identity.userId);

    // `resetGroups` first, then re-apply: posthog-js has no "leave this group",
    // so without it every event fired after navigating out of a pod is still
    // attributed to the last pod visited.
    ph.resetGroups();
    if (identity.organizationId) ph.group("organization", identity.organizationId);
    if (identity.podId) ph.group("pod", identity.podId);
}

export function resetAnalyticsIdentity(): void {
    ph?.reset();
}
