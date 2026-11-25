import { View } from "react-native";
import { Image } from "expo-image";
import { Text } from "./text";
import { color, scheme } from "./tokens";
import { hueOf } from "./hue";

export { hueOf };

/** A Lemma's face and a person's. A Lemma is its cast character
 *  (`lemma-frontend/src/shell/cast.ts`) on its own tint: a rounded tile, as
 *  the web draws it since "alive and honest" (#1223), never inside a ring.
 *  A picture someone chose fills the tile. A person is a disc with initials,
 *  so a list holding both still tells them apart at a glance. */

export const CAST = {
    arch: require("../../assets/cast/arch-sm.webp"),
    bean: require("../../assets/cast/bean-sm.webp"),
    bloom: require("../../assets/cast/bloom-sm.webp"),
    bolt: require("../../assets/cast/bolt-sm.webp"),
    cloud: require("../../assets/cast/cloud-sm.webp"),
    coil: require("../../assets/cast/coil-sm.webp"),
    cube: require("../../assets/cast/cube-sm.webp"),
    frame: require("../../assets/cast/frame-sm.webp"),
    gem: require("../../assets/cast/gem-sm.webp"),
    grid: require("../../assets/cast/grid-sm.webp"),
    kite: require("../../assets/cast/kite-sm.webp"),
    knot: require("../../assets/cast/knot-sm.webp"),
    loop: require("../../assets/cast/loop-sm.webp"),
    moon: require("../../assets/cast/moon-sm.webp"),
    peak: require("../../assets/cast/peak-sm.webp"),
    pleat: require("../../assets/cast/pleat-sm.webp"),
    prism: require("../../assets/cast/prism-sm.webp"),
    ring: require("../../assets/cast/ring-sm.webp"),
    spark: require("../../assets/cast/spark-sm.webp"),
    spool: require("../../assets/cast/spool-sm.webp"),
    sprout: require("../../assets/cast/sprout-sm.webp"),
    stack: require("../../assets/cast/stack-sm.webp"),
    wave: require("../../assets/cast/wave-sm.webp"),
    wedge: require("../../assets/cast/wedge-sm.webp"),
} as const;

export type CharacterName = keyof typeof CAST;

/** The face the product shows: each character's rig, standing at rest
 *  (`scripts/faces.mjs` draws these from the web's own rig). The web animates
 *  that rig wherever a face is 30px or more and keeps the walking portrait
 *  above for anything smaller (`ANIMATED_MIN` in `shell/mark.tsx`). */
const RIG: Record<CharacterName, number> = {
    arch: require("../../assets/rig/arch.webp"),
    bean: require("../../assets/rig/bean.webp"),
    bloom: require("../../assets/rig/bloom.webp"),
    bolt: require("../../assets/rig/bolt.webp"),
    cloud: require("../../assets/rig/cloud.webp"),
    coil: require("../../assets/rig/coil.webp"),
    cube: require("../../assets/rig/cube.webp"),
    frame: require("../../assets/rig/frame.webp"),
    gem: require("../../assets/rig/gem.webp"),
    grid: require("../../assets/rig/grid.webp"),
    kite: require("../../assets/rig/kite.webp"),
    knot: require("../../assets/rig/knot.webp"),
    loop: require("../../assets/rig/loop.webp"),
    moon: require("../../assets/rig/moon.webp"),
    peak: require("../../assets/rig/peak.webp"),
    pleat: require("../../assets/rig/pleat.webp"),
    prism: require("../../assets/rig/prism.webp"),
    ring: require("../../assets/rig/ring.webp"),
    spark: require("../../assets/rig/spark.webp"),
    spool: require("../../assets/rig/spool.webp"),
    sprout: require("../../assets/rig/sprout.webp"),
    stack: require("../../assets/rig/stack.webp"),
    wave: require("../../assets/rig/wave.webp"),
    wedge: require("../../assets/rig/wedge.webp"),
};

/** Below this the web draws the portrait, at or above it the rig. */
const RIG_FROM = 30;

/** Whether a face drawn at `size` points is the rig rather than the portrait. */
export function isRigAt(size: number): boolean {
    return size >= RIG_FROM;
}

/** A character's face at `size` points, as the product draws it there. */
export function castImage(name: CharacterName, size: number): number {
    return isRigAt(size) ? RIG[name] : CAST[name];
}

/** In the web's order (`CHARACTERS` in `shell/cast.ts`): the hash indexes it. */
const CHARACTERS: readonly CharacterName[] = [
    "arch", "bean", "bloom", "bolt", "cloud", "coil", "cube", "frame", "gem", "grid", "kite", "knot",
    "loop", "moon", "peak", "pleat", "prism", "ring", "spark", "spool", "sprout", "stack", "wave", "wedge",
];

/** `characterForSeed`, verbatim: a different multiplier from the hue's, so
 *  the two do not march in step. */
export function characterForSeed(seed: string): CharacterName {
    let hash = 7;
    for (let index = 0; index < seed.length; index += 1) {
        hash = (hash * 131 + seed.charCodeAt(index)) % 1000003;
    }
    return CHARACTERS[hash % CHARACTERS.length];
}


