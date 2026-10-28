/**
 * The web client's event contract — the mirror of the backend's
 * `app/core/analytics/event_catalog.py`.
 *
 * The backend emitter is default-deny on both keys and values, and
 * `test_analytics_safety.py` proves it with adversarial input. The client is
 * default-deny on keys: `captureEvent` is typed against this table, and the
 * `before_send` gate in `client.ts` drops anything else whatever the call site.
 * A shared name with divergent shapes is worse than two names, because it looks
 * correct in a dashboard, so the two names both sides use keep the backend's
 * property names exactly.
 *
 * Two rules, matching the backend's:
 *
 * * an event absent from this catalog is not an event;
 * * a property absent from its entry is dropped, not forwarded.
 *
 * And what belongs here at all: only what the server cannot see. Anything that
 * changes state is the backend's — it sees every origin, including the ones
 * with no browser — so these are the steps a person takes before any API call,
 * and the ones that end somewhere else (a provider's sign-in, a checkout, an
 * address book). `tests/analytics-catalog.test.ts` holds every entry to a call
 * site.
 */

export interface ClientAnalyticEvent {
    /** Allowlisted property keys. Ids, bounded enums and booleans only — never a
     *  name, an email, a path, a URL, or free text. */
    readonly properties: readonly string[];
}

export const CLIENT_CATALOG = {
    // --- the share → import loop ------------------------------------------
    //
    // The two names the backend lists as client-emitted (`CLIENT_EMITTED` in
    // `test_analytics_wiring.py`), so their keys are the backend entry's.

    /** A shared link opened, signed in or not — the reader who is not signed in
     *  is exactly the one this funnel is about. The page renders on the server
     *  without checking access, so `viewer_is_member` is not sent from it. */
    "share_link.viewed": {
        properties: ["bundle_id", "kind", "viewer_is_member"],
    },
    /** A contact card's channel row pressed — the moment the reader leaves for
     *  Telegram, WhatsApp or their mail client. `channel` is the bounded
     *  `ContactChannel` key and never the handle behind it, which is an address. */
    "share_link.contact_opened": { properties: ["channel"] },
    /** The vCard saved. The card's real conversion, and the last thing we can
     *  see: everything after it happens in an address book we have no view of. */
    "share_link.contact_saved": { properties: [] },
    /** An import begun — Remix, or reviewing a GitHub install — before any
     *  request: an abandoned one makes no API call the server could count. */
    "import.started": { properties: ["bundle_id", "is_remix"] },

    // --- arriving -------------------------------------------------------------

    /** The first screen of a new account: what it offered. Booleans, never the
     *  organization or the domain behind them. */
    "onboarding.arrival_viewed": { properties: ["invited", "domain_match"] },
    /** Which way somebody got into an organization: `invite`, `domain` or
     *  `new_org`. The membership itself is the backend's event. */
    "onboarding.arrived": { properties: ["route"] },

    // --- hiring a teammate ----------------------------------------------------

    /** One step of hiring shown: `shelf`, `describe`, `candidate`, `making`,
     *  `met`. All of it is one overlay at one address, so pageviews cannot tell
     *  the steps apart, and a step left before the hire makes no request. */
    "hire.step_viewed": { properties: ["step"] },
    /** The hire pressed, and which way: `role` (a card from the shelf),
     *  `described` (a job written out) or `explore` (a dealt face, no job). The
     *  pod it makes is the backend's `pod.created`. */
    "hire.started": { properties: ["path"] },

    // --- leaving for somewhere else -------------------------------------------

    /** A connector's sign-in opened at its provider. What comes back is the
     *  backend's `connector.connected`; this is the top of that funnel. */
    "connector.authorization_opened": { properties: [] },
    /** The payment page opened, for which plan id. A payment that never happens
     *  writes nothing at either end, so this is the only record it was tried. */
    "billing.checkout_opened": { properties: ["plan"] },
    /** "Not now" pressed while a checkout or a change was waiting. */
    "billing.checkout_abandoned": { properties: ["plan"] },
} as const satisfies Record<string, ClientAnalyticEvent>;

export type WebAnalyticEvent = keyof typeof CLIENT_CATALOG;

/** What a call site may send with `name`: its declared keys, each optional. */
export type EventProperties<E extends WebAnalyticEvent> = {
    readonly [K in (typeof CLIENT_CATALOG)[E]["properties"][number]]?: string | number | boolean;
};

/** Declared but raised by nothing yet.
 *
 *  Same ratchet as the backend's `KNOWN_GAPS`: a declared event nothing emits is
 *  a dashboard that is permanently zero, and the worst version of that is the
 *  one nobody knows about. `tests/analytics-catalog.test.ts` fails on an entry
 *  with no call site that is not named here, and on a name here that has one. */
export const KNOWN_UNEMITTED: readonly WebAnalyticEvent[] = [];

/** Properties the client attaches to every event, so they are never dropped by
 *  the allowlist above. Registered once at start (`registerSpine`). */
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
