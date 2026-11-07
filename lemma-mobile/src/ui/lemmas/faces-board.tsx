import { useEffect, useMemo, useRef, type ReactNode } from "react";
import { View, useWindowDimensions } from "react-native";
import Animated, { Easing, interpolate, useAnimatedStyle, useSharedValue, withRepeat, withSpring, withTiming, type SharedValue } from "react-native-reanimated";
import type { LemmaSection, NeedItem } from "../../data/types";
import { LemmaFace } from "../face";
import { Icon } from "../icons";
import { BREATH_MS, settle, spring } from "../motion";
import { Press } from "../press";
import { Num, Text } from "../text";
import { color } from "../tokens";
import { LIST, NEW_TILE, boardFor, standingOf, type Placed, type Standing } from "./board";

/** Where a face was on screen when it was touched: the drawer and the
 *  Lemma's page fly it on from there. */
export interface Origin {
    x: number;
    y: number;
    size: number;
}

/** Every Lemma as a face, in a grid or in standings rows. `mode` picks the
 *  layout and each face travels to its place in the other; `onOpen` hands
 *  the touched face's place on screen to whatever opens next. */
export function FacesBoard({ sections, needs, mode, onOpen, onNew }: {
    sections: readonly LemmaSection[];
    /** Everything waiting on the reader, workflow forms included (Home's list). */
    needs: readonly NeedItem[];
    mode: "grid" | "list";
    onOpen: (section: LemmaSection, origin: Origin) => void;
    onNew: () => void;
}) {
    const { width } = useWindowDimensions();
    const items = useMemo(() => sections.map((section) => {
        const waiting = Math.max(section.waiting, needs.filter((item) => item.podId === section.lemma.id).length);
        const counted = { ...section, waiting };
        return { id: section.lemma.id, name: section.lemma.name, standing: standingOf(counted), section: counted };
    }), [sections, needs]);
    const board = useMemo(() => boardFor(items, width, true), [items, width]);
    const progress = useSharedValue(mode === "list" ? 1 : 0);
    useEffect(() => {
        progress.value = withTiming(mode === "list" ? 1 : 0, settle);
    }, [mode, progress]);
    const height = useAnimatedStyle(() => ({ height: interpolate(progress.value, [0, 1], [board.gridHeight, board.listHeight]) }));
    return (
        <Animated.View style={[{ width }, height]}>
            {board.headers.map((header) => <GroupHeader key={header.label} label={header.label} y={header.y} progress={progress} />)}
            {board.order.map((id) => {
                const grid = board.grid.get(id)!;
                const list = board.list.get(id)!;
                if (id === NEW_TILE) return <NewTile key={id} grid={grid} list={list} progress={progress} onPress={onNew} />;
                const item = items.find((one) => one.id === id)!;
                return <FaceItem key={id} section={item.section} standing={item.standing} grid={grid} list={list} progress={progress} onOpen={onOpen} />;
            })}
        </Animated.View>
    );
}

function useRect(grid: Placed, list: Placed, progress: SharedValue<number>) {
    return useAnimatedStyle(() => ({
        position: "absolute",
        left: interpolate(progress.value, [0, 1], [grid.x, list.x]),
        top: interpolate(progress.value, [0, 1], [grid.y, list.y]),
        width: interpolate(progress.value, [0, 1], [grid.w, list.w]),
        height: interpolate(progress.value, [0, 1], [grid.h, list.h]),
    }));
}

/** The face moves by its centre and scales, so it never re-renders mid-move. */
function useFace(grid: Placed, list: Placed, progress: SharedValue<number>) {
    return useAnimatedStyle(() => {
        const scale = interpolate(progress.value, [0, 1], [1, list.face / grid.face]);
        const cx = interpolate(progress.value, [0, 1], [grid.cx - grid.x, list.cx - list.x]);
        const cy = interpolate(progress.value, [0, 1], [grid.cy - grid.y, list.cy - list.y]);
        return { position: "absolute", left: cx - grid.face / 2, top: cy - grid.face / 2, width: grid.face, height: grid.face, transform: [{ scale }] };
    });
}

