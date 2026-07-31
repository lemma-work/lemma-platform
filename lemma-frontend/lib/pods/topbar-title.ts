/**
 * Title ownership across the pod shell's four bands.
 *
 * The shell stacks a workspace tab strip, a context bar, and the page itself,
 * with the sidebar alongside — and any of them can carry the resource name.
 * Left to their own devices they all do, and the name ends up printed three or
 * four times within a couple of hundred pixels. These rules decide who prints
 * it, and live here — outside the component tree — so they can be pinned down
 * by tests.
 *
 * The bands are deliberately set at different altitudes so that the
 * appearances that DO remain read as different kinds of statement rather than
 * as three headings:
 *
 *   context bar   16px/600   the title — what you are acting on
 *   workspace tab 14px/400   the open document — what you can switch to
 *   sidebar row   12px/400   the location — where this lives
 *
 * The sidebar is never a candidate to cede: a nav list that does not mark its
 * active row is broken navigation, not clean design.
 */

export type PodTopbarTitleOwner = 'bar' | 'page' | 'tab';

/**
 * Whether the context bar should print the name.
 *
 * Two routes off the default:
 *
 * - `'page'` — the route renders its own heading, so the bar cedes for as long
 *   as that heading is actually on screen. Scroll it away, or switch to a tab
 *   that has none, and the bar takes it back.
 * - `'tab'` — the workspace tab strip is already showing this resource's name
 *   directly above the bar, so the bar drops to just the back link, mode
 *   switch, and actions. The moment the strip is not showing it — a compact
 *   viewport that hides the strip, or a route with no tab of its own — the bar
 *   takes the title back, because otherwise nothing on screen names the thing.
 *
 * Every other route keeps the bar title on unconditionally.
 */
export function barOwnsTitle(
    titleOwner: PodTopbarTitleOwner | undefined,
    heroTitleVisible: boolean,
    tabStripNamesResource = false,
): boolean {
    if (titleOwner === 'page') return !heroTitleVisible;
    if (titleOwner === 'tab') return !tabStripNamesResource;
    return true;
}

/**
 * The workspace tab's label.
 *
 * Deliberately not "whatever the context bar is showing": under
 * `titleOwner: 'page'` the bar title comes and goes with scroll, and a tab that
 * followed it would flicker. A route states `tabTitle` when it wants the tab to
 * read differently from the bar; otherwise the tab takes the title, but only
 * when that title is a plain string — titles are `ReactNode`, and some routes
 * pass a whole interactive element (a folder picker, say) that has no sensible
 * text form. Returns '' when there is nothing usable, leaving the caller to fall
 * back to its route-derived label.
 */
export function resolveTabLabel(
    tabTitle: string | undefined,
    title: unknown,
): string {
    return (tabTitle ?? (typeof title === 'string' ? title : '')).trim();
}
