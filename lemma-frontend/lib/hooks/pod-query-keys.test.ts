/**
 * The sidebar has to refresh when a pod changes.
 *
 * It reads `/organizations/navigation` now instead of a pod list per
 * organization, and that read is only as fresh as the invalidations that reach
 * it. Ten call sites across the app invalidate `['pods']` after creating,
 * importing, renaming or deleting one, and none of them know this query exists
 * — so the guarantee rests entirely on the key prefix, which is what these
 * assert against React Query's real matcher rather than by inspection.
 */

import { QueryClient, hashKey } from '@tanstack/react-query';
import { describe, expect, it } from 'vitest';

import {
    navigationQueryKey,
    organizationHomeQueryKey,
    podListQueryKey,
} from './pod-query-keys';

const ORG_ID = '019ff443-3c4a-7287-84a7-d5356cc4422d';
const POD_ID = '019ff443-3d83-7492-aa84-3b2b1b0575f6';

function clientWithSeededQueries() {
    const client = new QueryClient();
    for (const key of [
        navigationQueryKey(),
        organizationHomeQueryKey(ORG_ID),
        podListQueryKey(ORG_ID),
        ['pods', POD_ID],
    ]) {
        client.setQueryData([...key], { seeded: true });
    }
    return client;
}

/** Which seeded queries a `['pods']` invalidation would actually refetch. */
function invalidatedKeys(client: QueryClient) {
    return client
        .getQueryCache()
        .findAll({ queryKey: ['pods'] })
        .map((query) => query.queryHash);
}

describe('pod query keys', () => {
    it('puts the sidebar read where an existing pods invalidation finds it', () => {
        const client = clientWithSeededQueries();

        const matched = invalidatedKeys(client);

        expect(matched).toContain(hashKey([...navigationQueryKey()]));
    });

    it('puts the organization home read there too', () => {
        const client = clientWithSeededQueries();

        expect(invalidatedKeys(client)).toContain(
            hashKey([...organizationHomeQueryKey(ORG_ID)]),
        );
    });

    it('reaches every pod read from one invalidation', () => {
        const client = clientWithSeededQueries();

        // Four seeded queries, all under the same prefix: the point of sharing
        // it is that a caller invalidating `['pods']` needs to know about none
        // of them individually.
        expect(invalidatedKeys(client)).toHaveLength(4);
    });

    it('does not collide with a per-organization or per-pod key', () => {
        // The object part is what keeps these apart; an id is always a string,
        // so no organization or pod can ever be mistaken for a scoped read.
        expect(navigationQueryKey()).not.toEqual(podListQueryKey(ORG_ID));
        expect(organizationHomeQueryKey(ORG_ID)).not.toEqual(podListQueryKey(ORG_ID));
        expect(organizationHomeQueryKey(ORG_ID)).not.toEqual(
            organizationHomeQueryKey(POD_ID),
        );
    });

    it('gives each organization its own home entry', () => {
        const other = '019ffa02-42f6-735b-8bf6-8327c4866ab1';
        expect(organizationHomeQueryKey(ORG_ID)).not.toEqual(
            organizationHomeQueryKey(other),
        );
    });
});
