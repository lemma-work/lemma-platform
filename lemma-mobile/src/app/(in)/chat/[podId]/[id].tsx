import { useCallback, useEffect, useMemo, useRef, useState, type MutableRefObject } from "react";
import { Alert, KeyboardAvoidingView, ScrollView, TextInput, View, type NativeScrollEvent, type NativeSyntheticEvent } from "react-native";
import { useLocalSearchParams, useRouter } from "expo-router";
import { SafeAreaView, useSafeAreaInsets } from "react-native-safe-area-context";
import { adminRoutes } from "../../../../areas/admin/routes";
import { chatRoutes } from "../../../../areas/chat/routes";
import { moreRoutes } from "../../../../areas/more/routes";
import { useLemma } from "../../../../data/hooks";
import { NEW_CHAT, routes, workspace } from "../../../../data/routes";
import { Loading } from "../../../../ui/chrome";
import { LemmaFace } from "../../../../ui/face";
import { Icon } from "../../../../ui/icons";
import { identityOf } from "../../../../ui/identity";
import { Button, Press } from "../../../../ui/press";
import { Sheet } from "../../../../ui/sheet";
import { Text } from "../../../../ui/text";
import { useTheme } from "../../../../ui/theme";
import { color, font, TOUCH } from "../../../../ui/tokens";
import { BreathingRing } from "../../../../thread/breathing";
import { Composer } from "../../../../thread/composer";
import { mergeIndex, NOTHING_YET } from "../../../../thread/derive";
import { InteractionDock } from "../../../../thread/dock";
import { draftKeyFor } from "@web/thread/drafts";
import { Glyph } from "../../../../thread/glyphs";
import { takeFiles } from "../../../../thread/hand-over";
import { ChatLook } from "../../../../thread/look";
import { noModelSentence } from "@web/thread/model-setup";
import { ModelPicker } from "../../../../thread/model-picker";
import { runFailure } from "@web/thread/transcript-state";
import { useChat } from "../../../../thread/use-chat";
import { LiveReply, MessageMenu, TurnRow } from "../../../../thread/view";

/** How near the bottom still counts as reading the newest. */
const FOLLOW_PX = 80;
/** How near the top asks for the page before. */
const EARLIER_PX = 120;
const NO_CHOICES: never[] = [];

/** A chat with a Lemma: the web's conversation pane, for a phone. `id` is
 *  `new` until the first send creates the conversation. `ask` carries words
 *  from Home, a Lemma's page or the new-chat screen, sent once when the chat
 *  opens, with any `files` handed over; `fill` puts words in the box and
 *  sends nothing.
 *
 *  The Lemma's colour carries in (`ui/identity.ts`): the header sits on its
 *  wash, Send and the buttons and links below are its ink. Its face wears
 *  the violet breathing ring while a run goes, as on the Lemmas tab. */
export default function ChatScreen() {
    const params = useLocalSearchParams<{ podId: string; id: string }>();
    const adopted = useRef<string | null>(null);
    return <ChatBody key={usePaneKey(String(params.podId), String(params.id), adopted)} adopted={adopted} />;
}

/** The key the chat is drawn under. It changes when the route is taken to
 *  another conversation (a link, a notification), and not when a new chat
 *  is given its id, so a reply already streaming into it is not cut off:
 *  the `/o` frame's `usePaneKey`. */
function usePaneKey(podId: string, id: string, adopted: MutableRefObject<string | null>): string {
    const [held, setHeld] = useState({ podId, id, generation: 0 });
    if (held.podId !== podId || held.id !== id) {
        const same = held.podId === podId && held.id === NEW_CHAT && adopted.current === id;
        setHeld({ podId, id, generation: same ? held.generation : held.generation + 1 });
    }
    return `${podId}:${held.generation}`;
}

