import { View } from "react-native";
import { Image } from "expo-image";
import { Text } from "./text";
import { color } from "./tokens";
import { hueOf } from "./hue";

export { hueOf };

/** A Lemma's face and a person's. A Lemma is its cast character
 *  (`lemma-frontend/src/shell/cast.ts`), standing free on the page: the web
 *  sets it on a tinted tile, the phone lets it stand on its own, which reads
 *  livelier. A picture someone chose keeps a rounded clip, since a photo
 *  needs an edge. A person is a disc with initials, so a list holding both
 *  still tells them apart at a glance. */

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

/** `markTint(seed).background` with the light tokens (58% / 91%). */
export function tintOf(seed: string): string {
    return hsl(hueOf(seed), 58, 91);
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

export function LemmaFace({ id, icon, size }: { id: string; icon?: string | null; size: number }) {
    const drawn = drawnFor(id, icon);
    return (
        <View
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
            style={{
                width: size,
                height: size,
                alignItems: "center",
                justifyContent: "center",
                ...(drawn.kind === "image" ? { borderRadius: Math.round(size * 0.27), overflow: "hidden" as const, backgroundColor: tintOf(id) } : {}),
            }}
        >
            {drawn.kind === "cast" && <Image source={CAST[drawn.name]} style={{ width: size, height: size }} contentFit="contain" />}
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
