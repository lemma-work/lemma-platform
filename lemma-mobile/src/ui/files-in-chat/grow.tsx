import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Modal, View, useWindowDimensions } from "react-native";
import { useFocusEffect } from "expo-router";
import Animated, { interpolate, runOnJS, useAnimatedStyle, useSharedValue, withTiming } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Icon } from "../icons";
import { leave, settle } from "../motion";
import { Press } from "../press";
import { Text } from "../text";
import { color, TOUCH } from "../tokens";

/** A card that grows to the whole screen and shrinks back into its place.
 *
 *  The card is measured where it sits (`rectOf`) and a whole-screen surface
 *  travels out of that rect on the one curve, its content laid out at full
 *  size from the start and uncovered as the surface grows, so nothing
 *  reflows on the way. Closing travels back into the card's place (measured
 *  again, in case the chat moved under it). Used by everything a chat can
 *  open in place: a widget, a table, a document, a photo. */

export interface Rect {
    x: number;
    y: number;
    width: number;
    height: number;
}

/** Where a view sits in the window, or null when it is not laid out. */
export function rectOf(view: View | null): Promise<Rect | null> {
    return new Promise((resolve) => {
        if (!view) {
            resolve(null);
            return;
        }
        view.measureInWindow((x, y, width, height) => resolve(width > 0 && height > 0 ? { x, y, width, height } : null));
    });
}

/** `open` grows it out of `from`; false shrinks it back into `back` (or
 *  `from`) and then calls `onClosed`, when the caller lets it go. `fade`
 *  leaves by fading instead: something else has opened under it. */
export function Grown({ from, back, open, start = 0, fade = false, onGrown, onClosed, onRequestClose, ground = color.paper, radius = 14, children }: {
    from: Rect | null;
    /** 1 to begin whole screen, to shrink back into a card on the way back
     *  to it from a screen it grew into. */
    start?: 0 | 1;
    back?: Rect | null;
    open: boolean;
    fade?: boolean;
    onGrown?: () => void;
    onClosed: () => void;
    /** Android's back, while it is open. */
    onRequestClose?: () => void;
    ground?: string;
    radius?: number;
    children: ReactNode;
}) {
    const { width, height } = useWindowDimensions();
    const progress = useSharedValue<number>(start);
    const veil = useSharedValue(1);
    const [target, setTarget] = useState<Rect | null>(from);
    /* Nothing moves until the window is up (`onShow`): started before, in a
       slow build the whole move can run before Android has attached the
       window, and the card is left where it was. */
    const [shown, setShown] = useState(false);
    useEffect(() => {
        const late = setTimeout(() => setShown(true), 1500);
        return () => clearTimeout(late);
    }, []);
    const grown = useRef(onGrown);
    grown.current = onGrown;
    const closed = useRef(onClosed);
    closed.current = onClosed;

    useEffect(() => {
        if (!shown) return;
        /* The callbacks themselves go to the animation, never the refs that
           hold them: a ref handed to a worklet can no longer be written. */
        const onGrown = grown.current;
        const onClosed = closed.current;
        if (open) {
            setTarget(from);
            veil.value = 1;
            progress.value = withTiming(1, settle, (finished) => {
                if (finished && onGrown) runOnJS(onGrown)();
            });
            return;
        }
        if (fade) {
            veil.value = withTiming(0, leave, (finished) => {
                if (finished) runOnJS(onClosed)();
            });
            return;
        }
        setTarget(back ?? from);
        progress.value = withTiming(0, settle, (finished) => {
            if (finished) runOnJS(onClosed)();
        });
    }, [shown, open, fade, from, back, progress, veil]);

    const box = useAnimatedStyle(() => {
        const rect = target ?? { x: width * 0.1, y: height * 0.3, width: width * 0.8, height: height * 0.4 };
        return {
            position: "absolute",
            left: interpolate(progress.value, [0, 1], [rect.x, 0]),
            top: interpolate(progress.value, [0, 1], [rect.y, 0]),
            width: interpolate(progress.value, [0, 1], [rect.width, width]),
            height: interpolate(progress.value, [0, 1], [rect.height, height]),
            borderRadius: interpolate(progress.value, [0, 1], [radius, 0]),
            /* Clear at the card's own size, so the card under it is what is
               seen as the box leaves it and as it lands back on it. */
            opacity: veil.value * interpolate(progress.value, [0, 0.18], [0, 1], "clamp"),
        };
    });
    /* The content stays where it will be at full size, uncovered by the
       growing box rather than squeezed into it. */
    const inner = useAnimatedStyle(() => {
        const rect = target ?? { x: 0, y: 0, width, height };
        return {
            position: "absolute",
            width,
            height,
            left: -interpolate(progress.value, [0, 1], [rect.x, 0]),
            top: -interpolate(progress.value, [0, 1], [rect.y, 0]),
            opacity: interpolate(progress.value, [0.35, 0.9], [0, 1], "clamp"),
        };
    });

    return (
        <Modal visible transparent animationType="none" statusBarTranslucent onShow={() => setShown(true)} onRequestClose={onRequestClose}>
            <Animated.View style={[{ overflow: "hidden", backgroundColor: ground }, box]}>
                <Animated.View style={inner}>{children}</Animated.View>
            </Animated.View>
        </Modal>
    );
}

