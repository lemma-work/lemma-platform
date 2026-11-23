import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Modal, View, useWindowDimensions, type NativeScrollEvent, type NativeSyntheticEvent } from "react-native";
import { Image } from "expo-image";
import * as FileSystem from "expo-file-system/legacy";
import { StatusBar } from "expo-status-bar";
/* The list is gesture-handler's own, so a sideways swipe reaches it past
   the swipe-down and the pinch around it. */
import { FlatList, Gesture, GestureDetector, GestureHandlerRootView } from "react-native-gesture-handler";
import Animated, { interpolate, runOnJS, useAnimatedStyle, useSharedValue, withSpring, withTiming } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { shareFile } from "../../data/io";
import { haptics } from "../haptics";
import { Icon } from "../icons";
import { leave, settle, spring } from "../motion";
import { Press } from "../press";
import { Num, Text } from "../text";
import { color, TOUCH } from "../tokens";
import type { Rect } from "./grow";
import { FileGlyph } from "./glyphs";
import { ofCount } from "./model";

/** A photo a chat shows: where to read it from (a signed address, a file on
 *  this phone, or none yet), and what it is called. */
export interface Photo {
    key: string;
    uri: string | null;
    name: string;
    /** Its MIME type, for the share sheet. */
    type?: string;
}

const MAX_ZOOM = 4;
/** How long to wait for the window to say it is up before opening anyway. */
export const SHOW_FALLBACK_MS = 1500;

/** Photos whole screen, on black: swipe between them, pinch or double-tap
 *  to look closer, "1 of 5", share, and swipe down to put them away. It
 *  opens out of the photo that was touched and, closed on that photo, goes
 *  back into it. */
