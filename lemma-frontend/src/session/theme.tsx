import { SunIcon, MoonIcon, SystemIcon } from "@/ui/icons";
import { useEffect, useState } from "react";
import { isLandingPreview } from "@/marketing/preview-mode";
import { key } from "./storage";
import { CHAT_TEXT_SIZES, readChatTextSize, type ChatTextSize } from "./chat-text";

export type Theme = "system" | "light" | "dark";
export type Accent = "mono" | "violet" | "coral" | "forest" | "ocean" | "amber" | "plum" | "slate" | "ink";
export type Corners = "sharp" | "soft" | "round";

const THEME_KEY = key("theme");
/* A new key rather than the old one: every accent stored under `accent` was
 * picked while the app ignored it, so none of them is a look anybody has
 * seen. Starting everyone on Mono is starting them on what they already had. */
const ACCENT_KEY = key("app-accent");
const CORNERS_KEY = key("corners");
const CHAT_TEXT_KEY = key("chat-text-size");

function readTextSize(): ChatTextSize {
    try { return readChatTextSize(localStorage, CHAT_TEXT_KEY); }
    catch { return "default"; }
}

export const ACCENTS: { value: Accent; label: string; swatch: string }[] = [
    { value: "mono", label: "Mono", swatch: "#0d0d0d" },
    { value: "violet", label: "Violet", swatch: "#6b4fe0" },
    { value: "coral", label: "Coral", swatch: "#d54024" },
    { value: "forest", label: "Forest", swatch: "#2d7a58" },
    { value: "ocean", label: "Ocean", swatch: "#1f6f9e" },
    { value: "amber", label: "Amber", swatch: "#a56811" },
    { value: "plum", label: "Plum", swatch: "#9c3f6d" },
    { value: "slate", label: "Slate", swatch: "#4a5568" },
    { value: "ink", label: "Ink", swatch: "#26262c" },
];

export const CORNERS: { value: Corners; label: string }[] = [
    { value: "sharp", label: "Sharp" },
    { value: "soft", label: "Soft" },
    { value: "round", label: "Round" },
];

/** The landing demo opens light, to match the page around it, rather than
 *  following the visitor's system; so there "system" has to be written down
 *  when chosen, since an empty key means light. */
function readTheme(): Theme {
    const fallback: Theme = isLandingPreview() ? "light" : "system";
    try {
        const stored = localStorage.getItem(THEME_KEY);
        return stored === "light" || stored === "dark" || stored === "system" ? stored : fallback;
    } catch {
        return fallback;
    }
}

function readAccent(): Accent {
    try {
        const stored = localStorage.getItem(ACCENT_KEY);
        return ACCENTS.some((entry) => entry.value === stored) ? (stored as Accent) : "mono";
    } catch {
        return "mono";
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
            if (theme === "system" && !isLandingPreview()) localStorage.removeItem(THEME_KEY);
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
 *
 *  Each choice is a set of cards (the kit's `.kopts`): the whole card is the
 *  target, and the one in use is the blue tint with its dot filled.
 */
export function AppearancePanel() {
    const { theme, setTheme, accent, setAccent, corners, setCorners, textSize, setTextSize } = useAppearance();

    return (
        <div className="kset-form">
            <div className="kfield" role="radiogroup" aria-label="Theme">
                <span>Theme</span>
                <div className="kopts kset-opts">
                    {MODES.map((mode) => (
                        <label key={mode.value} className="kopt kopt--compact">
                            <input type="radio" name="appearance-theme" checked={theme === mode.value} onChange={() => setTheme(mode.value)} />
                            <span className="kopt__t"><mode.icon size={16} />{mode.value === "system" ? "Match this device" : mode.label}</span>
                            <span className="kopt__dot" aria-hidden="true" />
                        </label>
                    ))}
                </div>
            </div>

            <div className="kfield" role="radiogroup" aria-label="Chat text">
                <span>Chat text</span>
                <div className="kopts kset-opts">
                    {CHAT_TEXT_SIZES.map((entry) => (
                        <label key={entry.value} className="kopt kopt--compact">
                            <input type="radio" name="appearance-text" checked={textSize === entry.value} onChange={() => setTextSize(entry.value)} />
                            <span className="kopt__t">{entry.label}</span>
                            <span className="kopt__d">{entry.pixels}px</span>
                            <span className="kopt__dot" aria-hidden="true" />
                        </label>
                    ))}
                </div>
                <small>Messages and what you type. Saved in this browser.</small>
            </div>
            <div className="kset-preview" aria-label="Chat text preview">
                <span>Preview</span>
                <p>The draft is ready. Take a look and tell me what you’d like to change.</p>
            </div>

            <div className="kfield" role="radiogroup" aria-label="Accent">
                <span>Accent</span>
                <div className="kopts kset-opts kset-opts--small">
                    {ACCENTS.map((entry) => (
                        <label key={entry.value} className="kopt kopt--compact">
                            <input type="radio" name="appearance-accent" checked={accent === entry.value} onChange={() => setAccent(entry.value)} />
                            <span className="kopt__t"><span className="kset-swatch" style={{ background: entry.swatch }} aria-hidden="true" />{entry.label}</span>
                            <span className="kopt__dot" aria-hidden="true" />
                        </label>
                    ))}
                </div>
            </div>

            <div className="kfield" role="radiogroup" aria-label="Corners">
                <span>Corners</span>
                <div className="kopts kset-opts">
                    {CORNERS.map((entry) => (
                        <label key={entry.value} className="kopt kopt--compact">
                            <input type="radio" name="appearance-corners" checked={corners === entry.value} onChange={() => setCorners(entry.value)} />
                            <span className="kopt__t"><span className={"kset-corner kset-corner--" + entry.value} aria-hidden="true" />{entry.label}</span>
                            <span className="kopt__dot" aria-hidden="true" />
                        </label>
                    ))}
                </div>
            </div>
        </div>
    );
}
