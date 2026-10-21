import { hashKey, type QueryClient, type QueryKey } from "@tanstack/react-query";

/** Live changes to a query that is still being read for the first time.
 *
 *  The socket is subscribed before the timeline's first REST read returns,
 *  and a line written in between can arrive on the socket first: applied to
 *  nothing, it would be dropped, and the read -- begun before the line was
 *  written -- does not have it either. So a change to a query that exists
 *  but holds no data yet is held, in order, and applied the moment the read
 *  lands. Every change is an upsert by id, so one the read already has
 *  changes nothing. A query nobody has asked for is not held for: whoever
 *  asks reads it fresh. */
export class HeldUpdates {
    private readonly held = new Map<string, { key: QueryKey; updates: ((was: unknown) => unknown)[] }>();

    /** Apply `update` to what `key` holds, or hold it until the first read. */
    apply<T>(cache: QueryClient, key: QueryKey, update: (was: T) => T): void {
        const state = cache.getQueryState(key);
        if (state && state.data === undefined) {
            const hash = hashKey(key);
            const entry = this.held.get(hash) ?? { key, updates: [] };
            entry.updates.push(update as (was: unknown) => unknown);
            this.held.set(hash, entry);
            return;
        }
        cache.setQueryData<T>(key, (was) => (was === undefined ? was : update(was)));
    }

    /** How many changes wait on reads now. */
    get size(): number {
        let count = 0;
        for (const entry of this.held.values()) count += entry.updates.length;
        return count;
    }

    /** Apply what was held for every query whose read has landed. */
    flush(cache: QueryClient): void {
        for (const [hash, entry] of [...this.held]) {
            if (cache.getQueryData(entry.key) === undefined) continue;
            this.held.delete(hash);
            cache.setQueryData<unknown>(entry.key, (was: unknown) => entry.updates.reduce((data, update) => (data === undefined ? data : update(data)), was));
        }
    }

    /** Flush whenever a held query's read lands; returns how to stop. */
    watch(cache: QueryClient): () => void {
        return cache.getQueryCache().subscribe((event) => {
            if (event.type === "updated" && this.held.has(event.query.queryHash)) this.flush(cache);
        });
    }

    clear(): void {
        this.held.clear();
    }
}
