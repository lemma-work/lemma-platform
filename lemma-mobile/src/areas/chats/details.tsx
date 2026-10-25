import { useState } from "react";
import { Alert, KeyboardAvoidingView, ScrollView, View } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { SafeAreaView } from "react-native-safe-area-context";
import { useCacheKey, useLemma } from "../../data/hooks";
import { Loading } from "../../ui/chrome";
import { PersonFace } from "../../ui/face";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Num, Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { Choices, Field, Label, Rule, SaidBy, SearchField, Segments, SwitchRow, TextAction, Trouble } from "./bits";
import { Header } from "./chat-view";
import { chatKeys, keepChannel, refreshLists, useAbilities, useAgents, useChannel, useChatsSource, useMyId, useOnline, usePeople } from "./hooks";
import { agoSaid, answersSharedSaid, glyphOf, isForbidden, peopleRows } from "./model";
import { chatsRoutes, type DetailsTab } from "./routes";
import { SourceSettingsView } from "./sources-view";
import type { ChannelChange, MineChange } from "./source";
import { channelAbilities, type ChannelAbilities } from "@web/channels/abilities";
import { answersHint, placeName } from "@web/channels/chat-copy";
import { addable, channelTitle, personName, sinceShort, type AgentAnswers, type ChannelDetail, type ContactStatus, type NotifyLevel } from "@web/channels/model";
import { problemOf } from "@web/channels/problem";
import { muteChoices, mutedSaid, NOTIFY_VIA, publicWarning } from "@web/channels/settings-model";
import { isGroupPlatform, platformName } from "@web/channels/sources";
import { STATUS_LABELS } from "@web/channels/views";

const LEVELS: { level: NotifyLevel; label: string; said: string }[] = [
    { level: "ALL", label: "Every new message", said: "Tell me about every new message" },
    { level: "MENTIONS", label: "Only when I’m named or asked", said: "Tell me only when I’m named or asked" },
    { level: "NONE", label: "Nothing", said: "Tell me about nothing here" },
];

const ANSWERS: { value: AgentAnswers; label: string }[] = [
    { value: "MENTIONED", label: "When named" },
    { value: "EVERY_LINE", label: "Every message" },
];

/** A chat's details on a screen of their own (the web's `ChatDetails`,
 *  whose tabs split the way Slack splits a channel's): how the reader hears
 *  of it, what it is, who is in it, who answers, where it is mirrored from,
 *  and -- for whoever may change it -- its settings. What the reader may not
 *  change is shown, not offered; the server decides again. */
export function DetailsScreen({ podId, channelId, tab }: { podId: string; channelId: string; tab: DetailsTab | null }) {
    const detail = useChannel(podId, channelId);
    const lemma = useLemma(podId);
    const channel = detail.data ?? null;
    if (!channel) {
        return (
            <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
                <Header podId={podId} title="Details" subtitle={lemma?.name ?? " "} glyph={null} />
                {detail.isPending ? <Loading /> : <View style={{ padding: 20 }}><Trouble>{problemOf(detail.error, "Couldn’t load this chat.")}</Trouble></View>}
            </SafeAreaView>
        );
    }
    return <Details podId={podId} channel={channel} first={tab} />;
}

