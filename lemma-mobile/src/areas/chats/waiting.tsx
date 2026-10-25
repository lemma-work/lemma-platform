import { useMemo, useState } from "react";
import { View } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { buildSchemaFormFields, buildSchemaFormPayload, buildSchemaFormValues } from "lemma-sdk";
import { useCacheKey } from "../../data/hooks";
import { Loading } from "../../ui/chrome";
import { Form } from "../../ui/form/form";
import { fromSchemaFields, type FormErrors, type FormValues } from "../../ui/form/model";
import { Button, Press } from "../../ui/press";
import { Counted, Num, Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { Field, Label, Trouble } from "./bits";
import { chatKeys, useChatsSource } from "./hooks";
import { newMessages, whereSaid, type WaitingEntry } from "./model";
import { chatsRoutes } from "./routes";
import { channelTitle, sinceShort } from "@web/channels/model";
import { problemOf } from "@web/channels/problem";
import { choicesOf, formTarget, moveFor, waitingSchema, waitingUiSchema, type WaitingQuestion } from "@web/channels/waiting";

/** What waits on the reader in a Lemma's chats: the web's bell, laid out as
 *  a section at the top of a list rather than a panel behind an icon. New
 *  messages in their own chat with the Lemma, the questions put to them --
 *  each answered right here -- and the messages naming them, each opening
 *  its chat at that message. */
export function WaitingSection({ podId, entries, lemmaName, now, title = "Waiting on you" }: { podId: string; entries: WaitingEntry[]; lemmaName: string; now: number; title?: string }) {
    const router = useRouter();
    if (entries.length === 0) return null;
    return (
        <View accessibilityLabel={title}>
            <Label right={<Num size={12.5} tone={color.violetInk}>{entries.length}</Num>}>{title}</Label>
            {entries.map((entry) => {
                if (entry.kind === "question") return <WaitingRow key={entry.key} podId={podId} question={entry.question} now={now} />;
                if (entry.kind === "mention") {
                    const item = entry.mention;
                    return (
                        <Press key={entry.key} role="link" label={`${item.from} named you. ${whereSaid(item)}`} onPress={() => router.push(chatsRoutes.shared(podId, item.channelId, { line: item.id, thread: item.threadRootId }) as never)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH, paddingHorizontal: 4 }}>
                            <View style={{ width: 20, alignItems: "center" }}><Text size={15} tone={color.violetInk}>@</Text></View>
                            <Text size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                                {item.from}
                                <Text size={14} tone={color.ink3}>{"  " + (item.text ? item.text.replace(/\s+/g, " ") : whereSaid(item))}</Text>
                            </Text>
                            <Num size={13} tone={color.ink3}>{sinceShort(item.at, new Date(now))}</Num>
                        </Press>
                    );
                }
                return (
                    <Press key={entry.key} role="link" label={`${newMessages(entry.count)} from ${lemmaName}`} onPress={() => router.push(chatsRoutes.shared(podId, entry.channel.id) as never)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH, paddingHorizontal: 4 }}>
                        <View style={{ width: 20, alignItems: "center" }}><View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} /></View>
                        <Text size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                            {channelTitle(entry.channel, lemmaName)}
                            <Text size={14} tone={color.ink3}>{"  " + newMessages(entry.count)}</Text>
                        </Text>
                    </Press>
                );
            })}
        </View>
    );
}

/** One question, and the way to answer it (the web's `WaitingRow`): a few
 *  choices, approve or deny, a form, or words. `bare` on a chat's timeline,
 *  where the message above it already says who asked, and where. */
