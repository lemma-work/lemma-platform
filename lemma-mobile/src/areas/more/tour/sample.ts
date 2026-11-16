import type { TourSource } from "./source";

/** Ada, two days in. The tour never offers itself in sample data, as on the
 *  web; it is there under Help on the account screen. */
export function tourSample(): TourSource {
    return {
        account: async () => ({ id: "sample-ada", createdAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000).toISOString() }),
    };
}
