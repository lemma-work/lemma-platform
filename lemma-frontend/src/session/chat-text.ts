import type { KeyValueStore } from "./storage";

export const CHAT_TEXT_SIZES = [
    { value: "small", label: "Small", pixels: 14 },
    { value: "default", label: "Default", pixels: 15 },
    { value: "large", label: "Large", pixels: 17 },
] as const;
export type ChatTextSize = typeof CHAT_TEXT_SIZES[number]["value"];

export function parseChatTextSize(value: string | null): ChatTextSize {
    return value === "small" || value === "large" ? value : "default";
}

export function readChatTextSize(store: Pick<KeyValueStore, "getItem">, storageKey: string): ChatTextSize {
    try { return parseChatTextSize(store.getItem(storageKey)); }
    catch { return "default"; }
}