/** An HSL colour as hex: the web's `mark.tsx` builds its tints this way. */
export function hsl(hue: number, saturation: number, lightness: number): string {
    const s = saturation / 100;
    const l = lightness / 100;
    const k = (n: number) => (n + hue / 30) % 12;
    const a = s * Math.min(l, 1 - l);
    const f = (n: number) => l - a * Math.max(-1, Math.min(k(n) - 3, Math.min(9 - k(n), 1)));
    const hex = (x: number) => Math.round(x * 255).toString(16).padStart(2, "0");
    return `#${hex(f(0))}${hex(f(8))}${hex(f(4))}`;
}

/** `markTint(seed).background`: 58% / 91% in light, 42% / 26% in dark
 *  (`--mark-sat`, `--mark-bg` in the web's tokens.css). */
export function tintOf(seed: string): string {
    return scheme === "dark" ? hsl(hueOf(seed), 42, 26) : hsl(hueOf(seed), 58, 91);
}

/** `markTint(seed).color`: the ink drawn on that tint. */
export function tintInkOf(seed: string): string {
    return scheme === "dark" ? hsl(hueOf(seed), 42, 76) : hsl(hueOf(seed), 58, 34);
}

const IDENTITY = /^lemma-identity:(\d{1,3})$/;
const CAST_URL = /\/teammates\/cast\/([a-z]+)(?:-sm)?\.(?:webp|png)$/;
const LEGACY_URL = /\/teammates\/(loop|pleat|frame)-v1\.png$/;
const GLYPH = /^[\p{Extended_Pictographic}\p{Emoji_Component}\p{Regional_Indicator}‍️]+$/u;
const MEANINGFUL = /[\p{Extended_Pictographic}\p{Regional_Indicator}]/u;

type Drawn = { kind: "cast"; name: CharacterName } | { kind: "image"; url: string } | { kind: "glyph"; glyph: string };

/** What a pod's `icon_url` draws, in the web's order of how much somebody
 *  meant it: a cast path, a picture, an emoji, a chosen variant, else the
 *  character its id lands on. */
export function drawnFor(id: string, icon: string | null | undefined): Drawn {
    const raw = icon?.trim() ?? "";
    if (raw) {
        const variant = IDENTITY.exec(raw);
        if (variant) {
            const n = Number(variant[1]);
            return { kind: "cast", name: characterForSeed(n === 0 ? id : `${id}#${n}`) };
        }
        if (!raw.startsWith("lemma-identity:")) {
            if (GLYPH.test(raw) && MEANINGFUL.test(raw) && Array.from(raw).length <= 16) return { kind: "glyph", glyph: raw };
            const cast = CAST_URL.exec(raw) ?? LEGACY_URL.exec(raw);
            if (cast && cast[1] in CAST) return { kind: "cast", name: cast[1] as CharacterName };
            /* A picture: on the web, or (sample data) still on this phone. */
            if (/^(https?|file):\/\//.test(raw)) return { kind: "image", url: raw };
        }
    }
    return { kind: "cast", name: characterForSeed(id) };
}

/** A Lemma's face on its tile. `bare` leaves the tile off, for the few
 *  places the face stands on something already its colour. */
export function LemmaFace({ id, icon, size, bare = false }: { id: string; icon?: string | null; size: number; bare?: boolean }) {
    const drawn = drawnFor(id, icon);
    const tile = !bare || drawn.kind === "image";
    /* The rig carries its own margins and fills its box on the web; the
       portrait sits a little in from the tile's edge. */
    const inner = tile && drawn.kind === "cast" && !isRigAt(size) ? Math.round(size * 0.94) : size;
    return (
        <View
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
            style={{
                width: size,
                height: size,
                alignItems: "center",
                justifyContent: "center",
                ...(tile ? { borderRadius: Math.round(size * 0.3), overflow: "hidden" as const, backgroundColor: tintOf(id) } : {}),
            }}
        >
            {drawn.kind === "cast" && <Image source={castImage(drawn.name, size)} style={{ width: inner, height: inner }} contentFit="contain" />}
            {drawn.kind === "image" && <Image source={{ uri: drawn.url }} style={{ width: size, height: size }} contentFit="cover" />}
            {drawn.kind === "glyph" && <Text size={Math.round(size * 0.56)}>{drawn.glyph}</Text>}
        </View>
    );
}

export function initialsOf(name: string): string {
    const words = name.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return "?";
    const first = words[0].charAt(0);
    const last = words.length > 1 ? words[words.length - 1].charAt(0) : "";
    return (first + last).toUpperCase();
}

export function PersonFace({ name, size, dark = false }: { name: string; size: number; dark?: boolean }) {
    return (
        <View
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
            style={{
                width: size,
                height: size,
                borderRadius: size / 2,
                backgroundColor: dark ? color.me : color.wash,
                alignItems: "center",
                justifyContent: "center",
            }}
        >
            <Text size={Math.max(11, Math.round(size * 0.42))} weight={dark ? "medium" : "regular"} tone={dark ? color.onInk : color.ink}>
                {dark ? initialsOf(name).charAt(0) : initialsOf(name)}
            </Text>
        </View>
    );
}
