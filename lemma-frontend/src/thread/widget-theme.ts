/** What a widget is told about the pod it is drawn in.
 *
 *  A widget runs in an iframe on the API's origin, so it inherits nothing: not
 *  the theme, not the accent, not the faces. Left alone it renders in whatever
 *  it was written with and sits on a dark page glowing white.
 *
 *  The protocol is not ours to invent, and the first pass at this invented it
 *  anyway — `lemma-app-theme` carrying tokens named after this app's own
 *  variables. That is a real message type, but it is the one for **apps**, and
 *  the names were wrong besides, so every widget quietly ignored the lot and
 *  kept its defaults. The platform's `lib/assistant/widget-theme.ts` is the
 *  authority: widgets take `lemma-widget-theme`, and the vocabulary is
 *  semantic — `surface`, `muted`, `border` — never the host's own token names,
 *  because a widget is written once and drawn inside whichever host has it.
 *
 *  So this maps this app's palette onto those names. `lemma-app-theme` goes out
 *  alongside it because a widget runs the browser SDK, and the SDK registers a
 *  listener for that one the moment it finds itself framed; each side ignores
 *  the message it does not recognise. */

export const WIDGET_THEME_MESSAGE_TYPE = "lemma-widget-theme";
export const APP_THEME_MESSAGE_TYPE = "lemma-app-theme";

/** Canonical name → the token in this app that answers it. Anything with no
 *  answer is left out rather than guessed at, which is what the platform does:
 *  a widget falling back to its own value beats being handed a wrong one. */
const SOURCES: Record<string, string> = {
    "bg": "--canvas",
    "surface": "--paper",
    "subtle": "--chrome",
    "raised": "--wash-b",
    "text": "--ink",
    "muted": "--ink-2",
    "faint": "--ink-3",
    "border": "--line",
    "border-strong": "--line-2",
    "accent": "--accent",
    "accent-hover": "--accent",
    "accent-soft": "--accent-soft",
    "success": "--ok",
    "warning": "--wait",
    "danger": "--bad",
    "info": "--info",
    /* A categorical ramp, in the platform's order: two brand hues, then
       success, then a fourth, then muted text. A widget asking for chart-2 and
       getting this app's green where the platform gives a lilac is why the same
       dashboard came out in different colours on either side. */
    "chart-1": "--accent",
    "chart-2": "--info",
    "chart-3": "--ok",
    "chart-4": "--wait",
    "chart-5": "--ink-3",
    "radius-sm": "--r-sm",
    "radius-md": "--r-md",
    "radius-panel": "--r-xl",
    "ease-standard": "--ease-out",
    "ease-emphasized": "--ease-out",
};

/** The two vocabularies agree on everything but one name: a widget's main
 *  corner is `radius`, an app's is `radius-lg`. */
const RADIUS_MAIN = { widget: "radius", app: "radius-lg" } as const;

export interface ThemeMessage {
    type: string;
    theme: "light" | "dark";
    tokens: Record<string, string>;
}

/** Resolved rather than declared: the accent, the corners and light-or-dark all
 *  come out of the cascade, and "system" leaves no stamp on the element. */
function resolved(): { theme: "light" | "dark"; read: (name: string) => string; font: string } {
    const root = document.documentElement;
    const rootStyles = getComputedStyle(root);
    const stamped = root.dataset.theme;
    const theme = stamped === "dark" || (stamped !== "light" && window.matchMedia("(prefers-color-scheme: dark)").matches)
        ? "dark" as const
        : "light" as const;
    return {
        theme,
        read: (name) => rootStyles.getPropertyValue(name).trim(),
        font: getComputedStyle(document.body).fontFamily.trim(),
    };
}

export function buildThemeMessage({ prefix, type, theme, readToken, fontFamily }: {
    prefix: "lemma-widget" | "lemma-app";
    type: string;
    theme: "light" | "dark";
    readToken: (name: string) => string;
    fontFamily: string;
}): ThemeMessage {
    const read = readToken;
    const font = fontFamily;
    const tokens: Record<string, string> = {};
    const put = (name: string, value: string) => {
        /* The receiver drops anything past 512 characters, so a value that long
           would vanish silently on the other side. */
        if (value && value.length <= 512) tokens[`--${prefix}-${name}`] = value;
    };

    for (const [name, source] of Object.entries(SOURCES)) put(name, read(source));
    put(prefix === "lemma-widget" ? RADIUS_MAIN.widget : RADIUS_MAIN.app, read("--r-lg"));
    put("font", font);
    put("color-scheme", theme);
    /* Computed rather than read, exactly as the platform does: a soft danger
       ground is the one value no palette here carries. */
    put("danger-soft", theme === "dark" ? "#331919" : "#fef2f2");

    return { type, theme, tokens };
}

export function widgetThemeMessage(): ThemeMessage {
    const { theme, read, font } = resolved();
    return buildThemeMessage({ prefix: "lemma-widget", type: WIDGET_THEME_MESSAGE_TYPE, theme, readToken: read, fontFamily: font });
}

export function appThemeMessage(): ThemeMessage & { density: string } {
    const { theme, read, font } = resolved();
    const message = buildThemeMessage({ prefix: "lemma-app", type: APP_THEME_MESSAGE_TYPE, theme, readToken: read, fontFamily: font });
    return { ...message, density: "compact" };
}

/** Appearance changes are attribute writes on the root plus the OS flipping
 *  under "system", and a widget that only hears the first one goes stale the
 *  moment somebody's machine turns the lights off at sunset. */
export function onAppearanceChange(run: () => void): () => void {
    const observer = new MutationObserver(run);
    observer.observe(document.documentElement, {
        attributes: true,
        attributeFilter: ["data-theme", "data-accent", "data-corners"],
    });
    const system = window.matchMedia("(prefers-color-scheme: dark)");
    system.addEventListener("change", run);
    return () => {
        observer.disconnect();
        system.removeEventListener("change", run);
    };
}

/** The tokens as a stylesheet, for inline content that is not served by the
 *  platform and so has no receiver of its own. */
export function widgetThemeStyle(): string {
    const message = widgetThemeMessage();
    const declarations = Object.entries(message.tokens).map(([name, value]) => name + ":" + value).join(";");
    return `:root{${declarations};color-scheme:${message.theme}}`;
}
