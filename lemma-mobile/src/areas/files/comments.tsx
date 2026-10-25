import { useMemo, useRef, useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Platform, ScrollView, TextInput, View } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMe } from "../../data/hooks";
import { stampOf } from "../../data/model";
import { routes } from "../../data/routes";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Num, Text } from "../../ui/text";
import { color, font, TOUCH } from "../../ui/tokens";
import { FilesIcon } from "./glyphs";
import { useFilesKey, useFilesSource, useMentionable, useWakes } from "./hooks";
import { wakeLine } from "./model";
import type { CommentsStatus, Mentionable } from "./source";
import { commentRow, mentionAt, mentionsIn, pendingAsk, type CommentRow, type Thread } from "@web/docpages/comments/model";
import { docTitle } from "@web/library/doc-title";
import type { Anchor } from "./web/protocol";
import { canAskAgain, isPaused, wakeFor } from "@web/docpages/comments/wake";

/** A page's comments, from the bottom of the screen (the web's
 *  `comments/panel.tsx`). Threads in the order they began, open ones first;
 *  each is the words it is about, what people and agents said, and a box to
 *  answer in. Naming an agent with @ asks it: it wakes, reads the page, does
 *  the thing and answers in the thread. Naming a person tells them. */

const said = (error: unknown, fallback: string) => (error instanceof Error && error.message ? error.message : fallback);
const forbidden = (error: unknown) => (error as { statusCode?: number } | null)?.statusCode === 403;

export function CommentsSheet({ podId, path, lemmaName, open, status, threads, loading, draft, active, onClose, onDraftDone, onActive, onStartPage }: {
    podId: string;
    path: string;
    lemmaName: string;
    open: boolean;
    status: CommentsStatus | undefined;
    threads: Thread[];
    loading: boolean;
    draft: Anchor | "page" | null;
    active: string | null;
    onClose: () => void;
    onDraftDone: () => void;
    onActive: (id: string | null) => void;
    /** Start a comment on the page as a whole. */
    onStartPage: () => void;
}) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const [resolved, setResolved] = useState(false);
    const known = useMentionable(podId, lemmaName);
    const enable = useMutation({
        mutationFn: () => source.enableComments(podId),
        onSuccess: () => void cache.invalidateQueries({ queryKey: keyOf("comments-status") }),
    });
    const openOnes = threads.filter((one) => !one.root.resolved);
    const done = threads.filter((one) => one.root.resolved);
    /* The thread asked for first, the rest after it. */
    const shown = (resolved ? done : openOnes).slice().sort((a, b) => Number(b.root.id === active) - Number(a.root.id === active));
    const ask = (known.data ?? []).find((one) => one.kind === "agent");
    const askable = ask ? "@" + ask.label : "@ an agent";

    return (
        <Sheet open={open} onClose={onClose} tall>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
                <View style={{ flexDirection: "row", alignItems: "center", paddingHorizontal: 20, paddingBottom: 6, gap: 6 }}>
                    <Text size={17} weight="medium" style={{ flex: 1 }}>Comments</Text>
                    {status === "ready" ? (
                        (["Open", "Resolved"] as const).map((label) => {
                            const chosen = (label === "Resolved") === resolved;
                            const count = label === "Open" ? openOnes.length : done.length;
                            return (
                                <Press key={label} role="tab" label={label} accessibilityState={{ selected: chosen }} onPress={() => setResolved(label === "Resolved")}
                                    style={{ height: 32, paddingHorizontal: 12, borderRadius: 999, justifyContent: "center", backgroundColor: chosen ? color.wash : "transparent" }}>
                                    <Text size={13.5} tone={chosen ? color.ink : color.ink2}>{label}{count ? <Num size={13.5} tone={chosen ? color.ink : color.ink2}> {count}</Num> : null}</Text>
                                </Press>
                            );
                        })
                    ) : null}
                </View>
                <ScrollView style={{ maxHeight: 560 }} contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 12 }} keyboardShouldPersistTaps="handled">
                    {status === undefined ? <ActivityIndicator color={color.ink3} style={{ padding: 16 }} /> : null}
                    {status === "missing" ? (
                        <View style={{ gap: 12, paddingVertical: 6 }}>
                            <Text size={15} tone={color.ink2} style={{ lineHeight: 21 }}>
                                Comments aren’t on here yet. Turning them on adds one table, doc_comments, that everyone here can read, and where {askable} can be asked to answer.
                            </Text>
                            <Button title="Turn on comments" busy={enable.isPending} onPress={() => enable.mutate()} />
                            {enable.isError ? <Text size={13.5} tone={color.amber}>{forbidden(enable.error) ? "Only an editor here can turn comments on." : "Couldn’t turn comments on."}</Text> : null}
                        </View>
                    ) : null}
                    {status === "forbidden" ? <Text size={15} tone={color.ink2} style={{ paddingVertical: 8 }}>You can’t read comments here.</Text> : null}
                    {status === "ready" ? (
                        <>
                            {draft ? (
                                <View style={{ paddingVertical: 10, gap: 8, borderBottomWidth: 1, borderBottomColor: color.line }}>
                                    {draft !== "page" && draft.quote ? <Quote text={draft.quote} /> : null}
                                    <Composer podId={podId} path={path} known={known.data ?? []} anchor={draft === "page" ? null : draft} parentId={null}
                                        placeholder={draft === "page" ? "Comment on this page…" : `Comment, or ${askable} to ask…`}
                                        onDone={(id) => { onDraftDone(); if (id) onActive(id); }} onCancel={onDraftDone} />
                                </View>
                            ) : null}
                            {loading ? <ActivityIndicator color={color.ink3} style={{ padding: 16 }} /> : null}
                            {!loading && !draft && shown.length === 0 ? (
                                <Text size={15} tone={color.ink2} style={{ paddingVertical: 10, lineHeight: 21 }}>
                                    {resolved ? "Nothing resolved yet." : `No comments. Select some words and choose Comment, or ${askable} there to ask for a change.`}
                                </Text>
                            ) : null}
                            {shown.map((thread) => (
                                <ThreadView key={thread.root.id} podId={podId} path={path} thread={thread} known={known.data ?? []} active={thread.root.id === active} onClose={onClose} />
                            ))}
                            {!draft && !resolved ? (
                                <Press label="Comment on the page" onPress={onStartPage} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: TOUCH, marginTop: 4 }}>
                                    <FilesIcon name="comment" size={18} tone={color.ink2} />
                                    <Text size={15} tone={color.ink2}>Comment on the page</Text>
                                </Press>
                            ) : null}
                        </>
                    ) : null}
                </ScrollView>
            </KeyboardAvoidingView>
        </Sheet>
    );
}

