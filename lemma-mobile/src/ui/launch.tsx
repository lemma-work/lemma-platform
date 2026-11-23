import { useEffect, useRef } from "react";
import { StyleSheet, useColorScheme } from "react-native";
import Animated, { Easing, runOnJS, useAnimatedStyle, useReducedMotion, useSharedValue, withDelay, withRepeat, withSequence, withTiming } from "react-native-reanimated";
import * as SplashScreen from "expo-splash-screen";
import Svg, { Circle, Defs, RadialGradient, Stop } from "react-native-svg";
import { palettes } from "./tokens";

/** The app opening: Lemma's bars, doing what they do on the web's way in.
 *
 *  It starts exactly where the native splash leaves off -- the same bars, the
 *  same size, on the same ground -- so the hand-over cannot be seen. Then the
 *  moves the web's sign-up uses (`lemma-frontend/src/styles/arrival.css` and
 *  `auth.css`): a soft glow comes up behind the bars and they lift once, in
 *  turn (`auth-lift`); while the app is still reading the session they
 *  breathe (`ast-breathe`); once it is ready they sink and fade, as they do
 *  when a Lemma is born (`.ast[data-born] .ast__bars`), and the app is there.
 *  With reduced motion it only fades. */

/** `--brand-bars`, and the native splash's ink (`scripts/icons.mjs`). */
const BARS_INK = "#5a3fd4";

/** The native splash draws the 32-unit mark at 80% of a 96pt image, centred
 *  (`expo-splash-screen` in app.json, `draw(512, 0.8, …)` in icons.mjs). */
const MARK = 96 * 0.8;
const UNIT = MARK / 32;
/** x, y, height of each bar on that grid; every bar is 5 wide. */
const BARS = [
    [4, 19, 10],
    [13, 12, 17],
    [22, 3, 26],
] as const;

const LIFT = Easing.bezier(0.3, 1.5, 0.5, 1);
const SINK = Easing.bezier(0.5, 0, 0.3, 1);
/** `auth-lift`: up to 1.28 at 45% of 900ms, then back. */
const LIFT_UP_MS = 405;
const LIFT_DOWN_MS = 495;
const STAGGER_MS = 90;
const LIFTED_MS = LIFT_UP_MS + LIFT_DOWN_MS + STAGGER_MS * 2;

export function Launch({ ready, onGone }: { ready: boolean; onGone: () => void }) {
    /* The native splash follows the system's appearance, not the app's
       setting, so this starts on the same ground it does. */
    const ground = palettes[useColorScheme() === "dark" ? "dark" : "light"].paper;
    const still = useReducedMotion();
    const glow = useSharedValue(0);
    const lift = [useSharedValue(1), useSharedValue(1), useSharedValue(1)];
    const sink = useSharedValue(0);
    const veil = useSharedValue(1);
    const lifted = useRef(still);
    const leaving = useRef(false);
    const hidden = useRef(false);
    const readyNow = useRef(ready);
    readyNow.current = ready;

    /* The native splash goes once this has drawn its first frame over it. */
    const handOver = () => {
        if (hidden.current) return;
        hidden.current = true;
        requestAnimationFrame(() => void SplashScreen.hideAsync().catch(() => undefined));
    };

    const leave = () => {
        if (leaving.current) return;
        leaving.current = true;
        if (still) {
            veil.value = withTiming(0, { duration: 200 }, (done) => { if (done) runOnJS(onGone)(); });
            return;
        }
        sink.value = withTiming(1, { duration: 420, easing: SINK });
        veil.value = withDelay(160, withTiming(0, { duration: 300 }, (done) => { if (done) runOnJS(onGone)(); }));
    };

    useEffect(() => {
        if (still) return;
        glow.value = withTiming(1, { duration: 400 });
        lift.forEach((bar, index) => {
            bar.value = withDelay(index * STAGGER_MS, withSequence(
                withTiming(1.28, { duration: LIFT_UP_MS, easing: LIFT }),
                withTiming(1, { duration: LIFT_DOWN_MS, easing: LIFT }),
                /* Breathing, until it leaves: 2.8s a breath, a quarter second apart. */
                withDelay(index * 250, withRepeat(withSequence(
                    withTiming(1.05, { duration: 1400, easing: Easing.inOut(Easing.ease) }),
                    withTiming(1, { duration: 1400, easing: Easing.inOut(Easing.ease) }),
                ), -1)),
            ));
        });
        /* Once, on launch. `ready` is read from a ref: it may arrive before
           or after the lift has played. */
        const timer = setTimeout(() => {
            lifted.current = true;
            if (readyNow.current) leave();
        }, LIFTED_MS);
        return () => clearTimeout(timer);
    }, []);

    /* Ready, and the bars have had their lift: go. */
    useEffect(() => {
        if (ready && lifted.current) leave();
    }, [ready]);

    const veilStyle = useAnimatedStyle(() => ({ opacity: veil.value }));
    const glowStyle = useAnimatedStyle(() => ({ opacity: glow.value * (1 - sink.value) }));
    const markStyle = useAnimatedStyle(() => ({
        opacity: 1 - sink.value,
        transform: [{ translateY: sink.value * MARK * 0.3 }, { scale: 1 - sink.value * 0.7 }],
    }));
    const [first, second, third] = lift;
    const barStyles = [
        useAnimatedStyle(() => ({ transform: [{ scaleY: first.value }] })),
        useAnimatedStyle(() => ({ transform: [{ scaleY: second.value }] })),
        useAnimatedStyle(() => ({ transform: [{ scaleY: third.value }] })),
    ];

    return (
        <Animated.View
            pointerEvents="none"
            onLayout={handOver}
            style={[StyleSheet.absoluteFill, { backgroundColor: ground, alignItems: "center", justifyContent: "center", zIndex: 10 }, veilStyle]}
            accessibilityElementsHidden
            importantForAccessibility="no-hide-descendants"
        >
            <Animated.View style={[{ position: "absolute", width: 360, height: 360 }, glowStyle]}>
                <Svg width={360} height={360}>
                    <Defs>
                        <RadialGradient id="launch-glow" cx="50%" cy="50%" r="50%">
                            <Stop offset="0" stopColor={BARS_INK} stopOpacity={0.16} />
                            <Stop offset="0.75" stopColor={BARS_INK} stopOpacity={0} />
                        </RadialGradient>
                    </Defs>
                    <Circle cx={180} cy={180} r={180} fill="url(#launch-glow)" />
                </Svg>
            </Animated.View>
            <Animated.View style={[{ width: MARK, height: MARK }, markStyle]}>
                {BARS.map(([x, y, height], index) => (
                    <Animated.View
                        key={x}
                        style={[
                            {
                                position: "absolute",
                                left: x * UNIT,
                                top: y * UNIT,
                                width: 5 * UNIT,
                                height: height * UNIT,
                                borderRadius: 1.5 * UNIT,
                                backgroundColor: BARS_INK,
                                transformOrigin: "bottom",
                            },
                            barStyles[index],
                        ]}
                    />
                ))}
            </Animated.View>
        </Animated.View>
    );
}
