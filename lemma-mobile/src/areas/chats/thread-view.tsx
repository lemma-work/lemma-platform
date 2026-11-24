import { useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState, type Ref } from "react";
import { BackHandler, KeyboardAvoidingView, Pressable, ScrollView, StyleSheet, View, type LayoutChangeEvent } from "react-native";
import { Redirect } from "expo-router";
import { useQueryClient, type InfiniteData } from "@tanstack/react-query";
import { Gesture, GestureDetector } from "react-native-gesture-handler";
import Animated, { interpolate, runOnJS, useAnimatedStyle, useSharedValue, withSpring, withTiming } from "react-native-reanimated";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { useCacheKey, useLemma } from "../../data/hooks";
import { liveAvailable, podLive } from "../../live/pod-live";
import { Loading } from "../../ui/chrome";
import { haptics } from "../../ui/haptics";
import { Icon } from "../../ui/icons";
import { leave, spring } from "../../ui/motion";
import { Button, Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { LineActions } from "./actions";
import { Typing } from "./bits";
import { ChatComposer } from "./composer";
import { chatKeys, useAbilities, useAgents, useAppShown, useChannelLive, useInbox, useLines, useMyId, usePeople, useThread } from "./hooks";
import { LineView, type LineContext } from "./line";
import { snapAfter, snapOffset, type Snap } from "./room";
import { chatsRoutes } from "./routes";
import { channelAbilities } from "@web/channels/abilities";
import { typingIn, upsertThread } from "@web/channels/live";
import { channelReaders, mentionables } from "@web/channels/mentions";
import { channelTitle, personName, type ChannelDetail, type Line, type ThreadPage } from "@web/channels/model";
import { continues } from "@web/channels/timeline-model";

/** How many pages of a thread are read on, at most, to reach a reply asked
 *  for by the bell or search. */
const FOCUS_PAGES = 8;
/** The share of the screen a thread opens to. */
const HALF = 0.52;

/** A thread's own address (`/lemma/…/thread/…`), kept for links: it opens
 *  the chat with the thread drawn up over it. */
export function ThreadScreen({ podId, channelId, rootId, focusLine }: { podId: string; channelId: string; rootId: string; focusLine: string | null }) {
    return <Redirect href={chatsRoutes.shared(podId, channelId, { thread: rootId, line: focusLine }) as never} />;
}

/** One thread of a chat, as a drawer over the room (the web's
 *  `ThreadPanel`): it springs up to half the screen and is dragged by its
 *  head to full, or down, flicked, or tapped outside to go. The message it
 *  hangs off, its replies a page at a time, and "Reply in thread" at its
 *  foot. Who is typing a reply is said here, and only here. */
export interface ThreadDrawerHandle {
    /** Draw up the thread hanging off `root`, at the reply `line` if given. */
    open(root: string, line?: string | null): void;
}

/* Which thread is up is held here, not by the chat, so opening one draws
   only the drawer and never the whole room under it. */
export function ThreadDrawer({ podId, channel, initial, ref }: { podId: string; channel: ChannelDetail; initial: { root: string; line: string | null } | null; ref: Ref<ThreadDrawerHandle> }) {
    const insets = useSafeAreaInsets();
    const [thread, setThread] = useState(initial);
    useImperativeHandle(ref, () => ({ open: (root, line = null) => setThread({ root, line }) }), []);
    const onClose = useCallback(() => setThread(null), []);
    const rootId = thread?.root ?? null;
    const focusLine = thread?.line ?? null;
    const [room, setRoom] = useState(0);
    const [space, setSpace] = useState(0);
    const [shown, setShown] = useState<string | null>(rootId);
    /* The thread's messages are drawn a frame after the sheet starts up. */
    const [ready, setReady] = useState(Boolean(rootId));
    const [snap, setSnap] = useState<Snap>(focusLine ? "full" : "half");
    const open = rootId !== null;
    if (rootId && rootId !== shown) setShown(rootId);

    /* Its height: up to a little under the status bar, and no more than is
       left above the keyboard. */
    const full = Math.max(0, Math.min(room - insets.top - 10, space || room));
    const half = Math.min(full, Math.round(room * HALF));
    const offset = useSharedValue(10_000);
    const startedAt = useSharedValue(0);
    const halfAt = useSharedValue(0);
    const closedAt = useSharedValue(10_000);

    const close = onClose;
    const gone = useCallback(() => { setShown(null); setReady(false); }, []);
    useEffect(() => {
        halfAt.value = snapOffset("half", full, half);
        closedAt.value = full;
        if (!full) return;
        if (open) {
            /* From below the screen, wherever it was left. */
            if (offset.value > full) offset.value = full;
            offset.value = withSpring(snapOffset(snap, full, half), spring);
        }
        else offset.value = withTiming(full, leave, (finished) => { if (finished) runOnJS(gone)(); });
    }, [open, snap, full, half, offset, halfAt, closedAt]);
    useEffect(() => {
        if (!open) return;
        haptics.firm();
        setSnap(focusLine ? "full" : "half");
        const frame = requestAnimationFrame(() => setReady(true));
        return () => cancelAnimationFrame(frame);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [open, rootId]);
    useEffect(() => {
        if (!open) return;
        const back = BackHandler.addEventListener("hardwareBackPress", () => { close(); return true; });
        return () => back.remove();
    }, [open, close]);

    const settleAt = (to: Snap) => {
        if (to === "closed") { close(); return; }
        haptics.tick();
        setSnap(to);
        offset.value = withSpring(snapOffset(to, full, half), spring);
    };
    const drag = Gesture.Pan()
        .onBegin(() => { startedAt.value = offset.value; })
        .onUpdate((event) => { offset.value = Math.min(closedAt.value, Math.max(0, startedAt.value + event.translationY)); })
        .onEnd((event) => { runOnJS(settleAt)(snapAfter(offset.value, event.velocityY, halfAt.value, closedAt.value)); });

    /* Between half and full the sheet grows, so its foot -- the reply box --
       stays on screen; below half it slides away whole. */
    const sheet = useAnimatedStyle(() => ({
        height: closedAt.value - Math.min(offset.value, halfAt.value),
        transform: [{ translateY: Math.max(0, offset.value - halfAt.value) }],
    }));
    const scrim = useAnimatedStyle(() => ({ opacity: interpolate(offset.value, [halfAt.value, closedAt.value], [1, 0], "clamp") }));

    return (
        <View pointerEvents={open ? "box-none" : "none"} style={StyleSheet.absoluteFill} onLayout={(event) => setRoom(event.nativeEvent.layout.height)}>
            {shown ? (
                <>
                    <Animated.View style={[StyleSheet.absoluteFill, { backgroundColor: color.scrim }, scrim]}>
                        <Pressable accessibilityLabel="Close the thread" style={{ flex: 1 }} onPress={close} />
                    </Animated.View>
                    <KeyboardAvoidingView behavior="padding" style={StyleSheet.absoluteFill} pointerEvents="box-none">
                        <View style={{ flex: 1, justifyContent: "flex-end" }} pointerEvents="box-none" onLayout={(event) => setSpace(event.nativeEvent.layout.height)}>
                            {full > 0 ? (
                                <Animated.View style={[{ backgroundColor: color.paper, borderTopLeftRadius: 24, borderTopRightRadius: 24, paddingBottom: insets.bottom, overflow: "hidden" }, sheet]}>
                                    <GestureDetector gesture={drag}>
                                        <View collapsable={false}>
                                            <View style={{ alignSelf: "center", width: 36, height: 4, borderRadius: 2, backgroundColor: color.line2, marginTop: 10, marginBottom: 4 }} />
                                            <ThreadHead podId={podId} channel={channel} snap={snap} onSnap={settleAt} onClose={close} />
                                        </View>
                                    </GestureDetector>
                                    {ready ? <ThreadBody key={shown} podId={podId} channel={channel} rootId={shown} focusLine={focusLine} /> : <View style={{ flex: 1 }} />}
                                </Animated.View>
                            ) : null}
                        </View>
                    </KeyboardAvoidingView>
                </>
            ) : null}
        </View>
    );
}

function ThreadHead({ podId, channel, snap, onSnap, onClose }: { podId: string; channel: ChannelDetail; snap: Snap; onSnap: (to: Snap) => void; onClose: () => void }) {
    const lemma = useLemma(podId);
    return (
        <View style={{ flexDirection: "row", alignItems: "center", paddingLeft: 20, paddingRight: 6, paddingBottom: 6, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <Press label={snap === "full" ? "Thread. Make it smaller" : "Thread. Make it full screen"} onPress={() => onSnap(snap === "full" ? "half" : "full")} style={{ flex: 1, minWidth: 0, minHeight: TOUCH, justifyContent: "center" }}>
                <Text accessibilityRole="header" size={16} weight="medium">Thread</Text>
                <Text size={12.5} tone={color.ink2} numberOfLines={1}>{channelTitle(channel, lemma?.name ?? "The Lemma")}</Text>
            </Press>
            <Press label="Close the thread" onPress={onClose} style={{ width: TOUCH, height: TOUCH, alignItems: "center", justifyContent: "center" }}>
                <Icon name="close" size={16} width={2} tone={color.ink2} />
            </Press>
        </View>
    );
}

function ThreadBody({ podId, channel, rootId, focusLine }: { podId: string; channel: ChannelDetail; rootId: string; focusLine: string | null }) {
    const key = useCacheKey();
    const cache = useQueryClient();
    const me = useMyId();
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "The Lemma";
    const timeline = useLines(podId, channel.id);
    const thread = useThread(podId, channel.id, rootId);
    const live = useChannelLive(podId, channel, me);
    const may = channelAbilities(useAbilities(podId), channel, lemmaName);
    const agents = useAgents(podId);
    const people = usePeople(podId);
    const inbox = useInbox(podId);
    const shown = useAppShown();
    const frontId = agents.data?.find((agent) => agent.front)?.id ?? null;
    const [held, setHeld] = useState<Line | null>(null);
    /* The first page of a thread carries its first message: kept apart. */
    const root = timeline.lines.find((line) => line.id === rootId) ?? thread.replies.find((line) => line.id === rootId) ?? null;
    const replies = thread.replies.filter((line) => line.id !== rootId);
    const count = Math.max(root?.replyCount ?? 0, replies.length);

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

    /* A reply naming the reader is read in its thread: while it is open,
       the thread is read to the chat's newest message. */
    const namesMe = (inbox.data?.mentioned ?? []).some((one) => one.channelId === channel.id && one.threadRootId === rootId);
    const { markRead } = live;
    useEffect(() => {
        if (!shown || !namesMe) return;
        markRead(channel.lastSeq, rootId).catch(() => { /* read again when the inbox next says so */ });
    }, [shown, namesMe, channel.lastSeq, rootId, markRead]);

    /* Opened at one reply: read on until it is held, then there once. */
    const scroller = useRef<ScrollView>(null);
    const placed = useRef<string | null>(null);
    const pagesRead = useRef(0);
    const following = useRef(!focusLine);
    const focusHeld = replies.some((line) => line.id === focusLine);
    const { hasNextPage, isFetchingNextPage, fetchNextPage } = thread;
    useEffect(() => {
        if (!focusLine || focusHeld) return;
        if (hasNextPage && !isFetchingNextPage && pagesRead.current < FOCUS_PAGES) {
            pagesRead.current += 1;
            void fetchNextPage();
        }
    }, [focusLine, focusHeld, hasNextPage, isFetchingNextPage, fetchNextPage]);
    const laidOut = (line: Line) => (event: LayoutChangeEvent) => {
        const y = event.nativeEvent.layout.y;
        if (line.id === focusLine && placed.current !== line.id) {
            placed.current = line.id;
            following.current = false;
            setTimeout(() => scroller.current?.scrollTo({ y: Math.max(0, y - 120), animated: false }), 60);
        }
    };

    const posted = (line: Line) => {
        cache.setQueryData<InfiniteData<ThreadPage>>(chatKeys.thread(key, podId, channel.id, rootId), (was) => (was ? { ...was, pages: upsertThread(was.pages, line) } : was));
        /* The first message counts its replies: the socket sends it again
           with the new count; without the socket it is read again. */
        if (!liveAvailable() || podLive(podId).status !== "open") void cache.invalidateQueries({ queryKey: chatKeys.lines(key, podId, channel.id) });
        following.current = true;
        setTimeout(() => scroller.current?.scrollToEnd({ animated: true }), 50);
    };

    const context: LineContext = {
        podId, channel, me, frontId, agentName,
        lemma: lemma ? { id: lemma.id, name: lemma.name, iconUrl: lemma.iconUrl } : null,
        mayWrite: may.write,
        onHold: setHeld,
    };
    const typingNames = typingIn(live.typing, rootId).map((one) => personName(one.who, me));
    const rootAnswering = live.answering.filter((one) => one.messageId === rootId);

    return (
        <View style={{ flex: 1 }}>
            <ScrollView
                ref={scroller}
                style={{ flex: 1 }}
                contentContainerStyle={{ paddingTop: 6, paddingBottom: 12 }}
                keyboardShouldPersistTaps="handled"
                onScrollBeginDrag={() => { following.current = false; }}
                onContentSizeChange={() => { if (following.current) scroller.current?.scrollToEnd({ animated: false }); }}
            >
                {root ? <LineView line={root} context={context} inThread /> : timeline.isPending ? <Loading label="Reading the thread" /> : null}
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingHorizontal: 16, paddingVertical: 10 }}>
                    <Text size={12.5} tone={color.ink3}>
                        {thread.isPending ? "Loading replies…" : thread.isError && !thread.data ? "Couldn’t load the replies." : count === 0 ? "No replies yet" : count === 1 ? "1 reply" : count + " replies"}
                    </Text>
                    <View style={{ flex: 1, height: 1, backgroundColor: color.line }} />
                    {thread.isError && !thread.data ? <Button title="Try again" kind="quiet" height={30} size={13} onPress={() => void thread.refetch()} /> : null}
                </View>
                {replies.map((line, index) => (
                    <LineView
                        key={line.id}
                        line={line}
                        context={context}
                        inThread
                        continued={continues(replies[index - 1], line)}
                        answering={live.answering.filter((one) => one.messageId === line.id)}
                        highlighted={focusLine === line.id}
                        onLayout={laidOut(line)}
                    />
                ))}
                {thread.hasNextPage ? (
                    <Press label="Show later replies" disabled={thread.isFetchingNextPage} onPress={() => void thread.fetchNextPage()} style={{ alignSelf: "center", minHeight: 34, paddingHorizontal: 14, justifyContent: "center", borderRadius: 999, borderWidth: 1, borderColor: color.line2, marginTop: 8 }}>
                        <Text size={13} tone={color.ink2}>{thread.isFetchingNextPage ? "Loading…" : "Show later replies"}</Text>
                    </Press>
                ) : null}
                {rootAnswering.map((one) => (
                    <Text key={"a" + (one.agentId ?? "")} size={13} tone={color.ink2} style={{ paddingHorizontal: 56, paddingTop: 8 }}>{agentName(one.agentId)} is answering…</Text>
                ))}
            </ScrollView>
            <Typing names={typingNames} />
            <ChatComposer
                podId={podId}
                channel={channel}
                people={named}
                threadRootId={rootId}
                placeholder="Reply in thread"
                disabledReason={channel.archived ? "This chat is archived. Bring it back to write in it." : may.write ? null : may.writeRefused ?? "You can’t write here."}
                onPosted={posted}
                onTyping={() => live.typed(rootId)}
            />
            <LineActions podId={podId} channel={channel} line={held} me={me} mayWrite={may.write && !channel.archived} inThread people={named} onClose={() => setHeld(null)} onThread={() => undefined} />
        </View>
    );
}
