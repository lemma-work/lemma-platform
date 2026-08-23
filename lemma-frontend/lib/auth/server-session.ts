import "server-only";
import { cookies } from "next/headers";
import { sessionCookiePresent } from "./session-cookie";

/**
 * `sessionCookiePresent`, over the incoming request.
 *
 * Answered on the server, before anything renders, so the root page can pick
 * between the marketing page and the app shell in the *first* HTML rather than
 * after a `/users/me` round trip has resolved on the client. What the answer
 * does and does not mean is documented on `sessionCookiePresent` — it is a hint
 * about which placeholder to paint, not a claim about who is asking.
 *
 * Calling this opts the route into dynamic rendering, which is not a cost so
 * much as the point: a response that depends on a cookie must not be built once
 * and handed to everyone. Next marks such a response `private` for the same
 * reason, so nothing in front can serve one visitor's shell to another.
 *
 * When the session cookie is scoped to the API's own host rather than shared
 * across the parent domain, the frontend server never sees it and this is
 * always false. That is a return to the previous behaviour rather than a new
 * failure: the marketing page renders while the auth check flies, as it did
 * before this existed.
 */
export async function hasSessionCookie(): Promise<boolean> {
    const store = await cookies();
    return sessionCookiePresent((name) => store.get(name)?.value);
}
