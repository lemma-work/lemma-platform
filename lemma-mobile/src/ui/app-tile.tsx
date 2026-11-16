import { useState } from "react";
import { View } from "react-native";
import { Image } from "expo-image";
import { useRouter } from "expo-router";
import { routes } from "../data/routes";
import { coverUrl } from "./cover";
import type { AppItem } from "../data/types";
import { Press } from "./press";
import { Text } from "./text";
import { color } from "./tokens";

/** An app's tile: its cover, the 1200×630 picture it ships at
 *  `/.lemma/cover.png` on its own address (or the host draws one from its
 *  name), as the web shows it; never a live screenshot. A private app's cover
 *  answers only someone who has opened it, so a cover that will not load is
 *  expected, and the tile falls back to a quiet sketch read off the name. */

const COVER_RATIO = 1200 / 630;


type Sketch = "list" | "grid" | "board" | "bars" | "ledger";

function sketchOf(name: string): Sketch {
    const said = name.toLowerCase();
    if (/queue|list|inbox|feed|log/.test(said)) return "list";
    if (/board|kanban|pipeline|launch/.test(said)) return "board";
    if (/radar|chart|report|stats|dashboard|metric/.test(said)) return "bars";
    if (/invoice|ledger|books|pay|expense/.test(said)) return "ledger";
    if (/desk|grid|gallery|cards/.test(said)) return "grid";
    let hash = 0;
    for (const char of said) hash = (hash * 31 + char.charCodeAt(0)) % 997;
    return (["list", "grid", "board", "bars", "ledger"] as const)[hash % 5];
}

function Line({ width, dark = false, height = 8 }: { width: `${number}%`; dark?: boolean; height?: number }) {
    return <View style={{ height, width, borderRadius: 4, backgroundColor: dark ? color.sketchDark : color.sketch }} />;
}

export function SketchOf({ name, height }: { name: string; height: number }) {
    const kind = sketchOf(name);
    const pad = height > 90 ? 14 : 12;
    const box = { height, borderRadius: height > 90 ? 14 : 12, backgroundColor: color.chrome, borderWidth: 1, borderColor: color.line, padding: pad, gap: 7, overflow: "hidden" as const };
    if (kind === "grid") {
        return (
            <View style={[box, { flexDirection: "row", flexWrap: "wrap", gap: 7 }]}>
                {[0, 1, 2, 3].map((index) => <View key={index} style={{ width: "46%", flexGrow: 1, height: (height - pad * 2 - 7) / 2 - 1, borderRadius: 7, backgroundColor: index === 2 ? color.sketchDark : color.sketch }} />)}
            </View>
        );
    }
    if (kind === "board") {
        return (
            <View style={[box, { flexDirection: "row", gap: 6 }]}>
                {[3, 2, 1].map((cards, column) => (
                    <View key={column} style={{ flex: 1, gap: 5 }}>
                        {Array.from({ length: cards }, (_, index) => <View key={index} style={{ height: 16, borderRadius: 4, backgroundColor: column === 0 && index === 0 ? color.sketchDark : color.sketch }} />)}
                    </View>
                ))}
            </View>
        );
    }
    if (kind === "bars") {
        return (
            <View style={[box, { flexDirection: "row", alignItems: "flex-end", gap: 6 }]}>
                {[30, 55, 42, 75].map((tall, index) => <View key={index} style={{ flex: 1, height: `${tall}%`, borderRadius: 4, backgroundColor: index === 3 ? color.sketchDark : color.sketch }} />)}
            </View>
        );
    }
    if (kind === "ledger") {
        return (
            <View style={box}>
                {[[45, 22], [55, 18], [38, 24]].map(([left, right], index) => (
                    <View key={index} style={{ flexDirection: "row", gap: 6 }}>
                        <Line width={`${left}%`} dark={index === 0} />
                        <View style={{ flex: 1 }} />
                        <Line width={`${right}%`} />
                    </View>
                ))}
            </View>
        );
    }
    return (
        <View style={box}>
            <Line width="50%" dark />
            <Line width="88%" />
            <Line width="72%" />
            <Line width="80%" />
        </View>
    );
}

export function AppTile({ app, height }: { app: AppItem; height?: number }) {
    const router = useRouter();
    const [failed, setFailed] = useState<string | null>(null);
    const [boxHeight, setBoxHeight] = useState(height ?? 0);
    const src = coverUrl(app.url);
    const showCover = Boolean(src) && src !== failed;
    return (
        <Press role="link" label={`${app.label}, an app`} onPress={() => router.push(routes.app(app.podId, app.name) as never)} style={{ flex: 1, gap: 6 }}>
            <View
                onLayout={(event) => { if (!height) setBoxHeight(event.nativeEvent.layout.height); }}
                style={{ ...(height ? { height } : { aspectRatio: COVER_RATIO }), borderRadius: 12, overflow: "hidden", borderWidth: showCover ? 1 : 0, borderColor: color.line }}
            >
                {showCover ? (
                    <Image source={{ uri: src! }} style={{ flex: 1 }} contentFit="cover" transition={180} onError={() => setFailed(src)} accessibilityIgnoresInvertColors />
                ) : boxHeight > 0 ? <SketchOf name={app.name} height={boxHeight} /> : null}
            </View>
            <Text size={14.5} numberOfLines={1}>{app.label}</Text>
        </Press>
    );
}
