/**
 * What the pod switcher shows and what it hides. The menu itself is markup; the
 * decisions it makes — how a pod is named, whether the list has earned a search
 * field, what a typed query leaves behind — are here, where they can be read and
 * tested without a DOM.
 */

/**
 * Below this many pods the list is one glance and a search field is another
 * thing to read past. Above it, scanning turns into hunting.
 */
export const POD_FILTER_THRESHOLD = 6;

/** Structural, not the SDK's `Pod`: this module never fetches anything. */
export type SwitcherPod = {
    id: string;
    name: string;
    organization_name?: string;
};

export type SwitcherPodGroup<TPod extends SwitcherPod> = {
    pods: TPod[];
};

export function shouldShowPodFilter(podCount: number): boolean {
    return podCount > POD_FILTER_THRESHOLD;
}

/**
 * Pod names arrive as they were stored — `inbox_crm`, `morning-brief-desk` — and
 * the switcher is one of the few places every pod a person owns is read in a
 * column, where that punctuation is the loudest thing on screen.
 */
export function toPodDisplayLabel(value: string | null | undefined): string {
    const cleaned = (value || '')
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    if (!cleaned) return 'Untitled';

    return cleaned
        .split(' ')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

/**
 * Matches the label as displayed, not the stored name, so typing what you can
 * see always works — `inbox crm` finds `inbox_crm`. The organisation answers the
 * query too: once the same word appears under two of them, it is part of how you
 * name the pod to yourself.
 */
export function matchesPodQuery(pod: SwitcherPod, query: string): boolean {
    const needle = query.trim().toLowerCase();
    if (!needle) return true;

    return toPodDisplayLabel(pod.name).toLowerCase().includes(needle)
        || (pod.organization_name || '').toLowerCase().includes(needle);
}

export function filterSwitcherPods<TPod extends SwitcherPod>(
    pods: TPod[],
    query: string,
): TPod[] {
    return pods.filter((pod) => matchesPodQuery(pod, query));
}

/**
 * An organisation heading over nothing reads as an organisation whose pods you
 * have lost access to, so a group that the query empties is dropped whole.
 */
export function filterSwitcherPodGroups<
    TPod extends SwitcherPod,
    TGroup extends SwitcherPodGroup<TPod>,
>(groups: TGroup[], query: string): TGroup[] {
    return groups
        .map((group) => ({ ...group, pods: filterSwitcherPods(group.pods, query) }))
        .filter((group) => group.pods.length > 0);
}
