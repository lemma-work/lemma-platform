export type SplitTabs = { main: string; right: string | null };

/** Apps take the whole view: squeezed into the sidebar they reflow into
 *  something cramped and hard to use, so they never open beside anything. */
const isApp = (tab: string) => tab === "apps" || tab.startsWith("app:");

export function layoutForTab(selected: string, expanded: boolean, origin = "conversation"): SplitTabs {
    /* A space's own lists are places, not things opened from somewhere: they
       always take the stage. What is opened from them sits beside them. */
    if (selected.startsWith("space:") || selected.startsWith("bot:") || selected.startsWith("run:") || selected.startsWith("workflow:") || selected === "conversation" || selected === "profile" || selected === origin || expanded || isApp(selected)) return { main: selected, right: null };
    return { main: origin, right: selected };
}

/** Where a phone's resource sheet comes to rest when a drag on its bar lets go.
 *
 *  Two positions, and no half: a half-height sheet over a half-height
 *  conversation is the letterbox this replaced. A short, slow drag is a
 *  change of mind and goes back; a long one, or a flick, goes where it was
 *  heading. `travel` is how far down the bar moved, `speed` px/ms. */
export function settleSheet(peeked: boolean, travel: number, speed: number): boolean {
    if (travel > 64 || speed > 0.5) return true;
    if (travel < -64 || speed < -0.5) return false;
    return peeked;
}

export function clampPaneWidth(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) ? Math.min(65, Math.max(35, value)) : 52;
}
