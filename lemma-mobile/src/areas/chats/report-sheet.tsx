import { useEffect, useState } from "react";
import { KeyboardAvoidingView, ScrollView, TextInput, View } from "react-native";
import { useMutation } from "@tanstack/react-query";
import { Glyph } from "../../thread/glyphs";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import { Trouble } from "./bits";
import { useChatsSource } from "./hooks";
import { problemOf } from "@web/channels/problem";
import {
    MAX_REPORT_NOTE,
    REPORTED_LINE,
    REPORT_REASONS,
    reportLine,
    reportTitle,
    type Blockable,
    type ReportReason,
    type ReportTarget,
} from "@web/reports/model";

/** Reporting a reply or a message to the Lemma team, as a sheet: why, as one
 *  of a few plain picks, and any words (the web's report dialog). Once sent,
 *  the same sheet says so; for a person's message it offers to block them
 *  too (`onBlock`). A refusal is said in the sheet, where it was sent. */
export function ReportSheet({ podId, target, author, person, onBlock, onClose }: {
    podId: string;
    /** Null while nothing is being reported: the sheet is closed. */
    target: ReportTarget | null;
    author: string | null;
    person?: Blockable | null;
    onBlock?: (person: Blockable) => void;
    onClose: () => void;
}) {
    const source = useChatsSource();
    const [reason, setReason] = useState<ReportReason | null>(null);
    const [note, setNote] = useState("");
    const send = useMutation({
        mutationFn: (why: ReportReason) => {
            if (!target) throw new Error("Nothing to report.");
            return source.report(podId, target, why, note);
        },
    });
    const key = target ? (target.kind === "reply" ? target.messageId : target.lineId) : null;
    /* A new message to report starts with nothing picked. */
    const { reset } = send;
    useEffect(() => {
        setReason(null);
        setNote("");
        reset();
    }, [key, reset]);
    const busy = send.isPending;
    const close = () => { if (!busy) onClose(); };
    return (
        <Sheet open={target !== null} onClose={close} tall>
            <KeyboardAvoidingView behavior="padding">
                <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 16, paddingBottom: 8, gap: 14 }}>
                    {target && send.isSuccess ? (
                        <View style={{ gap: 14 }}>
                            <View style={{ width: 44, height: 44, borderRadius: 22, backgroundColor: color.pick, alignItems: "center", justifyContent: "center" }}>
                                <Glyph name="flag" size={20} tone={color.info} />
                            </View>
                            <View style={{ gap: 6 }}>
                                <Text accessibilityRole="header" size={20} weight="medium">Reported</Text>
                                <Text size={15.5} tone={color.ink2} style={{ lineHeight: 22 }}>{REPORTED_LINE}</Text>
                            </View>
                            {person && onBlock ? (
                                <View style={{ gap: 4, borderTopWidth: 1, borderTopColor: color.line, paddingTop: 14 }}>
                                    <Text size={16} weight="medium">Block {person.name} too?</Text>
                                    <Text size={14.5} tone={color.ink2} style={{ lineHeight: 20 }}>Blocking hides {person.name}’s messages from you.</Text>
                                </View>
                            ) : null}
                            <View style={{ flexDirection: "row", gap: 10, justifyContent: "flex-end" }}>
                                {person && onBlock ? <Button title={"Block " + person.name} kind="outline" height={48} size={16} onPress={() => onBlock(person)} /> : null}
                                <Button title="Done" height={48} size={16} onPress={onClose} />
                            </View>
                        </View>
                    ) : target ? (
                        <>
                            <View style={{ gap: 4 }}>
                                <Text accessibilityRole="header" size={20} weight="medium">{reportTitle(target, author)}</Text>
                                <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>{reportLine(target)}</Text>
                            </View>
                            <View accessibilityRole="radiogroup" accessibilityLabel="What is wrong with it?" style={{ gap: 8 }}>
                                {REPORT_REASONS.map((option) => {
                                    const on = option.id === reason;
                                    return (
                                        <Press
                                            key={option.id}
                                            role="radio"
                                            label={option.label}
                                            accessibilityState={{ selected: on, disabled: busy }}
                                            disabled={busy}
                                            onPress={() => setReason(option.id)}
                                            style={{ minHeight: TOUCH + 8, flexDirection: "row", alignItems: "center", justifyContent: "space-between", paddingHorizontal: 14, borderRadius: 14, backgroundColor: on ? color.pick : color.wash, borderWidth: 1.5, borderColor: on ? color.info : "transparent" }}
                                        >
                                            <Text size={16}>{option.label}</Text>
                                            <View style={{ width: 20, height: 20, borderRadius: 10, borderWidth: on ? 0 : 1.5, borderColor: color.ink3, backgroundColor: on ? color.info : "transparent", alignItems: "center", justifyContent: "center" }}>
                                                {on ? <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.paper }} /> : null}
                                            </View>
                                        </Press>
                                    );
                                })}
                            </View>
                            <View style={{ gap: 6 }}>
                                <Text size={14} tone={color.ink2}>Anything we should know? (optional)</Text>
                                <TextInput
                                    accessibilityLabel="Anything we should know? Optional"
                                    multiline
                                    maxLength={MAX_REPORT_NOTE}
                                    value={note}
                                    editable={!busy}
                                    onChangeText={setNote}
                                    style={{ minHeight: 80, maxHeight: 160, borderWidth: 1, borderColor: color.line2, borderRadius: 14, padding: 12, fontFamily: font.regular, fontSize: 16, color: color.ink, textAlignVertical: "top" }}
                                />
                            </View>
                            <Trouble>{send.isError ? problemOf(send.error, "Couldn’t send the report.") : null}</Trouble>
                            <View style={{ flexDirection: "row", gap: 10, justifyContent: "flex-end" }}>
                                <Button title="Cancel" kind="outline" height={48} size={16} disabled={busy} onPress={close} />
                                <Button title="Report" height={48} size={16} busy={busy} disabled={reason === null} onPress={() => { if (reason) send.mutate(reason); }} />
                            </View>
                        </>
                    ) : null}
                </ScrollView>
            </KeyboardAvoidingView>
        </Sheet>
    );
}
