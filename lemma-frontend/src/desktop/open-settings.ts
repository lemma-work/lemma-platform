import { useEffect, useRef } from "react";
import type { SettingsSection } from "@/settings/settings-modal";
import { settingsFromQuery } from "@/site/legacy-address";

/** Opening Settings from outside the page.
 *
 *  The desktop menu and tray live in the shell, which cannot reach into React.
 *  What it can do is evaluate one line in the workspace webview:
 *
 *      window.dispatchEvent(new CustomEvent("lemma:open-settings", { detail: { section: "models" } }))
 *
 *  and the authenticated shell opens the Settings modal at that section. A
 *  section this build does not have — a newer shell against an older frontend
 *  pack — opens Settings at its first page rather than doing nothing. Nothing
 *  else rides on this event, so a page can raise it too without any extra
 *  trust: it only ever opens a dialog the user could open themselves. */
export const OPEN_SETTINGS_EVENT = "lemma:open-settings";

/** The section an event asks for, or "account" when it names none we know. */
export function requestedSection(event: Event): SettingsSection {
    const detail = (event as CustomEvent<unknown>).detail;
    const section = typeof detail === "string"
        ? detail
        : detail && typeof detail === "object" ? (detail as { section?: unknown }).section : null;
    return settingsFromQuery(typeof section === "string" ? section : null) ?? "account";
}

/** Which part of the section to bring into view, when the event names one --
 *  "Set up on this Mac" beside a connector opens Advanced at that connector's
 *  form. A short word, never markup: it is matched against a fixed list. */
export function requestedFocus(event: Event): string | null {
    const detail = (event as CustomEvent<unknown>).detail;
    const focus = detail && typeof detail === "object" ? (detail as { focus?: unknown }).focus : null;
    return typeof focus === "string" && /^[a-z-]{1,32}$/.test(focus) ? focus : null;
}

/** Raise it from this page. */
export function openSettings(section: SettingsSection, focus?: string): void {
    window.dispatchEvent(new CustomEvent(OPEN_SETTINGS_EVENT, { detail: focus ? { section, focus } : { section } }));
}

/** Listen for it for as long as the caller is mounted. */
export function useOpenSettingsEvent(onOpen: (section: SettingsSection, focus: string | null) => void): void {
    const handler = useRef(onOpen);
    useEffect(() => {
        handler.current = onOpen;
    }, [onOpen]);
    useEffect(() => {
        const listen = (event: Event) => handler.current(requestedSection(event), requestedFocus(event));
        window.addEventListener(OPEN_SETTINGS_EVENT, listen);
        return () => window.removeEventListener(OPEN_SETTINGS_EVENT, listen);
    }, []);
}