function GroupHeader({ label, y, progress }: { label: string; y: number; progress: SharedValue<number> }) {
    const style = useAnimatedStyle(() => ({ position: "absolute", left: 20, top: y + 10, opacity: interpolate(progress.value, [0.4, 1], [0, 1], "clamp") }));
    return (
        <Animated.View style={style} pointerEvents="none">
            <Text size={12.5} tone={color.ink3}>{label}</Text>
        </Animated.View>
    );
}

function meta(section: LemmaSection, standing: Standing): { text: string; tone: string; number: boolean } {
    if (standing === "running") return { text: "running", tone: color.violetInk, number: false };
    if (section.owns?.display) return { text: section.owns.display, tone: standing === "behind" ? color.amber : color.ink3, number: true };
    if (standing === "quiet") return { text: "quiet", tone: color.ink3, number: false };
    return { text: "", tone: color.ink3, number: false };
}

function FaceItem({ section, standing, grid, list, progress, onOpen }: {
    section: LemmaSection;
    standing: Standing;
    grid: Placed;
    list: Placed;
    progress: SharedValue<number>;
    onOpen: (section: LemmaSection, origin: Origin) => void;
}) {
    const rect = useRect(grid, list, progress);
    const face = useFace(grid, list, progress);
    const gridLabels = useAnimatedStyle(() => ({ opacity: interpolate(progress.value, [0, 0.5], [1, 0], "clamp"), transform: [{ translateY: interpolate(progress.value, [0, 1], [0, -8]) }] }));
    const rowLabels = useAnimatedStyle(() => ({ opacity: interpolate(progress.value, [0.5, 1], [0, 1], "clamp"), transform: [{ translateX: interpolate(progress.value, [0, 1], [12, 0]) }] }));
    const target = useRef<View>(null);
    /* A small squish under the finger. */
    const squish = useSharedValue(1);
    const pressed = useAnimatedStyle(() => ({ transform: [{ scale: squish.value }] }));
    const said = meta(section, standing);
    const name = section.lemma.name;
    const label = `${name}${said.text ? `, ${said.text}` : ""}${section.waiting ? `, ${section.waiting} need you` : ""}`;
    return (
        <Animated.View style={rect}>
            <Press
                label={label}
                onPressIn={() => { squish.value = withSpring(0.9, spring); }}
                onPressOut={() => { squish.value = withSpring(1, spring); }}
                onPress={() => target.current?.measureInWindow((x, y, w) => onOpen(section, { x, y, size: w }))}
                style={{ flex: 1 }}
            >
                <Animated.View style={face}>
                    <Animated.View ref={target} collapsable={false} style={pressed}>
                        <Ringed standing={standing} size={grid.face}>
                            <LemmaFace id={section.lemma.id} icon={section.lemma.iconUrl} size={grid.face} />
                        </Ringed>
                    </Animated.View>
                    {section.waiting > 0 ? (
                        <View style={{ position: "absolute", top: -5, right: -5, minWidth: 22, height: 22, paddingHorizontal: 6, borderRadius: 11, backgroundColor: color.violet, borderWidth: 2, borderColor: color.paper, alignItems: "center", justifyContent: "center" }}>
                            <Num size={12} tone={color.onViolet}>{section.waiting > 99 ? "99+" : section.waiting}</Num>
                        </View>
                    ) : null}
                </Animated.View>
                <Animated.View style={[{ position: "absolute", left: 0, right: 0, top: GRID_LABEL_TOP, alignItems: "center", gap: 1 }, gridLabels]} pointerEvents="none">
                    <Text size={13} numberOfLines={1} tone={standing === "quiet" ? color.ink2 : color.ink} style={{ maxWidth: grid.w - 8 }}>{name}</Text>
                    {said.text ? (said.number ? <Num size={12} tone={said.tone}>{said.text}</Num> : <Text size={12} tone={said.tone}>{said.text}</Text>) : null}
                </Animated.View>
                <Animated.View style={[{ position: "absolute", left: LIST.faceLeft + LIST.face + 14, right: 20, top: 0, bottom: 0, flexDirection: "row", alignItems: "center", gap: 10, borderBottomWidth: 1, borderBottomColor: color.line }, rowLabels]} pointerEvents="none">
                    <Text size={16} numberOfLines={1} tone={standing === "quiet" ? color.ink2 : color.ink} style={{ flex: 1 }}>{name}</Text>
                    {said.text ? (said.number ? <Num size={14} tone={said.tone}>{said.text}</Num> : <Text size={14} tone={said.tone}>{said.text}</Text>) : null}
                    {section.waiting > 0 ? <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} /> : null}
                </Animated.View>
            </Press>
        </Animated.View>
    );
}

