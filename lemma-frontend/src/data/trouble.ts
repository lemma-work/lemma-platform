/** What went wrong, in a sentence worth putting on a screen.
 *
 *  The platform refuses in full sentences — `{ message, code, request_id }`,
 *  where the message is written for whoever is standing in front of it and
 *  usually says what to do instead:
 *
 *      This pod cannot be opened to everyone while its organization is not
 *      public. Ask an organization owner to open the organization first…
 *
 *  That is worth far more than anything this app could say about a 403, so it
 *  wins wherever there is one. But the SDK guarantees a `message` on every
 *  error, and where the body carried none it fills one in: the status's own
 *  name — "Forbidden", "Internal Server Error" — or, failing that, a string
 *  beginning "Generic Error:" with the entire raw body pasted into it. Printed
 *  in the UI, the first says nothing and the second is a JSON dump. So
 *  `problem.message` alone is not safe to show, which is the whole reason this
 *  file exists rather than the idiom being written out at each call.
 *
 *  Three sources, in order: what the platform said, then what the caller would
 *  have said, then — for a failure thrown by this app or by the sample source,
 *  which are already sentences — its own message.
 */

/** The platform's own words, if it sent any. */
function platformSaid(problem: unknown): string {
    if (!problem || typeof problem !== "object") return "";
    const body = (problem as { rawResponse?: unknown }).rawResponse;
    if (!body || typeof body !== "object") return "";
    const said = (body as { message?: unknown }).message;
    return typeof said === "string" ? said.trim() : "";
}

/** Whether this came off the wire — in which case its message is the SDK's
 *  stand-in rather than anybody's words, and the caller's sentence is better.
 *  A transport failure counts: "Network request failed: …" is a log line. */
function fromTheWire(problem: unknown): boolean {
    if (!problem || typeof problem !== "object") return false;
    if (typeof (problem as { statusCode?: unknown }).statusCode === "number") return true;
    if (typeof (problem as { status?: unknown }).status === "number") return true;
    return (problem as { name?: unknown }).name === "NetworkError";
}

export function saidAbout(problem: unknown, fallback: string): string {
    const said = platformSaid(problem);
    if (said) return said;
    if (fromTheWire(problem)) return fallback;
    const own = problem instanceof Error ? problem.message.trim() : "";
    return own || fallback;
}

/** The platform's code for a refusal — `POD_ACCESS_DENIED` and the like.
 *
 *  Not for printing. It is here for the places that have something better to
 *  do about one particular refusal than repeat it. */
export function codeOf(problem: unknown): string {
    if (!problem || typeof problem !== "object") return "";
    const code = (problem as { code?: unknown }).code;
    return typeof code === "string" ? code : "";
}
