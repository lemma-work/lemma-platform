import { useArea } from "../use-area";

/** The settings screens' reads and writes. Each part reaches its own slice
 *  through its folder's `hooks.ts` (`useAdminSource().people`, …). */
export function useAdminSource() {
    return useArea("admin");
}

/** A react-query key for a settings read: the cache key first (sample data
 *  and each server keep their own entries), then "admin", the part, and
 *  whatever names the read. */
export function adminKey(cacheKey: string, part: string, ...rest: readonly unknown[]): readonly unknown[] {
    return [cacheKey, "admin", part, ...rest];
}