function Details({ podId, channel, first }: { podId: string; channel: ChannelDetail; first: DetailsTab | null }) {
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "The Lemma";
    const may = channelAbilities(useAbilities(podId), channel, lemmaName);
    const agents = useAgents(podId);
    const frontId = agents.data?.find((agent) => agent.front)?.id ?? null;
    const people = channel.members.filter((one) => one.person.kind !== "AGENT").length;
    const answering = channel.members.filter((one) => one.person.kind === "AGENT" && one.person.id !== frontId).length + 1;
    const tabs: { id: DetailsTab; label: string; count?: number }[] = [
        { id: "about", label: "About" },
        ...(channel.kind !== "DIRECT" ? [{ id: "people" as const, label: "People", count: people }] : []),
        ...(channel.kind === "GROUP" ? [{ id: "answers" as const, label: "Who answers", count: answering }] : []),
        ...(channel.places.length > 0 ? [{ id: "connected" as const, label: "Connected" }] : []),
        ...(channel.kind === "GROUP" && may.manage ? [{ id: "settings" as const, label: "Settings" }] : []),
    ];
    const [at, setAt] = useState<DetailsTab>(tabs.some((one) => one.id === first) ? (first as DetailsTab) : "about");
    const current = tabs.some((one) => one.id === at) ? at : "about";
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <Header podId={podId} title={channelTitle(channel, lemmaName)} subtitle={lemmaName} glyph={glyphOf(channel)} />
                <ScrollView contentContainerStyle={{ padding: 16, paddingBottom: 32, gap: 12 }} keyboardShouldPersistTaps="handled">
                    {channel.me.joined ? <Notifications podId={podId} channel={channel} lemmaName={lemmaName} /> : null}
                    <Segments items={tabs.map((one) => ({ id: one.id, label: one.label, count: one.count }))} chosen={current} onPick={setAt} label="Chat details" />
                    <Rule />
                    {current === "about" ? <About podId={podId} channel={channel} may={may} lemmaName={lemmaName} onSettings={() => setAt("settings")} /> : null}
                    {current === "people" ? <People podId={podId} channel={channel} may={may} lemmaName={lemmaName} /> : null}
                    {current === "answers" ? <Answers podId={podId} channel={channel} may={may} lemmaName={lemmaName} lemma={lemma ? { id: lemma.id, iconUrl: lemma.iconUrl } : null} /> : null}
                    {current === "connected" ? <Connected podId={podId} channel={channel} lemmaName={lemmaName} /> : null}
                    {current === "settings" ? <Settings podId={podId} channel={channel} lemmaName={lemmaName} /> : null}
                </ScrollView>
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}

function refusal(error: unknown, lemmaName: string, what: string): string {
    return isForbidden(error) ? "Only who started this chat, or an editor of " + lemmaName + ", can " + what + "." : problemOf(error, "Couldn’t " + what + ".");
}

function useChange(podId: string, channelId: string) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    return useMutation({
        mutationFn: (body: ChannelChange) => source.update(podId, channelId, body),
        onSuccess: (saved) => keepChannel(cache, key, podId, saved),
    });
}

/* ── how the reader hears of it ────────────────────────────────────── */

function Notifications({ podId, channel, lemmaName }: { podId: string; channel: ChannelDetail; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const [open, setOpen] = useState(false);
    const set = useMutation({
        mutationFn: (change: MineChange) => source.setMine(podId, channel.id, change),
        onSuccess: (saved) => keepChannel(cache, key, podId, saved),
    });
    const level = set.isPending && set.variables?.notify ? set.variables.notify : channel.me.notify;
    const muted = mutedSaid(channel.me.mutedUntil);
    const via = channel.me.notifyVia?.[0] ?? "";
    const said = muted ?? LEVELS.find((one) => one.level === level)?.said ?? LEVELS[1].said;
    return (
        <View style={{ gap: 10 }}>
            <Press label={said + ". Change how you hear of it"} onPress={() => setOpen((was) => !was)} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: TOUCH }}>
                <Icon name="bell" size={18} tone={color.ink2} />
                <Text size={15} style={{ flex: 1 }}>{said}</Text>
                <Icon name="down" size={16} tone={color.ink3} />
            </Press>
            {open ? (
                <View style={{ gap: 12, paddingLeft: 28 }}>
                    <Choices label="Tell me about" options={LEVELS.map((one) => ({ value: one.level, label: one.label }))} value={level} disabled={set.isPending} onPick={(next) => set.mutate({ notify: next })} />
                    <Label>Mute it</Label>
                    {muted ? (
                        <TextAction label="Unmute" tone={color.ink} disabled={set.isPending} onPress={() => set.mutate({ mutedUntil: null })} />
                    ) : (
                        <Choices label="Mute it" options={muteChoices().map((one) => ({ value: one.until, label: one.label }))} value={""} disabled={set.isPending} onPick={(until) => set.mutate({ mutedUntil: until })} />
                    )}
                    <Label>Outside the app, on</Label>
                    <Choices
                        label="Outside the app, on"
                        options={[{ value: "", label: "Wherever " + lemmaName + " reaches me" }, ...NOTIFY_VIA.map((one) => ({ value: one.value, label: one.label }))]}
                        value={via}
                        disabled={set.isPending}
                        onPick={(next) => set.mutate({ notifyVia: next ? [next] : null })}
                    />
                </View>
            ) : null}
            <Trouble>{set.isError ? problemOf(set.error, "Couldn’t change that.") : null}</Trouble>
        </View>
    );
}

