import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { KeyboardAvoidingView, ScrollView, View, type LayoutChangeEvent, type NativeScrollEvent, type NativeSyntheticEvent } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { SafeAreaView } from "react-native-safe-area-context";
import { useCacheKey, useLemma } from "../../data/hooks";
import { Glyph } from "../../thread/glyphs";
import { Loading } from "../../ui/chrome";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { LineActions } from "./actions";
import { ChatMark, FaceStack, Trouble, Typing } from "./bits";
import { ChatComposer } from "./composer";
import { chatKeys, keepChannel, refreshLists, useAbilities, useAgents, useAppShown, useChannel, useChannelLive, useInbox, useLines, useMyId, useOnline, usePeople, useChatsSource } from "./hooks";
import { LineView, type LineContext } from "./line";
import { contactStrip, glyphOf, introOf, newMessages, placeholderFor, seenOnScreen, subtitleOf, whoAnswers, type LineBox } from "./model";
import { roomFaces } from "./room";
import { chatsRoutes } from "./routes";
import { channelAbilities } from "@web/channels/abilities";
import { ThreadDrawer, type ThreadDrawerHandle } from "./thread-view";
import { typingIn, upsertTimeline } from "@web/channels/live";
import { channelReaders, mentionables } from "@web/channels/mentions";
import { channelTitle, lineDays, newestSeq, personName, type ChannelDetail, type Line, type LinePage } from "@web/channels/model";
import { problemOf } from "@web/channels/problem";
import { readTarget, unseenThreadMention } from "@web/channels/read-state";
import { answeredOutOfPlace, continues, firstUnread, newSince, unreadBeforeHeld } from "@web/channels/timeline-model";
import { STATUS_LABELS } from "@web/channels/views";

/** How near the bottom still counts as reading the newest. */
const FOLLOW_PX = 80;
/** How near the top asks for the page before. */
const EARLIER_PX = 120;
/** How many older pages are read, at most, to reach a message asked for. */
const FOCUS_PAGES = 8;

export function Header({ podId, title, subtitle, glyph, onTitle, right, lead }: {
    podId: string;
    title: string;
    subtitle: string;
    glyph: Parameters<typeof ChatMark>[0]["glyph"] | null;
    onTitle?: () => void;
    right?: React.ReactNode;
    /** Before the title: who is in the room, as faces. The glyph then
     *  marks the second line instead. */
    lead?: React.ReactNode;
}) {
    const router = useRouter();
    /* A plain shared chat needs no mark beside faces; a private or
       mirrored one says so on its second line. */
    const marked = glyph && glyph.kind !== "hash" && glyph.kind !== "ring";
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 4, paddingTop: 4, paddingBottom: 8, paddingLeft: 4, paddingRight: 6, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <Press label="Back" onPress={() => (router.canGoBack() ? router.back() : router.replace(chatsRoutes.chats(podId) as never))} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                <Icon name="back" size={22} width={1.9} />
            </Press>
            {lead}
            <Press label={onTitle ? "About " + title : title} disabled={!onTitle} onPress={onTitle} style={{ flex: 1, minWidth: 0, flexDirection: "row", alignItems: "center", gap: 6, paddingLeft: lead ? 6 : 0 }}>
                {glyph && !lead ? <ChatMark glyph={glyph} size={17} width={26} /> : null}
                <View style={{ flex: 1, minWidth: 0 }}>
                    <Text size={16} weight="medium" numberOfLines={1}>{title}</Text>
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 2 }}>
                        {lead && marked ? <View style={{ marginLeft: -6 }}><ChatMark glyph={glyph} size={12} width={18} /></View> : null}
                        <Text size={12.5} tone={color.ink2} numberOfLines={1} style={{ flexShrink: 1 }}>{subtitle}</Text>
                    </View>
                </View>
            </Press>
            {right}
        </View>
    );
}

/** A chat that lives on a channel -- the reader's own with the Lemma, a group
 *  made here, a group mirrored from a platform, a contact's -- full screen:
 *  everything said in it, oldest at the top, and the box to say something.
 *  Kept live by the Lemma's socket while open, and marked read as far as the
 *  newest message the reader has seen (the web's `ChannelPage`). */
