/**
 * Telling "the server said no" apart from "the server did not answer".
 *
 * A session check that cannot reach the API used to end in `unauthenticated`,
 * so a server restarting under the page -- a Desktop install saving its
 * settings does exactly that -- sent a signed-in person to sign in. Only a 401
 * means that. Everything here decides which failures are the other kind, and
 * how long to wait before asking again.
 */

/** Statuses that are the path to the server failing rather than the server
 *  answering: a gateway with nothing behind it, an overloaded or restarting
 *  process, a rate limit, a timeout. */
export function isUnreachableStatus(status: number): boolean {
  return status >= 500 || status === 429 || status === 408 || status === 0;
}

/**
 * What a failed session refresh or `/users/me` call means for the session.
 *
 * `unreachable` for a request that never got an answer (fetch rejects with a
 * `TypeError`, this SDK's client with a `NetworkError`) or got one of the
 * statuses above; `absent` for everything else,
 * including a 401. supertokens-website rethrows the refresh `Response` itself,
 * so a status is read structurally rather than with `instanceof Response`.
 */
export function refreshFailureKind(error: unknown): "unreachable" | "absent" {
  if (!error || typeof error !== "object") return "absent";
  const candidate = error as { status?: unknown; statusCode?: unknown; name?: unknown };
  // `status` on a fetch Response, `statusCode` on this SDK's own ApiError.
  const status = typeof candidate.status === "number" ? candidate.status : candidate.statusCode;
  if (typeof status === "number") return isUnreachableStatus(status) ? "unreachable" : "absent";
  // A fetch that failed in transport, raw or as this SDK's NetworkError.
  return error instanceof TypeError || candidate.name === "TypeError" || candidate.name === "NetworkError"
    ? "unreachable"
    : "absent";
}

/**
 * How long to wait before the `attempt`-th retry (0-based): 1 s, 2 s, 4 s ...
 * up to 30 s. Short at first because the common cause is a restart that takes
 * a few seconds; capped because an outage should not be hammered.
 */
export function reconnectDelay(attempt: number): number {
  const exponent = Math.max(0, Math.min(Math.floor(attempt), 5));
  return Math.min(1_000 * 2 ** exponent, 30_000);
}

/**
 * Whether the API answers at all, from its liveness probe.
 *
 * Deliberately not a session check: a refresh on every retry would spend the
 * refresh breaker's budget (four a minute) on an outage and trip it, which
 * signs the person out -- the very thing being avoided. Any answer that is not
 * a gateway or server failure counts as up, a 404 included: a deployment that
 * does not route the probe is still answering.
 */
export async function probeReachable(
  apiUrl: string,
  fetchImpl: typeof fetch = fetch,
): Promise<boolean> {
  try {
    const response = await fetchImpl(`${apiUrl.replace(/\/$/, "")}/health/live`, {
      method: "GET",
      credentials: "omit",
      cache: "no-store",
    });
    return !isUnreachableStatus(response.status);
  } catch {
    return false;
  }
}