/* ── about ─────────────────────────────────────────────────────────── */

function Row({ label, children, action }: { label: string; children: React.ReactNode; action?: React.ReactNode }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "flex-start", gap: 12, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <View style={{ flex: 1, gap: 3 }}>
                <Text size={12.5} tone={color.ink3}>{label}</Text>
                {typeof children === "string" ? <Text size={15} style={{ lineHeight: 21 }}>{children}</Text> : children}
            </View>
            {action}
        </View>
    );
}

function About({ podId, channel, may, lemmaName, onSettings }: { podId: string; channel: ChannelDetail; may: ChannelAbilities; lemmaName: string; onSettings: () => void }) {
    const router = useRouter();
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const me = useMyId();
    const update = useChange(podId, channel.id);
    const [editing, setEditing] = useState<"title" | "topic" | null>(null);
    const [draft, setDraft] = useState("");
    const leave = useMutation({
        mutationFn: () => source.leave(podId, channel.id),
        onSuccess: () => {
            refreshLists(cache, key, podId);
            void cache.invalidateQueries({ queryKey: chatKeys.detail(key, podId, channel.id) });
            router.dismissTo(chatsRoutes.chats(podId) as never);
        },
    });
    const start = (field: "title" | "topic") => { setDraft((field === "title" ? channel.title : channel.topic) ?? ""); setEditing(field); };
    const save = () => {
        if (editing === "title" && draft.trim()) update.mutate({ title: draft.trim() }, { onSuccess: () => setEditing(null) });
        if (editing === "topic") update.mutate({ topic: draft.trim() || null }, { onSuccess: () => setEditing(null) });
    };
    const editor = (field: "title" | "topic") => (
        <View style={{ gap: 8, paddingTop: 4 }}>
            <Field accessibilityLabel={field === "title" ? "The chat’s name" : "What this chat is for"} autoFocus value={draft} maxLength={field === "title" ? 200 : 500} placeholder={field === "title" ? "Launch plan" : "What this chat is for"} onChangeText={setDraft} onSubmitEditing={save} returnKeyType="done" />
            <View style={{ flexDirection: "row", gap: 8 }}>
                <Button title="Cancel" kind="outline" height={36} size={14} onPress={() => setEditing(null)} />
                <Button title="Save" height={36} size={14} busy={update.isPending} disabled={field === "title" && !draft.trim()} onPress={save} />
            </View>
        </View>
    );
    const edit = (field: "title" | "topic") => (may.manage && editing !== field ? <TextAction label="Edit" onPress={() => start(field)} /> : undefined);

    if (channel.kind === "DIRECT") {
        return (
            <View>
                <Row label="What it is">{"Just you and " + lemmaName + ". " + answersHint(channel, lemmaName) + " Nobody else reads it."}</Row>
                {channel.places.length > 0 ? <Row label="Reached on">{[...new Set(channel.places.map((one) => platformName(one.platform)))].join(", ")}</Row> : null}
            </View>
        );
    }
    if (channel.kind === "CONTACT") {
        const contactId = channel.person?.id;
        return (
            <View style={{ gap: 6 }}>
                <Row label="Who" action={contactId ? <TextAction label="Open" onPress={() => router.push(chatsRoutes.contact(podId, contactId) as never)} /> : undefined}>
                    {personName(channel.person, me) + ", outside " + lemmaName + ". Only its editors and admins read this chat."}
                </Row>
                {channel.attention && !channel.assignee ? (
                    <Row label="Needs a person"><Text size={15}>{channel.attention.reason ?? "They asked for one."} <Num size={13} tone={color.ink3}>{sinceShort(channel.attention.since)}</Num></Text></Row>
                ) : null}
                {may.contactOps ? <ContactControls podId={podId} channel={channel} lemmaName={lemmaName} /> : null}
            </View>
        );
    }
    const shared = answersSharedSaid(channel.answersShared, lemmaName);
    return (
        <View style={{ gap: 6 }}>
            <Row label="Name" action={edit("title")}>{editing === "title" ? editor("title") : channelTitle(channel, lemmaName)}</Row>
            <Row label="Topic" action={edit("topic")}>{editing === "topic" ? editor("topic") : <Text size={15} tone={channel.topic ? color.ink : color.ink3}>{channel.topic ?? (may.manage ? "Add a topic" : "No topic")}</Text>}</Row>
            <Row label="Who answers">{answersHint(channel, lemmaName)}</Row>
            {may.manage ? (
                <SwitchRow label={shared.label} note={shared.note} value={channel.answersShared} disabled={update.isPending} onChange={(on) => update.mutate({ answersShared: on })} />
            ) : <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>{shared.note}</Text>}
            <Row label="Who can find it" action={may.manage ? <TextAction label="Change" onPress={onSettings} /> : undefined}>
                {channel.visibility === "PRIVATE" ? "Only the people in it." : "Anyone in " + lemmaName + " can find it and join."}
            </Row>
            <Row label="Started">{new Date(channel.createdAt).toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" })}</Row>
            <Trouble>{update.isError ? refusal(update.error, lemmaName, "change it") : null}</Trouble>
            {channel.me.joined ? (
                <View style={{ paddingTop: 10 }}>
                    <TextAction label={leave.isPending ? "Leaving…" : "Leave this chat"} tone={color.amber} busy={leave.isPending} onPress={() => Alert.alert("Leave this chat?", channel.visibility === "PUBLIC" ? "You can join again from the Lemma’s chats." : "Someone in it has to add you back.", [
                        { text: "Stay", style: "cancel" },
                        { text: "Leave", style: "destructive", onPress: () => leave.mutate() },
                    ])} />
                    <Trouble>{leave.isError ? problemOf(leave.error, "Couldn’t leave it.") : null}</Trouble>
                </View>
            ) : null}
        </View>
    );
}

/** A contact's chat as a support-inbox item: who answers them by hand,
 *  where it stands, and how long the assignee holds them before the Lemma
 *  answers again (the web's `ContactControls` and `AgentSwitch`). */
function ContactControls({ podId, channel, lemmaName }: { podId: string; channel: ChannelDetail; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const me = useMyId();
    const people = usePeople(podId);
    const change = useChange(podId, channel.id);
    const flip = useMutation({
        mutationFn: (on: boolean) => source.setAgentAnswers(podId, channel.id, on),
        onSuccess: (saved) => keepChannel(cache, key, podId, saved),
    });
    const [hours, setHours] = useState(String(channel.handBackAfterHours));
    const assignee = change.isPending && change.variables?.assignee !== undefined
        ? (change.variables.assignee === "me" ? me : change.variables.assignee)
        : channel.assignee?.id ?? null;
    const status = change.isPending && change.variables?.status ? change.variables.status : channel.status ?? "open";
    const on = flip.isPending && flip.variables !== undefined ? flip.variables : channel.agentsAnswer;
    const options = [
        { value: "", label: "Nobody" },
        ...(me ? [{ value: me, label: "You" }] : []),
        ...(people.data ?? []).filter((one) => one.userId !== me).map((one) => ({ value: one.userId, label: one.name })),
        ...(channel.assignee?.id && channel.assignee.id !== me && !(people.data ?? []).some((one) => one.userId === channel.assignee?.id) ? [{ value: channel.assignee.id, label: channel.assignee.name ?? "Someone" }] : []),
    ];
    return (
        <View style={{ gap: 12, paddingTop: 6 }}>
            <SwitchRow label={lemmaName + " answers them"} note={on ? lemmaName + " answers them. Switch off to answer them yourself." : "Off: only people answer them until you switch it back on."} value={on} disabled={flip.isPending} onChange={(next) => flip.mutate(next)} />
            <Trouble>{flip.isError ? (isForbidden(flip.error) ? "Only an editor can switch it." : "Couldn’t switch it.") : null}</Trouble>
            <Label>Assigned to</Label>
            <Choices label="Assigned to" options={options} value={assignee ?? ""} disabled={change.isPending} onPick={(picked) => change.mutate({ assignee: picked === "" ? null : picked === me ? "me" : picked })} />
            <Label>Where it stands</Label>
            <Choices label="Where it stands" options={(Object.keys(STATUS_LABELS) as ContactStatus[]).map((one) => ({ value: one, label: STATUS_LABELS[one] }))} value={status} disabled={change.isPending} onPick={(next) => change.mutate({ status: next })} />
            <Label>Hand back after</Label>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                <Field accessibilityLabel="Hours quiet before it hands back" keyboardType="number-pad" value={hours} onChangeText={setHours} style={{ width: 80 }} />
                <Text size={15} tone={color.ink2} style={{ flex: 1 }}>hours quiet</Text>
                <Button title="Save" kind="outline" height={36} size={14} disabled={change.isPending || Number(hours) === channel.handBackAfterHours} onPress={() => {
                    const value = Number(hours);
                    if (Number.isInteger(value) && value >= 1 && value <= 720) change.mutate({ handBackAfterHours: value });
                    else Alert.alert("Hours", "Between 1 and 720 hours.");
                }} />
            </View>
            <Trouble>{change.isError ? (isForbidden(change.error) ? "Only an editor of " + lemmaName + " can change that." : problemOf(change.error, "Couldn’t change that.")) : null}</Trouble>
        </View>
    );
}

/* ── people ────────────────────────────────────────────────────────── */

function People({ podId, channel, may, lemmaName }: { podId: string; channel: ChannelDetail; may: ChannelAbilities; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const router = useRouter();
    const me = useMyId();
    const everyone = usePeople(podId);
    const online = useOnline(podId);
    const [query, setQuery] = useState("");
    const [adding, setAdding] = useState(false);
    const needle = query.trim().toLowerCase();
    const matches = (name: string) => !needle || name.toLowerCase().includes(needle);
    const people = channel.members.filter((one) => one.person.kind !== "AGENT" && matches(personName(one.person, me) + " " + (one.person.name ?? "")));
    const candidates = may.manage ? addable((everyone.data ?? []).map((one) => ({ userId: one.userId, name: one.name, kind: "person" })), channel.members).filter((one) => matches(one.name)) : [];
    const reread = () => void cache.invalidateQueries({ queryKey: chatKeys.detail(key, podId, channel.id) });
    const add = useMutation({ mutationFn: (userId: string) => source.addMembers(podId, channel.id, { people: [userId] }), onSuccess: (saved) => keepChannel(cache, key, podId, saved) });
    const role = useMutation({ mutationFn: ({ id, owner }: { id: string; owner: boolean }) => source.updateMember(podId, channel.id, { kind: "MEMBER", id }, { role: owner ? "owner" : "member" }), onSuccess: reread });
    const remove = useMutation({
        mutationFn: (id: string) => (id === me ? source.leave(podId, channel.id) : source.removeMember(podId, channel.id, { kind: "MEMBER", id })),
        onSuccess: (_done, id) => {
            if (id === me) { refreshLists(cache, key, podId); router.dismissTo(chatsRoutes.chats(podId) as never); return; }
            reread();
        },
    });
    const join = useMutation({ mutationFn: () => source.join(podId, channel.id), onSuccess: (saved) => keepChannel(cache, key, podId, saved) });
    const group = channel.kind === "GROUP";
    const problem = add.error ?? role.error ?? remove.error ?? join.error;
    const rows = peopleRows(people.flatMap((one) => (one.person.id ? [{ userId: one.person.id, name: one.person.name ?? personName(one.person, me), email: null, role: one.role }] : [])), online, me);
    return (
        <View style={{ gap: 8 }}>
            <SearchField value={query} onChange={setQuery} placeholder="Find people" />
            {group && !channel.me.joined ? (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: TOUCH }}>
                    <Text size={15} style={{ flex: 1 }}>You are not in it.</Text>
                    <Button title="Join" height={36} size={14} busy={join.isPending} onPress={() => join.mutate()} />
                </View>
            ) : null}
            {may.manage ? (
                <Press label="Add people" onPress={() => setAdding((was) => !was)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH }}>
                    <View style={{ width: 32, height: 32, borderRadius: 16, borderWidth: 1, borderColor: color.line2, alignItems: "center", justifyContent: "center" }}><Icon name="plus" size={16} /></View>
                    <Text size={15.5}>Add people</Text>
                </Press>
            ) : null}
            {adding ? (candidates.length === 0 ? (
                <Text size={14} tone={color.ink2}>{needle ? "Nobody else in " + lemmaName + " matches." : "Everyone in " + lemmaName + " is in it already."}</Text>
            ) : candidates.map((one) => (
                <View key={one.userId} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH }}>
                    <PersonFace name={one.name} size={32} />
                    <Text size={15.5} numberOfLines={1} style={{ flex: 1 }}>{one.name}</Text>
                    <Button title="Add" kind="outline" height={34} size={13.5} disabled={add.isPending} onPress={() => one.userId && add.mutate(one.userId)} />
                </View>
            ))) : null}
            {rows.map((one) => {
                const member = channel.members.find((each) => each.person.id === one.userId);
                const contact = member?.person.kind === "CONTACT";
                const what = contact ? "Outside " + lemmaName : one.role === "owner" ? "Owner" : null;
                return (
                    <View key={one.userId} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH + 6 }}>
                        <View>
                            <PersonFace name={one.name} size={32} />
                            {one.here ? <View accessibilityLabel="Has the app open" style={{ position: "absolute", right: -1, bottom: -1, width: 10, height: 10, borderRadius: 5, backgroundColor: color.ink, borderWidth: 2, borderColor: color.paper }} /> : null}
                        </View>
                        <View style={{ flex: 1, minWidth: 0 }}>
                            <Text size={15.5} numberOfLines={1}>{one.name}{one.you ? <Text size={15.5} tone={color.ink3}> (you)</Text> : null}</Text>
                            {what ? <Text size={12.5} tone={color.ink3}>{what}</Text> : null}
                        </View>
                        {group && !contact ? (
                            <View style={{ flexDirection: "row", gap: 4 }}>
                                {may.manage && !one.you ? <TextAction label={one.role === "owner" ? "Not owner" : "Make owner"} disabled={role.isPending} onPress={() => role.mutate({ id: one.userId, owner: one.role !== "owner" })} /> : null}
                                {one.you || may.manage ? <TextAction label={one.you ? "Leave" : "Remove"} tone={color.amber} disabled={remove.isPending} onPress={() => remove.mutate(one.userId)} /> : null}
                            </View>
                        ) : null}
                    </View>
                );
            })}
            {rows.length === 0 && needle ? <Text size={14} tone={color.ink2}>Nobody in it matches.</Text> : null}
            <Trouble>{problem ? (isForbidden(problem) ? "Only who started this chat, or an editor, can change who is in it." : problemOf(problem, "Couldn’t do that.")) : null}</Trouble>
        </View>
    );
}