function Quote({ text }: { text: string }) {
    return (
        <View style={{ borderLeftWidth: 2, borderLeftColor: color.amber, paddingLeft: 10 }}>
            <Text size={14} tone={color.ink2} numberOfLines={4}>{text}</Text>
        </View>
    );
}

function ThreadView({ podId, path, thread, known, active, onClose }: { podId: string; path: string; thread: Thread; known: Mentionable[]; active: boolean; onClose: () => void }) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const [replying, setReplying] = useState(false);
    const waiting = thread.root.resolved ? null : pendingAsk(thread);
    const resolve = useMutation({
        mutationFn: () => source.updateComment(podId, thread.root.id, { resolved: !thread.root.resolved }),
        onSettled: () => void cache.invalidateQueries({ queryKey: keyOf("comments", path) }),
    });
    return (
        <View style={{ paddingVertical: 12, gap: 10, borderBottomWidth: 1, borderBottomColor: color.line, backgroundColor: active ? color.faint : "transparent", marginHorizontal: -20, paddingHorizontal: 20 }}>
            {thread.root.quote ? <Quote text={thread.root.quote} /> : null}
            {[thread.root, ...thread.replies].map((row) => <Message key={row.id} podId={podId} path={path} row={row} known={known} />)}
            {waiting ? <AgentStatus podId={podId} ask={waiting} known={known} onClose={onClose} /> : null}
            <View style={{ flexDirection: "row", gap: 4, marginLeft: -10 }}>
                {!thread.root.resolved && !replying ? (
                    <Press label="Reply" onPress={() => setReplying(true)} style={{ height: 34, paddingHorizontal: 10, justifyContent: "center" }}>
                        <Text size={14} tone={color.ink2}>Reply</Text>
                    </Press>
                ) : null}
                <Press label={thread.root.resolved ? "Reopen" : "Resolve"} disabled={resolve.isPending} onPress={() => resolve.mutate()} style={{ height: 34, paddingHorizontal: 10, flexDirection: "row", alignItems: "center", gap: 5 }}>
                    <Icon name="check" size={15} tone={color.ink2} />
                    <Text size={14} tone={color.ink2}>{thread.root.resolved ? "Reopen" : "Resolve"}</Text>
                </Press>
            </View>
            {replying ? (
                <Composer podId={podId} path={path} known={known} anchor={null} parentId={thread.root.id} placeholder="Reply…" onDone={() => setReplying(false)} onCancel={() => setReplying(false)} />
            ) : null}
        </View>
    );
}

