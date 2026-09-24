import { SunIcon, MoonIcon, SystemIcon } from "@/ui/icons";
import { useEffect, useState } from "react";
import { key } from "./storage";
import { CHAT_TEXT_SIZES, readChatTextSize, type ChatTextSize } from "./chat-text";

export type Theme = "system" | "light" | "dark";
export type Accent = "violet" | "coral" | "forest" | "ocean" | "amber" | "plum" | "slate" | "ink";
export type Corners = "sharp" | "soft" | "round";

const THEME_KEY = key("theme");
const ACCENT_KEY = key("accent");
const CORNERS_KEY = key("corners");
const CHAT_TEXT_KEY = key("chat-text-size");

function readTextSize(): ChatTextSize {
    try { return readChatTextSize(localStorage, CHAT_TEXT_KEY); }
    catch { return "default"; }
}

export const ACCENTS: { value: Accent; label: string; swatch: string }[] = [
    { value: "violet", label: "Violet", swatch: "#6b4fe0" },
    { value: "coral", label: "Coral", swatch: "#dd5238" },
    { value: "forest", label: "Forest", swatch: "#2d7a58" },
    { value: "ocean", label: "Ocean", swatch: "#1f6f9e" },
    { value: "amber", label: "Amber", swatch: "#b06f12" },
    { value: "plum", label: "Plum", swatch: "#9c3f6d" },
    { value: "slate", label: "Slate", swatch: "#4a5568" },
    { value: "ink", label: "Ink", swatch: "#26262c" },
];

export const CORNERS: { value: Corners; label: string }[] = [
    { value: "sharp", label: "Sharp" },
    { value: "soft", label: "Soft" },
    { value: "round", label: "Round" },
];

function readTheme(): Theme {
    try {
        const stored = localStorage.getItem(THEME_KEY);
        return stored === "light" || stored === "dark" ? stored : "system";
    } catch {
        return "system";
    }
}

function readAccent(): Accent {
    try {
        const stored = localStorage.getItem(ACCENT_KEY);
        return ACCENTS.some((entry) => entry.value === stored) ? (stored as Accent) : "violet";
    } catch {
        return "violet";
    }
}

function readCorners(): Corners {
    try {
        const stored = localStorage.getItem(CORNERS_KEY);
        return CORNERS.some((entry) => entry.value === stored) ? (stored as Corners) : "soft";
    } catch {
        return "soft";
    }
}

/** System is the absence of a stamp, not a third value on the element — the
 *  stylesheet resolves it through `prefers-color-scheme`, so clearing the
 *  attribute is what hands control back to the OS. */
function apply(theme: Theme, accent: Accent, corners: Corners, textSize: ChatTextSize): void {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
    root.setAttribute("data-accent", accent);
    root.setAttribute("data-corners", corners);
    root.setAttribute("data-chat-text-size", textSize);
}

export function initTheme(): void {
    apply(readTheme(), readAccent(), readCorners(), readTextSize());
}

const MODES: { value: Theme; label: string; icon: typeof SunIcon }[] = [
    { value: "system", label: "System", icon: SystemIcon },
    { value: "light", label: "Light", icon: SunIcon },
    { value: "dark", label: "Dark", icon: MoonIcon },
];

/** Appearance choices are held together because they are written
 *  together: one effect, one storage write, one attribute pass.
 *
 *  Kept in this browser rather than on the account. It is a property of the
 *  screen somebody is sitting at — a laptop at night and a monitor in an
 *  office want different answers from the same person.
 */
export function useAppearance() {
    const [theme, setTheme] = useState<Theme>(readTheme);
    const [accent, setAccent] = useState<Accent>(readAccent);
    const [corners, setCorners] = useState<Corners>(readCorners);
    const [textSize, setTextSize] = useState<ChatTextSize>(readTextSize);

    useEffect(() => {
        apply(theme, accent, corners, textSize);
        try {
            if (theme === "system") localStorage.removeItem(THEME_KEY);
            else localStorage.setItem(THEME_KEY, theme);
            localStorage.setItem(ACCENT_KEY, accent);
            localStorage.setItem(CORNERS_KEY, corners);
            localStorage.setItem(CHAT_TEXT_KEY, textSize);
        } catch {
            /* a browser refusing storage still renders in the OS theme */
        }
    }, [theme, accent, corners, textSize]);

    return { theme, setTheme, accent, setAccent, corners, setCorners, textSize, setTextSize };
}

/** Appearance as a section of settings rather than a dropdown in the header.
 *
 *  It was a menu behind a gear beside the teammate's name, which put a choice
 *  about the whole app inside the chrome of one teammate. Nothing about it is
 *  per-teammate, so it belongs where the other account-wide choices are.
 */
export function AppearancePanel() {
    const { theme, setTheme, accent, setAccent, corners, setCorners, textSize, setTextSize } = useAppearance();

    return (
        <div className="appearance">
            <div className="appearance__group">
                <div className="theme__label">Theme</div>
                <div className="theme__modes">
                    {MODES.map((mode) => (
                        <button
                            key={mode.value}
                            className="theme__mode"
                            aria-current={theme === mode.value}
                            onClick={() => setTheme(mode.value)}
                        >
                            <mode.icon size={17} />
                            {mode.label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="appearance__group">
                <div className="theme__label" id="chat-text-label">Chat text size</div>
                <div className="theme__modes" role="group" aria-labelledby="chat-text-label">
                    {CHAT_TEXT_SIZES.map(entry => <button
                        key={entry.value} className="theme__mode" type="button"
                        aria-pressed={textSize === entry.value}
                        onClick={() => setTextSize(entry.value)}
                    >{entry.label}</button>)}
                </div>
                <p className="appearance__hint">Message and composer text. Saved in this browser.</p>
                <div className="appearance__chat-preview" aria-label="Chat text preview">
                    <span>Your teammate</span>
                    <p>The draft is ready. Take a look and tell me what you’d like to change.</p>
                </div>
            </div>

            <div className="appearance__group">
                <div className="theme__label">Accent</div>
                <div className="theme__accents">
                    {ACCENTS.map((entry) => (
                        <button
                            key={entry.value}
                            className="theme__accent"
                            aria-current={accent === entry.value}
                            title={entry.label}
                            onClick={() => setAccent(entry.value)}
                        >
                            <span className="theme__swatch" style={{ background: entry.swatch }} />
                            {entry.label}
                        </button>
                    ))}
                </div>
            </div>

            <div className="appearance__group">
                <div className="theme__label">Corners</div>
                <div className="theme__modes">
                    {CORNERS.map((entry) => (
                        <button
                            key={entry.value}
                            className={"theme__mode theme__corner theme__corner--" + entry.value}
                            aria-current={corners === entry.value}
                            onClick={() => setCorners(entry.value)}
                        >
                            <span className="theme__cornerbox" />
                            {entry.label}
                        </button>
                    ))}
                </div>
            </div>
        </div>
    );
}
