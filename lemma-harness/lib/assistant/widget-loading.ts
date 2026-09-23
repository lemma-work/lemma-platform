export interface WidgetLoadingState {
    embedTokenLoading: boolean;
    iframeSrc: string | null;
    loadedIframeSrc: string | null;
}

export function isWidgetLoading({
    embedTokenLoading,
    iframeSrc,
    loadedIframeSrc,
}: WidgetLoadingState): boolean {
    return embedTokenLoading || Boolean(iframeSrc && loadedIframeSrc !== iframeSrc);
}

export function normalizeWidgetLoadingMessages(messages: string[]): string[] {
    return messages.map((message) => message.trim()).filter(Boolean).slice(0, 4);
}

export function selectWidgetLoadingMessage(messages: string[], index: number): string {
    if (messages.length === 0) return "Loading widget";
    return messages[index % messages.length] || "Loading widget";
}