export function PhotoViewer({ photos, start, from, onClose }: { photos: readonly Photo[]; start: number; from: Rect | null; onClose: () => void }) {
    const { width, height } = useWindowDimensions();
    const insets = useSafeAreaInsets();
    const [index, setIndex] = useState(start);
    const [zoomed, setZoomed] = useState(false);
    const [chrome, setChrome] = useState(true);
    const enter = useSharedValue(0);
    const drop = useSharedValue(0);
    const leaving = useRef(false);

    /* Only once the window is up (`onShow`): started before, in a slow
       build the whole opening can run before Android has attached the
       window, and the photo is left where it was. */
    const shown = useRef(false);
    const show = () => {
        if (shown.current) return;
        shown.current = true;
        enter.value = withTiming(1, settle);
    };
    useEffect(() => {
        /* In case the window never says so. */
        const late = setTimeout(show, SHOW_FALLBACK_MS);
        return () => clearTimeout(late);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const done = useCallback(() => onClose(), [onClose]);
    /* Back into the photo it came out of, when that is the one showing;
       otherwise it fades. */
    const close = useCallback(() => {
        if (leaving.current) return;
        leaving.current = true;
        if (index === start && from) enter.value = withTiming(0, settle, (finished) => { if (finished) runOnJS(done)(); });
        else enter.value = withTiming(0, leave, (finished) => { if (finished) runOnJS(done)(); });
    }, [index, start, from, enter, done]);
    const dropAway = useCallback(() => {
        if (leaving.current) return;
        leaving.current = true;
        haptics.tick();
        drop.value = withTiming(height, leave, (finished) => { if (finished) runOnJS(done)(); });
    }, [drop, height, done]);

    const swipe = Gesture.Pan()
        .enabled(!zoomed)
        .activeOffsetY([-14, 14])
        .failOffsetX([-14, 14])
        .onUpdate((event) => {
            drop.value = Math.max(-60, event.translationY);
        })
        .onEnd((event) => {
            if (event.translationY > 140 || event.velocityY > 900) runOnJS(dropAway)();
            else drop.value = withSpring(0, spring);
        });

    const ground = useAnimatedStyle(() => ({ opacity: enter.value * (1 - Math.min(1, Math.max(0, drop.value) / 420) * 0.85) }));
    const page = useAnimatedStyle(() => {
        const pulled = Math.max(0, drop.value);
        const shrink = 1 - Math.min(0.18, pulled / 1600);
        /* Out of the touched photo: from its centre and its width. */
        const rect = from && index === start ? from : null;
        const fromScale = rect ? rect.width / width : 0.92;
        const scale = interpolate(enter.value, [0, 1], [fromScale, 1]) * shrink;
        const x = rect ? interpolate(enter.value, [0, 1], [rect.x + rect.width / 2 - width / 2, 0]) : 0;
        const y = rect ? interpolate(enter.value, [0, 1], [rect.y + rect.height / 2 - height / 2, 0]) : 0;
        return { opacity: rect ? 1 : enter.value, transform: [{ translateX: x }, { translateY: y + drop.value }, { scale }] };
    });
    const controls = useAnimatedStyle(() => ({ opacity: interpolate(enter.value, [0.6, 1], [0, 1], "clamp") * (1 - Math.min(1, Math.max(0, drop.value) / 160)) }));

    const current = photos[index] ?? photos[0];
    const onScrolled = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
        const at = Math.round(event.nativeEvent.contentOffset.x / width);
        if (at !== index && at >= 0 && at < photos.length) {
            setIndex(at);
            haptics.tick();
        }
    };

    return (
        <Modal visible transparent animationType="none" statusBarTranslucent onShow={show} onRequestClose={close}>
            <StatusBar style="light" />
            <GestureHandlerRootView style={{ flex: 1 }}>
                <Animated.View pointerEvents="none" style={[{ position: "absolute", left: 0, top: 0, right: 0, bottom: 0, backgroundColor: color.photo }, ground]} />
                <GestureDetector gesture={swipe}>
                    <Animated.View style={[{ flex: 1 }, page]}>
                        <FlatList
                            data={photos as Photo[]}
                            keyExtractor={(photo) => photo.key}
                            horizontal
                            pagingEnabled
                            scrollEnabled={!zoomed && photos.length > 1}
                            showsHorizontalScrollIndicator={false}
                            initialScrollIndex={start}
                            getItemLayout={(_, at) => ({ length: width, offset: width * at, index: at })}
                            onMomentumScrollEnd={onScrolled}
                            renderItem={({ item }) => (
                                <ZoomPage photo={item} width={width} height={height} onZoomed={setZoomed} onTap={() => setChrome((was) => !was)} />
                            )}
                        />
                    </Animated.View>
                </GestureDetector>
                {chrome ? (
                    <Animated.View pointerEvents="box-none" style={[{ position: "absolute", left: 0, right: 0, top: 0, paddingTop: insets.top + 4, paddingHorizontal: 8, flexDirection: "row", alignItems: "center" }, controls]}>
                        <Press label="Close" onPress={close} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                            <Icon name="close" size={22} width={2} tone={color.onPhoto} />
                        </Press>
                        <View style={{ flex: 1, alignItems: "center" }}>
                            {photos.length > 1 ? <Num size={15} tone={color.onPhoto} accessibilityLiveRegion="polite">{ofCount(index, photos.length)}</Num> : null}
                        </View>
                        <Press label={`Share ${current.name}`} onPress={() => void share(current)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                            <FileGlyph name="share" size={21} tone={color.onPhoto} />
                        </Press>
                    </Animated.View>
                ) : null}
                {chrome ? (
                    <Animated.View pointerEvents="none" style={[{ position: "absolute", left: 20, right: 20, bottom: insets.bottom + 18, alignItems: "center" }, controls]}>
                        <Text size={13.5} tone={color.onPhoto} numberOfLines={1}>{current.name}</Text>
                    </Animated.View>
                ) : null}
            </GestureHandlerRootView>
        </Modal>
    );
}

/** One photo, which pinches and double-taps closer and pans while close. */
function ZoomPage({ photo, width, height, onZoomed, onTap }: { photo: Photo; width: number; height: number; onZoomed: (zoomed: boolean) => void; onTap: () => void }) {
    const scale = useSharedValue(1);
    const held = useSharedValue(1);
    const x = useSharedValue(0);
    const y = useSharedValue(0);
    const heldX = useSharedValue(0);
    const heldY = useSharedValue(0);
    const [close, setClose] = useState(false);
    const zoom = (on: boolean) => {
        setClose(on);
        onZoomed(on);
    };
    const bound = (value: number, size: number, s: number) => {
        "worklet";
        const most = (size * (s - 1)) / 2;
        return Math.max(-most, Math.min(most, value));
    };
    const pinch = Gesture.Pinch()
        .onStart(() => { held.value = scale.value; })
        .onUpdate((event) => { scale.value = Math.max(1, Math.min(MAX_ZOOM, held.value * event.scale)); })
        .onEnd(() => {
            if (scale.value < 1.05) {
                scale.value = withSpring(1, spring);
                x.value = withSpring(0, spring);
                y.value = withSpring(0, spring);
                runOnJS(zoom)(false);
            } else runOnJS(zoom)(true);
        });
    const pan = Gesture.Pan()
        .enabled(close)
        .onStart(() => { heldX.value = x.value; heldY.value = y.value; })
        .onUpdate((event) => {
            x.value = bound(heldX.value + event.translationX, width, scale.value);
            y.value = bound(heldY.value + event.translationY, height, scale.value);
        });
    const twice = Gesture.Tap()
        .numberOfTaps(2)
        .onEnd((event) => {
            if (scale.value > 1.2) {
                scale.value = withTiming(1, settle);
                x.value = withTiming(0, settle);
                y.value = withTiming(0, settle);
                runOnJS(zoom)(false);
            } else {
                const s = 2.5;
                scale.value = withTiming(s, settle);
                x.value = withTiming(bound((width / 2 - event.x) * (s - 1), width, s), settle);
                y.value = withTiming(bound((height / 2 - event.y) * (s - 1), height, s), settle);
                runOnJS(zoom)(true);
            }
        });
    const once = Gesture.Tap().onEnd(() => { runOnJS(onTap)(); });
    const gesture = Gesture.Simultaneous(pinch, pan, Gesture.Exclusive(twice, once));
    const style = useAnimatedStyle(() => ({ transform: [{ translateX: x.value }, { translateY: y.value }, { scale: scale.value }] }));
    return (
        <GestureDetector gesture={gesture}>
            <Animated.View style={[{ width, height, alignItems: "center", justifyContent: "center" }, style]}>
                {photo.uri ? (
                    <Image source={{ uri: photo.uri }} style={{ width, height }} contentFit="contain" accessibilityLabel={photo.name} transition={120} />
                ) : (
                    <Text size={14} tone={color.onPhoto}>This photo is not here yet.</Text>
                )}
            </Animated.View>
        </GestureDetector>
    );
}

/** Onto the phone's share sheet: a photo on the web is fetched into the
 *  cache first (its address is signed, so it needs no headers). */
async function share(photo: Photo): Promise<void> {
    if (!photo.uri) return;
    try {
        let local = photo.uri;
        const safe = photo.name.replace(/[^\w.\- ]+/g, "_").slice(-80) || "photo.jpg";
        if (/^https?:/.test(local)) {
            local = (await FileSystem.downloadAsync(local, `${FileSystem.cacheDirectory}lemma-share-${Date.now()}-${safe}`)).uri;
        } else if (local.startsWith("data:")) {
            const [head, body] = local.split(",", 2);
            if (!/;base64$/.test(head)) throw new Error("Sample data has no photo to share.");
            local = `${FileSystem.cacheDirectory}lemma-share-${Date.now()}-${safe}`;
            await FileSystem.writeAsStringAsync(local, body, { encoding: FileSystem.EncodingType.Base64 });
        }
        await shareFile(local, photo.type);
    } catch (problem) {
        Alert.alert("Not shared", problem instanceof Error ? problem.message : "That photo could not be shared.");
    }
}