const GRID_LABEL_TOP = 76;

/** A face's standing, drawn around the character: a soft circle in violet
 *  for what needs you, amber for behind; a running Lemma breathes and bobs, as
 *  if at work; on track is left alone; quiet fades. */
function Ringed({ standing, size, children }: { standing: Standing; size: number; children: ReactNode }) {
    const radius = size / 2 + 6;
    const breath = useSharedValue(0);
    useEffect(() => {
        if (standing === "running") breath.value = withRepeat(withTiming(1, { duration: BREATH_MS, easing: Easing.inOut(Easing.sin) }), -1, true);
        else breath.value = 0;
    }, [standing, breath]);
    const halo = useAnimatedStyle(() => ({ opacity: interpolate(breath.value, [0, 1], [0.9, 0]), transform: [{ scale: interpolate(breath.value, [0, 1], [1, 1.16]) }] }));
    const bob = useAnimatedStyle(() => ({ transform: [{ translateY: interpolate(breath.value, [0, 1], [1.5, -2.5]) }] }));
    const ring = standing === "needs" ? color.violet : standing === "behind" ? color.amber : standing === "running" ? color.violet : null;
    return (
        <View style={{ width: size, height: size, opacity: standing === "quiet" ? 0.55 : 1 }}>
            {ring ? (
                <View pointerEvents="none" style={{ position: "absolute", left: -6, top: -6, right: -6, bottom: -6, borderRadius: radius, borderWidth: 2, borderColor: ring }} />
            ) : null}
            {standing === "running" ? (
                <Animated.View pointerEvents="none" style={[{ position: "absolute", left: -6, top: -6, right: -6, bottom: -6, borderRadius: radius, borderWidth: 2, borderColor: color.violet }, halo]} />
            ) : null}
            <Animated.View style={standing === "running" ? bob : undefined}>{children}</Animated.View>
        </View>
    );
}

function NewTile({ grid, list, progress, onPress }: { grid: Placed; list: Placed; progress: SharedValue<number>; onPress: () => void }) {
    const rect = useRect(grid, list, progress);
    const face = useFace(grid, list, progress);
    const gridLabel = useAnimatedStyle(() => ({ opacity: interpolate(progress.value, [0, 0.5], [1, 0], "clamp") }));
    const rowLabel = useAnimatedStyle(() => ({ opacity: interpolate(progress.value, [0.5, 1], [0, 1], "clamp") }));
    return (
        <Animated.View style={rect}>
            <Press label="New Lemma" onPress={onPress} style={{ flex: 1 }}>
                <Animated.View style={[face, { borderRadius: Math.round(grid.face * 0.27), borderWidth: 1.5, borderStyle: "dashed", borderColor: color.line2, alignItems: "center", justifyContent: "center" }]}>
                    <Icon name="plus" size={24} tone={color.ink3} />
                </Animated.View>
                <Animated.View style={[{ position: "absolute", left: 0, right: 0, top: GRID_LABEL_TOP, alignItems: "center" }, gridLabel]} pointerEvents="none">
                    <Text size={13} tone={color.ink2}>New Lemma</Text>
                </Animated.View>
                <Animated.View style={[{ position: "absolute", left: LIST.faceLeft + LIST.face + 14, top: 0, bottom: 0, justifyContent: "center" }, rowLabel]} pointerEvents="none">
                    <Text size={16} tone={color.ink2}>New Lemma</Text>
                </Animated.View>
            </Press>
        </Animated.View>
    );
}