export function WaitingRow({ podId, question, bare = false, now = Date.now() }: { podId: string; question: WaitingQuestion; bare?: boolean; now?: number }) {
    const router = useRouter();
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const [text, setText] = useState("");
    const [problem, setProblem] = useState<string | null>(null);
    const move = moveFor(question);
    const target = formTarget(question);
    const done = () => {
        setProblem(null);
        void cache.invalidateQueries({ queryKey: chatKeys.inbox(key, podId) });
        void cache.invalidateQueries({ queryKey: chatKeys.lines(key, podId, question.channelId) });
        void cache.invalidateQueries({ queryKey: chatKeys.detail(key, podId, question.channelId) });
        if (question.threadRootId) void cache.invalidateQueries({ queryKey: chatKeys.thread(key, podId, question.channelId, question.threadRootId) });
        void cache.invalidateQueries({ queryKey: [key, "pulse"] });
    };
    const answer = useMutation({
        mutationFn: (given: Record<string, unknown>) => source.answer(podId, question.channelId, question.id, given),
        onSuccess: done,
        onError: (failure) => setProblem(problemOf(failure, "Couldn’t send your answer.")),
    });

    return (
        <View style={{ gap: 10, paddingVertical: bare ? 4 : 10, paddingHorizontal: bare ? 0 : 4, borderBottomWidth: bare ? 0 : 1, borderBottomColor: color.line }}>
            {!bare ? (
                <Press role="link" label={`${question.from}, ${whereSaid(question)}. Open it there`} onPress={() => router.push(chatsRoutes.shared(podId, question.channelId, { line: question.id, thread: question.threadRootId }) as never)} style={{ gap: 3 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: color.violet }} />
                        <Text size={14} tone={color.ink2} numberOfLines={1} style={{ flex: 1 }}>{question.from} · {whereSaid(question)}</Text>
                        <Num size={13} tone={color.ink3}>{sinceShort(question.askedAt, new Date(now))}</Num>
                    </View>
                    {question.text ? <Counted size={15.5}>{question.text}</Counted> : null}
                </Press>
            ) : null}

            {move === "choose" ? (
                <View accessibilityRole="radiogroup" accessibilityLabel="Answers" style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                    {choicesOf(question).map((choice) => (
                        <Button key={choice} title={choice} kind="outline" height={36} size={14} disabled={answer.isPending} onPress={() => answer.mutate({ choice })} />
                    ))}
                </View>
            ) : null}

            {move === "approve" ? (
                <View style={{ flexDirection: "row", gap: 8 }}>
                    <Button title="Approve" height={36} size={14} busy={answer.isPending && answer.variables?.decision === "APPROVE_ONCE"} disabled={answer.isPending} onPress={() => answer.mutate({ decision: "APPROVE_ONCE" })} />
                    <Button title="Deny" kind="outline" height={36} size={14} disabled={answer.isPending} onPress={() => answer.mutate({ decision: "DENY" })} />
                </View>
            ) : null}

            {move === "form" && target ? <FormAnswer podId={podId} runId={target.runId} nodeId={target.nodeId} onDone={done} /> : null}

            {move === "write" || move === "choose" ? (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    <Field
                        accessibilityLabel={"Answer " + question.from}
                        value={text}
                        onChangeText={setText}
                        placeholder={move === "choose" ? "Or write your own" : "Your answer"}
                        returnKeyType="send"
                        onSubmitEditing={() => { if (text.trim()) answer.mutate({ text: text.trim() }); }}
                        style={{ flex: 1, minHeight: 40, paddingVertical: 8 }}
                    />
                    <Button title="Send" height={40} size={14} busy={answer.isPending && typeof answer.variables?.text === "string"} disabled={answer.isPending || !text.trim()} onPress={() => answer.mutate({ text: text.trim() })} />
                </View>
            ) : null}

            <Trouble>{problem}</Trouble>
        </View>
    );
}

/** A workflow's form a question waits on, drawn natively and submitted in
 *  place (the web's `FormAnswer`). A run that has moved past the step says
 *  so rather than drawing a form the server would refuse. */
export function FormAnswer({ podId, runId, nodeId, onDone }: { podId: string; runId: string; nodeId: string; onDone: () => void }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const [values, setValues] = useState<FormValues | null>(null);
    const [errors, setErrors] = useState<FormErrors>({});
    const [problem, setProblem] = useState<string | null>(null);
    const run = useQuery({ queryKey: chatKeys.run(key, podId, runId), queryFn: () => source.workflowRun(podId, runId), staleTime: 15_000 });
    const schema = useMemo(() => waitingSchema(run.data as Parameters<typeof waitingSchema>[0], nodeId), [run.data, nodeId]);
    const ui = useMemo(() => waitingUiSchema(run.data as Parameters<typeof waitingUiSchema>[0], nodeId), [run.data, nodeId]);
    const fields = useMemo(() => (schema ? fromSchemaFields(buildSchemaFormFields(schema, ui ?? undefined)) : []), [schema, ui]);
    const current = values ?? (schema ? buildSchemaFormValues(schema, undefined, ui ?? undefined) : {});
    const submit = useMutation({
        mutationFn: async () => {
            const built = buildSchemaFormPayload(schema ?? {}, current, ui ?? undefined);
            if (!built.isValid) {
                setErrors(built.errors);
                throw new Error("Some fields need a look.");
            }
            await source.submitForm(podId, runId, nodeId, built.data as Record<string, unknown>);
        },
        onSuccess: () => {
            setProblem(null);
            void run.refetch();
            onDone();
        },
        onError: (failure) => {
            const status = (failure as { statusCode?: number } | null)?.statusCode;
            setProblem(status === 422 ? "The workflow is no longer waiting on this step." : problemOf(failure, "Couldn’t send the form."));
        },
    });

    if (run.isPending) return <Loading label="Reading the form" />;
    if (run.isError) {
        return (
            <View style={{ gap: 6 }}>
                <Trouble>That form could not be read.</Trouble>
                <Button title="Try again" kind="outline" height={36} size={14} onPress={() => void run.refetch()} />
            </View>
        );
    }
    if (!schema) return <Text size={14.5} tone={color.ink2}>This has been handled already: the workflow has moved on.</Text>;
    return (
        <View style={{ gap: 14 }}>
            <Form
                fields={fields}
                values={current}
                errors={errors}
                onChange={(name, value) => {
                    setValues({ ...current, [name]: value });
                    setErrors((was) => (was[name] ? { ...was, [name]: "" } : was));
                }}
            />
            <View style={{ flexDirection: "row" }}>
                <Button title="Send the form" height={40} size={14} busy={submit.isPending} onPress={() => submit.mutate()} />
            </View>
            <Trouble>{problem}</Trouble>
        </View>
    );
}
