import { ScrollView, View } from "react-native";
import { Markdown } from "../../../ui/markdown";
import { Button } from "../../../ui/press";
import { Text } from "../../../ui/text";
import { color, font } from "../../../ui/tokens";
import { FilesIcon } from "../glyphs";
import { describeSize, formatOf } from "../model";
import type { FileContent } from "../source";
import { sayLocked, type Locked } from "@web/thread/document-save";
import { splitFrontmatter } from "@web/skills/skill-frontmatter";

/** Text as it is: CSV, JSON, a log. These are files where the bytes are the
 *  point, so nothing here edits them (the web's `editableKind`). Long lines
 *  scroll sideways rather than wrap, so a row of a CSV stays one row. */
export function TextView({ text }: { text: string }) {
    return (
        <ScrollView contentContainerStyle={{ paddingVertical: 14 }}>
            <ScrollView horizontal contentContainerStyle={{ paddingHorizontal: 20 }}>
                <Text selectable size={13} style={{ fontFamily: font.num, lineHeight: 20, color: color.ink }}>{text}</Text>
            </ScrollView>
        </ScrollView>
    );
}

/** A page shown to read: one the editor would damage (HTML in it), or one
 *  you may not change. Its frontmatter is shown as the header it is, never
 *  as prose. */
export function ReadPage({ text, locked }: { text: string; locked: Locked | null }) {
    const { front, body } = splitFrontmatter(text);
    return (
        <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 40, gap: 14 }}>
            {front ? (
                <View style={{ borderLeftWidth: 2, borderLeftColor: color.line2, backgroundColor: color.chrome, paddingVertical: 10, paddingHorizontal: 12, borderTopRightRadius: 10, borderBottomRightRadius: 10 }}>
                    <Text selectable size={11.5} tone={color.ink3} style={{ fontFamily: font.num, lineHeight: 18 }}>{front}</Text>
                </View>
            ) : null}
            <Markdown text={body} size={16.5} />
            {locked ? (
                <Text size={13} tone={color.ink3} style={{ borderTopWidth: 1, borderTopColor: color.line, paddingTop: 12 }}>{sayLocked(locked)}</Text>
            ) : null}
        </ScrollView>
    );
}

/** Nothing here draws it: an office file, an archive. Its name, what it is,
 *  and the two ways onward a phone has. */
export function OtherFile({ file, onOpen, onKeep, busy }: { file: FileContent; onOpen: () => void; onKeep: () => void; busy: "open" | "keep" | null }) {
    return (
        <View style={{ flex: 1, justifyContent: "center", paddingHorizontal: 28, gap: 22 }}>
            <View style={{ alignItems: "center", gap: 10 }}>
                <FilesIcon name="file" size={44} tone={color.ink2} width={1.5} />
                <Text size={17} numberOfLines={3} style={{ textAlign: "center" }}>{file.name}</Text>
                <Text size={14} tone={color.ink3} style={{ textAlign: "center" }}>
                    {file.note ?? [formatOf(file.name), describeSize(file.size)].filter(Boolean).join(" · ")}
                </Text>
            </View>
            <View style={{ gap: 10 }}>
                <Button title="Open in another app" busy={busy === "open"} onPress={onOpen} />
                <Button title="Save to phone" kind="outline" busy={busy === "keep"} onPress={onKeep} />
            </View>
        </View>
    );
}
