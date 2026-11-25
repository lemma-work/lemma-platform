import { useEffect } from "react";
import { View } from "react-native";
import Animated, { Easing, ReduceMotion, useAnimatedStyle, useSharedValue, withDelay, withRepeat, withSequence, withTiming } from "react-native-reanimated";
import { color } from "./tokens";

/** The sidebar's live bars (`LiveBars`): the logo's three bars, rising and
 *  falling while a Lemma is working. Still with reduced motion. */
export function LiveBars({ size = 12 }: { size?: number }) {
    return (
        <View accessibilityLabel="Working now" style={{ flexDirection: "row", alignItems: "flex-end", gap: 1.5, height: size }}>
            {[0.45, 1, 0.7].map((tall, index) => <Bar key={index} tall={tall} size={size} delay={index * 150} />)}
        </View>
    );
}

function Bar({ tall, size, delay }: { tall: number; size: number; delay: number }) {
    const lift = useSharedValue(1);
    useEffect(() => {
        const step = { duration: 550, easing: Easing.inOut(Easing.sin), reduceMotion: ReduceMotion.System };
        lift.value = withDelay(delay, withRepeat(withSequence(withTiming(0.45, step), withTiming(1, step)), -1));
    }, [delay, lift]);
    const style = useAnimatedStyle(() => ({ transform: [{ scaleY: lift.value }] }));
    return <Animated.View style={[{ width: 2.5, height: size * tall, borderRadius: 1, backgroundColor: color.violet, transformOrigin: "bottom" }, style]} />;
}
