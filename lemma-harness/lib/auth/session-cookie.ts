/**
 * Does a request arrive holding a session?
 *
 * Deliberately free of `server-only` and `next/headers`, which the binding in
 * `server-session.ts` supplies. Both of those refuse to load outside a server
 * component — including under the unit suite, which is node-only — so keeping
 * the decision here is what lets it be asserted on at all.
 *
 * This is a hint, never an authorization. Nothing here validates a token; the
 * server has no way to and does not need to. The only thing it decides is which
 * of two placeholders the root page paints while the real auth check runs, and
 * both branches converge on the same verified answer a moment later. A forged
 * cookie buys a stranger a loading spinner.
 */

/**
 * The cookies SuperTokens sets for a session, in `tokenTransferMethod:
 * "cookie"` mode (see `ensureCookieSessionSupport` in the SDK).
 *
 * `sFrontToken` is the one the frontend itself reads — it is what makes
 * `doesSessionExist()` answer true, and what `clearFrontendSessionState` in
 * `browser-session.ts` clears to end a session locally. `st-access-token` is
 * HttpOnly and unreadable from the browser, but a server sees every cookie the
 * browser sends, so it works just as well here and outlives any future change
 * to how the frontend stores its half.
 */
export const SESSION_HINT_COOKIES = ["sFrontToken", "st-access-token"] as const;

/**
 * True when the request carries either session cookie.
 *
 * Either alone counts. They are set together, so requiring both would turn a
 * partial expiry into a marketing page flashed at someone who is signed in —
 * exactly the thing this exists to stop.
 */
export function sessionCookiePresent(
    readCookie: (name: string) => string | undefined,
): boolean {
    return SESSION_HINT_COOKIES.some((name) => Boolean(readCookie(name)));
}
