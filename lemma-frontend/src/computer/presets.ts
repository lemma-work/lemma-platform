/** The screen's sizes, and which one this person last chose.
 *
 *  The display used to follow the pane: every divider drag became a remote
 *  resize, and the picture went away for half a second while the machine
 *  agreed the new geometry. Now the pane rescales the picture locally, and the
 *  display only changes when somebody picks one of these — a layout a site has
 *  a reason to serve. The same four are in the relay
 *  (`sandbox_runtime/browser_relay/display.py`), which refuses any other.
 */

export type PresetName = "desktop" | "wide" | "tablet" | "phone";

export interface Preset {
    name: PresetName;
    label: string;
    width: number;
    height: number;
}

export const PRESETS: readonly Preset[] = [
    { name: "desktop", label: "Desktop", width: 1280, height: 800 },
    { name: "wide", label: "Wide", width: 1440, height: 900 },
    { name: "tablet", label: "Tablet", width: 820, height: 1180 },
    { name: "phone", label: "Phone", width: 390, height: 844 },
];

export const DEFAULT_PRESET: PresetName = "desktop";

export function presetNamed(name: string | null | undefined): Preset {
    return PRESETS.find((preset) => preset.name === name) ?? PRESETS[0]!;
}

/** Which preset a `WxH` size is, if it is one. */
export function presetForSize(size: string | null | undefined): Preset | null {
    if (!size) return null;
    return PRESETS.find((preset) => `${preset.width}x${preset.height}` === size) ?? null;
}

const STORED = "lemma.screen.preset";

/** The preset this person chose last, in this browser. A convenience: when
 *  storage is unavailable — a private window, blocked site data — it is the
 *  default and nothing breaks. */
export function storedPreset(): PresetName {
    try {
        return presetNamed(globalThis.localStorage?.getItem(STORED)).name;
    } catch {
        return DEFAULT_PRESET;
    }
}

export function storePreset(name: PresetName): void {
    try {
        globalThis.localStorage?.setItem(STORED, name);
    } catch {
        /* storage refused; the choice still applies to this session */
    }
}

/** A name for one mounted pane, carried on its socket and on its resize so the
 *  machine can tell which viewer holds the screen's size. Random and
 *  short-lived: it names a pane, not a person — the API prefixes the person. */
export function newViewerId(): string {
    const bytes = new Uint8Array(9);
    globalThis.crypto.getRandomValues(bytes);
    return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
}
