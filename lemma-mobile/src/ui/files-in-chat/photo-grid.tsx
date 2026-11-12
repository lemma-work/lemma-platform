import { useRef, useState } from "react";
import { View } from "react-native";
import { Image } from "expo-image";
import { Press } from "../press";
import { Num } from "../text";
import { color } from "../tokens";
import { rectOf, type Rect } from "./grow";
import { photoTiles } from "./model";
import { PhotoViewer, type Photo } from "./photo-viewer";

export type { Photo } from "./photo-viewer";

const GAP = 3;
const RADIUS = 14;

/** Photos sent together, as one piece: one large and two small, "+N" on
 *  the last when there are more. A tap opens them whole screen from the
 *  photo touched. Any chat draws photos this way, a person's or a Lemma's. */
export function PhotoGrid({ photos, label = "Photos" }: { photos: readonly Photo[]; label?: string }) {
    const [width, setWidth] = useState(0);
    const [viewing, setViewing] = useState<{ index: number; from: Rect | null } | null>(null);
    const tiles = useRef<(View | null)[]>([]);
    const { shown, more } = photoTiles(photos.length);
    if (shown === 0) return null;

    const open = async (index: number) => setViewing({ index, from: await rectOf(tiles.current[index]) });
    const tile = (index: number, w: number, h: number, corners: object) => {
        const photo = photos[index];
        const last = index === shown - 1 && more > 0;
        return (
            <Press
                key={photo.key}
                label={last ? `${photo.name}, and ${more} more` : photo.name}
                accessibilityHint="Opens the photos whole screen"
                onPress={() => void open(index)}
                style={{ width: w, height: h }}
            >
                <View ref={(view) => { tiles.current[index] = view; }} collapsable={false} style={[{ width: w, height: h, overflow: "hidden", backgroundColor: color.wash }, corners]}>
                    {photo.uri ? <Image source={{ uri: photo.uri }} style={{ width: w, height: h }} contentFit="cover" transition={160} recyclingKey={photo.key} /> : null}
                    {last ? (
                        <View style={{ position: "absolute", left: 0, top: 0, right: 0, bottom: 0, backgroundColor: color.photoScrim, alignItems: "center", justifyContent: "center" }}>
                            <Num size={22} tone={color.onPhoto}>+{more}</Num>
                        </View>
                    ) : null}
                </View>
            </Press>
        );
    };

    const tall = Math.round(width * (shown === 1 ? 0.75 : 0.66));
    const half = Math.round((width - GAP) / 2);
    const big = Math.round((width - GAP) * 0.62);
    const small = width - GAP - big;
    const smallTall = Math.round((tall - GAP) / 2);
    return (
        <View accessibilityLabel={`${label}: ${photos.length}`} onLayout={(event) => setWidth(event.nativeEvent.layout.width)} style={{ alignSelf: "stretch" }}>
            {width > 0 ? (
                shown === 1 ? (
                    tile(0, width, tall, { borderRadius: RADIUS })
                ) : shown === 2 ? (
                    <View style={{ flexDirection: "row", gap: GAP }}>
                        {tile(0, half, tall, { borderTopLeftRadius: RADIUS, borderBottomLeftRadius: RADIUS })}
                        {tile(1, width - GAP - half, tall, { borderTopRightRadius: RADIUS, borderBottomRightRadius: RADIUS })}
                    </View>
                ) : (
                    <View style={{ flexDirection: "row", gap: GAP }}>
                        {tile(0, big, tall, { borderTopLeftRadius: RADIUS, borderBottomLeftRadius: RADIUS })}
                        <View style={{ gap: GAP }}>
                            {tile(1, small, smallTall, { borderTopRightRadius: RADIUS })}
                            {tile(2, small, tall - GAP - smallTall, { borderBottomRightRadius: RADIUS })}
                        </View>
                    </View>
                )
            ) : <View style={{ height: 180 }} />}
            {viewing ? <PhotoViewer photos={photos} start={viewing.index} from={viewing.from} onClose={() => setViewing(null)} /> : null}
        </View>
    );
}