/* ── who answers ───────────────────────────────────────────────────── */

function Answers({ podId, channel, may, lemmaName, lemma }: { podId: string; channel: ChannelDetail; may: ChannelAbilities; lemmaName: string; lemma: { id: string; iconUrl: string | null } | null }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const agents = useAgents(podId);
    const [adding, setAdding] = useState(false);
    const front = agents.data?.find((agent) => agent.front && agent.id) ?? null;
    const seated = channel.members.filter((one) => one.person.kind === "AGENT");
    const frontSeat = front ? seated.find((one) => one.person.id === front.id) ?? null : null;
    const others = seated.filter((one) => !front || one.person.id !== front.id);
    const seatedIds = new Set(seated.map((one) => one.person.id));
    const candidates = (agents.data ?? []).filter((agent) => agent.id && !agent.front && !seatedIds.has(agent.id));
    const reread = () => void cache.invalidateQueries({ queryKey: chatKeys.detail(key, podId, channel.id) });
    const add = useMutation({ mutationFn: ({ id, answers }: { id: string; answers: AgentAnswers }) => source.addMembers(podId, channel.id, { agents: [{ id, answers }] }), onSuccess: (saved) => { keepChannel(cache, key, podId, saved); setAdding(false); } });
    const change = useMutation({ mutationFn: ({ id, answers }: { id: string; answers: AgentAnswers }) => source.updateMember(podId, channel.id, { kind: "AGENT", id }, { answers }), onSuccess: reread });
    const remove = useMutation({ mutationFn: (id: string) => source.removeMember(podId, channel.id, { kind: "AGENT", id }), onSuccess: reread });
    const pending = add.isPending || change.isPending || remove.isPending;
    const row = (id: string, name: string, answers: AgentAnswers, note: string, isSeated: boolean, removable: boolean) => (
        <View key={"a" + id} style={{ gap: 8, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: color.line }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                <SaidBy person={{ kind: "AGENT", id, name }} lemma={lemma} frontId={front?.id ?? null} size={32} />
                <View style={{ flex: 1, minWidth: 0 }}>
                    <Text size={15.5} numberOfLines={1}>{name}</Text>
                    <Text size={12.5} tone={color.ink3}>{note}</Text>
                </View>
                {may.manage && removable ? <TextAction label="Remove" tone={color.amber} disabled={pending} onPress={() => remove.mutate(id)} /> : null}
            </View>
            {may.manage ? <Choices label={"When " + name + " answers"} options={ANSWERS} value={answers} disabled={pending} onPick={(next) => (isSeated ? change.mutate({ id, answers: next }) : add.mutate({ id, answers: next }))} /> : null}
        </View>
    );
    return (
        <View style={{ gap: 4 }}>
            <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{lemmaName} is in every chat here. Others answer only where they are added. Each answers with the access of whoever asked.</Text>
            {agents.isPending ? <Loading /> : null}
            {front ? row(front.id, lemmaName, frontSeat?.answers ?? "MENTIONED", (frontSeat?.answers === "EVERY_LINE" ? "Answers every message" : "Answers when named") + " · always here", Boolean(frontSeat), false) : null}
            {others.map((one) => {
                const id = one.person.id;
                if (!id) return null;
                const name = agents.data?.find((agent) => agent.id === id)?.label ?? one.person.name ?? "Someone";
                return row(id, name, one.answers ?? "MENTIONED", one.answers === "EVERY_LINE" ? "Answers every message" : "Answers when named", true, true);
            })}
            {may.manage && candidates.length > 0 ? (
                <Press label="Add another" onPress={() => setAdding((was) => !was)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH }}>
                    <View style={{ width: 32, height: 32, alignItems: "center", justifyContent: "center" }}><Icon name="plus" size={18} /></View>
                    <Text size={15.5}>Add another</Text>
                </Press>
            ) : null}
            {adding ? candidates.map((agent) => (
                <View key={agent.id} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH + 4 }}>
                    <SaidBy person={{ kind: "AGENT", id: agent.id, name: agent.label }} lemma={lemma} frontId={front?.id ?? null} size={32} />
                    <View style={{ flex: 1, minWidth: 0 }}>
                        <Text size={15.5} numberOfLines={1}>{agent.label}</Text>
                        {agent.blurb ? <Text size={12.5} tone={color.ink3} numberOfLines={1}>{agent.blurb}</Text> : null}
                    </View>
                    <Button title="Add" kind="outline" height={34} size={13.5} disabled={pending} onPress={() => add.mutate({ id: agent.id, answers: "MENTIONED" })} />
                </View>
            )) : null}
            <Trouble>{add.error ?? change.error ?? remove.error ? problemOf(add.error ?? change.error ?? remove.error, "Couldn’t do that.") : null}</Trouble>
        </View>
    );
}

