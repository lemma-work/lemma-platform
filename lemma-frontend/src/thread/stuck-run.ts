/** A run the page believes is going, that nothing is carrying.
 *
 *  A server restarted mid-run drops the stream the conversation was reading.
 *  The session's own reconnect loop covers most of that, but it ends when the
 *  conversation cannot be re-read while the server is down, and then the page
 *  says "is working…" for a run it has no line to -- for ever. After
 *  `STUCK_AFTER_MS` of a running status with no stream the conversation offers
 *  to reload itself instead. Its own module so the rule is testable: the test
 *  runner cannot load `.tsx`.
 */

export const STUCK_AFTER_MS = 20_000;

/** Delay before re-reading a conversation whose stream failed in transport:
 *  long enough for a restarting server to be listening again. */
export const TRANSPORT_RELOAD_MS = 3_000;

export function runLooksStuck(
    state: "idle" | "running" | "waiting" | "failed",
    isStreaming: boolean,
    quietForMs: number,
): boolean {
    return state === "running" && !isStreaming && quietForMs >= STUCK_AFTER_MS;
}
