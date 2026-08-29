/**
 * The web client's event contract — the mirror of the backend's
 * `app/core/analytics/event_catalog.py`.
 *
 * The backend emitter is default-deny on both keys and values, and
 * `test_analytics_safety.py` proves it with adversarial input. Until this file
 * existed the client had none of that: `captureEvent` took an arbitrary
 * property bag straight to a third party, which is how two events already drifted
 * from the contract they share a *name* with. A shared name with divergent
 * shapes is worse than two names, because it looks correct in a dashboard.
 *
 * Two rules, matching the backend's:
 *
 * * an event absent from this catalog is not an event;
 * * a property absent from its entry is dropped, not forwarded.
 *
 * Both are enforced in one `before_send` gate in `client.ts`, so no call site
 * can bypass them.
 */

export interface ClientAnalyticEvent {
    /** Allowlisted property keys. Ids, bounded enums and booleans only — never a
     *  name, an email, a path, a URL, or free text. */
    readonly properties: readonly string[];
}

export const CLIENT_CATALOG = {
    /** The marketing page. Top of the funnel, before any API call exists. */
    "landing.viewed": { properties: [] },
    /** A shared link opened. `kind` and `viewer_is_member` must stay identical to
     *  the backend entry of the same name. */
    "share_link.viewed": { properties: ["bundle_id", "kind", "viewer_is_member"] },
    /** A contact card's channel row pressed — the moment the reader leaves for
     *  Telegram, WhatsApp or their mail client. `channel` is the bounded
     *  `ContactChannel` key and never the handle behind it, which is an address. */
    "share_link.contact_opened": { properties: ["channel"] },
    /** The vCard saved. The card's real conversion, and the last thing we can
     *  see: everything after it happens in an address book we have no view of. */
    "share_link.contact_saved": { properties: [] },
    /** The import button pressed — the step the server never sees, because an
     *  abandoned import makes no API call. */
    "import.started": { properties: ["bundle_id", "is_remix"] },
    /** Pod creation begun in the UI, before anything is persisted. */
    "pod.create_started": { properties: [] },
    /** A client-side error. `error_class` is a constructor name, never a message
     *  — messages carry user content. */
    "client.error": { properties: ["error_class"] },

    // --- onboarding -------------------------------------------------------
    //
    // Onboarding renders every step at "/", so pageviews cannot tell them
    // apart and the funnel was previously unmeasurable end to end. `step` is
    // the bounded SetupStep union, never free text.
    /** One onboarding step shown. */
    "onboarding.step_viewed": { properties: ["step"] },
    /** The user is inside a pod: the end of onboarding, however they got there.
     *  `entry_kind` says which of the three routes they took. */
    "onboarding.pod_ready": { properties: ["entry_kind", "elapsed_bucket"] },

    // --- activation -------------------------------------------------------
    //
    // The three transitions that each permanently widen what the product can
    // do, and the thing worth optimising rather than step completion. Elapsed
    // time is bucketed rather than exact: a duration is not an id, an enum or a
    // boolean, and the funnel only ever reads it in bands anyway.
    /** A chat surface went live. The pod can be *reached* only once its owner
     *  has messaged the bot — a backend fact — so this is the nearest thing the
     *  client can honestly witness, and is named for what it measures. */
    "activation.surface_connected": { properties: ["platform", "elapsed_bucket"] },
    /** The pod does work for its owner: an app page opened. */
    "activation.app_opened": { properties: ["elapsed_bucket"] },
    /** The pod is shared: an invited teammate accepted and landed inside it. */
    "activation.member_joined": { properties: ["elapsed_bucket"] },
} as const satisfies Record<string, ClientAnalyticEvent>;

export type WebAnalyticEvent = keyof typeof CLIENT_CATALOG;

/** Declared but raised by nothing yet.
 *
 *  Same ratchet as the backend's `KNOWN_GAPS`: a declared event nothing emits is
 *  a dashboard that is permanently zero, and the worst version of that is the
 *  one nobody knows about. Emitting one means deleting it from here. */
export const KNOWN_UNEMITTED: readonly WebAnalyticEvent[] = [
    "landing.viewed",
    "pod.create_started",
];

/** Properties the client attaches to every event, so they are never dropped by
 *  the allowlist above. */
export const CLIENT_SPINE_PROPERTIES: readonly string[] = ["deployment"];

export function isCatalogued(name: string): name is WebAnalyticEvent {
    return Object.prototype.hasOwnProperty.call(CLIENT_CATALOG, name);
}

/** Drop every property this event has not declared. */
export function allowedProperties(
    name: WebAnalyticEvent,
    properties: Record<string, unknown>,
): Record<string, unknown> {
    const allowed = new Set<string>([
        ...CLIENT_CATALOG[name].properties,
        ...CLIENT_SPINE_PROPERTIES,
    ]);
    const kept: Record<string, unknown> = {};
    for (const [key, value] of Object.entries(properties)) {
        if (allowed.has(key)) kept[key] = value;
    }
    return kept;
}
