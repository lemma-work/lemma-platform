import { useArea } from "../use-area";

/** The more screens' reads and writes. Each part reaches its own slice
 *  through its folder's `hooks.ts` (`useMoreSource().search`, …). */
export function useMoreSource() {
    return useArea("more");
}

/** A react-query key for one of these reads: the cache key first (sample
 *  data and each server keep their own entries, `useCacheKey()`), then
 *  "more", the part, and whatever names the read. */
export function moreKey(cacheKey: string, part: string, ...rest: readonly unknown[]): readonly unknown[] {
    return [cacheKey, "more", part, ...rest];
}
