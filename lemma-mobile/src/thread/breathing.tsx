import { useEffect, type ReactNode } from "react";
import { View } from "react-native";
import Animated, { Easing, ReduceMotion, interpolate, useAnimatedStyle, useSharedValue, withRepeat, withTiming } from "react-native-reanimated";
import { BREATH_MS } from "../ui/motion";
import { color } from "../ui/tokens";

/** Something running, breathing: the ring a running Lemma's face wears on
 *  the Lemmas tab, and the small dot beside the step a run is on. One slow
 *  breath for both; still under reduce motion. */

function useBreath(on: boolean) {
    const breath = useSharedValue(0);
    useEffect(() => {
        if (on) breath.value = withRepeat(withTiming(1, { duration: BREATH_MS, easing: Easing.inOut(Easing.sin), reduceMotion: ReduceMotion.System }), -1, true);
        else breath.value = withTiming(0, { duration: 200 });
    }, [on, breath]);
    return breath;
}

/** A Lemma's face, while it runs: a soft violet circle around the character
 *  holds and a second breathes out from it, and the character bobs, as if at
 *  work. The Lemmas tab's `Ringed`, for a face beside a chat's title. */
export function BreathingRing({ running, size, children }: { running: boolean; size: number; children: ReactNode }) {
    const inset = Math.max(4, Math.round(size * 0.15));
    const breath = useBreath(running);
    const halo = useAnimatedStyle(() => ({ opacity: interpolate(breath.value, [0, 1], [0.85, 0]), transform: [{ scale: interpolate(breath.value, [0, 1], [1, 1.2]) }] }));
    const lift = Math.max(1, size * 0.05);
    const bob = useAnimatedStyle(() => ({ transform: [{ translateY: interpolate(breath.value, [0, 1], [lift * 0.6, -lift]) }] }));
    const ring = { position: "absolute" as const, left: -inset, top: -inset, right: -inset, bottom: -inset, borderRadius: size / 2 + inset, borderWidth: 2, borderColor: color.violet };
    return (
        <View style={{ width: size, height: size }}>
            {running ? <View pointerEvents="none" style={ring} /> : null}
            {running ? <Animated.View pointerEvents="none" style={[ring, halo]} /> : null}
            <Animated.View style={running ? bob : undefined}>{children}</Animated.View>
        </View>
    );
}

/** The dot beside a run's current step, in the Lemma's colour. */
export function BreathingDot({ tone, size = 7 }: { tone: string; size?: number }) {
    const breath = useBreath(true);
    const style = useAnimatedStyle(() => ({ opacity: interpolate(breath.value, [0, 1], [1, 0.35]), transform: [{ scale: interpolate(breath.value, [0, 1], [1, 0.72]) }] }));
    return <Animated.View style={[{ width: size, height: size, borderRadius: size / 2, backgroundColor: tone }, style]} />;
}
