/**
 * Is this rejection the server saying "not you"?
 *
 * Used to decide whether retrying could possibly help. By the time a rejection
 * reaches react-query the session layer has already refreshed and retried
 * underneath, so a 401 here is not a transient blip that another attempt might
 * catch -- it is the answer. Retrying it re-enters that refresh-and-retry path
 * and multiplies one unusable session into a sustained request storm.
 *
 * Deliberately only 401. A 403 is an authenticated user meeting a permission or
 * RLS denial, which is equally not worth retrying but is a different thing, and
 * the SDK draws that line in the same place (`http.ts` marks the session
 * unauthenticated on 401 alone).
 *
 * Structural rather than `instanceof`: the error crosses a package boundary, so
 * a duplicated copy of the SDK in the module graph would make `instanceof`
 * quietly false and put the retry storm back.
 */
export function isUnauthorized(error: unknown): boolean {
    if (!error || typeof error !== 'object') {
        return false;
    }
    const candidate = error as { statusCode?: unknown; name?: unknown };
    return candidate.statusCode === 401 || candidate.name === 'UnauthorizedError';
}