export function SharedChatScreen({ podId, channelId, focusLine, focusThread }: { podId: string; channelId: string; focusLine: string | null; focusThread: string | null }) {
    const detail = useChannel(podId, channelId);
    const lemma = useLemma(podId);
    const channel = detail.data ?? null;
    if (!channel) {
        return (
            <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
                <Header podId={podId} title={detail.isPending ? " " : "Chat"} subtitle={lemma?.name ?? " "} glyph={null} />
                {detail.isPending ? <Loading label="Reading the chat" /> : (
                    <View style={{ padding: 20, gap: 12 }}>
                        <Text size={16} style={{ lineHeight: 22 }}>{problemOf(detail.error, "Couldn’t load this chat.")}</Text>
                        <View style={{ flexDirection: "row" }}><Button title="Try again" kind="outline" onPress={() => void detail.refetch()} /></View>
                    </View>
                )}
            </SafeAreaView>
        );
    }
    return <ChannelView key={channel.id} podId={podId} channel={channel} focusLine={focusLine} focusThread={focusThread} />;
}

/** A contact's chat: where it stands, and the one move that changes it. */
function ContactStrip({ podId, channel, me, lemmaName, mayAct }: { podId: string; channel: ChannelDetail; me: string | null; lemmaName: string; mayAct: boolean }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const move = useMutation({
        mutationFn: (assignee: "me" | null) => source.update(podId, channel.id, { assignee }),
        onSuccess: (saved) => keepChannel(cache, key, podId, saved),
    });
    const strip = contactStrip(channel, me, lemmaName, mayAct);
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: color.line, backgroundColor: strip.asks ? color.violetWash : "transparent" }}>
            <Text size={13.5} tone={strip.asks ? color.violetInk : color.ink2} style={{ flex: 1, lineHeight: 19 }}>{strip.state}</Text>
            {strip.act ? <Button title={strip.act.label} kind={strip.act.to ? "primary" : "outline"} height={34} size={13.5} busy={move.isPending} onPress={() => move.mutate(strip.act!.to)} /> : null}
            {!mayAct && channel.status ? <Text size={12.5} tone={color.ink3}>{STATUS_LABELS[channel.status]}</Text> : null}
            {move.isError ? <Trouble>Couldn’t change it.</Trouble> : null}
        </View>
    );
}

