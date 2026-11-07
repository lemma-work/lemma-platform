import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma } from "../../../data/hooks";
import { stampOf, timeOf } from "../../../data/model";
import type { MemoryNote } from "@web/thread/memory-notes";
import { changedLately } from "@web/thread/memory-notes";
import { Press } from "../../../ui/press";
import { Counted, Num, Text } from "../../../ui/text";
import { color, TOUCH } from "../../../ui/tokens";
import { filesRoutes } from "../../files/routes";
import { lemmaPart } from "../sections";
import type { PartProps } from "../types";
import { Group, ReadProblem, Reading, Say, SettingsScreen, isForbidden } from "../ui";
import { useMemoryNotes } from "./hooks";
import { openingScope, scopeEmpty, scopeNote, type Scope } from "./model";

/** What a Lemma has written down as it worked, beside what people taught it
 *  (Skills): read from the files themselves, so it is the notes that exist
 *  and nothing it was asked to report about itself. A dot marks a note
 *  changed this week. Shared and personal notes are split the way Files
 *  splits them; a note opens in the file viewer, to read or fix. */
export function MemoryScreen({ podId }: PartProps) {
    const router = useRouter();
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "this Lemma";
    const part = lemmaPart("memory")!;
    const notes = useMemoryNotes(podId);
    const [chosen, setChosen] = useState<Scope | null>(null);
    const all = notes.data ?? [];
    const shared = all.filter((note) => !note.private);
    const personal = all.filter((note) => note.private);
    const scope = openingScope(all, chosen);
    const list = scope === "shared" ? shared : personal;
    const now = Date.now();
    return (
        <SettingsScreen title={part.title(name)} note={part.note(name)} back={lemma?.name ?? "Settings"}>
            {notes.isPending ? <Reading /> : null}
            {notes.isError ? (
                <ReadProblem said={isForbidden(notes.error) ? `You may not read ${name}’s notes.` : `Couldn’t load ${name}’s notes.`} onRetry={() => void notes.refetch()} />
            ) : null}
            {notes.isSuccess && all.length === 0 ? <Say>{name} hasn’t written anything down yet. Correct it once and it will.</Say> : null}
            {notes.isSuccess && all.length > 0 ? (
                <View style={{ gap: 10 }}>
                    <View accessibilityRole="tablist" style={{ flexDirection: "row", gap: 22, borderBottomWidth: 1, borderBottomColor: color.line }}>
                        {(["shared", "personal"] as const).map((each) => {
                            const on = scope === each;
                            const many = (each === "shared" ? shared : personal).length;
                            const word = each === "shared" ? "Shared" : "Personal";
                            return (
                                <Press
                                    key={each}
                                    role="tab"
                                    label={`${word}, ${many}`}
                                    accessibilityState={{ selected: on }}
                                    onPress={() => setChosen(each)}
                                    style={{ flexDirection: "row", alignItems: "center", gap: 6, minHeight: TOUCH, borderBottomWidth: 2, borderBottomColor: on ? color.ink : "transparent", marginBottom: -1 }}
                                >
                                    <Text size={15.5} weight={on ? "medium" : "regular"} tone={on ? color.ink : color.ink3}>{word}</Text>
                                    <Num size={13.5} tone={color.ink3}>{many}</Num>
                                </Press>
                            );
                        })}
                    </View>
                    <Text size={13.5} tone={color.ink3} style={{ lineHeight: 19 }}>{scopeNote(scope, name)}</Text>
                    {list.length === 0 ? <Say>{scopeEmpty(scope, name)}</Say> : (
                        <Group>
                            {list.map((note, index) => (
                                <NoteRow key={note.path} note={note} now={now} last={index === list.length - 1} onOpen={() => router.push(filesRoutes.file(podId, note.path) as never)} />
                            ))}
                        </Group>
                    )}
                </View>
            ) : null}
        </SettingsScreen>
    );
}

/** One note: changed this week or not, its topic, the line it was written
 *  with, and when it last changed. */
function NoteRow({ note, now, last, onOpen }: { note: MemoryNote; now: number; last: boolean; onOpen: () => void }) {
    const fresh = changedLately(note.updated, now);
    const at = timeOf(note.updated);
    const stamp = at === null ? "" : stampOf(at, now);
    return (
        <Press
            role="link"
            label={[note.topic, fresh ? "changed this week" : "", note.gloss].filter(Boolean).join(", ")}
            onPress={onOpen}
            style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: note.gloss ? 58 : 50, paddingVertical: 6, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}
        >
            <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: fresh ? color.ink : color.line2 }} />
            <View style={{ flex: 1, minWidth: 0, gap: 1 }}>
                <Text size={16} numberOfLines={1}>{note.topic}</Text>
                {note.gloss ? <Text size={13} tone={color.ink3} numberOfLines={2}>{note.gloss}</Text> : null}
            </View>
            {stamp ? <Counted size={13.5} tone={color.ink3}>{stamp}</Counted> : null}
        </Press>
    );
}
