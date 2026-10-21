/** What went wrong, in the API's own words when it said something short
 *  enough to show, else `fallback` and a nudge to try again. */
export function problemOf(error: unknown, fallback: string): string {
    const message = error instanceof Error ? error.message.trim() : "";
    return message && message.length < 200 ? message : fallback + " Try again.";
}