/* ── where it is mirrored ──────────────────────────────────────────── */

function Connected({ podId, channel, lemmaName }: { podId: string; channel: ChannelDetail; lemmaName: string }) {
    const [open, setOpen] = useState<string | null>(channel.places.length === 1 ? channel.places[0].id : null);
    return (
        <View style={{ gap: 6 }}>
            <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>
                {channel.kind === "GROUP" ? "Messages in these places show up here, and what you send here goes back to the place it answers." : "Where messages in this chat come from, and go back to."}
            </Text>
            {channel.places.map((place) => {
                const settled = channel.kind === "GROUP" && isGroupPlatform(place.platform);
                return (
                    <View key={place.id} style={{ gap: 8, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: color.line }}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                            <View style={{ flex: 1, minWidth: 0 }}>
                                <Text size={15.5} numberOfLines={1}>{place.title ?? platformName(place.platform)}</Text>
                                <Text size={12.5} tone={color.ink3}>{placeName({ platform: place.platform, title: null })}{place.pending ? " · being created" : place.lastInboundAt ? " · last message " + agoSaid(place.lastInboundAt) : ""}</Text>
                            </View>
                            {settled ? <TextAction label={open === place.id ? "Hide" : "Settings"} onPress={() => setOpen((was) => (was === place.id ? null : place.id))} /> : null}
                        </View>
                        {open === place.id && settled ? <SourceSettingsView podId={podId} channelId={channel.id} sourceId={place.id} lemmaName={lemmaName} /> : null}
                    </View>
                );
            })}
        </View>
    );
}

