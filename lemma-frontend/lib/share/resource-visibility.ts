/**
 * The visibility scale, as pure data.
 *
 * Lives here rather than in the share dialog so the ordering and the
 * normalization can be tested without loading a React/Query component surface.
 * The dialog owns presentation (icons, tone, copy); this file owns what the
 * levels *are* and how a stored string maps onto one.
 */

export type ResourceVisibilityValue = 'PERSONAL' | 'POD' | 'RESTRICTED' | 'PUBLIC';

/** Ordered narrow to wide, so the list reads as one widening scale. */
export const VISIBILITY_VALUES: ResourceVisibilityValue[] = [
    'PERSONAL',
    'POD',
    'RESTRICTED',
    'PUBLIC',
];

/**
 * The only level whose audience includes people who are not in the pod.
 *
 * This is what needs the `/s/…` share wrapper: a `/pod/…` URL drops a non-member
 * on the request-access wall no matter what the resource's own visibility
 * permits. Everything narrower stops at pod membership, so a workspace URL is
 * the right link for it.
 */
const REACHES_OUTSIDE_POD: ResourceVisibilityValue[] = ['PUBLIC'];

/** Spellings accepted from older payloads and bundles, per canonical level. */
const VISIBILITY_ALIASES: Record<string, ResourceVisibilityValue> = {
    PRIVATE: 'PERSONAL',
    OWNER: 'PERSONAL',
    USER: 'PERSONAL',
    ALL: 'POD',
};

/**
 * Canonical string -> level, mirroring the backend's
 * `normalize_resource_visibility`. Unknown values fall back to POD: the safe
 * direction, since POD is narrower than anything it could have meant.
 */
export function normalizeResourceVisibility(value?: string | null): ResourceVisibilityValue {
    const normalized = String(value || 'POD').trim().toUpperCase();
    const alias = VISIBILITY_ALIASES[normalized];
    if (alias) return alias;
    if ((VISIBILITY_VALUES as string[]).includes(normalized)) {
        return normalized as ResourceVisibilityValue;
    }
    return 'POD';
}

/** Whether picking this level hands the resource to people outside the pod. */
export function reachesOutsidePod(value: ResourceVisibilityValue): boolean {
    return REACHES_OUTSIDE_POD.includes(value);
}

/**
 * What the backend stores when nobody picks a level.
 *
 * Apps are the exception: an app is deployed to its own public host and the
 * page itself is served to anonymous browsers, so the backend creates it
 * PUBLIC (its data calls are still authorized one by one, and the SDK's
 * AppGate turns a denial into sign-in or request-access). Everything else
 * starts POD.
 *
 * Callers use this to decide whether a stored value is worth showing: a badge
 * that flags the default is noise, and — worse, after apps flipped — a badge
 * that stays silent on the *narrowed* case tells the reader the opposite of
 * what is true.
 */
export function defaultVisibilityFor(resourceType?: string | null): ResourceVisibilityValue {
    return resourceType === 'app' ? 'PUBLIC' : 'POD';
}
