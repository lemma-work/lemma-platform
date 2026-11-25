import { ActivityIndicator, ScrollView, View } from "react-native";
import { Press } from "../press";
import { Num, Text } from "../text";
import { color } from "../tokens";
import { FileGlyph } from "./glyphs";
import { rowCount, type Sheet } from "./model";

const CELL = 112;
const NUMBER = /^[$€£₹-]?\d[\d,.:]*%?$/;

/** A spreadsheet or a CSV in a chat: its first rows where they were sent,
 *  how many rows it has, and Open for the rest. Any chat draws one this way. */
export function SheetPreview({ name, sheet, loading = false, failed, tone = color.ink, onOpen }: {
    name: string;
    sheet: Sheet | null;
    loading?: boolean;
    /** Why there are no rows to show, when there are none. */
    failed?: string | null;
    /** The accent: Open, in the chat's colour. */
    tone?: string;
    onOpen?: () => void;
}) {
    const columns = sheet?.header.length ? sheet.header : sheet?.rows[0]?.map((_, index) => `Column ${index + 1}`) ?? [];
    return (
        <View accessibilityLabel={sheet ? `${name}, ${rowCount(sheet.total)}` : name} style={{ alignSelf: "stretch", borderWidth: 1, borderColor: color.line2, borderRadius: 14, overflow: "hidden", backgroundColor: color.paper }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 40, paddingLeft: 12, paddingRight: onOpen ? 4 : 12, borderBottomWidth: 1, borderBottomColor: color.line }}>
                <FileGlyph name="sheet" size={15} tone={color.ink2} />
                <Text size={14} weight="medium" numberOfLines={1} style={{ flexShrink: 1 }}>{name}</Text>
                <View style={{ flex: 1 }} />
                {onOpen ? (
                    <Press role="link" label={`Open ${name}`} onPress={onOpen} style={{ minHeight: 40, paddingHorizontal: 10, justifyContent: "center" }}>
                        <Text size={13.5} tone={tone}>Open</Text>
                    </Press>
                ) : null}
            </View>
            {loading ? (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, padding: 12 }}>
                    <ActivityIndicator size="small" color={color.ink3} />
                    <Text size={13} tone={color.ink3}>Reading {name}…</Text>
                </View>
            ) : null}
            {!loading && !sheet && failed ? <Text size={13} tone={color.ink3} style={{ padding: 12 }}>{failed}</Text> : null}
            {sheet && columns.length > 0 ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} nestedScrollEnabled>
                    <View>
                        <View style={{ flexDirection: "row", backgroundColor: color.chrome, borderBottomWidth: 1, borderBottomColor: color.line }}>
                            {columns.map((column, index) => (
                                <Text key={index} size={12} tone={color.ink3} numberOfLines={1} style={{ width: CELL, paddingHorizontal: 10, paddingVertical: 7 }}>{column}</Text>
                            ))}
                        </View>
                        {sheet.rows.map((row, index) => (
                            <View key={index} style={{ flexDirection: "row", borderBottomWidth: index < sheet.rows.length - 1 ? 1 : 0, borderBottomColor: color.line }}>
                                {columns.map((_, at) => {
                                    const cell = (row[at] ?? "").replace(/\s+/g, " ");
                                    return NUMBER.test(cell)
                                        ? <Num key={at} numberOfLines={1} style={{ width: CELL, paddingHorizontal: 10, paddingVertical: 8, fontSize: 13, color: color.ink }}>{cell}</Num>
                                        : <Text key={at} size={13} numberOfLines={1} style={{ width: CELL, paddingHorizontal: 10, paddingVertical: 8 }}>{cell}</Text>;
                                })}
                            </View>
                        ))}
                    </View>
                </ScrollView>
            ) : null}
            {sheet ? (
                <View style={{ flexDirection: "row", gap: 6, paddingHorizontal: 12, paddingVertical: 8, borderTopWidth: 1, borderTopColor: color.line }}>
                    <Num size={12} tone={color.ink3}>{rowCount(sheet.total)}</Num>
                    {sheet.total > sheet.rows.length ? <Text size={12} tone={color.ink3}>· the first {sheet.rows.length} here</Text> : null}
                </View>
            ) : null}
        </View>
    );
}
