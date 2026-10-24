import { useState } from "react";
import { ActivityIndicator, View } from "react-native";
import { useRouter } from "expo-router";
import { useLemma } from "../../data/hooks";
import { PersonFace } from "../../ui/face";
import { Icon } from "../../ui/icons";
import { Press } from "../../ui/press";
import { Num, Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { EntryRow, Label, SearchField, Segments, TextAction } from "./bits";
import { allChannels, useChannelList, useMaySeeContacts, useMyId, useOnline, usePeople, useSettled } from "./hooks";
import { LemmaChats } from "./list";
import { CHAT_FILTERS, glyphOf, peopleRows, type ChatFilter } from "./model";
import { chatsRoutes } from "./routes";
import { StartSheet } from "./starters";
import { channelTitle, sinceShort } from "@web/channels/model";
import { filterFor, needsPerson } from "@web/channels/views";

/** A Lemma's chats, on its page: what waits on the reader, then every chat
 *  with it and every group it answers in, searchable and paged; New starts
 *  a chat, a group, or brings it into a group on a platform. */
export function ChatsTab({ podId, name }: { podId: string; name: string }) {
    const lemma = useLemma(podId);
    const [typed, setTyped] = useState("");
    const search = useSettled(typed, 250);
    const [filter, setFilter] = useState<ChatFilter>("all");
    const [starting, setStarting] = useState(false);
    const now = Date.now();
    return (
        <View style={{ gap: 10 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <View style={{ flex: 1 }}><SearchField value={typed} onChange={setTyped} placeholder={"Search " + name + "’s chats"} /></View>
                <Press label="Start a chat" onPress={() => setStarting(true)} style={{ height: 42, paddingHorizontal: 14, borderRadius: 12, borderWidth: 1, borderColor: color.line2, flexDirection: "row", alignItems: "center", gap: 6 }}>
                    <Icon name="plus" size={16} />
                    <Text size={14.5}>New</Text>
                </Press>
            </View>
            <Segments items={CHAT_FILTERS.map((one) => ({ id: one.id, label: one.label }))} chosen={filter} onPick={setFilter} label="Which chats" />
            <LemmaChats lemma={{ id: podId, name, iconUrl: lemma?.iconUrl ?? null }} filter={filter} search={search} now={now} />
            <StartSheet podId={podId} lemmaName={name} open={starting} onClose={() => setStarting(false)} />
        </View>
    );
}

/** The people in a Lemma, who has the app open, and -- for whoever looks
 *  after them -- the contacts who wrote in: who is waiting on a person, and
 *  the way to all of them and to where they write. */
export function PeopleTab({ podId, name }: { podId: string; name: string }) {
    const me = useMyId();
    const people = usePeople(podId);
    const online = useOnline(podId);
    const maySee = useMaySeeContacts(podId);
    const rows = peopleRows(people.data ?? [], online, me);
    return (
        <View>
            <Label right={people.data ? <Num size={12.5} tone={color.ink3}>{people.data.length}</Num> : undefined}>{"People in " + name}</Label>
            {people.isPending ? <View style={{ padding: 16 }}><ActivityIndicator color={color.ink3} /></View> : null}
            {people.isError ? <Text size={14} tone={color.amber}>Couldn’t read who is in {name}.</Text> : null}
            {rows.map((one) => (
                <View key={one.userId} accessibilityLabel={one.name + (one.you ? ", you" : "") + (one.here ? ", has the app open" : "") + ", " + one.role} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH, paddingHorizontal: 4 }}>
                    <View>
                        <PersonFace name={one.name} size={26} dark={one.you} />
                        {one.here && !one.you ? <View style={{ position: "absolute", right: -2, bottom: -2, width: 10, height: 10, borderRadius: 5, backgroundColor: color.ink, borderWidth: 2, borderColor: color.paper }} /> : null}
                    </View>
                    <Text size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                        {one.name}
                        {one.you ? <Text size={14} tone={color.ink3}> (you)</Text> : one.email ? <Text size={13.5} tone={color.ink3}>{"  " + one.email}</Text> : null}
                    </Text>
                    <Text size={13} tone={color.ink3}>{one.role}</Text>
                </View>
            ))}
            {maySee ? <ContactsPart podId={podId} name={name} /> : null}
        </View>
    );
}

function ContactsPart({ podId, name }: { podId: string; name: string }) {
    const router = useRouter();
    const waiting = useChannelList(podId, filterFor("needs", null));
    const channels = allChannels(waiting.data).filter(needsPerson);
    const now = Date.now();
    return (
        <View>
            <Label>Contacts</Label>
            {waiting.isPending ? <View style={{ padding: 12 }}><ActivityIndicator color={color.ink3} /></View> : null}
            {!waiting.isPending && channels.length === 0 ? <Text size={14.5} tone={color.ink2} style={{ paddingHorizontal: 4, paddingVertical: 6 }}>Nobody is waiting on a person.</Text> : null}
            {channels.slice(0, 5).map((channel) => (
                <EntryRow key={channel.id} now={now} entry={{
                    key: channel.id,
                    title: channelTitle(channel, name),
                    said: "Needs a person" + (channel.attention?.reason ? ": " + channel.attention.reason : "") + (channel.attention ? " · " + sinceShort(channel.attention.since) : ""),
                    at: null,
                    href: chatsRoutes.shared(podId, channel.id),
                    glyph: glyphOf(channel),
                    needsYou: true,
                    unread: 0,
                    running: false,
                    channel,
                }} />
            ))}
            <TextAction label="Every conversation with a contact" tone={color.ink} onPress={() => router.push(chatsRoutes.contacts(podId, "inbox") as never)} />
            <TextAction label="All contacts" tone={color.ink} onPress={() => router.push(chatsRoutes.contacts(podId, "people") as never)} />
            <TextAction label="Where they write, and what they can reach" tone={color.ink} onPress={() => router.push(chatsRoutes.contacts(podId, "setup") as never)} />
        </View>
    );
}