function ChatBody({ adopted }: { adopted: MutableRefObject<string | null> }) {
    const router = useRouter();
    const insets = useSafeAreaInsets();
    const params = useLocalSearchParams<{ podId: string; id: string; ask?: string; fill?: string; files?: string }>();
    const podId = String(params.podId);
    const { scheme } = useTheme();
    const identity = useMemo(() => identityOf(podId, scheme), [podId, scheme]);
    const [id, setId] = useState(String(params.id));
    const lemma = useLemma(podId);
    const name = lemma?.name ?? "the Lemma";
    const chat = useChat(podId, id, name, (made) => {
        adopted.current = made;
        setId(made);
        router.setParams({ id: made, ask: undefined, files: undefined, fill: undefined } as never);
    });

    /* The words this screen has sent for its `ask`, so a re-render or the same
       route opened again does not send them twice. */
    const asked = useRef<string | null>(null);
    useEffect(() => {
        if (!params.ask || asked.current === params.ask || id !== NEW_CHAT || !lemma) return;
        asked.current = String(params.ask);
        const files = takeFiles(params.files);
        if (files.length > 0) chat.attach(files);
        void chat.send(String(params.ask)).catch(() => undefined);
    }, [params.ask, params.files, id, lemma, chat]);

    /* A widget's ask, or a refused send taken back to edit: into the box. */
    const [fill, setFill] = useState<{ text: string; id: number } | null>(null);
    const filledFrom = useRef<string | null>(null);
    useEffect(() => {
        if (!params.fill || filledFrom.current === params.fill) return;
        filledFrom.current = String(params.fill);
        setFill({ text: String(params.fill), id: Date.now() });
    }, [params.fill]);
    useEffect(() => {
        if (chat.refill) setFill(chat.refill);
    }, [chat.refill]);
    const onCompose = useCallback((text: string, newConversation: boolean) => {
        if (newConversation) router.push(chatRoutes.filled(podId, text) as never);
        else setFill({ text, id: Date.now() });
    }, [router, podId]);
    const filled = useCallback(() => {
        setFill(null);
        chat.refilled();
    }, [chat]);

    /* Following the newest: only while the reader is at the bottom. A jump
       back appears once they have scrolled away from it. */
    const scroller = useRef<ScrollView>(null);
    const following = useRef(true);
    const [away, setAway] = useState(false);
    const earlierAsked = useRef(false);
    /* Only the reader's own scroll -- a drag and the fling after it -- says
       whether they are following, and asks for the page before. The scrolls
       this screen makes itself, to keep up with a reply whose pictures and
       cards are still growing, report positions that are already stale, and
       read as the reader leaving the bottom. */
    const dragging = useRef(false);
    const flinging = useRef(false);
    const onScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
        if (!dragging.current && !flinging.current) return;
        const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
        const fromBottom = contentSize.height - layoutMeasurement.height - contentOffset.y;
        following.current = fromBottom < FOLLOW_PX;
        setAway(!following.current && contentSize.height > layoutMeasurement.height);
        if (contentOffset.y < EARLIER_PX && chat.hasMore && !chat.loadingOlder) earlier();
    };
    const mark = (held: { current: boolean }, on: boolean) => () => { held.current = on; };
    /* Keep the reader's line where it was while a page arrives above it:
       only then, since the same rule would also hold the chat at its top
       when it first opens. */
    const [holding, setHolding] = useState(false);
    const earlier = () => {
        if (earlierAsked.current) return;
        earlierAsked.current = true;
        setHolding(true);
        void chat.loadOlder().finally(() => {
            earlierAsked.current = false;
            setTimeout(() => setHolding(false), 600);
        });
    };
    const toNewest = (animated: boolean) => {
        following.current = true;
        setAway(false);
        scroller.current?.scrollToEnd({ animated });
    };

    const [crowded, setCrowded] = useState(false);
    const [menu, setMenu] = useState(false);
    const [renaming, setRenaming] = useState<string | null>(null);
    const title = chat.title ?? (id === NEW_CHAT || (!chat.loading && !chat.error && chat.turns.length <= 1) ? "New chat" : "Chat");

    const working = chat.state === "running" && !chat.loading;
    /* A run that ends folds its line and lands its last words a moment
       after the stream stops; a reader following it is taken to them. */
    const wasWorking = useRef(working);
    useEffect(() => {
        const ended = wasWorking.current && !working;
        wasWorking.current = working;
        if (!ended || !following.current) return;
        const timer = setTimeout(() => { if (following.current) scroller.current?.scrollToEnd({ animated: true }); }, 350);
        return () => clearTimeout(timer);
    }, [working]);
    const live = Boolean(chat.streaming && (chat.streaming.text || chat.streaming.thinking || chat.streaming.tool || chat.streaming.waiting));
    const mergeInto = mergeIndex(chat.turns, live, working);
    const inFlight = chat.streaming ?? (working ? NOTHING_YET : null);
    const dockedId = chat.waitingOn?.id ?? chat.signingIn?.id ?? null;
    const placeholder = chat.waitingOn?.kind === "question"
        ? "Or answer in your own words…"
        : chat.reply.choices.find((one) => one.mode === chat.reply.mode)?.label.concat("…") ?? `Reply to ${name}`;

    const archive = () => {
        Alert.alert("Archive this chat?", "It leaves your chats. Anyone with its link can still read it.", [
            { text: "Cancel", style: "cancel" },
            {
                text: "Archive",
                onPress: () => void chat.archive()
                    .then(() => (router.canGoBack() ? router.back() : router.replace(routes.lemma(podId) as never)))
                    .catch(() => Alert.alert("Not archived", "That did not go through. Try again.")),
            },
        ]);
    };
    const saveName = () => {
        const next = (renaming ?? "").trim();
        setRenaming(null);
        if ((next || null) === (chat.title ?? null)) return;
        void chat.rename(next || null).catch(() => Alert.alert("Not renamed", "That did not go through. Try again."));
    };

    /* Stable, as the composer is memoised: it would otherwise redraw for
       every token of a reply, and a box that redraws that often lags. */
    const { send } = chat;
    const onSend = useCallback((text: string) => {
        toNewest(false);
        return send(text);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [send]);
    /* A voice call about this chat (area G). */
    const call = useCallback(() => router.push(moreRoutes.call(podId, chat.conversationId) as never), [router, podId, chat.conversationId]);
    const model = useMemo(
        () => <ModelPicker podId={podId} conversationId={chat.conversationId} agentId={chat.agentId} picked={chat.pickedModel} onPicked={chat.setPickedModel} />,
        [podId, chat.conversationId, chat.agentId, chat.pickedModel, chat.setPickedModel],
    );
    const choices = chat.waitingOn?.kind === "question" ? NO_CHOICES : chat.reply.choices;

    const failure = runFailure(chat.error);
    const failedLine = chat.state === "failed" && !chat.onReload;
    const errorLine = Boolean(chat.error) && (chat.state !== "failed" || Boolean(chat.onReload));
    const modelsFix = chat.noModel ? "Add a model" : chat.modelsAction ? "Open Models" : null;

    return (
        <SafeAreaView edges={["bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <ChatLook podId={podId} timeOf={chat.timeOf}>
            <MessageMenu>
                <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingTop: insets.top + 4, paddingBottom: 8, paddingLeft: 4, paddingRight: 8, borderBottomWidth: 1, borderBottomColor: color.line, backgroundColor: identity.wash }}>
                        <Press label={`Back to ${name}`} onPress={() => (router.canGoBack() ? router.back() : router.replace(routes.lemma(podId) as never))} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                            <Icon name="back" size={22} width={1.9} />
                        </Press>
                        {lemma ? (
                            <View accessibilityLabel={working ? `${name} is working` : undefined} style={{ marginHorizontal: 4 }}>
                                <BreathingRing running={working} size={30}>
                                    <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={30} />
                                </BreathingRing>
                            </View>
                        ) : null}
                        <Press role="link" label={`${title}, with ${name}`} onPress={() => router.push(routes.lemma(podId) as never)} style={{ flex: 1, minWidth: 0, paddingLeft: 4 }}>
                            <Text size={16} weight="medium" numberOfLines={1}>{title}</Text>
                            <Text size={12.5} tone={color.ink2}>{lemma?.name ?? " "}</Text>
                        </Press>
                        <Press label="More: rename, archive, add people" onPress={() => setMenu(true)} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                            <Icon name="more" size={20} />
                        </Press>
                    </View>

                    <View style={{ flex: 1 }}>
                        <ScrollView
                            ref={scroller}
                            onScroll={onScroll}
                            onScrollBeginDrag={mark(dragging, true)}
                            onScrollEndDrag={mark(dragging, false)}
                            onMomentumScrollBegin={mark(flinging, true)}
                            onMomentumScrollEnd={mark(flinging, false)}
                            scrollEventThrottle={64}
                            maintainVisibleContentPosition={holding ? { minIndexForVisible: 1 } : undefined}
                            onContentSizeChange={() => { if (following.current) scroller.current?.scrollToEnd({ animated: false }); }}
                            contentContainerStyle={{ paddingTop: 14, paddingHorizontal: 16, paddingBottom: 16, gap: 18 }}
                            keyboardShouldPersistTaps="handled"
                        >
                            {chat.hasMore ? (
                                <Press label={chat.loadingOlder ? "Loading earlier messages" : "Earlier messages"} disabled={chat.loadingOlder} onPress={earlier} style={{ alignSelf: "center", minHeight: 34, paddingHorizontal: 14, justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: color.line2 }}>
                                    <Text size={13} tone={color.ink2}>{chat.loadingOlder ? "Loading earlier…" : "↑ Earlier"}</Text>
                                </Press>
                            ) : <View />}
                            {chat.loading && chat.turns.length === 0 ? <Loading label="Reading the chat" /> : null}
                            {!chat.loading && chat.turns.length === 0 && !inFlight && lemma && !chat.error ? (
                                <View style={{ paddingVertical: 24, alignItems: "center", gap: 10 }}>
                                    <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={44} />
                                    <Text size={15} tone={color.ink2}>{id === NEW_CHAT ? `Ask ${lemma.name} anything, or say what to do.` : "This chat is empty. Send a message to start it."}</Text>
                                </View>
                            ) : null}
                            {lemma ? chat.turns.map((turn, index) => (
                                <TurnRow
                                    key={turn.id}
                                    turn={turn}
                                    streaming={index === mergeInto ? inFlight : null}
                                    lemma={lemma}
                                    conversationId={chat.conversationId}
                                    outsiders={chat.reply.thread?.outsiders ? lemma.name : undefined}
                                    dockedId={dockedId}
                                    runEnded={chat.runEnded}
                                    resumesOnAnswer={chat.resumesOnAnswer}
                                    onResolve={chat.resolve}
                                    onRetrySend={chat.retrySend}
                                    onEditSend={chat.editSend}
                                    onCompose={onCompose}
                                />
                            )) : null}
                            {lemma && inFlight && mergeInto < 0 ? <LiveReply lemma={lemma} streaming={inFlight} podId={podId} conversationId={chat.conversationId} /> : null}

                            {failedLine ? (
                                <View accessibilityRole="alert" style={{ gap: 10, padding: 14, borderRadius: 14, backgroundColor: color.amberWash }}>
                                    <Text size={14.5} tone={color.amber} style={{ lineHeight: 20 }}>{chat.noModel ? noModelSentence(name) : failure.text}</Text>
                                    <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                                        {chat.onRetry && !chat.noModel ? <Button title="Try again" height={36} size={14} onPress={chat.onRetry} /> : null}
                                        {modelsFix ? <Button title={modelsFix} kind="outline" height={36} size={14} onPress={() => router.push(adminRoutes.section("models") as never)} /> : null}
                                        {!chat.noModel && failure.codingAgents ? <Button title="Coding agents settings" kind="outline" height={36} size={14} onPress={() => router.push(adminRoutes.section("models") as never)} /> : null}
                                    </View>
                                </View>
                            ) : null}
                            {errorLine ? (
                                <View accessibilityRole="alert" style={{ gap: 10, padding: 14, borderRadius: 14, backgroundColor: color.amberWash }}>
                                    <Text size={14.5} tone={color.amber} style={{ lineHeight: 20 }}>{chat.noModel ? noModelSentence(name) : chat.error}</Text>
                                    {modelsFix || chat.onReload ? (
                                        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                                            {chat.onReload ? <Button title={chat.reloadLabel} height={36} size={14} disabled={chat.loading} onPress={chat.onReload} /> : null}
                                            {modelsFix ? <Button title={modelsFix} kind="outline" height={36} size={14} onPress={() => router.push(adminRoutes.section("models") as never)} /> : null}
                                        </View>
                                    ) : null}
                                </View>
                            ) : null}
                        </ScrollView>
                        {away ? (
                            <Press label="Jump to the newest message" onPress={() => toNewest(true)} style={{ position: "absolute", right: 16, bottom: 12, width: 40, height: 40, borderRadius: 20, backgroundColor: color.paper, borderWidth: 1, borderColor: color.line2, alignItems: "center", justifyContent: "center", elevation: 2 }}>
                                <Glyph name="arrow-down" size={18} tone={color.ink} />
                            </Press>
                        ) : null}
                    </View>

                    {lemma ? (
                        <InteractionDock
                            interaction={chat.waitingOn}
                            name={lemma.name}
                            onResolve={chat.resolve}
                            runEnded={chat.runEnded}
                            resumesOnAnswer={chat.resumesOnAnswer}
                            signIn={chat.signingIn}
                            podId={podId}
                            conversationId={chat.conversationId}
                            crowded={crowded}
                            answerWith={chat.answerWith}
                        />
                    ) : null}
                    <Composer
                        placeholder={placeholder}
                        note={chat.note}
                        busy={chat.busy}
                        canStop={chat.canStop}
                        fill={fill}
                        onFilled={filled}
                        onSend={onSend}
                        onStop={chat.stop}
                        attachments={chat.attachments}
                        onAttach={chat.attach}
                        onRemoveAttachment={chat.unattach}
                        queued={chat.queued}
                        queuedNote={chat.queuedNote}
                        onWithdraw={chat.withdraw}
                        choices={choices}
                        choice={chat.reply.mode}
                        onChoose={chat.reply.choose}
                        onTall={setCrowded}
                        draftKey={draftKeyFor(podId, chat.conversationId)}
                        model={model}
                        onCall={call}
                        tone={identity.ink}
                        onTone={identity.onInk}
                    />
                </KeyboardAvoidingView>
            </MessageMenu>
            </ChatLook>

            <Sheet open={menu} onClose={() => setMenu(false)} title={title}>
                {[
                    { label: "Rename", glyph: "edit" as const, run: () => setRenaming(chat.title ?? ""), live: Boolean(chat.conversationId) },
                    { label: "Archive", glyph: "trash" as const, run: archive, live: Boolean(chat.conversationId) },
                    { label: "Add people, on the web", glyph: "external" as const, run: () => router.push(routes.web(podId, chat.conversationId ? workspace.conversation(podId, chat.conversationId) : workspace.pod(podId), title) as never), live: true },
                ].filter((one) => one.live).map((one) => (
                    <Press key={one.label} label={one.label} onPress={() => { setMenu(false); setTimeout(one.run, 200); }} style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: 52, paddingHorizontal: 20 }}>
                        <Glyph name={one.glyph} size={19} tone={color.ink} />
                        <Text size={16}>{one.label}</Text>
                    </Press>
                ))}
                {!chat.conversationId ? <Text size={13} tone={color.ink3} style={{ paddingHorizontal: 20, paddingVertical: 8 }}>A new chat can be renamed once it has started.</Text> : null}
            </Sheet>

            <Sheet open={renaming !== null} onClose={() => setRenaming(null)} title="Rename this chat">
                <View style={{ paddingHorizontal: 20, paddingTop: 4, gap: 12 }}>
                    <TextInput
                        accessibilityLabel="The chat's name"
                        autoFocus
                        value={renaming ?? ""}
                        onChangeText={setRenaming}
                        placeholder="Leave empty to let it name itself"
                        placeholderTextColor={color.ink3}
                        maxLength={200}
                        returnKeyType="done"
                        onSubmitEditing={saveName}
                        style={{ fontFamily: font.regular, fontSize: 16, color: color.ink, borderWidth: 1, borderColor: color.line2, borderRadius: 12, paddingHorizontal: 12, paddingVertical: 10 }}
                    />
                    <Button title="Save" onPress={saveName} />
                </View>
            </Sheet>
        </SafeAreaView>
    );
}
