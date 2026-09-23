/** What the app makes of the SDK's answer about who is asking.
 *
 *  Its own module, and not because of tidiness: the test runner strips types
 *  from `.ts` and cannot load `.tsx` at all, so anything living beside JSX is
 *  untestable here. These functions carry the decisions — everything in
 *  `session.tsx` is rendering and redirects.
 */

export type SessionStatus = "loading" | "in" | "out" | "sample" | "unconfigured";

/** Is this rejection the server saying "not you"?
 *
 *  Only 401. A 403 is somebody signed in meeting a permission or an RLS denial,
 *  which is a different thing said in a different sentence — and signing them
 *  out of an app they are entitled to use is the wrong answer to it.
 *
 *  Read structurally rather than with `instanceof`: the error crosses a package
 *  boundary, and a duplicated copy of the SDK anywhere in the module graph would
 *  make `instanceof` quietly false. By the time a rejection gets here the
 *  session layer has already refreshed and retried underneath it, so a 401 is
 *  the answer rather than a blip worth another go.
 */
export function isUnauthorized(error: unknown): boolean {
    if (!error || typeof error !== "object") return false;
    const candidate = error as { statusCode?: unknown; name?: unknown };
    return candidate.statusCode === 401 || candidate.name === "UnauthorizedError";
}

/** Is this rejection the server saying "not yours"?
 *
 *  The other half of the sentence above. A 403 is somebody who is signed in
 *  meeting a permission boundary — asking an organization about its spending
 *  without belonging to it, say — and the honest answer is to name the
 *  boundary, not to retry, and certainly not to sign them out.
 *
 *  Structural for the same reason: a second copy of the SDK anywhere in the
 *  module graph makes `instanceof` quietly false.
 */
export function isForbidden(error: unknown): boolean {
    if (!error || typeof error !== "object") return false;
    const candidate = error as { statusCode?: unknown; name?: unknown };
    return candidate.statusCode === 403 || candidate.name === "ForbiddenError";
}

/** Is this rejection the server saying "there is no such thing"?
 *
 *  The third of the set, and the one the door needs. Asking to join a pod is
 *  the only call that can tell a teammate who is not yours from a teammate who
 *  does not exist: the platform answers a real request for the first and 404
 *  for the second, and it does that deliberately — answering 404 to a
 *  non-member would hide the pod, at the cost of leaving the one route into it
 *  open exclusively to people already inside.
 *
 *  So this is what turns "ask to join" into "that link is wrong", and the two
 *  must not be said with the same sentence. Structural, for the reason the
 *  other two are.
 */
export function isMissing(error: unknown): boolean {
    if (!error || typeof error !== "object") return false;
    const candidate = error as { statusCode?: unknown; name?: unknown };
    return candidate.statusCode === 404 || candidate.name === "NotFoundError";
}

/** The SDK's three answers, plus the two this app adds.
 *
 *  `sample` wins over all of them. Sample mode has no backend to be
 *  authenticated against, so asking it to sign in would leave nothing on
 *  screen — which is the one job it has. It outranks `unconfigured` for the
 *  same reason: a mode that never reaches the network does not need an origin
 *  to reach it at.
 *
 *  `unconfigured` then beats the rest, because it is not an answer about a
 *  person at all. With no API origin there is nothing to ask, and every other
 *  state would be this app reporting a fact it has not established — "signed
 *  out" most of all, which sends somebody to a sign-in button that cannot
 *  work.
 *
 *  `loading` is kept apart from `out` on purpose. Collapsing them shows the
 *  door to somebody who is already signed in, every time they open the app,
 *  for as long as `GET /users/me` takes.
 */
export function sessionStatus(
    auth: "loading" | "authenticated" | "unauthenticated",
    sample: boolean,
    configured = true,
): SessionStatus {
    if (sample) return "sample";
    if (!configured) return "unconfigured";
    if (auth === "authenticated") return "in";
    if (auth === "loading") return "loading";
    return "out";
}

/** Whether a front door should step aside for somebody who is already in.
 *
 *  The root route is the marketing page and the workspace is elsewhere, so this
 *  is the rule that decides which of the two a visitor is owed.
 *
 *  `direct` is the answer to `GET /users/me`, or null for "not asked". It has
 *  to exist, and the whole bug it fixes lives in this signature: the SDK
 *  answers `unauthenticated` without touching the network whenever the front
 *  token is missing from *this* origin, which is exactly the state somebody is
 *  in for the first load after signing in at the auth site. A door that took
 *  that answer as final showed the marketing page to a signed-in person every
 *  single time. So `unauthenticated` only sticks once the API has said it too.
 *
 *  Sample mode and an unconfigured origin never enter. Neither has a session to
 *  be in: one has no backend at all, the other has nowhere to ask.
 */
export function entersTheApp(
    sdk: "loading" | "authenticated" | "unauthenticated",
    direct: boolean | null,
    sample: boolean,
    configured = true,
): boolean {
    if (sample || !configured) return false;
    if (sdk === "authenticated") return true;
    return direct === true;
}

export function doorFor(badToken: boolean, alreadySent: boolean): "token" | "stalled" | "send" {
    if (badToken) return "token";
    if (alreadySent) return "stalled";
    return "send";
}
