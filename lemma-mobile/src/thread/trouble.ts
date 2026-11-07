/** What a failed send says, in the platform's words when it gave some: the
 *  web's `data/trouble.ts` (`saidAboutSending`) and `shell/connection.ts`
 *  (`isTransportFailure`). Pure. */

function platformSaid(problem: unknown): string {
    if (!problem || typeof problem !== "object") return "";
    const body = (problem as { rawResponse?: unknown }).rawResponse;
    if (!body || typeof body !== "object") return "";
    const said = (body as { message?: unknown }).message;
    return typeof said === "string" ? said.trim() : "";
}

function fromTheWire(problem: unknown): boolean {
    if (!problem || typeof problem !== "object") return false;
    if (typeof (problem as { statusCode?: unknown }).statusCode === "number") return true;
    if (typeof (problem as { status?: unknown }).status === "number") return true;
    return (problem as { name?: unknown }).name === "NetworkError";
}

/** The platform's own sentence, else this app's error's, else `fallback` --
 *  never a status line from the wire, which is written for logs. */
export function saidAbout(problem: unknown, fallback: string): string {
    const said = platformSaid(problem);
    if (said) return said;
    if (fromTheWire(problem)) return fallback;
    const own = problem instanceof Error ? problem.message.trim() : "";
    return own || fallback;
}

export const UNREACHABLE = "Lemma couldn’t be reached — it may be restarting. Try again in a few seconds.";

export function unreachable(problem: unknown): boolean {
    if (!problem || typeof problem !== "object") return false;
    if ((problem as { name?: unknown }).name === "NetworkError") return true;
    return problem instanceof TypeError && /fetch|network|load failed/i.test(problem.message);
}

export function saidAboutSending(problem: unknown, fallback: string): string {
    return unreachable(problem) ? UNREACHABLE : saidAbout(problem, fallback);
}

/** The stream failed in transport -- a server restarting under it, most
 *  often -- rather than the run failing. */
export function isTransportFailure(error: unknown): boolean {
    if (!error || typeof error !== "object") return false;
    const candidate = error as { statusCode?: unknown; status?: unknown; name?: unknown };
    const status = candidate.statusCode ?? candidate.status;
    if (typeof status === "number") return status === 502 || status === 503 || status === 504;
    return error instanceof TypeError || candidate.name === "TypeError" || candidate.name === "NetworkError";
}
