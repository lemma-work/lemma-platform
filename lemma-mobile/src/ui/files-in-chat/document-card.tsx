import { ActivityIndicator, View, useWindowDimensions } from "react-native";
import { Image } from "expo-image";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { Icon } from "../icons";
import { Press } from "../press";
import { Text } from "../text";
import { color } from "../tokens";
import { FileGlyph, type FileGlyphName } from "./glyphs";
import { useGrowInto } from "./grow";

const PAGE_W = 62;
const PAGE_H = 80;

/** A document in a chat: its first page, its name, and kind · pages ·
 *  size. A tap grows the card into the whole-screen reader, which `onOpen`
 *  opens under it as it lands; coming back, the page shrinks into the card.
 *  Any chat draws documents this way. */
export function DocumentCard({ name, line, preview, loading = false, glyph = "page", grows = true, onOpen }: {
    name: string;
    /** "PDF · 4 pages · 120 KB". */
    line: string;
    /** The first page as a picture, on this phone; null while there is none. */
    preview: string | null;
    loading?: boolean;
    glyph?: FileGlyphName;
    /** False when there is no reader to grow into, and `onOpen` hands the
     *  file to another app: a file a group message shares, say. */
    grows?: boolean;
    /** Opens the reader, once the card has grown to the whole screen. */
    onOpen: () => void;
}) {
    const { card, grow, overlay } = useGrowInto(onOpen);
    return (
        <>
            <Press label={`${name}, ${line}`} accessibilityHint={grows ? "Opens the document" : "Opens it in another app"} disabled={!grows && loading} onPress={() => (grows ? void grow() : onOpen())} style={{ alignSelf: "stretch" }}>
                <View ref={card} collapsable={false} style={{ flexDirection: "row", alignItems: "center", gap: 12, padding: 10, paddingRight: 14, borderWidth: 1, borderColor: color.line2, borderRadius: 14, backgroundColor: color.paper }}>
                    <FirstPage preview={preview} loading={loading} glyph={glyph} width={PAGE_W} height={PAGE_H} />
                    <View style={{ flex: 1, minWidth: 0, gap: 3 }}>
                        <Text size={15} numberOfLines={2} style={{ lineHeight: 20 }}>{name}</Text>
                        <Text size={12.5} tone={color.ink3} numberOfLines={1} style={{ fontVariant: ["tabular-nums"] }}>{line}</Text>
                    </View>
                    <Icon name="forward" size={14} width={1.9} tone={color.ink3} />
                </View>
            </Press>
            {overlay(<Reading name={name} preview={preview} glyph={glyph} />)}
        </>
    );
}

/** The first page, or a page drawn in its place. */
function FirstPage({ preview, loading, glyph, width, height }: { preview: string | null; loading: boolean; glyph: FileGlyphName; width: number; height: number }) {
    return (
        <View style={{ width, height, borderRadius: 6, borderWidth: 1, borderColor: color.line2, backgroundColor: color.paper, overflow: "hidden", alignItems: "center", justifyContent: "center" }}>
            {preview ? (
                <Image source={{ uri: preview }} style={{ width, height }} contentFit="cover" contentPosition="top" transition={160} />
            ) : loading ? (
                <ActivityIndicator size="small" color={color.ink3} />
            ) : (
                <View style={{ alignSelf: "stretch", paddingHorizontal: 9, gap: 5 }}>
                    <View style={{ alignSelf: "flex-start", marginBottom: 2 }}><FileGlyph name={glyph} size={14} tone={color.ink3} /></View>
                    {[0.9, 1, 0.75, 1, 0.6].map((w, index) => <View key={index} style={{ height: 3, width: `${w * 100}%`, borderRadius: 1.5, backgroundColor: color.sketch }} />)}
                </View>
            )}
        </View>
    );
}

/** What the grown card shows while it travels: the reader's ground with the
 *  first page on it, where the reader will draw it. */
export function Reading({ name, preview, glyph }: { name: string; preview: string | null; glyph: FileGlyphName }) {
    const { width, height } = useWindowDimensions();
    const insets = useSafeAreaInsets();
    const pageW = width - 32;
    return (
        <View style={{ flex: 1, backgroundColor: color.paper, paddingTop: insets.top }}>
            <View style={{ height: 52, justifyContent: "center", paddingHorizontal: 56 }}>
                <Text size={16} weight="medium" numberOfLines={1}>{name}</Text>
            </View>
            <View style={{ flex: 1, alignItems: "center", paddingTop: 12, backgroundColor: color.wash }}>
                <FirstPage preview={preview} loading={false} glyph={glyph} width={pageW} height={Math.min(height * 0.7, pageW * 1.294)} />
            </View>
        </View>
    );
}
