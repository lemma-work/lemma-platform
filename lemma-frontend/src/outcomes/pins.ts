"use client";

import { useSyncExternalStore } from "react";
import { key } from "@/session/storage";
import type { OpenChat } from "./address";

/** A chat or an app the reader pinned to the top of the sidebar. Its name
 *  is kept with it, so a pin still reads after the chat has fallen off the
 *  recent list the sidebar is built from. A pin is one or the other: a chat
 *  carries `open`, an app carries `app`. */
export interface Pin {
    key: string;
    title: string;
    open?: OpenChat;
    app?: { podId: string; name: string };
}

/** Pins are this reader's, on this browser only. */
const PINS_KEY = key("outcomes-pinned");
const NONE: readonly Pin[] = [];

let held: readonly Pin[] | null = null;
const listeners = new Set<() => void>();

function isPin(value: unknown): value is Pin {
    if (!value || typeof value !== "object") return false;
    const pin = value as Partial<Pin>;
    if (typeof pin.key !== "string" || typeof pin.title !== "string") return false;
    const chat = pin.open !== null && typeof pin.open === "object" && typeof pin.open.podId === "string" && typeof pin.open.id === "string";
    const app = pin.app !== null && typeof pin.app === "object" && typeof pin.app.podId === "string" && typeof pin.app.name === "string";
    return chat || app;
}

function read(): readonly Pin[] {
    if (held) return held;
    try {
        const raw = localStorage.getItem(PINS_KEY);
        const parsed: unknown = raw ? JSON.parse(raw) : [];
        held = Array.isArray(parsed) ? parsed.filter(isPin) : NONE;
    } catch {
        held = NONE;
    }
    return held;
}

function keep(next: readonly Pin[]) {
    held = next;
    try { localStorage.setItem(PINS_KEY, JSON.stringify(next)); } catch { /* kept for this visit only */ }
    for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
    listeners.add(listener);
    return () => listeners.delete(listener);
}

/** The key a chat is pinned under: the same one its sidebar line has. */
export function pinKey(open: OpenChat): string {
    return (open.kind === "shared" ? "g:" : "c:") + open.podId + ":" + open.id;
}

/** The key an app is pinned under. */
export function appPinKey(podId: string, name: string): string {
    return "a:" + podId + ":" + name;
}

/** The reader's pins, oldest first, the way they were added. */
export function usePins(): readonly Pin[] {
    return useSyncExternalStore(subscribe, read, () => NONE);
}

function toggle(pin: Pin) {
    const pins = read();
    keep(pins.some((one) => one.key === pin.key) ? pins.filter((one) => one.key !== pin.key) : [...pins, pin]);
}

/** Pin a chat, or unpin it if it is pinned. */
export function togglePin(open: OpenChat, title: string) {
    toggle({ key: pinKey(open), open, title });
}

/** Pin an app, or unpin it if it is pinned. */
export function toggleAppPin(podId: string, name: string, title: string) {
    toggle({ key: appPinKey(podId, name), app: { podId, name }, title });
}

/** Keep a pinned chat's new name with its pin. */
export function retitlePin(open: OpenChat, title: string) {
    const at = pinKey(open);
    const pins = read();
    if (pins.some((pin) => pin.key === at)) keep(pins.map((pin) => (pin.key === at ? { ...pin, title } : pin)));
}

/** Forget a pin, for a chat that is gone. */
export function unpin(open: OpenChat) {
    const at = pinKey(open);
    keep(read().filter((pin) => pin.key !== at));
}
