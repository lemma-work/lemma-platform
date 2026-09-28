export type SplitTabs = { main: string; right: string | null };

/** Apps take the whole view: squeezed into the sidebar they reflow into
 *  something cramped and hard to use, so they never open beside anything. */
const isApp = (tab: string) => tab === "apps" || tab.startsWith("app:");

export function layoutForTab(selected: string, expanded: boolean, origin = "conversation"): SplitTabs {
    if (selected === "conversation" || selected === "profile" || selected === origin || expanded || isApp(selected)) return { main: selected, right: null };
    return { main: origin, right: selected };
}

export function clampPaneWidth(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) ? Math.min(65, Math.max(35, value)) : 52;
}
