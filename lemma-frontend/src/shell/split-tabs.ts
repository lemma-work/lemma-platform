export type SplitTabs = { main: string; right: string | null };

export function layoutForTab(selected: string, expanded: boolean, origin = "conversation"): SplitTabs {
    if (selected === "conversation" || selected === "profile" || selected === origin || expanded) return { main: selected, right: null };
    return { main: origin, right: selected };
}

export function clampPaneWidth(value: unknown): number {
    return typeof value === "number" && Number.isFinite(value) ? Math.min(65, Math.max(35, value)) : 52;
}