/* ── settings ──────────────────────────────────────────────────────── */

function Settings({ podId, channel, lemmaName }: { podId: string; channel: ChannelDetail; lemmaName: string }) {
    const router = useRouter();
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const update = useChange(podId, channel.id);
    const destroy = useMutation({
        mutationFn: () => source.destroy(podId, channel.id),
        onSuccess: () => {
            refreshLists(cache, key, podId);
            cache.removeQueries({ queryKey: chatKeys.detail(key, podId, channel.id) });
            router.dismissTo(chatsRoutes.chats(podId) as never);
        },
    });
    const isPrivate = channel.visibility === "PRIVATE";
    const busy = update.isPending || destroy.isPending;
    return (
        <View style={{ gap: 6 }}>
            <Row label={isPrivate ? "Make it public" : "Make it private"} action={<Button title={isPrivate ? "Make public" : "Make private"} kind="outline" height={34} size={13.5} disabled={busy} onPress={() => (isPrivate
                ? Alert.alert("Make it public?", publicWarning(channel.title, lemmaName), [{ text: "Keep it private", style: "cancel" }, { text: "Make it public", onPress: () => update.mutate({ visibility: "PUBLIC" }) }])
                : update.mutate({ visibility: "PRIVATE" }))} />}>
                {isPrivate ? "Anyone in " + lemmaName + " could find it, join, and read everything said in it." : "Only the people in it will see it. Nobody else can find it or join."}
            </Row>
            <Row label={channel.archived ? "Bring it back" : "Archive this chat"} action={<Button title={channel.archived ? "Bring back" : "Archive"} kind="outline" height={34} size={13.5} disabled={busy} onPress={() => update.mutate({ archived: !channel.archived })} />}>
                {channel.archived ? "It is listed again, and people can write in it." : "Nobody can write in it, and it leaves everyone’s list. You can bring it back."}
            </Row>
            {channel.places.length === 0 ? (
                <Row label="Delete this chat" action={<Button title="Delete" kind="outline" height={34} size={13.5} disabled={busy} onPress={() => Alert.alert("Delete " + (channel.title ?? "this chat") + "?", "Everything said in it goes, for everyone. This cannot be undone.", [
                    { text: "Keep it", style: "cancel" },
                    { text: "Delete", style: "destructive", onPress: () => destroy.mutate() },
                ])} />}>
                    Everything said in it goes, for everyone. This cannot be undone.
                </Row>
            ) : null}
            <Trouble>{update.isError ? refusal(update.error, lemmaName, "change it") : destroy.isError ? refusal(destroy.error, lemmaName, "delete it") : null}</Trouble>
        </View>
    );
}
