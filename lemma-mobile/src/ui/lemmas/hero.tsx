import { useEffect, useRef, useState } from "react";
import { Modal, View } from "react-native";
import Animated, { interpolate, useAnimatedStyle, useSharedValue, withTiming } from "react-native-reanimated";
import { LemmaFace } from "../face";
import { settle } from "../motion";
import type { Origin } from "./faces-board";

/** A Lemma's page, opened with its face flying on from where it was (the
 *  drawer's header): `from` rides in the address as `x,y,size`. */
export function heroHref(podId: string, from: Origin): string {
    return `/lemma/${encodeURIComponent(podId)}?from=${Math.round(from.x)},${Math.round(from.y)},${Math.round(from.size)}`;
}

export function originOf(raw: string | undefined): Origin | null {
    const parts = (raw ?? "").split(",").map(Number);
    if (parts.length !== 3 || parts.some((part) => !Number.isFinite(part))) return null;
    return { x: parts[0], y: parts[1], size: parts[2] };
}

/** The page's own face, landed on by the flying one: until it lands, the
 *  page's face is hidden and a copy travels from `from` to it. */
export function HeroFace({ id, icon, size, from }: { id: string; icon: string | null; size: number; from: Origin | null }) {
    const here = useRef<View>(null);
    const [to, setTo] = useState<Origin | null>(null);
    const [landed, setLanded] = useState(!from);
    const progress = useSharedValue(0);
    useEffect(() => {
        if (!to || landed) return;
        progress.value = withTiming(1, settle);
        const timer = setTimeout(() => setLanded(true), settle.duration ?? 480);
        return () => clearTimeout(timer);
    }, [to, landed, progress]);
    const flying = useAnimatedStyle(() => {
        if (!from || !to) return { opacity: 0 };
        const scale = interpolate(progress.value, [0, 1], [from.size / size, 1]);
        const cx = interpolate(progress.value, [0, 1], [from.x + from.size / 2, to.x + size / 2]);
        const cy = interpolate(progress.value, [0, 1], [from.y + from.size / 2, to.y + size / 2]);
        return { opacity: 1, position: "absolute", left: cx - size / 2, top: cy - size / 2, transform: [{ scale }] };
    });
    return (
        <>
            <View ref={here} collapsable={false} onLayout={() => here.current?.measureInWindow((x, y) => setTo({ x, y, size }))} style={{ opacity: landed ? 1 : 0 }}>
                <LemmaFace id={id} icon={icon} size={size} />
            </View>
            {/* Over everything, in window coordinates, for the half second it
                is in flight. */}
            {!landed && from ? (
                <Modal visible transparent animationType="none" statusBarTranslucent>
                    <Animated.View pointerEvents="none" style={flying}>
                        <LemmaFace id={id} icon={icon} size={size} />
                    </Animated.View>
                </Modal>
            ) : null}
        </>
    );
}
