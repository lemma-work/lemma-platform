/**
 * Query keys for everything that reads a list of pods.
 *
 * All of them sit under the `['pods']` prefix deliberately. Ten call sites
 * across the app already invalidate `['pods']` after a pod is created,
 * imported, renamed or deleted, and React Query matches by prefix — so sharing
 * the prefix means every one of those refreshes the sidebar without being
 * touched, and a future one cannot forget to.
 *
 * Kept in its own module, free of React and of the SDK client, so the keys can
 * be asserted against React Query's own matcher without loading a client
 * component.
 */

export const POD_QUERY_ROOT = 'pods' as const;

/** Every organization with its pods — the sidebar's single read. */
export const navigationQueryKey = () =>
    [POD_QUERY_ROOT, { scope: 'navigation' }] as const;

/** One organization's pods with their apps, agents and the caller's roles. */
export const organizationHomeQueryKey = (orgId?: string) =>
    [POD_QUERY_ROOT, { scope: 'home', orgId }] as const;

/**
 * The object part is what keeps these from colliding with `['pods', podId]` and
 * `['pods', orgId]`, which are always uuid strings.
 */
export const podListQueryKey = (orgId?: string) => [POD_QUERY_ROOT, orgId] as const;
