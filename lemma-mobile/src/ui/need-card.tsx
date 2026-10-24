import { useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useAnswer, useCard } from "../data/hooks";
import { ageOf, hrefOfNeed } from "../data/model";
import type { Lemma, NeedItem } from "../data/types";
import { LemmaFace, PersonFace } from "./face";
import { Button, Pill, Press } from "./press";
import { Counted, Text } from "./text";
import { color } from "./tokens";

/** A thing that needs the reader, answerable where it stands: approve or
 *  not, one of a few choices, or a way into where it is answered. */
export function NeedCard({ item, lemma, behind, now }: { item: NeedItem; lemma: Lemma | null; behind: boolean; now: number }) {
    const router = useRouter();
    const card = useCard(item);
    const answer = useAnswer();
    const [said, setSaid] = useState<string | null>(null);
    const [picked, setPicked] = useState<string | null>(null);
    const shown = card.data ?? { ...item, note: item.detail, answer: { kind: "open" as const, label: "Open" } };
    const open = () => router.push(hrefOfNeed(item) as never);
    const decide = (decision: "APPROVE_ONCE" | "DENY", answers?: Record<string, unknown>, label?: string) => {
        setPicked(label ?? decision);
        answer.mutate({ item, decision, answers }, {
            onSuccess: () => setSaid(decision === "DENY" ? "Declined." : label ? `Answered: ${label}.` : "Approved."),
            onError: (problem) => {
                setPicked(null);
                setSaid(problem instanceof Error ? problem.message : "That did not go through.");
            },
        });
    };
    const yes = item.kind === "APPROVAL" || item.kind === "PERMISSION";
    return (
        <View style={{ borderWidth: 1, borderColor: color.line2, borderRadius: 16, paddingTop: 14, paddingHorizontal: 14, paddingBottom: 12, gap: 8 }}>
            <Press role="link" label={`${item.podName}: ${shown.title}. Open`} onPress={open} style={{ gap: 8 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    {item.kind === "CONTACT" ? <PersonFace name={item.title.replace(/^Reply to /, "")} size={22} /> : <LemmaFace id={item.podId} icon={lemma?.iconUrl} size={22} />}
                    <Text size={13} weight="medium">{item.podName}</Text>
                    <Text size={13} tone={color.ink2}>{ageOf(item.sinceMs, now)}</Text>
                    <View style={{ flex: 1 }} />
                    {behind ? <Pill text="Behind" tone="amber" /> : yes ? <Pill text="Your yes" tone="violet" /> : null}
                </View>
                <Counted size={16} style={{ lineHeight: 21.6 }}>{shown.title}</Counted>
                {shown.note ? <Counted size={13} tone={color.ink3}>{shown.note}</Counted> : null}
            </Press>
            {said ? (
                <Text size={14} tone={color.ink2} accessibilityLiveRegion="polite">{said}</Text>
            ) : shown.answer.kind === "approve" ? (
                <View style={{ flexDirection: "row", gap: 8, marginTop: 2 }}>
                    <Button title={shown.answer.approve} onPress={() => decide("APPROVE_ONCE")} busy={answer.isPending && picked === "APPROVE_ONCE"} disabled={answer.isPending} />
                    <Button title={shown.answer.decline} kind="outline" onPress={() => decide("DENY")} busy={answer.isPending && picked === "DENY"} disabled={answer.isPending} />
                </View>
            ) : shown.answer.kind === "choose" ? (
                (() => {
                    const choice = shown.answer;
                    const pair = choice.options.length === 2;
                    return (
                        <View style={{ flexDirection: "row", gap: 8, marginTop: 2 }}>
                            {choice.options.map((option, index) => (
                                <Button
                                    key={option}
                                    title={option}
                                    kind={pair && index === 0 ? "primary" : "outline"}
                                    grow={!pair}
                                    onPress={() => decide("APPROVE_ONCE", { [choice.questionId]: option }, option)}
                                    busy={answer.isPending && picked === option}
                                    disabled={answer.isPending}
                                />
                            ))}
                        </View>
                    );
                })()
            ) : (
                <View style={{ flexDirection: "row", marginTop: 2 }}>
                    <Button title={shown.answer.label} onPress={open} />
                </View>
            )}
        </View>
    );
}
