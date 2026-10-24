import { View } from "react-native";
import { useRouter } from "expo-router";
import type { SideRow } from "../data/types";
import { stampOf } from "../data/model";
import { Icon, type IconName } from "./icons";
import { Press } from "./press";
import { Num, Text } from "./text";
import { color, TOUCH } from "./tokens";

/** One line under a Lemma: the glyph on the left says what it is, the end
 *  says its state -- a violet dot when it needs you, an unread count, a
 *  violet ring while it works, else when it last moved. */

const PLATFORM_ICON: Record<string, IconName> = {
    WHATSAPP: "whatsapp",
    TELEGRAM: "telegram",
    SLACK: "slack",
    RESEND: "mail",
    EMAIL: "mail",
};

export function RowGlyph({ row, width = 28 }: { row: Pick<SideRow, "kind" | "platforms" | "key">; width?: number }) {
    const box = { width, alignItems: "center" as const, justifyContent: "center" as const };
    if (row.kind === "shared") return <View style={box}><Text size={16} tone={color.ink2}>#</Text></View>;
    const platform = row.platforms.map((one) => PLATFORM_ICON[one]).find(Boolean);
    if (platform) return <View style={box}><Icon name={platform} size={16} tone={color.ink2} /></View>;
    if (row.kind === "line") return <View style={box}><Icon name="bell" size={16} tone={color.ink2} /></View>;
    if (row.kind === "automation") return <View style={box}><Icon name={row.key.startsWith("auto:s:") ? "clock" : "automation"} size={16} tone={color.ink2} /></View>;
    if (row.kind === "table") return <View style={box}><Icon name="table" size={16} tone={color.ink2} /></View>;
    if (row.key.startsWith("app:")) return <View style={box}><Icon name="app" size={16} tone={color.ink2} /></View>;
    /* A chat with the Lemma: a small ring. */
    return (
        <View style={box}>
            <View style={{ width: 7, height: 7, borderRadius: 4, borderWidth: 1.5, borderColor: color.idleInk }} />
        </View>
    );
}

export function RowEnd({ row, now }: { row: Pick<SideRow, "needsYou" | "unread" | "running" | "at">; now: number }) {
    if (row.needsYou) return <View accessibilityLabel="Needs you" style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} />;
    if (row.unread > 0) {
        return (
            <View accessibilityLabel={`${row.unread} new`} style={{ minWidth: 20, height: 20, paddingHorizontal: 6, borderRadius: 999, backgroundColor: color.wash, alignItems: "center", justifyContent: "center" }}>
                <Num size={12} tone={color.ink}>{row.unread > 99 ? "99+" : row.unread}</Num>
            </View>
        );
    }
    if (row.running) {
        return (
            <View accessibilityLabel="Working" style={{ width: 13, height: 13, borderRadius: 7, backgroundColor: color.violetWash, alignItems: "center", justifyContent: "center" }}>
                <View style={{ width: 9, height: 9, borderRadius: 5, borderWidth: 2, borderColor: color.violet }} />
            </View>
        );
    }
    if (row.at !== null) return <Num size={13} tone={color.ink3}>{stampOf(row.at, now)}</Num>;
    return null;
}

export function SideRowLine({ row, now, glyphWidth = 28, pad = 8, showRuns = false }: { row: SideRow; now: number; glyphWidth?: number; pad?: number; showRuns?: boolean }) {
    const router = useRouter();
    const runs = showRuns && row.kind === "automation" && (row.runs ?? 0) > 1 ? ` · ${row.runs} runs` : "";
    return (
        <Press
            role="link"
            label={row.title + (row.needsYou ? ", needs you" : row.running ? ", working" : "")}
            onPress={() => router.push(row.href as never)}
            style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH, paddingHorizontal: pad }}
        >
            <RowGlyph row={row} width={glyphWidth} />
            <Text size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                {row.title}
                {runs ? <Text size={15.5} tone={color.ink3}>{runs}</Text> : null}
            </Text>
            <RowEnd row={row} now={now} />
        </Press>
    );
}
