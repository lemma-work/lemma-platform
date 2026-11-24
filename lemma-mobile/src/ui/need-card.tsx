import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useAnswer, useCard } from "../data/hooks";
import { ageOf, filterOf, hrefOfNeed } from "../data/model";
import type { Lemma, NeedItem } from "../data/types";
import { LemmaFace, PersonFace } from "./face";
import { haptics } from "./haptics";
import { Icon } from "./icons";
import { Button, Press } from "./press";
import { Counted, Text } from "./text";
import { color } from "./tokens";

/** One thing that needs the reader, as a row of the web's Needs you
 *  (`outcomes/answer.tsx`): a face, what it is, where and how long ago, and
 *  the answer right in the row -- approve or deny, one of its choices, a
 *  form to fill in, or the way to where it is answered. Once answered the
 *  row says what happened, and the list drops it when it is read again. */
export function NeedRow({ item, lemma, now, title, onForm, inLemma = false, first = false }: {
    item: NeedItem;
    lemma: Lemma | null;
    now: number;
    /** What to call it, when the caller words it shorter (sample data). */
    title?: string;
    /** A workflow's form: filled in a sheet the caller holds. */
    onForm?: (item: NeedItem) => void;
    /** On the Lemma's own page, where its name is already said. */
    inLemma?: boolean;
    first?: boolean;
}) {
    const router = useRouter();
    const card = useCard(item);
    const answer = useAnswer();
    const [said, setSaid] = useState<{ text: string; ok: boolean } | null>(null);
    const [picked, setPicked] = useState<string | null>(null);
    const shown = card.data ?? { ...item, note: item.detail, answer: { kind: "open" as const, label: "Open" } };
    const name = title ?? shown.title;
    const open = () => router.push(hrefOfNeed(item) as never);
    const kind = filterOf(item.kind);
    const form = item.kind === "FORM" && item.workflowRunId && onForm;
    const where = [inLemma ? null : item.podName, item.kind === "CONTACT" ? "Waiting on a person" : null, ageOf(item.sinceMs, now)].filter(Boolean).join(" · ");

    const decide = (decision: "APPROVE_ONCE" | "DENY", answers?: Record<string, unknown>, label?: string) => {
        setPicked(label ?? decision);
        answer.mutate({ item, decision, answers }, {
            onSuccess: () => {
                haptics.done();
                setSaid({
                    ok: decision !== "DENY",
                    text: decision === "DENY" ? `Denied. ${item.podName} goes on without it.` : label ? `Answered: ${label}. ${item.podName} carries on.` : `Approved. ${item.podName} carries on.`,
                });
            },
            onError: (problem) => {
                setPicked(null);
                setSaid({ ok: false, text: problem instanceof Error && problem.message ? problem.message : "That didn’t go through. Try again." });
            },
        });
    };

    const end = (title: string, onPress: () => void) => (
        <Button title={title} kind="outline" height={34} size={14} onPress={onPress} />
    );

    return (
        <View style={{ flexDirection: "row", gap: 12, paddingVertical: 14, paddingHorizontal: 20, borderTopWidth: first ? 0 : 1, borderTopColor: color.line }}>
            {item.kind === "CONTACT" ? <PersonFace name={item.title.replace(/^Reply to /, "")} size={34} /> : <LemmaFace id={item.podId} icon={lemma?.iconUrl} size={34} />}
            <View style={{ flex: 1, minWidth: 0 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                    <Press role="link" label={`${name}. ${where}. Open`} onPress={open} style={{ flex: 1, minWidth: 0 }}>
                        <Counted size={15.5} style={{ lineHeight: 21 }}>{name}</Counted>
                        <Text size={13} tone={color.ink3} style={{ marginTop: 2 }}>{where}</Text>
                    </Press>
                    {/* What has nothing to answer here gets its one verb at the end. */}
                    {said ? null
                        : form ? end("Fill in", () => onForm(item))
                        : shown.answer.kind === "open" ? end(item.kind === "CONTACT" ? "Reply" : kind === "decide" ? "Decide" : kind === "answer" ? "Answer" : "Open", open)
                        : null}
                </View>
                {shown.note && !said && shown.answer.kind !== "open" ? (
                    <View style={{ marginTop: 10, paddingVertical: 10, paddingHorizontal: 12, borderRadius: 12, backgroundColor: color.wash }}>
                        <Counted size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{shown.note}</Counted>
                    </View>
                ) : null}
                {said ? (
                    <View accessibilityLiveRegion="polite" style={{ flexDirection: "row", alignItems: "center", gap: 6, marginTop: 10 }}>
                        {said.ok ? <Icon name="check" size={16} tone={color.ok} width={2} /> : null}
                        <Text size={14} tone={said.ok ? color.ok : color.ink2} style={{ flex: 1 }}>{said.text}</Text>
                    </View>
                ) : shown.answer.kind === "approve" ? (
                    <View style={{ flexDirection: "row", gap: 8, marginTop: 10 }}>
                        <Button title="Deny" kind="outline" height={36} size={14} onPress={() => decide("DENY")} busy={answer.isPending && picked === "DENY"} disabled={answer.isPending} />
                        <Button title="Approve" height={36} size={14} onPress={() => decide("APPROVE_ONCE")} busy={answer.isPending && picked === "APPROVE_ONCE"} disabled={answer.isPending} />
                    </View>
                ) : shown.answer.kind === "choose" ? (
                    (() => {
                        const choice = shown.answer;
                        return (
                            <View style={{ gap: 8, marginTop: 10 }}>
                                {choice.options.map((option) => (
                                    <Press
                                        key={option}
                                        label={option}
                                        disabled={answer.isPending}
                                        onPress={() => decide("APPROVE_ONCE", { [choice.questionId]: option }, option)}
                                        style={{ minHeight: 44, paddingHorizontal: 14, justifyContent: "center", borderRadius: 12, borderWidth: 1, borderColor: picked === option ? color.info : color.line2, backgroundColor: picked === option ? color.pick : "transparent" }}
                                    >
                                        <Text size={15} tone={picked === option ? color.info : color.ink}>{option}</Text>
                                    </Press>
                                ))}
                                <Press role="link" label="Or in your own words" onPress={open} style={{ minHeight: 40, justifyContent: "center", paddingHorizontal: 2 }}>
                                    <Text size={14} tone={color.ink2}>Or in your own words</Text>
                                </Press>
                            </View>
                        );
                    })()
                ) : null}
            </View>
        </View>
    );
}
