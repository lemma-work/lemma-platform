import { useRef, useState } from "react";
import { ActivityIndicator, Pressable, View } from "react-native";
import Animated, { Easing, ReduceMotion, cancelAnimation, runOnJS, useAnimatedStyle, useSharedValue, withTiming } from "react-native-reanimated";
import { haptics } from "../ui/haptics";
import { leave } from "../ui/motion";
import { Text } from "../ui/text";

/** How long a consequential approval is held. */
export const HOLD_MS = 1000;

/** A yes that has to be held for a second, for what cannot be taken back:
 *  a fill sweeps the button while it is held, a firm tap says the hold
 *  began and a success says it went. Let go early and it drains away.
 *
 *  The hold is a timer, not a decoration, so reduce motion does not end it
 *  at once. A screen reader's double tap approves: holding is a way of
 *  saying "are you sure" to a thumb, and the reader has already been told
 *  what this does. */
export function HoldButton({ title, onConfirm, busy = false, disabled = false, tone, onTone, ground, hint, height = 52, size = 16, grow = false }: {
    title: string;
    height?: number;
    size?: number;
    grow?: boolean;
    onConfirm: () => void;
    busy?: boolean;
    disabled?: boolean;
    /** The fill, and the words before it. */
    tone: string;
    /** The words over the fill. */
    onTone: string;
    /** The button before the fill. */
    ground: string;
    hint: string;
}) {
    const fill = useSharedValue(0);
    const [width, setWidth] = useState(0);
    const [holding, setHolding] = useState(false);
    const done = useRef(false);
    const off = disabled || busy;

    const confirm = () => {
        if (done.current) return;
        done.current = true;
        setHolding(false);
        haptics.done();
        onConfirm();
        /* Ready again if what it started fails and the card stays. */
        setTimeout(() => {
            done.current = false;
            fill.value = withTiming(0, leave);
        }, 600);
    };
    const begin = () => {
        if (off || done.current) return;
        setHolding(true);
        haptics.firm();
        fill.value = withTiming(1, { duration: HOLD_MS * (1 - fill.value), easing: Easing.linear, reduceMotion: ReduceMotion.Never }, (finished) => {
            if (finished) runOnJS(confirm)();
        });
    };
    const end = () => {
        if (done.current) return;
        setHolding(false);
        cancelAnimation(fill);
        fill.value = withTiming(0, leave);
    };

    const sweep = useAnimatedStyle(() => ({ width: fill.value * width }));
    const label = busy ? title : holding ? "Keep holding" : title;
    const words = (tint: string) => (
        <View style={{ width, height: "100%", flexDirection: "row", alignItems: "center", justifyContent: "center", gap: 8 }}>
            {busy ? <ActivityIndicator size="small" color={tint} /> : <Text size={size} weight="medium" tone={tint} numberOfLines={1}>{label}</Text>}
        </View>
    );
    return (
        <Pressable
            accessibilityRole="button"
            accessibilityLabel={title}
            accessibilityHint={hint}
            accessibilityState={{ disabled: off, busy }}
            accessibilityActions={[{ name: "activate" }]}
            onAccessibilityAction={(event) => { if (event.nativeEvent.actionName === "activate" && !off) confirm(); }}
            onPressIn={begin}
            onPressOut={end}
            disabled={off}
            onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
            style={{ height, borderRadius: 14, backgroundColor: ground, overflow: "hidden", opacity: disabled ? 0.45 : 1, flexGrow: grow ? 1 : 0, flexBasis: grow ? 0 : "auto" }}
        >
            {words(tone)}
            <Animated.View pointerEvents="none" style={[{ position: "absolute", left: 0, top: 0, bottom: 0, backgroundColor: tone, overflow: "hidden" }, sweep]}>
                {words(onTone)}
            </Animated.View>
        </Pressable>
    );
}
