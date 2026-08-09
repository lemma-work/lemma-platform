/**
 * Product analytics for the web client.
 *
 * The backend is the primary emitter (docs/design/product-analytics.md §5):
 * anything with a state change is captured off the domain event bus, which
 * covers every origin including the ones with no browser present. This file
 * therefore emits only what the server cannot see — navigation, funnel steps
 * abandoned before any API call, and client errors.
 *
 * Two settings are deliberate and must not be relaxed:
 *
 * - **autocapture off.** Lemma renders customer business data: table records,
 *   agent transcripts, file contents. Autocapture harvests DOM text into event
 *   properties, which is the fastest possible way to put a customer's records
 *   into a third-party analytics database.
 * - **session replay off.** The same reason, more so.
 */

import posthog from "posthog-js";
import { config, isLocalDeployment } from "@/lib/config";

/** Events the web client owns. Names mirror the backend catalog's
 * `noun.verb_past`, and the two share a vocabulary on purpose. */
export type WebAnalyticEvent =
    | "landing.viewed"
    | "share_link.viewed"
    | "import.started"
    | "pod.create_started"
    | "client.error";

type PropertyValue = string | number | boolean;

let started = false;

/**
 * A Desktop-local installation runs the whole backend on the user's machine,
 * and the README promises exactly that. It reports nothing, and the check is
 * the existing deployment helper rather than a new flag that could drift.
 */
function analyticsAllowed(): boolean {
    if (typeof window === "undefined") return false;
    if (isLocalDeployment()) return false;
    return Boolean(process.env.NEXT_PUBLIC_ANALYTICS_KEY);
}

export function startAnalytics(): void {
    if (started || !analyticsAllowed()) return;
    started = true;
    posthog.init(process.env.NEXT_PUBLIC_ANALYTICS_KEY as string, {
        // Same-origin, so an ad blocker does not silently eat a share of the
        // data — and the share it eats is not random, it skews toward exactly
        // the technical users Lemma sells to.
        api_host: "/ingest",
        ui_host: process.env.NEXT_PUBLIC_ANALYTICS_HOST || "https://eu.posthog.com",
        autocapture: false,
        disable_session_recording: true,
        capture_pageview: false, // App Router navigations are captured by hand.
        capture_pageleave: true,
        // Anonymous traffic must not mint a person record for every bot that
        // hits the landing page.
        person_profiles: "identified_only",
        persistence: "localStorage+cookie",
    });
}

export function captureEvent(
    name: WebAnalyticEvent,
    properties: Record<string, PropertyValue> = {},
): void {
    if (!started || !analyticsAllowed()) return;
    posthog.capture(name, { ...properties, deployment: config.DEPLOYMENT });
}

export function capturePageview(pathname: string): void {
    if (!started || !analyticsAllowed()) return;
    // The path only — never `window.location.href`. Query strings in this app
    // carry ids, tokens and filters, and none of that belongs in analytics.
    posthog.capture("$pageview", { $current_url: pathname });
}

/**
 * Join the pre-signup session to the person. The landing pages and the app are
 * one Next application, so the anonymous id set on the marketing page is
 * already the right one to alias — no cross-domain stitching needed.
 */
export function identifyUser(userId: string, organizationId?: string): void {
    if (!started || !analyticsAllowed()) return;
    posthog.identify(userId);
    if (organizationId) {
        posthog.group("organization", organizationId);
    }
}

export function resetAnalyticsIdentity(): void {
    if (!started || !analyticsAllowed()) return;
    posthog.reset();
}
