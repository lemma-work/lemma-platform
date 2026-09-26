/** Whether the API is answering, decided from its liveness probe.
 *
 *  Its own module so the decisions are testable: the test runner strips types
 *  from `.ts` and cannot load `.tsx`. `reconnect-strip.tsx` only renders what
 *  these say.
 *
 *  Nothing polls while things work. A query that fails the way a restarting
 *  server makes it fail -- no answer, or a 502/503/504 from the gateway --
 *  starts a probe; only a probe that also fails shows the strip, so one flaky
 *  request does not. From then it probes on a short backoff until the server
 *  answers, and that answer is the recovery.
 */

export type Connection = { phase: "up" | "checking" | "down"; failures: number };

export const CONNECTED: Connection = { phase: "up", failures: 0 };

/** Did the liveness probe get an answer from the server itself? Anything but a
 *  gateway or server failure counts, a 404 included: a deployment that does
 *  not route the probe is still answering. */
export function healthAnswered(status: number): boolean {
    return status > 0 && status < 500;
}

/** A failure that says the server did not answer, rather than what it said. */
export function isTransportFailure(error: unknown): boolean {
    if (!error || typeof error !== "object") return false;
    const candidate = error as { statusCode?: unknown; status?: unknown; name?: unknown };
    const status = candidate.statusCode ?? candidate.status;
    if (typeof status === "number") return status === 502 || status === 503 || status === 504;
    return error instanceof TypeError || candidate.name === "TypeError" || candidate.name === "NetworkError";
}

/** Something failed in transport: go and look, unless already looking. */
export function suspect(connection: Connection): Connection {
    return connection.phase === "up" ? { phase: "checking", failures: 0 } : connection;
}

/** The next state after a probe, and whether that probe was the recovery the
 *  app should refetch everything for. A recovery only counts after the strip
 *  was shown: a single request that failed while the server was fine changed
 *  nothing anyone needs refetched. */
export function afterProbe(connection: Connection, answered: boolean): { next: Connection; recovered: boolean } {
    if (answered) return { next: CONNECTED, recovered: connection.phase === "down" };
    return { next: { phase: "down", failures: connection.failures + 1 }, recovered: false };
}

/** How long to wait before the next probe: at once when first suspecting,
 *  then 1 s, 2 s, 4 s, capped at 5 s -- a restart takes a few seconds and the
 *  strip should go away soon after the server is back. */
export function probeDelay(connection: Connection): number {
    if (connection.phase === "checking") return 0;
    return Math.min(1_000 * 2 ** Math.max(0, connection.failures - 1), 5_000);
}

/** The event the strip announces a recovery with, for views that hold state
 *  react-query does not -- a conversation's live run, for one. */
export const RECONNECTED_EVENT = "lemma:reconnected";
