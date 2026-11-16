import { useState } from "react";
import { Alert, KeyboardAvoidingView, Platform, TextInput, View } from "react-native";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCacheKey, useLemma } from "../../data/hooks";
import { copyText } from "../../thread/clipboard";
import { Glyph, type GlyphName } from "../../thread/glyphs";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import { Trouble } from "./bits";
import { lineKeys, useAgents, useChatsSource } from "./hooks";
import { useMentionMenu } from "./mention-menu";
import { lineOffers } from "./model";
import { mentionsIn, type Mentionable } from "@web/channels/mentions";
import type { ChannelDetail, Line, Part } from "@web/channels/model";
import { problemOf } from "@web/channels/problem";

/** How long a sheet takes to leave: iOS will not show an alert, or another
 *  sheet, over one still going. */
const AFTER_SHEET_MS = Platform.OS === "ios" ? 350 : 0;

/** What holding a message offers (the web's hover tools, as a sheet): reply
 *  in its thread, copy, change, delete -- or, for whoever may change the
 *  chat, remove anybody's -- and share an answer kept to the reader. A copy
 *  already carried outside stays as sent, and a delete says so first. */
export function LineActions({ podId, channel, line, me, mayWrite, inThread, people, onClose, onThread }: {
    podId: string;
    channel: ChannelDetail;
    line: Line | null;
    me: string | null;
    mayWrite: boolean;
    inThread: boolean;
    people: readonly Mentionable[];
    onClose: () => void;
    onThread: (line: Line) => void;
}) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const [editing, setEditing] = useState<Line | null>(null);
    const reread = (held: Line) => { for (const at of lineKeys(key, podId, channel.id, held)) void cache.invalidateQueries({ queryKey: at }); };
    const remove = useMutation({
        mutationFn: (held: Line) => source.remove(podId, channel.id, held.id),
        onSuccess: (_done, held) => reread(held),
        onError: (error) => Alert.alert("Not deleted", problemOf(error, "Couldn’t delete it.")),
    });
    const share = useMutation({
        mutationFn: (held: Line) => source.share(podId, channel.id, held.id),
        onSuccess: (_done, held) => reread(held),
        onError: (error) => Alert.alert("Not shared", problemOf(error, "Couldn’t share it.")),
    });
    const offers = line ? lineOffers(line, channel, me, mayWrite, inThread) : null;
    const rows: { glyph: GlyphName; label: string; tone?: string; run: () => void }[] = [];
    if (line && offers) {
        if (offers.reply) rows.push({ glyph: "tree", label: line.replyCount > 0 ? "Open the thread" : "Reply in a thread", run: () => onThread(line) });
        if (offers.copy) rows.push({ glyph: "copy", label: "Copy", run: () => copyText(line.text ?? "") });
        if (offers.edit) rows.push({ glyph: "edit", label: "Edit", run: () => setEditing(line) });
        if (offers.share) rows.push({
            glyph: "eye", label: "Share with everyone here", run: () => Alert.alert("Share this answer?", "Everyone in this chat will read it.", [
                { text: "Keep it to me", style: "cancel" },
                { text: "Share", onPress: () => share.mutate(line) },
            ]),
        });
        if (offers.remove) rows.push({
            glyph: "trash", label: offers.removeLabel, tone: color.amber, run: () => Alert.alert(offers.removeTitle, offers.removeNote, [
                { text: "Keep", style: "cancel" },
                { text: offers.removeLabel === "Delete" ? "Delete" : "Remove", style: "destructive", onPress: () => remove.mutate(line) },
            ]),
        });
    }
    return (
        <>
            <Sheet open={line !== null && editing === null} onClose={onClose}>
                <View style={{ paddingHorizontal: 8 }}>
                    {line?.text ? <Text size={13.5} tone={color.ink3} numberOfLines={2} style={{ paddingHorizontal: 12, paddingBottom: 8 }}>{line.text.replace(/\s+/g, " ")}</Text> : null}
                    {rows.length === 0 ? <Text size={15} tone={color.ink2} style={{ padding: 12 }}>Nothing to do with this message.</Text> : null}
                    {rows.map((row) => (
                        <Press key={row.label} label={row.label} onPress={() => { onClose(); setTimeout(row.run, AFTER_SHEET_MS); }} style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: TOUCH + 4, paddingHorizontal: 12 }}>
                            <Glyph name={row.glyph} size={18} tone={row.tone ?? color.ink} />
                            <Text size={16} tone={row.tone ?? color.ink}>{row.label}</Text>
                        </Press>
                    ))}
                </View>
            </Sheet>
            {editing ? <EditSheet podId={podId} channel={channel} line={editing} people={people} onClose={() => setEditing(null)} /> : null}
        </>
    );
}

