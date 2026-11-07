import { useEffect, useRef, useState } from "react";
import { Animated, PanResponder, View, type LayoutChangeEvent } from "react-native";
import { Image } from "expo-image";
import { useVideoPlayer, VideoView } from "expo-video";
import { useAudioPlayer, useAudioPlayerStatus } from "expo-audio";
import { Press } from "../../../ui/press";
import { Num, Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { FilesIcon } from "../glyphs";

/** Images, video and audio, shown as the things they are: no card, no
 *  header over them. Each is drawn from its signed link, which needs no
 *  headers; one that will not draw says so, and the screen offers the file
 *  instead (`onFail`). */

const MAX_ZOOM = 5;
const DOUBLE_TAP_MS = 280;

/** An image that fills the screen and zooms: pinch with two fingers, drag
 *  once zoomed, tap twice to zoom in or back out. */
export function ZoomImage({ uri, label, onFail }: { uri: string; label: string; onFail: () => void }) {
    const [box, setBox] = useState({ width: 0, height: 0 });
    const scale = useRef(new Animated.Value(1)).current;
    const shiftX = useRef(new Animated.Value(0)).current;
    const shiftY = useRef(new Animated.Value(0)).current;
    const now = useRef({ scale: 1, x: 0, y: 0 });
    const start = useRef({ scale: 1, x: 0, y: 0, distance: 0 });
    const lastTap = useRef(0);
    const boxRef = useRef(box);
    boxRef.current = box;

    const clamp = (x: number, y: number, s: number) => {
        const maxX = (boxRef.current.width * (s - 1)) / 2;
        const maxY = (boxRef.current.height * (s - 1)) / 2;
        return { x: Math.max(-maxX, Math.min(maxX, x)), y: Math.max(-maxY, Math.min(maxY, y)) };
    };
    const settle = (s: number, x: number, y: number, animated: boolean) => {
        const at = clamp(x, y, s);
        now.current = { scale: s, ...at };
        if (animated) {
            Animated.parallel([
                Animated.spring(scale, { toValue: s, useNativeDriver: true, friction: 8 }),
                Animated.spring(shiftX, { toValue: at.x, useNativeDriver: true, friction: 8 }),
                Animated.spring(shiftY, { toValue: at.y, useNativeDriver: true, friction: 8 }),
            ]).start();
        } else {
            scale.setValue(s);
            shiftX.setValue(at.x);
            shiftY.setValue(at.y);
        }
    };
    const spread = (touches: readonly { pageX: number; pageY: number }[]) =>
        touches.length < 2 ? 0 : Math.hypot(touches[0].pageX - touches[1].pageX, touches[0].pageY - touches[1].pageY);

    const responder = useRef(PanResponder.create({
        onStartShouldSetPanResponder: () => true,
        onMoveShouldSetPanResponder: (event, gesture) => gesture.numberActiveTouches > 1 || now.current.scale > 1,
        onPanResponderGrant: (event) => {
            start.current = { ...now.current, distance: spread(event.nativeEvent.touches) };
        },
        onPanResponderMove: (event, gesture) => {
            const touches = event.nativeEvent.touches;
            if (touches.length >= 2) {
                const distance = spread(touches);
                if (!start.current.distance) start.current = { ...now.current, distance };
                const s = Math.max(1, Math.min(MAX_ZOOM, (start.current.scale * distance) / start.current.distance));
                settle(s, start.current.x, start.current.y, false);
            } else if (now.current.scale > 1) {
                settle(now.current.scale, start.current.x + gesture.dx, start.current.y + gesture.dy, false);
            }
        },
        onPanResponderRelease: (_event, gesture) => {
            const tapped = Math.abs(gesture.dx) < 6 && Math.abs(gesture.dy) < 6 && gesture.numberActiveTouches <= 1;
            if (tapped) {
                const at = Date.now();
                if (at - lastTap.current < DOUBLE_TAP_MS) {
                    lastTap.current = 0;
                    settle(now.current.scale > 1.2 ? 1 : 2.5, 0, 0, true);
                    return;
                }
                lastTap.current = at;
            }
            if (now.current.scale < 1.05) settle(1, 0, 0, true);
        },
        onPanResponderTerminationRequest: () => now.current.scale <= 1,
    })).current;

    return (
        <View style={{ flex: 1, overflow: "hidden", backgroundColor: color.wash }} onLayout={(event: LayoutChangeEvent) => setBox(event.nativeEvent.layout)} {...responder.panHandlers}>
            <Animated.View style={{ flex: 1, transform: [{ translateX: shiftX }, { translateY: shiftY }, { scale }] }}>
                <Image source={{ uri }} style={{ flex: 1 }} contentFit="contain" accessibilityLabel={label} onError={onFail} transition={120} />
            </Animated.View>
        </View>
    );
}

export function VideoPlayer({ uri, onFail }: { uri: string; onFail: () => void }) {
    const player = useVideoPlayer(uri, (made) => { made.loop = false; });
    useEffect(() => {
        const listening = player.addListener("statusChange", ({ status }) => {
            if (status === "error") onFail();
        });
        return () => listening.remove();
    }, [player, onFail]);
    return (
        <View style={{ flex: 1, justifyContent: "center", backgroundColor: color.wash }}>
            <VideoView player={player} style={{ width: "100%", height: "100%" }} contentFit="contain" nativeControls />
        </View>
    );
}

function clock(seconds: number): string {
    if (!Number.isFinite(seconds) || seconds < 0) return "0:00";
    const whole = Math.floor(seconds);
    const hours = Math.floor(whole / 3600);
    const minutes = Math.floor((whole % 3600) / 60);
    const rest = String(whole % 60).padStart(2, "0");
    return hours ? `${hours}:${String(minutes).padStart(2, "0")}:${rest}` : `${minutes}:${rest}`;
}

/** A recording: play and pause, where it is, and a line to tap to go there. */
export function AudioPlayer({ uri, name }: { uri: string; name: string }) {
    const player = useAudioPlayer(uri);
    const status = useAudioPlayerStatus(player);
    const [width, setWidth] = useState(0);
    const fill = status.duration > 0 ? Math.min(1, status.currentTime / status.duration) : 0;
    const toggle = () => {
        if (status.playing) player.pause();
        else {
            if (status.didJustFinish || (status.duration > 0 && status.currentTime >= status.duration - 0.25)) void player.seekTo(0);
            player.play();
        }
    };
    return (
        <View style={{ flex: 1, justifyContent: "center", paddingHorizontal: 28, gap: 26 }}>
            <View style={{ alignItems: "center", gap: 10 }}>
                <FilesIcon name="audio" size={44} tone={color.ink2} width={1.5} />
                <Text size={17} numberOfLines={2} style={{ textAlign: "center" }}>{name}</Text>
            </View>
            <View style={{ gap: 8 }}>
                <Press
                    label="Go to a point in the recording"
                    onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
                    onPress={(event) => { if (width > 0 && status.duration > 0) void player.seekTo((event.nativeEvent.locationX / width) * status.duration); }}
                    style={{ height: 28, justifyContent: "center" }}
                >
                    <View style={{ height: 4, borderRadius: 2, backgroundColor: color.track }}>
                        <View style={{ width: `${fill * 100}%`, height: 4, borderRadius: 2, backgroundColor: status.playing ? color.violet : color.ink }} />
                    </View>
                </Press>
                <View style={{ flexDirection: "row", justifyContent: "space-between" }}>
                    <Num size={13} tone={color.ink3}>{clock(status.currentTime)}</Num>
                    <Num size={13} tone={color.ink3}>{status.isLoaded ? clock(status.duration) : "…"}</Num>
                </View>
            </View>
            <Press label={status.playing ? "Pause" : "Play"} onPress={toggle} disabled={!status.isLoaded} style={{ alignSelf: "center", width: 64, height: 64, borderRadius: 32, backgroundColor: color.ink, alignItems: "center", justifyContent: "center", opacity: status.isLoaded ? 1 : 0.4, minHeight: TOUCH }}>
                {status.playing ? (
                    <View style={{ flexDirection: "row", gap: 6 }}>
                        <View style={{ width: 5, height: 20, borderRadius: 1.5, backgroundColor: color.onInk }} />
                        <View style={{ width: 5, height: 20, borderRadius: 1.5, backgroundColor: color.onInk }} />
                    </View>
                ) : (
                    <View style={{ marginLeft: 4, width: 0, height: 0, borderTopWidth: 11, borderBottomWidth: 11, borderLeftWidth: 18, borderTopColor: "transparent", borderBottomColor: "transparent", borderLeftColor: color.onInk }} />
                )}
            </Press>
        </View>
    );
}
