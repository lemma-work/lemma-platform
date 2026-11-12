import { useEffect, useState, type ReactNode } from "react";
import { Modal, Pressable, StyleSheet, View, useWindowDimensions } from "react-native";
import { Gesture, GestureDetector, GestureHandlerRootView } from "react-native-gesture-handler";
import Animated, { interpolate, runOnJS, useAnimatedStyle, useSharedValue, withSpring, withTiming, type SharedValue } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { haptics } from "./haptics";
import { leave, spring } from "./motion";
import { color } from "./tokens";

/** A drawer from the bottom that springs up and follows the finger: drag it
 *  down, flick it, or tap outside to let it go. `above` is drawn over the
 *  drawer and the scrim, in window coordinates, for something that flies in
 *  from where it was touched (a Lemma's face). `progress` (0 closed, 1 open)
 *  lets that follow the drawer. */
export function Drawer({ open, onClose, height, children, above, instant = false }: {
    open: boolean;
    /** Leave at once rather than slide away: something is opening over it. */
    instant?: boolean;
    onClose: () => void;
    /** The drawer's height; at most most of the screen. */
    height: number;
    children: ReactNode;
    /** Gets the drawer's top edge as laid out, in the same window coordinates. */
    above?: (progress: SharedValue<number>, top: number | null) => ReactNode;
}) {
    const insets = useSafeAreaInsets();
    const { height: screen } = useWindowDimensions();
    const tall = Math.min(height + insets.bottom, screen * 0.9);
    const offset = useSharedValue(tall);
    const progress = useSharedValue(0);
    const [shown, setShown] = useState(open);
    const [top, setTop] = useState<number | null>(null);

    useEffect(() => {
        if (open) {
            setShown(true);
            offset.value = tall;
            offset.value = withSpring(0, spring);
            progress.value = withSpring(1, spring);
            haptics.firm();
        } else if (shown && instant) {
            setShown(false);
            setTop(null);
        } else if (shown) {
            offset.value = withTiming(tall, leave);
            progress.value = withTiming(0, leave, (finished) => {
                if (finished) runOnJS(setShown)(false);
            });
        }
    }, [open, tall, shown, instant, offset, progress]);

    const drag = Gesture.Pan()
        .activeOffsetY(8)
        .onUpdate((event) => {
            offset.value = Math.max(0, event.translationY);
            progress.value = 1 - offset.value / tall;
        })
        .onEnd((event) => {
            if (event.translationY > tall * 0.28 || event.velocityY > 900) {
                runOnJS(onClose)();
            } else {
                offset.value = withSpring(0, spring);
                progress.value = withSpring(1, spring);
            }
        });

    const sheet = useAnimatedStyle(() => ({ transform: [{ translateY: offset.value }] }));
    const scrim = useAnimatedStyle(() => ({ opacity: interpolate(progress.value, [0, 1], [0, 1]) }));

    /* Gone in the same render that let it go, when something opens over it. */
    if (!shown || (!open && instant)) return null;
    return (
        <Modal visible transparent animationType="none" statusBarTranslucent onRequestClose={onClose}>
            <GestureHandlerRootView style={{ flex: 1 }}>
                <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: color.scrim }, scrim]}>
                    <Pressable accessibilityLabel="Close" style={{ flex: 1 }} onPress={onClose} />
                </Animated.View>
                <GestureDetector gesture={drag}>
                    <Animated.View
                        onLayout={(event) => setTop(event.nativeEvent.layout.y)}
                        style={[{ position: "absolute", left: 0, right: 0, bottom: 0, height: tall, backgroundColor: color.paper, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingBottom: insets.bottom }, sheet]}
                    >
                        <View style={{ alignSelf: "center", width: 36, height: 4, borderRadius: 2, backgroundColor: color.line2, marginTop: 10, marginBottom: 6 }} />
                        {children}
                    </Animated.View>
                </GestureDetector>
                {above?.(progress, top)}
            </GestureHandlerRootView>
        </Modal>
    );
}