function Message({ podId, path, row, known }: { podId: string; path: string; row: CommentRow; known: Mentionable[] }) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const me = useMe();
    const remove = useMutation({
        mutationFn: () => source.deleteComment(podId, row.id),
        onSettled: () => void cache.invalidateQueries({ queryKey: keyOf("comments", path) }),
    });
    const agent = row.authorAgent ? known.find((one) => one.kind === "agent" && (one.label.toLowerCase() === row.authorAgent!.toLowerCase() || one.key.toLowerCase() === row.authorAgent!.toLowerCase())) : null;
    const person = !row.authorAgent ? known.find((one) => one.kind === "person" && one.key === row.authorId) : null;
    const mine = !row.authorAgent && row.authorId !== null && row.authorId === me.data?.id;
    const name = agent?.label ?? row.authorAgent ?? (mine ? "You" : person?.label ?? row.authorName ?? "Someone");
    const at = row.createdAt ? Date.parse(row.createdAt) : NaN;
    return (
        <View style={{ flexDirection: "row", gap: 10 }}>
            <View style={{ width: 24, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center", backgroundColor: agent || row.authorAgent ? color.violetWash : color.wash }}>
                <Text size={11.5} tone={agent || row.authorAgent ? color.violetInk : color.ink2}>{(name[0] ?? "?").toUpperCase()}</Text>
            </View>
            <View style={{ flex: 1, minWidth: 0, gap: 2 }}>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    <Text size={14} weight="medium" numberOfLines={1} style={{ flexShrink: 1 }}>{name}</Text>
                    {Number.isFinite(at) ? <Num size={12.5} tone={color.ink3}>{stampOf(at, Date.now())}</Num> : null}
                    <View style={{ flex: 1 }} />
                    {mine ? (
                        <Press label="Delete this comment" disabled={remove.isPending} onPress={() => remove.mutate()} style={{ padding: 4 }}>
                            <FilesIcon name="trash" size={15} tone={color.ink3} />
                        </Press>
                    ) : null}
                </View>
                <WithMentions body={row.body} known={known} />
            </View>
        </View>
    );
}

/** The body, with the names it mentions picked out. */
function WithMentions({ body, known }: { body: string; known: Mentionable[] }) {
    const names = mentionsIn(body, known).map((one) => "@" + one.label);
    if (names.length === 0) return <Text size={15} style={{ lineHeight: 21 }}>{body}</Text>;
    const pattern = new RegExp("(" + names.map((one) => one.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "gi");
    return (
        <Text size={15} style={{ lineHeight: 21 }}>
            {body.split(pattern).map((part, at) => (at % 2 === 1 ? <Text key={at} size={15} tone={color.violetInk}>{part}</Text> : part))}
        </Text>
    );
}

/* A firing still on its way to an answer. */
const MOVING = new Set(["RECEIVED", "PROCESSING", "DISPATCHED"]);

/** What the named agent is doing about a comment, read off its wake-up's own
 *  runs: the firing whose row is this comment. */
function AgentStatus({ podId, ask, known, onClose }: { podId: string; ask: CommentRow; known: Mentionable[]; onClose: () => void }) {
    const router = useRouter();
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const key = ask.mentionedAgent!;
    const agent = known.find((one) => one.kind === "agent" && one.key.toLowerCase() === key.toLowerCase());
    const name = agent?.label ?? key;
    const wakes = useWakes(podId, true);
    const job = wakeFor(wakes.data ?? [], key);
    const asked = ask.createdAt ? Date.parse(ask.createdAt) : NaN;
    const runs = useQuery({
        queryKey: keyOf("wake-runs", job?.id ?? null),
        enabled: Boolean(job),
        queryFn: () => source.wakeRuns(podId, job!.id),
        refetchInterval: (query) => {
            const mine = query.state.data?.find((one) => one.subjectId === ask.id);
            if (mine) return MOVING.has(mine.status) ? 4000 : false;
            return Date.now() - asked <= 90_000 ? 4000 : 30_000;
        },
    });
    const run = (runs.data ?? []).find((one) => one.subjectId === ask.id) ?? null;
    const turnOn = useMutation({
        mutationFn: async () => {
            await source.turnOnWake(podId, { key, label: name }, job);
            await source.askAgain(podId, ask.id, key);
        },
        onSuccess: () => void cache.invalidateQueries({ queryKey: keyOf("wakes") }),
    });
    const aged = Number.isFinite(asked) ? Date.now() - asked : 0;
    if (wakes.isPending) return null;
    const line = !job ? `${name} isn’t set to answer comments, so it hasn’t seen this.`
        : isPaused(job) && !run ? `${name}’s comment replies are paused, so it hasn’t seen this.`
        : wakeLine(name, run, aged);
    const offer = !job ? `Let ${name} answer` : isPaused(job) && !run ? "Turn back on"
        : (run?.tone === "bad" || (!run && aged > 90_000)) && canAskAgain(job) ? "Ask again" : null;
    const working = Boolean(job) && !(isPaused(job!) && !run) && run?.tone !== "bad" && run?.status !== "COMPLETED" && run?.status !== "FILTERED";
    return (
        <View style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 8, paddingVertical: 6, paddingHorizontal: 10, borderRadius: 10, backgroundColor: working ? color.violetWash : color.chrome }}>
            <Text size={13.5} tone={run?.tone === "bad" ? color.amber : working ? color.violetInk : color.ink2} style={{ flexShrink: 1 }}>{line}</Text>
            {run?.targetRunId && run.targetKind === "agent" ? (
                <Press label="Watch" onPress={() => { onClose(); router.push(routes.chat(podId, run.targetRunId) as never); }} style={{ paddingVertical: 2 }}>
                    <Text size={13.5} tone={color.ink} style={{ textDecorationLine: "underline" }}>Watch</Text>
                </Press>
            ) : null}
            {offer ? (
                <Press label={offer} disabled={turnOn.isPending} onPress={() => turnOn.mutate()} style={{ paddingVertical: 2 }}>
                    {turnOn.isPending ? <ActivityIndicator size="small" color={color.ink3} /> : <Text size={13.5} tone={color.ink} style={{ textDecorationLine: "underline" }}>{offer}</Text>}
                </Press>
            ) : null}
            {turnOn.isError ? <Text size={13} tone={color.amber}>{forbidden(turnOn.error) ? "Only an editor can turn that on." : said(turnOn.error, "Couldn’t turn it on.")}</Text> : null}
        </View>
    );
}

