/** Create something, or replace what already holds its name.
 *
 *  The library's promise for a second upload of the same name: it is a new
 *  version of that document, not a second document and not an error. The
 *  server refuses the create with a 409, so the replacement is an update of
 *  the file already there. Anything else the create throws is the caller's.
 */
export async function createOrReplace<T>(
    create: () => Promise<T>,
    replace: () => Promise<T>,
): Promise<T> {
    try {
        return await create();
    } catch (failure) {
        if (!isConflict(failure)) throw failure;
        return await replace();
    }
}

/** The SDK's ApiError carries the HTTP status as `statusCode`. */
function isConflict(failure: unknown): boolean {
    return typeof failure === "object" && failure !== null
        && (failure as { statusCode?: unknown }).statusCode === 409;
}
