/**
 * Carrying an install intent across a signup.
 *
 * Someone who lands on `/import/github/<owner>/<repo>`, picks a destination and
 * presses Continue is sent off to sign up. They come back to the same URL — and
 * used to come back to a page that had forgotten they asked for anything, which
 * is the point in the funnel where a first-time visitor gives up. These two
 * functions are the whole of the memory: one stamps the intent on the way out,
 * the other reads and clears it on the way back.
 */

/** Marks a return leg: this visitor clicked Continue, then went off to sign up. */
export const RESUME_PARAM = 'resume';

export type ImportDestination = 'new' | 'existing';

/** The URL the auth service should return the visitor to. */
export function buildImportReturnUrl(
    href: string,
    destination: ImportDestination,
): string {
    const url = new URL(href);
    url.searchParams.set('destination', destination);
    url.searchParams.set(RESUME_PARAM, '1');
    return url.toString();
}

export function hasResumeMarker(href: string): boolean {
    return new URL(href).searchParams.has(RESUME_PARAM);
}

/**
 * The same URL without the marker. Cleared *before* the installer opens, so a
 * refresh of the plan screen is a refresh and not a second install.
 */
export function withoutResumeMarker(href: string): string {
    const url = new URL(href);
    url.searchParams.delete(RESUME_PARAM);
    return url.toString();
}