function Composer({ podId, path, known, anchor, parentId, placeholder, onDone, onCancel }: {
    podId: string;
    path: string;
    known: Mentionable[];
    anchor: Anchor | null;
    parentId: string | null;
    placeholder: string;
    onDone: (id: string | null) => void;
    onCancel: () => void;
}) {
    const source = useFilesSource();
    const keyOf = useFilesKey(podId);
    const cache = useQueryClient();
    const me = useMe();
    const [body, setBody] = useState("");
    const [caret, setCaret] = useState(0);
    const input = useRef<TextInput>(null);
    const others = useMemo(() => known.filter((one) => one.kind === "agent" || one.key !== me.data?.id), [known, me.data?.id]);
    const typing = mentionAt(body, caret);
    const choices = typing ? others.filter((one) => one.label.toLowerCase().startsWith(typing.query.toLowerCase())).slice(0, 6) : [];
    const named = mentionsIn(body, others);
    const agent = named.find((one) => one.kind === "agent") ?? null;
    const wakes = useWakes(podId, Boolean(agent));
    const agentWakes = agent ? wakeFor(wakes.data ?? [], agent.key) : null;
    const wake = useMutation({
        mutationFn: () => source.turnOnWake(podId, { key: agent!.key, label: agent!.label }, agentWakes),
        onSuccess: () => void cache.invalidateQueries({ queryKey: keyOf("wakes") }),
    });
    const send = useMutation({
        mutationFn: async () => {
            const author = { id: me.data?.id ?? null, name: me.data?.name || me.data?.email || "Someone" };
            const row = await source.addComment(podId, commentRow({ filePath: path, anchor, body, parentId, mentions: named }), author);
            /* People named are told. Best effort: the comment stands either way. */
            for (const person of named.filter((one) => one.kind === "person")) {
                void source.notify(podId, person.key, `You were mentioned on ${docTitle(path)}:\n\n${body.trim()}`).catch(() => undefined);
            }
            return row;
        },
        onSuccess: (row) => {
            setBody("");
            void cache.invalidateQueries({ queryKey: keyOf("comments", path) });
            onDone(row.parentId ?? row.id);
        },
    });
    const choose = (one: Mentionable) => {
        if (!typing) return;
        const next = body.slice(0, typing.start) + "@" + one.label + " " + body.slice(caret);
        const at = typing.start + one.label.length + 2;
        setBody(next);
        setCaret(at);
    };
    return (
        <View style={{ gap: 8 }}>
            {choices.length > 0 ? (
                <View style={{ borderWidth: 1, borderColor: color.line2, borderRadius: 12, overflow: "hidden" }}>
                    {choices.map((one) => (
                        <Press key={one.kind + one.key} label={`Mention ${one.label}`} onPress={() => choose(one)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: TOUCH, paddingHorizontal: 12 }}>
                            <Text size={15} style={{ flex: 1 }} numberOfLines={1}>{one.label}</Text>
                            <Text size={12.5} tone={color.ink3}>{one.kind === "agent" ? "Agent" : "Person"}</Text>
                        </Press>
                    ))}
                </View>
            ) : null}
            <TextInput
                ref={input}
                accessibilityLabel={placeholder}
                value={body}
                onChangeText={(next) => { setBody(next); setCaret(next.length); }}
                onSelectionChange={(event) => setCaret(event.nativeEvent.selection.start)}
                placeholder={placeholder}
                placeholderTextColor={color.ink3}
                multiline
                autoFocus
                style={{ minHeight: 64, maxHeight: 160, paddingHorizontal: 12, paddingVertical: 10, borderRadius: 12, borderWidth: 1, borderColor: color.line2, fontFamily: font.regular, fontSize: 15.5, color: color.ink, textAlignVertical: "top" }}
            />
            {agent ? (
                <View style={{ flexDirection: "row", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
                    <Text size={13} tone={color.ink2}>
                        {agentWakes && !isPaused(agentWakes) ? `${agent.label} will read this and answer here.`
                            : agentWakes ? `${agent.label}’s comment replies are paused.`
                            : wakes.isSuccess ? `${agent.label} isn’t set to answer comments yet.` : ""}
                    </Text>
                    {wakes.isSuccess && (!agentWakes || isPaused(agentWakes)) ? (
                        <Press label={agentWakes ? "Turn back on" : `Let ${agent.label} answer`} disabled={wake.isPending} onPress={() => wake.mutate()}>
                            <Text size={13} tone={color.ink} style={{ textDecorationLine: "underline" }}>{wake.isPending ? "Turning on…" : agentWakes ? "Turn back on" : `Let ${agent.label} answer`}</Text>
                        </Press>
                    ) : null}
                    {wake.isError ? <Text size={13} tone={color.amber}>{forbidden(wake.error) ? "Only an editor can set that up." : said(wake.error, "Couldn’t set that up.")}</Text> : null}
                </View>
            ) : null}
            <View style={{ flexDirection: "row", gap: 10 }}>
                <Button title="Cancel" kind="outline" grow height={38} onPress={onCancel} />
                <Button title={parentId ? "Reply" : "Comment"} grow height={38} busy={send.isPending} disabled={!body.trim()} onPress={() => send.mutate()} />
            </View>
            {send.isError ? <Text size={13.5} tone={color.amber}>{said(send.error, "Couldn’t post that.")}</Text> : null}
        </View>
    );
}