/** Changing what one's own message says: the composer's `@` menu, the
 *  people it names kept with it. Who it names goes with the words only when
 *  that changed -- then with its files and links, which the change keeps. */
function EditSheet({ podId, channel, line, people, onClose }: { podId: string; channel: ChannelDetail; line: Line; people: readonly Mentionable[]; onClose: () => void }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const named = line.parts.filter((part): part is Extract<Part, { type: "mention" }> => part.type === "mention");
    const [draft, setDraft] = useState(line.text ?? "");
    const [picked, setPicked] = useState<Mentionable[]>(() => named.flatMap((part) => (part.name ? [{ kind: part.kind, id: part.id, name: part.name }] : [])));
    const lemma = useLemma(podId);
    const frontId = useAgents(podId).data?.find((agent) => agent.front)?.id ?? null;
    const mention = useMentionMenu({ people, text: draft, setText: setDraft, onPicked: (who) => setPicked((was) => [...was, who]), lemma: lemma ? { id: lemma.id, iconUrl: lemma.iconUrl } : null, frontId });
    const edit = useMutation({
        mutationFn: (change: { text: string; parts?: Part[] }) => source.edit(podId, channel.id, line.id, change.text, change.parts),
        onSuccess: () => {
            for (const at of lineKeys(key, podId, channel.id, line)) void cache.invalidateQueries({ queryKey: at });
            onClose();
        },
    });
    const save = () => {
        const text = draft.trim();
        if (!text || edit.isPending) return;
        /* A name with no words to find it by -- named by an id alone -- is
           kept as it was. */
        const kept = named.filter((part) => !part.name);
        const now = mentionsIn(text, picked);
        const keyOf = (one: { kind: string; id: string }) => one.kind + ":" + one.id;
        const before = new Set(named.map(keyOf));
        const after = new Set([...now, ...kept].map(keyOf));
        const renamed = before.size !== after.size || [...after].some((one) => !before.has(one));
        if (!renamed) {
            if (text === line.text) onClose();
            else edit.mutate({ text });
            return;
        }
        const mentions: Part[] = [
            ...kept,
            ...now.map((one): Part => ({ type: "mention", kind: one.kind, id: one.id, name: picked.find((who) => keyOf(who) === keyOf(one))?.name ?? null })),
        ];
        edit.mutate({ text, parts: [...mentions, ...line.parts.filter((part) => part.type !== "mention")] });
    };
    return (
        <Sheet open onClose={onClose} title="Change what you said" tall>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
                <View style={{ paddingHorizontal: 16, gap: 10 }}>
                    {mention.menu}
                    <TextInput
                        accessibilityLabel="Change what you said"
                        autoFocus
                        multiline
                        value={draft}
                        selection={mention.selection}
                        onSelectionChange={mention.onSelectionChange}
                        onChangeText={mention.onChangeText}
                        style={{ minHeight: 88, maxHeight: 220, borderWidth: 1, borderColor: color.line2, borderRadius: 14, padding: 12, fontFamily: font.regular, fontSize: 16, color: color.ink, textAlignVertical: "top" }}
                    />
                    <Trouble>{edit.isError ? problemOf(edit.error, "Couldn’t change it.") : null}</Trouble>
                    <View style={{ flexDirection: "row", gap: 10, justifyContent: "flex-end" }}>
                        <Button title="Cancel" kind="outline" onPress={onClose} />
                        <Button title="Save" busy={edit.isPending} disabled={!draft.trim()} onPress={save} />
                    </View>
                </View>
            </KeyboardAvoidingView>
        </Sheet>
    );
}