/** Opening and closing a `Grown`, for a card: measure, grow, shrink back
 *  into wherever the card is by then. */
export function useGrow() {
    const card = useRef<View>(null);
    const [state, setState] = useState<{ from: Rect | null; back: Rect | null; open: boolean } | null>(null);
    return {
        card,
        shown: state !== null,
        open: state?.open ?? false,
        from: state?.from ?? null,
        back: state?.back ?? null,
        grow: async () => {
            const from = await rectOf(card.current);
            setState({ from, back: null, open: true });
        },
        shrink: async () => {
            const back = await rectOf(card.current);
            setState((was) => (was ? { ...was, back: back ?? was.from, open: false } : was));
        },
        closed: () => setState(null),
    };
}

/** How long a card grown into a screen stays over it while it opens. */
const COVER_MS = 380;

/** A card that opens a screen by growing into it: it grows to the whole
 *  screen, `open` pushes the screen under it as it lands, and it fades off
 *  the screen. Back on this one, the screen's stand-in shrinks into the
 *  card again. `overlay` draws the stand-in. */
export function useGrowInto(open: () => void) {
    const card = useRef<View>(null);
    const [phase, setPhase] = useState<{ step: "growing" | "covering" | "returning"; from: Rect | null } | null>(null);
    const away = useRef(false);
    const opener = useRef(open);
    opener.current = open;

    useFocusEffect(useCallback(() => {
        if (!away.current) return;
        away.current = false;
        void rectOf(card.current).then((from) => setPhase(from ? { step: "returning", from } : null));
    }, []));

    const grown = () => {
        away.current = true;
        opener.current();
        setTimeout(() => setPhase((was) => (was ? { ...was, step: "covering" } : was)), COVER_MS);
    };
    return {
        card,
        grow: async () => setPhase({ step: "growing", from: await rectOf(card.current) }),
        overlay: (standIn: ReactNode) => (phase ? (
            <Grown
                from={phase.from}
                open={phase.step === "growing"}
                start={phase.step === "returning" ? 1 : 0}
                fade={phase.step === "covering"}
                onGrown={phase.step === "growing" ? grown : undefined}
                onClosed={() => setPhase(null)}
            >
                {standIn}
            </Grown>
        ) : null),
    };
}

/** A card grown to the whole screen: its name, a way to close it, room for
 *  one more action, and the card's content with all the room there is. */
export function GrownPage({ title, onClose, action, children }: { title: string; onClose: () => void; action?: ReactNode; children: ReactNode }) {
    const insets = useSafeAreaInsets();
    return (
        <View style={{ flex: 1, backgroundColor: color.paper, paddingTop: insets.top, paddingBottom: insets.bottom }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 4, height: 54, paddingHorizontal: 4, borderBottomWidth: 1, borderBottomColor: color.line }}>
                <Press label="Close" onPress={onClose} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="close" size={20} width={2} />
                </Press>
                <Text accessibilityRole="header" size={16.5} weight="medium" numberOfLines={1} style={{ flex: 1 }}>{title}</Text>
                {action}
            </View>
            <View style={{ flex: 1 }}>{children}</View>
        </View>
    );
}
