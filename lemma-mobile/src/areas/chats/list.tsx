import { useMemo, useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../data/hooks";
import type { Lemma } from "../../data/types";
import { LemmaFace } from "../../ui/face";
import { Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Num, Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { EntryRow, Label, TextAction, Trouble } from "./bits";
import { allChannels, refreshLists, useChannelList, useChatsSource, useConversations, useInbox, useInboxLive, useMyId, useSaid } from "./hooks";
import { chatEntries, glyphOf, lastSaid, newMessages, waitingEntries, type ChatEntry, type ChatFilter } from "./model";
import { chatsRoutes } from "./routes";
import { WaitingSection } from "./waiting";
import { channelTitle, senderLabel, sinceShort, type Channel } from "@web/channels/model";
import { problemOf } from "@web/channels/problem";
import { hitText, snippet } from "@web/channels/search-model";

/** How many chats a Lemma shows in All chats before "More". */
const FIRST = 6;
const MORE = 20;
const ARCHIVED = { archived: true };
const LISTED = { archived: false };

/** One Lemma's chats (the web's Chats page, for a phone): what waits on the
 *  reader, then every chat with it and every chat that lives on a channel
 *  -- groups, their own chat, mirrored groups -- most recently active first,
 *  the whole history a page at a time. A search asks the server for chats by
 *  title and for what was said in the shared ones. `compact` is a Lemma's
 *  part of All chats: its face, the first few, and more on asking. */
export function LemmaChats({ lemma, filter, search, compact = false, now }: { lemma: Pick<Lemma, "id" | "name" | "iconUrl">; filter: ChatFilter; search: string; compact?: boolean; now: number }) {
    const router = useRouter();
    const podId = lemma.id;
    const me = useMyId();
    const channels = useChannelList(podId, LISTED);
    const conversations = useConversations(podId, search);
    const inbox = useInbox(podId);
    /* Live only for one Lemma's own list: All chats would hold a socket
       open per Lemma, and reads its counts on a poll instead. */
    useInboxLive(podId, !compact);
    const [shown, setShown] = useState(compact ? FIRST : Number.POSITIVE_INFINITY);
    const [held, setHeld] = useState<Channel | null>(null);
    const [archived, setArchived] = useState(false);

    const listed = useMemo(() => allChannels(channels.data), [channels.data]);
    const chats = useMemo(() => (conversations.data?.pages ?? []).flatMap((page) => page.items), [conversations.data]);
    const entries = useMemo(() => chatEntries({ podId, lemmaName: lemma.name, chats, channels: listed, filter, search, me }), [podId, lemma.name, chats, listed, filter, search, me]);
    const mine = listed.find((channel) => channel.kind === "DIRECT") ?? null;
    const waiting = search ? [] : waitingEntries(inbox.data, mine);
    const visible = entries.slice(0, shown);
    const more = entries.length > shown || conversations.hasNextPage;
    const pending = channels.isPending || conversations.isPending;

    const showMore = () => {
        if (entries.length > shown) setShown((was) => was + MORE);
        if (entries.length <= shown + MORE && conversations.hasNextPage && !conversations.isFetchingNextPage) void conversations.fetchNextPage();
    };

    const quiet = !pending && entries.length === 0 && waiting.length === 0;
    if (compact && quiet && search) return null;

    return (
        <View accessibilityLabel={compact ? lemma.name : undefined} style={{ marginTop: compact ? 18 : 0 }}>
            {compact ? (
                <Press role="link" label={"All of " + lemma.name + "’s chats"} onPress={() => router.push(chatsRoutes.chats(podId) as never)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: 36, paddingHorizontal: 4 }}>
                    <LemmaFace id={lemma.id} icon={lemma.iconUrl} size={22} />
                    <Text size={15} weight="medium" numberOfLines={1} style={{ flex: 1 }}>{lemma.name}</Text>
                    {inbox.data && (inbox.data.waiting + inbox.data.mentions) > 0 ? <Num size={12.5} tone={color.violetInk}>{inbox.data.waiting + inbox.data.mentions}</Num> : null}
                </Press>
            ) : null}
            <WaitingSection podId={podId} entries={waiting} lemmaName={lemma.name} now={now} title={compact ? "Waiting on you here" : "Waiting on you"} />
            {!compact && waiting.length ? <Label>Chats</Label> : null}
            {pending ? <View style={{ padding: 16 }}><ActivityIndicator color={color.ink3} /></View> : null}
            {conversations.isError && !conversations.data ? <Trouble>{problemOf(conversations.error, "Couldn’t load the chats.")}</Trouble> : null}
            {quiet ? <Text size={15} tone={color.ink2} style={{ paddingHorizontal: 4, paddingVertical: 10 }}>{search ? "Nothing matches that." : "No chats yet."}</Text> : null}
            {visible.map((entry) => <EntryRow key={entry.key} entry={entry} now={now} onHold={entry.channel ? () => setHeld(entry.channel) : undefined} />)}
            {more ? (
                <TextAction label={conversations.isFetchingNextPage ? "Loading…" : compact ? "More from " + lemma.name : "Older chats"} busy={conversations.isFetchingNextPage} onPress={showMore} />
            ) : null}
            {search.length >= 2 ? <Said podId={podId} lemmaName={lemma.name} search={search} compact={compact} now={now} /> : null}
            {!compact && !search ? (
                <>
                    <TextAction label={archived ? "Hide archived chats" : "Archived chats"} tone={color.ink3} onPress={() => setArchived((was) => !was)} />
                    {archived ? <Archived podId={podId} lemmaName={lemma.name} now={now} /> : null}
                </>
            ) : null}
            <HeldChat podId={podId} channel={held} lemmaName={lemma.name} onClose={() => setHeld(null)} />
        </View>
    );
}

/** What holding a chat in the list offers: mark it read, its details. */
function HeldChat({ podId, channel, lemmaName, onClose }: { podId: string; channel: Channel | null; lemmaName: string; onClose: () => void }) {
    const router = useRouter();
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const read = useMutation({
        mutationFn: (one: Channel) => source.markRead(podId, one.id, one.lastSeq),
        onSuccess: () => refreshLists(cache, key, podId),
    });
    const go = (href: string) => { onClose(); router.push(href as never); };
    return (
        <Sheet open={channel !== null} onClose={onClose} title={channel ? channelTitle(channel, lemmaName) : undefined}>
            {channel ? (
                <View style={{ paddingHorizontal: 8 }}>
                    {channel.unread.lines > 0 || channel.unread.mentions > 0 ? (
                        <Press label="Mark read" onPress={() => { read.mutate(channel); onClose(); }} style={{ minHeight: TOUCH + 4, justifyContent: "center", paddingHorizontal: 12 }}>
                            <Text size={16}>Mark read</Text>
                            <Text size={13} tone={color.ink3}>{newMessages(channel.unread.lines)}</Text>
                        </Press>
                    ) : null}
                    <Press label="Open" onPress={() => go(chatsRoutes.shared(podId, channel.id))} style={{ minHeight: TOUCH + 4, justifyContent: "center", paddingHorizontal: 12 }}>
                        <Text size={16}>Open</Text>
                    </Press>
                    <Press label="Details and people" onPress={() => go(chatsRoutes.details(podId, channel.id))} style={{ minHeight: TOUCH + 4, justifyContent: "center", paddingHorizontal: 12 }}>
                        <Text size={16}>Details and people</Text>
                    </Press>
                </View>
            ) : null}
        </Sheet>
    );
}

/** The chats nobody can write in any more, to bring one back from its settings. */
function Archived({ podId, lemmaName, now }: { podId: string; lemmaName: string; now: number }) {
    const me = useMyId();
    const list = useChannelList(podId, ARCHIVED);
    const channels = allChannels(list.data);
    if (list.isPending) return <View style={{ padding: 12 }}><ActivityIndicator color={color.ink3} /></View>;
    if (channels.length === 0) return <Text size={14} tone={color.ink3} style={{ paddingHorizontal: 4, paddingBottom: 8 }}>Nothing is archived.</Text>;
    return (
        <>
            {channels.map((channel) => {
                const entry: ChatEntry = {
                    key: "archived:" + channel.id,
                    title: channelTitle(channel, lemmaName),
                    said: lastSaid(channel, me),
                    at: Date.parse(channel.lastMessageAt ?? channel.createdAt) || null,
                    href: chatsRoutes.shared(podId, channel.id),
                    glyph: glyphOf(channel),
                    needsYou: false,
                    unread: 0,
                    running: false,
                    channel,
                };
                return <EntryRow key={entry.key} entry={entry} now={now} />;
            })}
        </>
    );
}

/** What was said in the shared chats, each opening its chat at that message. */
function Said({ podId, lemmaName, search, compact, now }: { podId: string; lemmaName: string; search: string; compact: boolean; now: number }) {
    const router = useRouter();
    const me = useMyId();
    const found = useSaid(podId, search);
    const hits = (found.data?.pages ?? []).flatMap((page) => page.items);
    const shown = compact ? hits.slice(0, 4) : hits;
    if (found.isPending) return <View style={{ padding: 12 }}><ActivityIndicator color={color.ink3} /></View>;
    if (found.isError) return <Trouble>Couldn’t search what was said.</Trouble>;
    if (hits.length === 0) return compact ? null : <Text size={14} tone={color.ink3} style={{ paddingHorizontal: 4, paddingVertical: 8 }}>Nothing said in a shared chat matches “{search}”.</Text>;
    return (
        <View>
            <Label>Said in shared chats</Label>
            {shown.map((hit) => {
                const where = channelTitle({ kind: hit.channel.kind, title: hit.channel.title, person: hit.channel.person, sources: [] }, lemmaName);
                return (
                    <Press key={hit.line.id} role="link" label={senderLabel(hit.line, me) + " in " + where} onPress={() => router.push(chatsRoutes.shared(podId, hit.channel.id, { line: hit.line.id, thread: hit.line.threadRootId }) as never)} style={{ paddingVertical: 8, paddingHorizontal: 4, gap: 2, borderBottomWidth: 1, borderBottomColor: color.line }}>
                        <View style={{ flexDirection: "row", gap: 8 }}>
                            <Text size={14} numberOfLines={1} style={{ flex: 1 }}>{senderLabel(hit.line, me)} <Text size={13} tone={color.ink3}>in {where}{hit.line.threadRootId ? ", in a thread" : ""}</Text></Text>
                            <Num size={12.5} tone={color.ink3}>{sinceShort(hit.line.createdAt, new Date(now))}</Num>
                        </View>
                        <Text size={14} tone={color.ink2} numberOfLines={2}>
                            {snippet(hitText(hit), search).map((piece, index) => <Text key={index} size={14} tone={piece.match ? color.ink : color.ink2} style={piece.match ? { backgroundColor: color.wash } : undefined}>{piece.text}</Text>)}
                        </Text>
                    </Press>
                );
            })}
            {!compact && found.hasNextPage ? <TextAction label={found.isFetchingNextPage ? "Loading…" : "More of what was said"} busy={found.isFetchingNextPage} onPress={() => void found.fetchNextPage()} /> : null}
            {compact && hits.length > shown.length ? <TextAction label={"All " + hits.length + " in " + lemmaName} onPress={() => router.push(chatsRoutes.chats(podId) as never)} /> : null}
        </View>
    );
}
