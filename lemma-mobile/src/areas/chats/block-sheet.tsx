import { useEffect, useState } from "react";
import { ScrollView, View } from "react-native";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../data/hooks";
import { Button } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { SwitchRow, Trouble } from "./bits";
import { useChatsSource } from "./hooks";
import { problemOf } from "@web/channels/problem";
import {
    blockLine,
    blockTitle,
    memberBlockNote,
    stopAnsweringLabel,
    stopAnsweringLine,
    type Blockable,
} from "@web/reports/model";

/** Blocking a person, for the reader alone (the web's block dialog). Who may
 *  change the Lemma is also offered to stop it answering a contact, which
 *  holds for everybody. Every chat of the Lemma is read again afterwards, so
 *  the person's messages go from all of them. */
export function BlockSheet({ podId, person, lemma, mayStopAnswering, onClose }: {
    podId: string;
    /** Null while nobody is being blocked: the sheet is closed. */
    person: Blockable | null;
    lemma: string;
    mayStopAnswering: boolean;
    onClose: () => void;
}) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const offerStop = Boolean(person) && mayStopAnswering && person?.kind === "CONTACT";
    const [stop, setStop] = useState(false);
    const save = useMutation({
        mutationFn: async (whom: Blockable) => {
            await source.block(podId, whom);
            if (offerStop && stop) await source.setContactBlocked(podId, whom.id, true);
        },
        onSuccess: () => {
            void cache.invalidateQueries({ queryKey: [key, "chats", podId] });
            onClose();
        },
    });
    const { reset } = save;
    useEffect(() => {
        setStop(false);
        reset();
    }, [person?.id, reset]);
    const busy = save.isPending;
    const close = () => { if (!busy) onClose(); };
    return (
        <Sheet open={person !== null} onClose={close}>
            {person ? (
                <ScrollView contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 8, gap: 14 }}>
                    <View style={{ gap: 6 }}>
                        <Text accessibilityRole="header" size={20} weight="medium">{blockTitle(person)}</Text>
                        <Text size={15.5} tone={color.ink2} style={{ lineHeight: 22 }}>{blockLine(person, lemma)}</Text>
                    </View>
                    {offerStop ? (
                        <View style={{ borderRadius: 14, backgroundColor: color.wash, paddingHorizontal: 14, paddingVertical: 4 }}>
                            <SwitchRow
                                label={stopAnsweringLabel(person, lemma)}
                                note={stopAnsweringLine(person, lemma)}
                                value={stop}
                                disabled={busy}
                                onChange={setStop}
                            />
                        </View>
                    ) : null}
                    {person.kind === "MEMBER" ? <Text size={14} tone={color.ink3} style={{ lineHeight: 20 }}>{memberBlockNote(person, lemma)}</Text> : null}
                    <Trouble>{save.isError ? problemOf(save.error, "Couldn’t block them.") : null}</Trouble>
                    <View style={{ flexDirection: "row", gap: 10, justifyContent: "flex-end" }}>
                        <Button title="Cancel" kind="outline" height={48} size={16} disabled={busy} onPress={close} />
                        <Button title="Block" height={48} size={16} busy={busy} onPress={() => save.mutate(person)} />
                    </View>
                </ScrollView>
            ) : null}
        </Sheet>
    );
}
