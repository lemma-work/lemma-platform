import { hsl } from "./face";
import { identityHue } from "./hue";
import { useTheme } from "./theme";
import { color, type Scheme } from "./tokens";

/** A Lemma's own colour: its face's hue (the web's `markHue`, so both apps
 *  agree), made strong enough for a button, a bar and a page's tint, so a
 *  Lemma is known before its name is read.
 *
 *  Status keeps the app's two signals, violet for what needs you or is
 *  running and amber for behind, so a hue that falls in either band is moved
 *  just out of it: a Lemma's colour never reads as a status. */

export { identityHue };

export interface Identity {
    /** For a button, a bar, a link in the Lemma's colour. */
    ink: string;
    /** Text or a glyph on `ink`. */
    onInk: string;
    /** A page's ground, faintly the Lemma's. */
    wash: string;
    /** Something to sit on the wash: a selected row, a chip. */
    soft: string;
}

function channels(hex: string): [number, number, number] {
    const raw = hex.replace("#", "");
    return [0, 2, 4].map((at) => parseInt(raw.slice(at, at + 2), 16)) as [number, number, number];
}

/** `amount` of `over` laid on `under`, as hex. */
export function blend(over: string, under: string, amount: number): string {
    const top = channels(over);
    const bottom = channels(under);
    return "#" + top.map((value, index) => Math.round(value * amount + bottom[index] * (1 - amount)).toString(16).padStart(2, "0")).join("");
}

export function identityOf(id: string, scheme: Scheme): Identity {
    const hue = identityHue(id);
    const ink = scheme === "dark" ? hsl(hue, 52, 62) : hsl(hue, 58, 36);
    const paper = color.paper.startsWith("#") ? color.paper : scheme === "dark" ? "#212121" : "#ffffff";
    return {
        ink,
        onInk: scheme === "dark" ? "#141414" : "#ffffff",
        wash: blend(ink, paper, scheme === "dark" ? 0.12 : 0.06),
        soft: blend(ink, paper, scheme === "dark" ? 0.24 : 0.14),
    };
}

export function useIdentity(id: string): Identity {
    const { scheme } = useTheme();
    return identityOf(id, scheme);
}
