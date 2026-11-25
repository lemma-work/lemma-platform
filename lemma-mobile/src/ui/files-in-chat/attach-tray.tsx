import { useEffect, useRef, useState } from "react";
import { ScrollView, View } from "react-native";
import { Image } from "expo-image";
import Animated, { useAnimatedStyle, useSharedValue, withTiming } from "react-native-reanimated";
import { pickFiles, pickPhotos, takePhoto, type PickedFile } from "../../data/io";
import { haptics } from "../haptics";
import { Icon } from "../icons";
import { settle } from "../motion";
import { Press } from "../press";
import { Text } from "../text";
import { color } from "../tokens";
import { FileGlyph, type FileGlyphName } from "./glyphs";
import { pickedFrom, recentPhotos, type Recent, type RecentPhoto } from "./recent-photos";

const TILE = 76;

/** The box's +, which turns into an × while the tray is up.
 *
 *  At rest it is never turned: the glyph itself is a + or an ×, and only
 *  the eighth of a turn between them moves. A turn left on the UI thread does
 *  not always outlast the app being covered (a permission prompt, the
 *  camera), and a + that should be an × says the wrong thing. */
export function AttachToggle({ open, onPress }: { open: boolean; onPress: () => void }) {
    const turn = useSharedValue(0);
    const first = useRef(true);
    useEffect(() => {
        if (first.current) {
            first.current = false;
            return;
        }
        /* An × turned back 45 degrees is the +, and the other way round:
           from where the other glyph stood, to rest. */
        turn.value = open ? -45 : 45;
        turn.value = withTiming(0, settle);
    }, [open, turn]);
    const style = useAnimatedStyle(() => ({ transform: [{ rotate: `${turn.value}deg` }] }));
    return (
        <Press label={open ? "Close the attach tray" : "Attach a photo or a file"} accessibilityState={{ expanded: open }} onPress={onPress} style={{ width: 36, height: 36, alignItems: "center", justifyContent: "center" }}>
            <Animated.View style={style}>
                {open ? <Icon name="close" size={17} width={1.9} tone={color.ink2} /> : <Icon name="plus" size={21} width={1.9} tone={color.ink2} />}
            </Animated.View>
        </Press>
    );
}

/** Attaching, as a strip above the box rather than a menu: the camera, the
 *  latest photos, and files. One tap attaches a photo and a second takes it
 *  back; the photo says it is going with a check. Any chat's box opens one.
 *
 *  `chosen` is what is attached already, by the address it was attached
 *  from, so the strip can say which. */
export function AttachTray({ chosen, onPicked, onUnpicked, onRefused, onDone, tone = color.ink, onTone = color.onInk }: {
    chosen: ReadonlySet<string>;
    onPicked: (files: PickedFile[]) => void;
    /** A photo tapped a second time: by the address it was attached from. */
    onUnpicked: (uri: string) => void;
    onRefused: (why: string) => void;
    /** Something was picked by a picker of the phone's own: the strip goes. */
    onDone: () => void;
    tone?: string;
    onTone?: string;
}) {
    const [recent, setRecent] = useState<Recent | null>(null);
    /* Where each library photo was attached from, once it was. */
    const attachedAs = useRef(new Map<string, string>());
    useEffect(() => {
        let gone = false;
        void recentPhotos(true).then((found) => { if (!gone) setRecent(found); });
        return () => { gone = true; };
    }, []);

    const run = (pick: () => Promise<PickedFile[]>) => {
        void pick()
            .then((files) => {
                if (files.length === 0) return;
                onPicked(files);
                onDone();
            })
            .catch((problem: unknown) => onRefused(problem instanceof Error ? problem.message : "That could not be attached."));
    };
    const toggle = (photo: RecentPhoto) => {
        const as = attachedAs.current.get(photo.id) ?? photo.uri;
        haptics.tick();
        if (chosen.has(as)) {
            onUnpicked(as);
            return;
        }
        void pickedFrom(photo)
            .then((file) => {
                attachedAs.current.set(photo.id, file.uri);
                onPicked([file]);
            })
            .catch(() => onRefused(`${photo.name} could not be attached.`));
    };

    const photos = recent?.kind === "ready" ? recent.photos : [];
    return (
        <View accessibilityLabel="Attach" style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
            <ScrollView horizontal showsHorizontalScrollIndicator={false} keyboardShouldPersistTaps="handled" style={{ flex: 1 }} contentContainerStyle={{ gap: 6, paddingRight: 6 }}>
                <Tile glyph="camera" label="Camera" onPress={() => run(async () => { const one = await takePhoto(); return one ? [one] : []; })} />
                {photos.map((photo) => {
                    const on = chosen.has(attachedAs.current.get(photo.id) ?? photo.uri);
                    return (
                        <Press key={photo.id} label={on ? `${photo.name}, attached. Take it back` : `Attach ${photo.name}`} accessibilityState={{ selected: on }} onPress={() => toggle(photo)} style={{ width: TILE, height: TILE, borderRadius: 12, overflow: "hidden", backgroundColor: color.wash }}>
                            <Image source={{ uri: photo.uri }} style={{ width: TILE, height: TILE }} contentFit="cover" recyclingKey={photo.id} transition={100} />
                            {on ? (
                                <View style={{ position: "absolute", left: 0, top: 0, right: 0, bottom: 0, borderRadius: 12, borderWidth: 2.5, borderColor: tone }}>
                                    <View style={{ position: "absolute", right: 5, top: 5, width: 20, height: 20, borderRadius: 10, backgroundColor: tone, alignItems: "center", justifyContent: "center" }}>
                                        <Icon name="check" size={12} width={2.6} tone={onTone} />
                                    </View>
                                </View>
                            ) : null}
                        </Press>
                    );
                })}
                <Tile glyph="photos" label={photos.length > 0 ? "All photos" : "Photos"} onPress={() => run(pickPhotos)} />
            </ScrollView>
            <Tile glyph="folder" label="Files" onPress={() => run(pickFiles)} />
        </View>
    );
}

function Tile({ glyph, label, onPress }: { glyph: FileGlyphName; label: string; onPress: () => void }) {
    return (
        <Press label={label} onPress={onPress} style={{ width: TILE, height: TILE, borderRadius: 12, backgroundColor: color.wash, alignItems: "center", justifyContent: "center", gap: 6 }}>
            <FileGlyph name={glyph} size={22} tone={color.ink} />
            <Text size={12} tone={color.ink2} numberOfLines={1}>{label}</Text>
        </Press>
    );
}
