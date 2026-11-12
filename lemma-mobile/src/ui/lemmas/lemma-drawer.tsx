import { useMemo, useState } from "react";
import { ScrollView, View, useWindowDimensions } from "react-native";
import { useRouter } from "expo-router";
import Animated, { interpolate, useAnimatedStyle, type SharedValue } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useNeeds } from "../../data/hooks";
import { NOTHING_OWNED } from "../../data/model";
import type { LemmaSection, SideRow } from "../../data/types";
import { Meter } from "../chrome";
import { Drawer } from "../drawer";
import { LemmaFace } from "../face";
import { useIdentity } from "../identity";
import { Press } from "../press";
import { RowEnd, RowGlyph } from "../rows";
import { Num, Text } from "../text";
import { color } from "../tokens";
import type { Origin } from "./faces-board";
import { OneThing } from "./one-thing";

const HEIGHT = 540;
const FACE = 52;
/** Where the face settles in the drawer, from the drawer's top edge. */
const FACE_LEFT = 20;
const FACE_TOP = 22;

/** A Lemma, tapped: its face lifts out of the board into this drawer, with
 *  what it owns, where it stands, the one thing it needs from you, what it
 *  did lately, and the way into its page in its own colour. `onOpen` hands
 *  the page where the face sits now, so it flies on from here. */
export function LemmaDrawer({ section, origin, onClose, onOpen, leaving = false }: {
    section: LemmaSection | null;
    /** The page is opening: leave at once, under the flying face. */
    leaving?: boolean;
    origin: Origin | null;
    onClose: () => void;
    onOpen: (section: LemmaSection, from: Origin) => void;
}) {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const { height: screen } = useWindowDimensions();
    /* Until the drawer says where it was laid out, a guess; the window's
       height leaves out bars the drawer reaches under. */
    const [laidTop, setLaidTop] = useState<number | null>(null);
    const top = laidTop ?? screen - Math.min(HEIGHT + insets.bottom, screen * 0.9);
    const settled: Origin = { x: FACE_LEFT, y: top + FACE_TOP, size: FACE };
    const { needs } = useNeeds();
    const lemma = section?.lemma ?? null;
    const identity = useIdentity(lemma?.id ?? "");
    const need = useMemo(() => (lemma ? needs.filter((item) => item.podId === lemma.id).sort((a, b) => b.sinceMs - a.sinceMs)[0] ?? null : null), [needs, lemma]);
    const owns = section?.owns ?? null;
    const now = Date.now();
    const go = (row: SideRow) => {
        onClose();
        router.push(row.href as never);
    };
    return (
        <Drawer
            open={Boolean(section)}
            instant={leaving}
            onClose={onClose}
            height={HEIGHT}
            above={(progress, edge) => {
                if (edge !== null && edge !== laidTop) queueMicrotask(() => setLaidTop(edge));
                return lemma && origin && edge !== null ? <Flying id={lemma.id} icon={lemma.iconUrl} from={origin} to={{ x: FACE_LEFT, y: edge + FACE_TOP, size: FACE }} progress={progress} /> : null;
            }}
        >
            {section && lemma ? (
                <View style={{ flex: 1 }}>
                    <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 96, gap: 14 }}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: FACE, marginTop: 6 }}>
                            <View style={{ width: FACE, height: FACE }} />
                            <View style={{ flex: 1, gap: 2 }}>
                                <Text accessibilityRole="header" size={19} weight="medium" numberOfLines={1}>{lemma.name}</Text>
                                <Text size={13.5} tone={color.ink2} numberOfLines={2}>{owns?.sentence ? `Owns: ${owns.sentence}` : NOTHING_OWNED}</Text>
                            </View>
                        </View>
                        {owns?.display ? (
                            <View style={{ gap: 6 }}>
                                <View style={{ flexDirection: "row", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                                    <Num size={26} weight="medium" tone={color.ink}>{owns.display}</Num>
                                    {owns.label ? <Text size={13.5} tone={color.ink2}>{owns.label}</Text> : null}
                                    {owns.standing === "behind" ? <Text size={13.5} tone={color.amber}>Behind</Text> : null}
                                </View>
                                {owns.fill !== null ? <Meter fill={owns.fill} mark={owns.mark} label={owns.display} tone={identity.ink} /> : null}
                            </View>
                        ) : null}
                        {need ? <OneThing item={need} onOpen={onClose} /> : null}
                        <View>
                            <Text size={12.5} tone={color.ink3} style={{ marginBottom: 2 }}>Recently</Text>
                            {section.rows.length === 0 ? <Text size={15} tone={color.ink2} style={{ paddingVertical: 8 }}>Nothing yet this week.</Text> : null}
                            {section.rows.slice(0, 5).map((row) => (
                                <Press key={row.key} role="link" label={row.title} onPress={() => go(row)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 46, borderBottomWidth: 1, borderBottomColor: color.line }}>
                                    <RowGlyph row={row} width={20} />
                                    <Text size={15} numberOfLines={1} style={{ flex: 1 }}>{row.title}</Text>
                                    <RowEnd row={row} now={now} />
                                </Press>
                            ))}
                        </View>
                    </ScrollView>
                    <View style={{ position: "absolute", left: 20, right: 20, bottom: 18 }}>
                        <Press label={`Open ${lemma.name}`} onPress={() => onOpen(section, settled)} style={{ height: 50, borderRadius: 14, backgroundColor: identity.ink, alignItems: "center", justifyContent: "center" }}>
                            <Text size={16} weight="medium" tone={identity.onInk}>Open {lemma.name}</Text>
                        </Press>
                    </View>
                </View>
            ) : null}
        </Drawer>
    );
}

/** The face on its way: from where it was touched to the drawer's header,
 *  following the drawer as it opens, is dragged, and closes. */
function Flying({ id, icon, from, to, progress }: { id: string; icon: string | null; from: Origin; to: Origin; progress: SharedValue<number> }) {
    const style = useAnimatedStyle(() => {
        const size = interpolate(progress.value, [0, 1], [from.size, to.size]);
        const cx = interpolate(progress.value, [0, 1], [from.x + from.size / 2, to.x + to.size / 2]);
        const cy = interpolate(progress.value, [0, 1], [from.y + from.size / 2, to.y + to.size / 2]);
        return { position: "absolute", left: cx - FACE / 2, top: cy - FACE / 2, width: FACE, height: FACE, transform: [{ scale: size / FACE }] };
    });
    return (
        <Animated.View pointerEvents="none" style={style}>
            <LemmaFace id={id} icon={icon} size={FACE} />
        </Animated.View>
    );
}
