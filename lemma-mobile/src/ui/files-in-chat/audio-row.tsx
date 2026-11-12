import { useEffect, useMemo, useState } from "react";
import { View } from "react-native";
import { useAudioPlayer, useAudioPlayerStatus, type AudioPlayer } from "expo-audio";
import { haptics } from "../haptics";
import { Press } from "../press";
import { Num, Text } from "../text";
import { color } from "../tokens";
import { FileGlyph } from "./glyphs";
import { barsOf, clockOfSeconds } from "./model";

const BAR = 3;
const BAR_GAP = 2;
const HEIGHT = 30;

/** The one recording playing, so starting another pauses it. */
let playing: AudioPlayer | null = null;

/** A recording in a chat, played where it was sent: play, its bars filling
 *  as it goes, how long. A tap on the bars goes to that point. Any chat
 *  draws one this way. */
export function AudioRow({ uri, name, seed, tone = color.ink, onTone = color.onInk }: {
    /** Where to play it from; null while that is not known. */
    uri: string | null;
    name: string;
    /** What its bars are drawn from: its path, say. */
    seed: string;
    /** The play button, in the chat's colour. */
    tone?: string;
    onTone?: string;
}) {
    const player = useAudioPlayer(uri ? { uri } : null);
    const status = useAudioPlayerStatus(player);
    const [width, setWidth] = useState(0);
    const count = width > 0 ? Math.max(12, Math.floor((width + BAR_GAP) / (BAR + BAR_GAP))) : 36;
    const bars = useMemo(() => barsOf(seed, count), [seed, count]);
    const fill = status.duration > 0 ? Math.min(1, status.currentTime / status.duration) : 0;
    useEffect(() => () => {
        if (playing === player) playing = null;
    }, [player]);

    const toggle = () => {
        if (status.playing) {
            player.pause();
            return;
        }
        if (playing && playing !== player) {
            try { playing.pause(); } catch { /* already released */ }
        }
        playing = player;
        if (status.didJustFinish || (status.duration > 0 && status.currentTime >= status.duration - 0.25)) void player.seekTo(0);
        haptics.tick();
        player.play();
    };
    const ready = Boolean(uri) && status.isLoaded;
    return (
        <View accessibilityLabel={`${name}, a recording${status.duration ? `, ${clockOfSeconds(status.duration)}` : ""}`} style={{ alignSelf: "stretch", flexDirection: "row", alignItems: "center", gap: 12, paddingVertical: 10, paddingLeft: 10, paddingRight: 14, borderWidth: 1, borderColor: color.line2, borderRadius: 14, backgroundColor: color.paper }}>
            <Press label={status.playing ? `Pause ${name}` : `Play ${name}`} onPress={toggle} disabled={!ready} style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: tone, alignItems: "center", justifyContent: "center", opacity: ready ? 1 : 0.45 }}>
                <View style={{ marginLeft: status.playing ? 0 : 2 }}><FileGlyph name={status.playing ? "pause" : "play"} size={17} tone={onTone} /></View>
            </Press>
            <View style={{ flex: 1, minWidth: 0, gap: 4 }}>
                <Press
                    label={`Go to a point in ${name}`}
                    disabled={!ready || status.duration <= 0}
                    onLayout={(event) => setWidth(event.nativeEvent.layout.width)}
                    onPress={(event) => { if (width > 0 && status.duration > 0) void player.seekTo((event.nativeEvent.locationX / width) * status.duration); }}
                    style={{ height: HEIGHT, flexDirection: "row", alignItems: "center", gap: BAR_GAP, overflow: "hidden" }}
                >
                    {bars.map((bar, index) => (
                        <View key={index} style={{ width: BAR, height: Math.max(3, Math.round(bar * HEIGHT)), borderRadius: BAR / 2, backgroundColor: (index + 0.5) / bars.length <= fill ? tone : color.line2 }} />
                    ))}
                </Press>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    <Text size={12.5} tone={color.ink2} numberOfLines={1} style={{ flex: 1 }}>{name}</Text>
                    <Num size={12} tone={color.ink3}>
                        {status.playing || status.currentTime > 0 ? `${clockOfSeconds(status.currentTime)} / ` : ""}{status.duration > 0 ? clockOfSeconds(status.duration) : uri ? "…" : ""}
                    </Num>
                </View>
            </View>
        </View>
    );
}
