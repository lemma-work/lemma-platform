/** Small words the settings screens share. Pure, so `tests/` can hold them. */

/** "3 people", "1 person". */
export function count(n: number, one: string, many: string): string {
    return `${n} ${n === 1 ? one : many}`;
}

/** "Researcher, Writer +2". */
export function firstFew(names: readonly string[], shown = 2): string {
    const head = names.slice(0, shown).join(", ");
    return names.length > shown ? `${head} +${names.length - shown}` : head;
}

/** What a failure says, as a person would: the server's own words when it
 *  sent some, else the fallback. A transport's stock words ("Network request
 *  failed") and a bare HTTP reason ("Not Found") say nothing a person can
 *  act on, so they get the fallback too. */
export function problemOf(error: unknown, fallback: string): string {
    const said = error instanceof Error ? error.message.trim() : "";
    if (!said || STOCK.test(said)) return fallback;
    return said;
}

const STOCK = /^(Request failed|Failed to fetch|Network request failed|HTTP \d{3}|(Bad Request|Unauthorized|Forbidden|Not Found|Method Not Allowed|Conflict|Unprocessable (Entity|Content)|Too Many Requests|Internal Server Error|Bad Gateway|Service Unavailable|Gateway Timeout)\.?$)/i;

/** The HTTP status a failure carries: the SDK's `statusCode`, or a plain
 *  `status`. Null for a failure that never reached the server. */
export function statusOf(error: unknown): number | null {
    const held = error as { statusCode?: unknown; status?: unknown } | null;
    const status = held?.statusCode ?? held?.status;
    return typeof status === "number" ? status : null;
}

/** Whether the server refused this person, rather than failing. */
export function isForbidden(error: unknown): boolean {
    if (statusOf(error) === 403) return true;
    return error instanceof Error && /\b403\b|forbidden/i.test(error.message);
}

/** Whether the server has no such thing, rather than failing. */
export function isMissing(error: unknown): boolean {
    if (statusOf(error) === 404) return true;
    return error instanceof Error && /\b404\b|not found/i.test(error.message);
}
