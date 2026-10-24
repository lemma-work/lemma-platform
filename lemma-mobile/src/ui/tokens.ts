import { Platform } from "react-native";

/** The `/o` frame's tokens, for a phone, light and dark (the web's
 *  `space-tokens.css` and `tokens.css`).
 *
 *  Two signal colours and nothing else: violet for what a Lemma is doing or
 *  what needs you, amber for behind. Small grey text is #737373 on white, not
 *  the web's #8f8f8f, which fails 4.5:1 there.
 *
 *  Screens read `color.x` while they render, and `color` holds whichever
 *  palette is in use (`applyScheme`, called by `ui/theme.tsx`), so a
 *  screen is dark-ready by using a token and never a literal colour. */
const light = {
    paper: "#ffffff",
    wash: "#f4f4f4",
    chrome: "#f9f9f9",
    ink: "#0d0d0d",
    ink2: "#5d5d5d",
    ink3: "#737373",
    line: "rgba(0,0,0,0.06)",
    line2: "rgba(0,0,0,0.10)",
    /** A wash faint enough to sit behind a glyph. */
    faint: "rgba(0,0,0,0.05)",
    violet: "#5a3fd4",
    violetInk: "#4b33c4",
    violetWash: "#efebfc",
    /** Text or a glyph on a violet ground. */
    onViolet: "#ffffff",
    amber: "#7a4f00",
    amberWash: "#fbf0d9",
    /** Text or a glyph on an ink ground: a primary button, a chosen filter. */
    onInk: "#ffffff",
    /** A send button with nothing to send. */
    idle: "#e9e9e9",
    idleInk: "#8f8f8f",
    track: "#f0f0f0",
    /** The placeholder lines an app's thumbnail is drawn with. */
    sketch: "#e6e6e6",
    sketchDark: "#d6d6d6",
    /** The person's own avatar disc. */
    me: "#0e0e10",
    /** Behind a sheet. */
    scrim: "rgba(0,0,0,0.28)",
    /** Over a screen that is busy: nearly the paper. */
    veil: "rgba(255,255,255,0.94)",
    /** A photo shown whole screen: black in both themes, as a photo is. */
    photo: "#000000",
    onPhoto: "#ffffff",
    /** Over a photo, under words: "+2", a viewer's controls. */
    photoScrim: "rgba(0,0,0,0.45)",
};

export type Palette = typeof light;
export type Scheme = "light" | "dark";

const dark: Palette = {
    paper: "#212121",
    wash: "#2f2f2f",
    chrome: "#181818",
    ink: "#ececec",
    ink2: "#b4b4b4",
    ink3: "#8f8f8f",
    line: "rgba(255,255,255,0.07)",
    line2: "rgba(255,255,255,0.12)",
    faint: "rgba(255,255,255,0.07)",
    violet: "#a08cff",
    violetInk: "#b3a3ff",
    violetWash: "rgba(160,140,255,0.16)",
    onViolet: "#1b1a18",
    amber: "#dcb06a",
    amberWash: "rgba(220,176,106,0.16)",
    onInk: "#0d0d0d",
    idle: "#303030",
    idleInk: "#8f8f8f",
    track: "#2f2f2f",
    sketch: "#2f2f2f",
    sketchDark: "#3a3a3a",
    me: "#ececec",
    scrim: "rgba(0,0,0,0.5)",
    veil: "rgba(33,33,33,0.94)",
    photo: "#000000",
    onPhoto: "#ffffff",
    photoScrim: "rgba(0,0,0,0.45)",
};

export const palettes: Record<Scheme, Palette> = { light, dark };

export const color: Palette = { ...light };

/** Swap the palette `color` holds. Only `ui/theme.tsx` calls it, and it
 *  redraws every screen after, since a screen reads its colours as it draws. */
export function applyScheme(scheme: Scheme): void {
    Object.assign(color, palettes[scheme]);
}

/** Albert Sans for words, Inter for anything that counts. Never above 500:
 *  hierarchy comes from size and space, not weight.
 *
 *  The fonts are built into the app (`expo-font` in app.json), and a built-in
 *  font goes by its file name on Android but by its PostScript name on iOS.
 *  A name iOS does not know lays text out in the system face, which is
 *  narrower, and Albert Sans drawn into that box is clipped. */
const named = (android: string, ios: string) => (Platform.OS === "ios" ? ios : android);

export const font = {
    regular: named("AlbertSans_400Regular", "AlbertSans-Regular"),
    medium: named("AlbertSans_500Medium", "AlbertSans-Medium"),
    num: named("Inter_400Regular", "Inter-Regular"),
    numMedium: named("Inter_500Medium", "Inter-Medium"),
} as const;

/** The smallest a thing a thumb aims at may be. */
export const TOUCH = 44;