function ChannelView({ podId, channel, focusLine, focusThread }: { podId: string; channel: ChannelDetail; focusLine: string | null; focusThread: string | null }) {
    const router = useRouter();
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const me = useMyId();
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "The Lemma";
    const timeline = useLines(podId, channel.id);
    const live = useChannelLive(podId, channel, me);
    const abilities = useAbilities(podId);
    const may = channelAbilities(abilities, channel, lemmaName);
    const agents = useAgents(podId);
    const people = usePeople(podId);
    const online = useOnline(podId);
    const inbox = useInbox(podId);
    const shown = useAppShown();
    const lines = timeline.lines;
    const frontId = agents.data?.find((agent) => agent.front)?.id ?? null;
    const [held, setHeld] = useState<Line | null>(null);
    const [unreadFrom] = useState(channel.me.readSeq);

    const join = useMutation({
        mutationFn: () => source.join(podId, channel.id),
        onSuccess: (saved) => keepChannel(cache, key, podId, saved),
    });

    const named = useMemo(() => mentionables(
        (agents.data ?? []).map((agent) => ({ id: agent.id, label: agent.label })),
        channel.members.map((one) => one.person),
        me,
        channelReaders(channel, (people.data ?? []).map((one) => ({ userId: one.userId, name: one.name }))),
    ), [agents.data, channel, people.data, me]);
    const agentName = useCallback((agentId: string | null) => {
        const member = channel.members.find((one) => one.person.kind === "AGENT" && one.person.id === agentId);
        return member?.person.name ?? agents.data?.find((agent) => agent.id === agentId)?.label ?? lemmaName;
    }, [channel.members, agents.data, lemmaName]);

    /* A thread opens as a drawer over this chat: from a message, or asked
       for by the bell, search or a link (at one of its replies). */
    const drawer = useRef<ThreadDrawerHandle>(null);
    const [askedThread] = useState(() => (focusThread ? { root: focusThread, line: focusLine } : null));
    const openThread = useCallback((line: Line) => drawer.current?.open(line.id), []);

    /* ── scrolling, seeing, following ─────────────────────────────── */
    const scroller = useRef<ScrollView>(null);
    const following = useRef(!focusLine || Boolean(focusThread));
    const dragging = useRef(false);
    const flinging = useRef(false);
    const boxes = useRef(new Map<string, LineBox>());
    const view = useRef({ top: 0, height: 0, content: 0 });
    const [progress, setProgress] = useState({ seenMax: 0, atEnd: true, unreadAbove: false });
    const [away, setAway] = useState(false);
    const [leftAt, setLeftAt] = useState<number | null>(null);
    const [holding, setHolding] = useState(false);
    const [highlight, setHighlight] = useState<string | null>(focusThread ? null : focusLine);
    const placed = useRef<string | null>(null);
    const focusPages = useRef(0);
    const [jumping, setJumping] = useState(0);
    const unread = firstUnread(lines, unreadFrom, me);
    const unreadEarlier = unreadBeforeHeld(lines, unreadFrom, Boolean(timeline.hasNextPage));
    const byId = useMemo(() => new Map(lines.map((line) => [line.id, line])), [lines]);
    const days = useMemo(() => lineDays(lines), [lines]);
    const newest = lines.at(-1)?.seq ?? 0;
    const fresh = leftAt === null ? 0 : newSince(lines, leftAt, me);

    const measure = useCallback(() => {
        const { top, height, content } = view.current;
        const atEnd = content - top - height < FOLLOW_PX;
        const seenMax = seenOnScreen(boxes.current.values(), top, height);
        const mark = unread ? boxes.current.get(unread) : undefined;
        const unreadAbove = unreadEarlier || Boolean(mark && mark.y < top);
        setProgress((was) => (was.seenMax === seenMax && was.atEnd === atEnd && was.unreadAbove === unreadAbove ? was : { seenMax, atEnd, unreadAbove }));
    }, [unread, unreadEarlier]);

    const earlier = () => {
        if (!timeline.hasNextPage || timeline.isFetchingNextPage) return;
        setHolding(true);
        void timeline.fetchNextPage().finally(() => setTimeout(() => setHolding(false), 600));
    };
    const onScroll = (event: NativeSyntheticEvent<NativeScrollEvent>) => {
        const { contentOffset, contentSize, layoutMeasurement } = event.nativeEvent;
        view.current = { top: contentOffset.y, height: layoutMeasurement.height, content: contentSize.height };
        measure();
        if (!dragging.current && !flinging.current) return;
        /* The reader moved: a message asked for is let go. */
        if (highlight) placed.current = highlight;
        const fromBottom = contentSize.height - layoutMeasurement.height - contentOffset.y;
        following.current = fromBottom < FOLLOW_PX;
        setAway(!following.current && contentSize.height > layoutMeasurement.height);
        if (following.current) setLeftAt(null);
        else setLeftAt((was) => was ?? newest);
        if (contentOffset.y < EARLIER_PX) earlier();
    };
    const mark = (flag: { current: boolean }, on: boolean) => () => { flag.current = on; };
    const toNewest = (animated: boolean) => {
        following.current = true;
        setAway(false);
        setLeftAt(null);
        scroller.current?.scrollToEnd({ animated });
    };
    /* Opened at one message: there, as soon as it is laid out, and only
       once; it is drawn out for a moment, then let go. */
    const wanted = useRef(highlight);
    wanted.current = highlight;
    const [placedAt, setPlacedAt] = useState<string | null>(null);
    const laidOut = (line: Line) => (event: LayoutChangeEvent) => {
        const { y, height } = event.nativeEvent.layout;
        boxes.current.set(line.id, { seq: line.seq, y, height });
        measure();
        if (line.id === wanted.current && placed.current !== line.id) {
            placed.current = line.id;
            following.current = false;
            setTimeout(() => scroller.current?.scrollTo({ y: Math.max(0, y - 120), animated: false }), 60);
            setPlacedAt(line.id);
        }
    };
    useEffect(() => {
        if (!placedAt) return;
        const timer = setTimeout(() => setHighlight(null), 2600);
        return () => clearTimeout(timer);
    }, [placedAt]);

    /* Older than the pages held: read back a few pages for it. */
    const { hasNextPage, isFetchingNextPage, isPending: firstRead, fetchNextPage } = timeline;
    useEffect(() => {
        if (!highlight || firstRead || byId.has(highlight)) return;
        if (hasNextPage && !isFetchingNextPage && focusPages.current < FOCUS_PAGES) {
            focusPages.current += 1;
            void fetchNextPage();
        }
    }, [highlight, byId, firstRead, hasNextPage, isFetchingNextPage, fetchNextPage]);

    /* "Jump to unread": read back until the first unread message is held. */
    useEffect(() => {
        if (!jumping) return;
        const box = unread ? boxes.current.get(unread) : undefined;
        if (box && !unreadEarlier) {
            following.current = false;
            scroller.current?.scrollTo({ y: Math.max(0, box.y - 60), animated: true });
            setJumping(0);
            return;
        }
        if (timeline.hasNextPage && !timeline.isFetchingNextPage && jumping <= FOCUS_PAGES) {
            setJumping((count) => count + 1);
            earlier();
        } else if (!timeline.hasNextPage || jumping > FOCUS_PAGES) {
            setJumping(0);
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [jumping, unread, unreadEarlier, lines.length]);

    /* Read as far as the newest message seen: not while the app is behind
       another, and not past a reply naming the reader in a thread they have
       not opened. */
    const heldBack = unseenThreadMention(inbox.data?.mentioned ?? [], channel.id, null);
    const marked = useRef(0);
    const { markRead } = live;
    useEffect(() => {
        if (!shown || timeline.isPending || inbox.isPending) return;
        const target = readTarget({
            seenMax: progress.seenMax,
            atEnd: progress.atEnd,
            newestShown: newestSeq(lines) ?? 0,
            lastSeq: channel.lastSeq,
            readSeq: channel.me.readSeq,
            marked: marked.current,
            heldBack,
        });
        if (target === null) return;
        const was = marked.current;
        marked.current = target;
        markRead(target).catch(() => { marked.current = was; });
    }, [shown, progress, timeline.isPending, lines, channel.lastSeq, channel.me.readSeq, markRead, heldBack, inbox.isPending]);

    const posted = (line: Line) => {
        cache.setQueryData<InfiniteData<LinePage>>(chatKeys.lines(key, podId, channel.id), (was) => (was ? { ...was, pages: upsertTimeline(was.pages, line).pages } : was));
        refreshLists(cache, key, podId);
        setHighlight(null);
        setTimeout(() => toNewest(true), 50);
    };

    const title = channelTitle(channel, lemmaName);
    const typingNames = typingIn(live.typing, null).map((one) => personName(one.who, me));
    const faces = roomFaces(channel.members, me, frontId ? { kind: "AGENT", id: frontId, name: lemmaName } : null);
    const lemmaFace = lemma ? { id: lemma.id, iconUrl: lemma.iconUrl } : null;
    const context: LineContext = {
        podId, channel, me, frontId, agentName,
        lemma: lemma ? { id: lemma.id, name: lemma.name, iconUrl: lemma.iconUrl } : null,
        mayWrite: may.write,
        onThread: openThread,
        onHold: setHeld,
        onQuote: (lineId) => {
            const box = boxes.current.get(lineId);
            if (box) { following.current = false; scroller.current?.scrollTo({ y: Math.max(0, box.y - 80), animated: true }); }
        },
    };
    const notJoined = channel.kind === "GROUP" && !channel.me.joined;
    const disabledReason = channel.archived ? "This chat is archived. Bring it back from its settings to write in it again."
        : notJoined ? "Join this chat to write in it."
        : may.writeRefused;

    return (
        <View style={{ flex: 1, backgroundColor: color.paper }}>
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <Header
                    podId={podId}
                    title={title}
                    subtitle={subtitleOf(channel, lemmaName, me)}
                    glyph={glyphOf(channel)}
                    onTitle={() => router.push(chatsRoutes.details(podId, channel.id, "about") as never)}
                    lead={faces.shown.length ? (
                        <Press label={"Who is in it: " + faces.total} onPress={() => router.push(chatsRoutes.details(podId, channel.id, "people") as never)} style={{ height: TOUCH, justifyContent: "center" }}>
                            <FaceStack faces={faces.shown} more={faces.more} lemma={lemmaFace} frontId={frontId} online={online} size={28} />
                        </Press>
                    ) : null}
                    right={(
                        <>
                            <Press label="Details, people and settings" onPress={() => router.push(chatsRoutes.details(podId, channel.id) as never)} style={{ width: TOUCH - 4, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                                <Icon name="more" size={20} />
                            </Press>
                        </>
                    )}
                />
                {channel.kind === "CONTACT" ? <ContactStrip podId={podId} channel={channel} me={me} lemmaName={lemmaName} mayAct={may.contactOps} /> : null}
                {notJoined ? (
                    <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingHorizontal: 16, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: color.line }}>
                        <Text size={13.5} tone={color.ink2} style={{ flex: 1 }}>You are not in it. Anyone in {lemmaName} can join.</Text>
                        <Button title="Join" height={34} size={13.5} busy={join.isPending} onPress={() => join.mutate()} />
                    </View>
                ) : null}

                <View style={{ flex: 1 }}>
                    <ScrollView
                        ref={scroller}
                        onScroll={onScroll}
                        onScrollBeginDrag={mark(dragging, true)}
                        onScrollEndDrag={mark(dragging, false)}
                        onMomentumScrollBegin={mark(flinging, true)}
                        onMomentumScrollEnd={mark(flinging, false)}
                        onLayout={(event) => { view.current = { ...view.current, height: event.nativeEvent.layout.height }; measure(); }}
                        scrollEventThrottle={64}
                        maintainVisibleContentPosition={holding ? { minIndexForVisible: 1 } : undefined}
                        onContentSizeChange={(_width, height) => {
                            view.current = { ...view.current, content: height };
                            /* A scroll the screen makes itself reports no
                               position in time to be read: where it lands is
                               known, so what is seen is measured from there. */
                            if (following.current) {
                                scroller.current?.scrollToEnd({ animated: false });
                                view.current = { ...view.current, top: Math.max(0, height - view.current.height) };
                            }
                            measure();
                        }}
                        contentContainerStyle={{ paddingTop: 10, paddingBottom: 12 }}
                        keyboardShouldPersistTaps="handled"
                    >
                        {timeline.hasNextPage ? (
                            <Press label={timeline.isFetchingNextPage ? "Loading earlier messages" : "Earlier messages"} disabled={timeline.isFetchingNextPage} onPress={earlier} style={{ alignSelf: "center", minHeight: 34, paddingHorizontal: 14, justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: color.line2, marginBottom: 8 }}>
                                <Text size={13} tone={color.ink2}>{timeline.isFetchingNextPage ? "Loading earlier…" : "↑ Earlier"}</Text>
                            </Press>
                        ) : <View />}
                        {timeline.isPending ? <Loading label="Reading the chat" /> : null}
                        {timeline.isError && !timeline.data ? (
                            <View style={{ padding: 16, gap: 8 }}>
                                <Trouble>Couldn’t load the messages.</Trouble>
                                <View style={{ flexDirection: "row" }}><Button title="Try again" kind="outline" height={36} size={14} onPress={() => void timeline.refetch()} /></View>
                            </View>
                        ) : null}
                        {!timeline.isPending && !timeline.hasNextPage ? (
                            <View style={{ paddingHorizontal: 20, paddingTop: 8, paddingBottom: 6, gap: 8 }}>
                                <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{introOf(channel, lemmaName, me)}</Text>
                                {channel.kind !== "GROUP" ? <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{whoAnswers(channel, lemmaName, me)}</Text> : null}
                                {channel.kind === "GROUP" && may.manage ? (
                                    <Press label="Add people" onPress={() => router.push(chatsRoutes.details(podId, channel.id, "people") as never)} style={{ flexDirection: "row", alignItems: "center", gap: 6, minHeight: 36, alignSelf: "flex-start" }}>
                                        <Icon name="people" size={16} tone={color.ink} />
                                        <Text size={14}>Add people</Text>
                                    </Press>
                                ) : null}
                            </View>
                        ) : null}
                        {days.map((day) => (
                            <Fragment key={day.key}>
                                <View accessibilityRole="header" style={{ alignItems: "center", paddingTop: 14, paddingBottom: 2 }}>
                                    <Text size={12} tone={color.ink3}>{day.label}</Text>
                                </View>
                                {day.lines.map((line, index) => (
                                    <Fragment key={line.id}>
                                        {line.id === unread ? (
                                            <View accessibilityLabel="New since you last read" style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 16, paddingTop: 8 }}>
                                                <View style={{ flex: 1, height: 1, backgroundColor: color.violet }} />
                                                <Text size={12} tone={color.violetInk}>New</Text>
                                            </View>
                                        ) : null}
                                        <LineView
                                            line={line}
                                            context={context}
                                            continued={line.id !== unread && continues(day.lines[index - 1], line)}
                                            quote={answeredOutOfPlace(line, day.lines[index - 1], byId)}
                                            answering={live.answering.filter((one) => one.messageId === line.id)}
                                            highlighted={highlight === line.id}
                                            onLayout={laidOut(line)}
                                        />
                                    </Fragment>
                                ))}
                            </Fragment>
                        ))}
                    </ScrollView>
                    {progress.unreadAbove ? (
                        <Press label={jumping ? "Reading back" : "Jump to unread"} disabled={jumping > 0} onPress={() => setJumping(1)} style={{ position: "absolute", top: 8, alignSelf: "center", minHeight: 32, paddingHorizontal: 14, borderRadius: 999, borderWidth: 1, borderColor: color.line2, backgroundColor: color.paper, justifyContent: "center", elevation: 2 }}>
                            <Text size={13} tone={color.ink}>{jumping ? "Reading back…" : "Jump to unread"}</Text>
                        </Press>
                    ) : null}
                    {away ? (
                        <Press label={fresh > 0 ? newMessages(fresh) + ". Go to the newest" : "Go to the newest message"} onPress={() => toNewest(true)} style={{ position: "absolute", right: 16, bottom: 12, minWidth: 40, height: 40, paddingHorizontal: fresh > 0 ? 14 : 0, borderRadius: 20, backgroundColor: fresh > 0 ? color.ink : color.paper, borderWidth: fresh > 0 ? 0 : 1, borderColor: color.line2, alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 6, elevation: 2 }}>
                            {fresh > 0 ? <Text size={13.5} tone={color.onInk}>{newMessages(fresh)}</Text> : null}
                            <Glyph name="arrow-down" size={17} tone={fresh > 0 ? color.onInk : color.ink} />
                        </Press>
                    ) : null}
                </View>

                <Typing names={typingNames} />
                {live.announced ? <Text accessibilityLiveRegion="polite" style={{ position: "absolute", width: 1, height: 1, opacity: 0 }}>{live.announced}</Text> : null}
                <ChatComposer
                    podId={podId}
                    channel={channel}
                    people={named}
                    placeholder={placeholderFor(channel, lemmaName)}
                    disabledReason={disabledReason}
                    onPosted={posted}
                    onTyping={() => live.typed(null)}
                />
            </KeyboardAvoidingView>
            <LineActions podId={podId} channel={channel} line={held} me={me} mayWrite={may.write && !channel.archived} inThread={false} people={named} onClose={() => setHeld(null)} onThread={openThread} />
        </SafeAreaView>
        <ThreadDrawer ref={drawer} podId={podId} channel={channel} initial={askedThread} />
        </View>
    );
}
