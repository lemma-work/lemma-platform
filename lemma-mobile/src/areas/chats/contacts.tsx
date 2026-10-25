import { useMemo, useState } from "react";
import { ActivityIndicator, Alert, KeyboardAvoidingView, Linking, Platform, ScrollView, Share, View } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { SafeAreaView } from "react-native-safe-area-context";
import { useCacheKey, useLemma } from "../../data/hooks";
import { copyText } from "../../thread/clipboard";
import { Loading } from "../../ui/chrome";
import { PersonFace } from "../../ui/face";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { ChatMark, Choices, EntryRow, Field, Label, Rule, SearchField, Segments, SwitchRow, TextAction, Trouble } from "./bits";
import { Header } from "./chat-view";
import { allChannels, chatKeys, useChannelList, useChatsSource, useMaySeeContacts, useMyId, useSurfaces } from "./hooks";
import { botAnswerChoices, glyphOf, isForbidden, lastSaid } from "./model";
import { chatsRoutes, type ContactsTab } from "./routes";
import { contactName, EMPTY_WIDGET_DRAFT, handleText, readOrigins, widgetAnswerChoices, widgetProblem, type Contact, type ContactReach, type NewWebWidget, type WebWidget, type WidgetAnswer, type WidgetDraft } from "@web/data/contacts";
import { channelTitle, personName, type ContactStatus } from "@web/channels/model";
import { problemOf } from "@web/channels/problem";
import { platformName } from "@web/channels/sources";
import type { Surface } from "@web/data/types";
import { CONTACT_VIEWS, STATUS_LABELS, emptyView, filterFor, needsPerson, type ChannelView } from "@web/channels/views";

/** The numbers, bots and addresses a stranger can write to (the web's
 *  `contactsSupported`). */
const ANSWERS_CONTACTS = ["WHATSAPP", "TELEGRAM", "RESEND"];

/** A Lemma's contacts: the people its numbers, bots and web chats answer who
 *  are not in it (the web's Contacts place). Three tabs, each a thing a
 *  person does here: answer who is waiting (Inbox), find somebody (People),
 *  and decide where they can write and what they can reach (Setup). With
 *  nobody yet, it opens on Setup. */
export function ContactsScreen({ podId, tab }: { podId: string; tab: ContactsTab | null }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const lemma = useLemma(podId);
    const lemmaName = lemma?.name ?? "The Lemma";
    const maySee = useMaySeeContacts(podId);
    const contacts = useQuery({ queryKey: chatKeys.contacts(key, podId), queryFn: () => source.contacts(podId), enabled: maySee, staleTime: 30_000 });
    const count = contacts.data?.length ?? null;
    const [picked, setPicked] = useState<ContactsTab | null>(tab);
    const at: ContactsTab = picked ?? (count === 0 ? "setup" : "inbox");
    return (
        <SafeAreaView edges={["top", "bottom"]} style={{ flex: 1, backgroundColor: color.paper }}>
            <KeyboardAvoidingView behavior="padding" style={{ flex: 1 }}>
                <Header podId={podId} title="Contacts" subtitle={"People outside " + lemmaName + " it answers"} glyph={null} />
                <ScrollView contentContainerStyle={{ padding: 16, gap: 10, paddingBottom: 32 }} keyboardShouldPersistTaps="handled">
                    {!maySee ? <Text size={15} tone={color.ink2}>Only {lemmaName}’s editors and admins see its contacts.</Text> : (
                        <>
                            <Segments items={[{ id: "inbox", label: "Inbox" }, { id: "people", label: "People", count }, { id: "setup", label: "Setup" }]} chosen={at} onPick={setPicked} label="Contacts" />
                            <Rule />
                            {at !== "setup" && count === 0 ? (
                                <View style={{ gap: 6 }}>
                                    <Text size={15} tone={color.ink2}>Nobody outside {lemmaName} has written yet.</Text>
                                    <TextAction label="Set up where they can write" tone={color.ink} onPress={() => setPicked("setup")} />
                                </View>
                            ) : at === "inbox" ? <Queue podId={podId} lemmaName={lemmaName} />
                                : at === "people" ? <People podId={podId} contacts={contacts.data ?? null} pending={contacts.isPending} error={contacts.error} />
                                : <Setup podId={podId} lemmaName={lemmaName} />}
                        </>
                    )}
                </ScrollView>
            </KeyboardAvoidingView>
        </SafeAreaView>
    );
}

/* ── the queue ─────────────────────────────────────────────────────── */

/** The conversations with contacts as a queue (the web's `ContactQueue`):
 *  who needs a person, who is the reader's, who nobody has taken, and all. */
function Queue({ podId, lemmaName }: { podId: string; lemmaName: string }) {
    const me = useMyId();
    const [view, setView] = useState<ChannelView>("needs");
    const [status, setStatus] = useState<ContactStatus | "">("open");
    const list = useChannelList(podId, filterFor(view, view === "needs" ? null : status || null));
    const rows = useMemo(() => allChannels(list.data), [list.data]);
    const now = Date.now();
    return (
        <View style={{ gap: 8 }}>
            <Segments items={CONTACT_VIEWS.map((one) => ({ id: one.view, label: one.view === "contacts" ? "All" : one.label }))} chosen={view} onPick={setView} label="Which conversations" />
            {view !== "needs" ? <Choices label="Where they stand" options={[{ value: "", label: "Any" }, ...(Object.keys(STATUS_LABELS) as ContactStatus[]).map((one) => ({ value: one, label: STATUS_LABELS[one] }))]} value={status} onPick={setStatus} /> : null}
            {list.isPending ? <Loading /> : rows.length === 0 ? <Text size={15} tone={color.ink2} style={{ paddingVertical: 8 }}>{emptyView(view)}</Text> : null}
            {rows.map((channel) => {
                const asks = needsPerson(channel);
                const who = channel.assignee ? (channel.assignee.id === me ? "You are answering" : personName(channel.assignee, me) + " is answering") : null;
                return (
                    <EntryRow key={channel.id} now={now} entry={{
                        key: channel.id,
                        title: channelTitle(channel, lemmaName),
                        said: asks ? "Needs a person" + (channel.attention?.reason ? ": " + channel.attention.reason : "") : [who, lastSaid(channel, me)].filter(Boolean).join(" · "),
                        at: Date.parse(channel.lastMessageAt ?? channel.createdAt) || null,
                        href: chatsRoutes.shared(podId, channel.id),
                        glyph: glyphOf(channel),
                        needsYou: asks,
                        unread: channel.unread.lines,
                        running: false,
                        channel,
                    }} />
                );
            })}
            {list.hasNextPage ? <TextAction label={list.isFetchingNextPage ? "Loading…" : "Show more"} busy={list.isFetchingNextPage} onPress={() => void list.fetchNextPage()} /> : null}
        </View>
    );
}

/* ── people ────────────────────────────────────────────────────────── */

function People({ podId, contacts, pending, error }: { podId: string; contacts: Contact[] | null; pending: boolean; error: unknown }) {
    const router = useRouter();
    const [query, setQuery] = useState("");
    const needle = query.trim().toLowerCase();
    if (pending) return <Loading />;
    if (!contacts) return <Trouble>{isForbidden(error) ? "You may not see these contacts." : "Couldn’t load the contacts."}</Trouble>;
    const shown = contacts.filter((one) => !needle || (contactName(one) + " " + one.handles.map(handleText).join(" ")).toLowerCase().includes(needle));
    return (
        <View style={{ gap: 4 }}>
            <SearchField value={query} onChange={setQuery} placeholder="Find by name, email or phone" />
            {shown.map((one) => (
                <Press key={one.id} role="link" label={contactName(one)} onPress={() => router.push(chatsRoutes.contact(podId, one.id) as never)} style={{ flexDirection: "row", alignItems: "center", gap: 12, minHeight: TOUCH + 4, paddingHorizontal: 4 }}>
                    <PersonFace name={contactName(one)} size={28} />
                    <Text size={15.5} numberOfLines={1} style={{ flex: 1, minWidth: 0 }}>
                        {contactName(one)}
                        <Text size={13.5} tone={color.ink3}>{"  " + (one.anonymous ? "Not confirmed yet" : one.handles.map(handleText).join(" · ") || "No handle")}</Text>
                    </Text>
                    <Text size={13} tone={color.ink3}>{new Date(one.createdAt).toLocaleDateString([], { day: "numeric", month: "short" })}</Text>
                </Press>
            ))}
            {shown.length === 0 ? <Text size={14.5} tone={color.ink2} style={{ paddingVertical: 8 }}>Nobody matches.</Text> : null}
        </View>
    );
}

/* ── setup ─────────────────────────────────────────────────────────── */

function Setup({ podId, lemmaName }: { podId: string; lemmaName: string }) {
    return (
        <View style={{ gap: 6 }}>
            <Bots podId={podId} lemmaName={lemmaName} />
            <Rule />
            <Widgets podId={podId} lemmaName={lemmaName} />
            <Rule />
            <Acknowledge podId={podId} lemmaName={lemmaName} />
            <Rule />
            <Reach podId={podId} lemmaName={lemmaName} />
        </View>
    );
}

/** Whom each of the Lemma's own numbers, bots and addresses answers. Lemma's
 *  shared number and bot are every Lemma's at once: they answer only people
 *  in it, so they are not a choice here (the web's `ContactBots`). */
function Bots({ podId, lemmaName }: { podId: string; lemmaName: string }) {
    const surfaces = useSurfaces(podId);
    const bots = (surfaces.data ?? []).filter((one) => ANSWERS_CONTACTS.includes(one.platform.toUpperCase()));
    return (
        <View style={{ gap: 6 }}>
            <Label>Where they write</Label>
            <Text size={13.5} tone={color.ink3}>Whom each number, bot and address answers.</Text>
            {surfaces.isPending ? <Loading /> : bots.length === 0 ? <Text size={14.5} tone={color.ink2}>No WhatsApp number, Telegram bot or email address yet.</Text> : bots.map((one) => <BotRow key={one.id} podId={podId} surface={one} lemmaName={lemmaName} />)}
            {bots.length > 0 ? <Text size={12.5} tone={color.ink3}>Lemma’s shared WhatsApp number and Telegram bot answer only people in {lemmaName}.</Text> : null}
        </View>
    );
}

function BotRow({ podId, surface, lemmaName }: { podId: string; surface: Surface; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const detail = useQuery({ queryKey: chatKeys.bot(key, podId, surface.name), queryFn: () => source.botAnswer(podId, surface.name) });
    const save = useMutation({
        mutationFn: (answer: WidgetAnswer) => source.setBotAnswer(podId, surface.name, answer),
        onSuccess: () => void cache.invalidateQueries({ queryKey: chatKeys.bot(key, podId, surface.name) }),
    });
    if (detail.isPending || (detail.data && !detail.data.own)) return null;
    const choices = botAnswerChoices(lemmaName);
    const value = save.isPending && save.variables ? save.variables : detail.data?.answer ?? "off";
    return (
        <View style={{ gap: 8, paddingVertical: 8 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 10 }}>
                <ChatMark glyph={{ kind: "platform", platform: surface.platform }} size={17} width={22} />
                <Text size={15.5} numberOfLines={1} style={{ flex: 1 }}>{surface.handle || platformName(surface.platform)}</Text>
                <Text size={13} tone={color.ink3}>{platformName(surface.platform)}</Text>
            </View>
            <Choices label={"Whom " + (surface.handle || platformName(surface.platform)) + " answers"} options={choices} value={value} disabled={save.isPending} onPick={(next) => save.mutate(next)} />
            <Text size={12.5} tone={color.ink3}>{choices.find((one) => one.value === value)?.note}</Text>
            <Trouble>{save.isError ? "Couldn’t change it. Only an editor of " + lemmaName + " can." : null}</Trouble>
        </View>
    );
}

/** The Lemma's chats on websites, and the links that share them (the web's
 *  `Widgets`): whom each answers, its link and code, a new signing secret,
 *  and taking one away. */
function Widgets({ podId, lemmaName }: { podId: string; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const widgets = useQuery({ queryKey: chatKeys.widgets(key, podId), queryFn: () => source.widgets(podId), staleTime: 60_000 });
    const reread = () => void cache.invalidateQueries({ queryKey: chatKeys.widgets(key, podId) });
    const change = useMutation({ mutationFn: ({ id, answer }: { id: string; answer: WidgetAnswer }) => source.updateWidget(podId, id, { answer }), onSuccess: reread });
    const remove = useMutation({ mutationFn: (id: string) => source.deleteWidget(podId, id), onSuccess: reread });
    const rotate = useMutation({ mutationFn: (widget: WebWidget) => source.reissueWidget(podId, widget.id).then((secret) => ({ widget, secret })) });
    const [open, setOpen] = useState<string | null>(null);
    const [making, setMaking] = useState(false);
    const [revealed, setRevealed] = useState<{ widget: WebWidget; secret: string } | null>(null);
    const choices = widgetAnswerChoices();
    return (
        <View style={{ gap: 6 }}>
            <Label right={<TextAction label="Add" tone={color.ink} onPress={() => setMaking(true)} />}>Web chat</Label>
            {widgets.isPending ? <Loading /> : !widgets.data ? <Trouble>Couldn’t load them.</Trouble> : widgets.data.length === 0 ? <Text size={14.5} tone={color.ink2}>A chat for your website, or a link to share.</Text> : null}
            {(widgets.data ?? []).map((widget) => (
                <View key={widget.id} style={{ gap: 8, paddingVertical: 8, borderBottomWidth: 1, borderBottomColor: color.line }}>
                    <Press label={(open === widget.id ? "Fold " : "Set up ") + widget.name} onPress={() => setOpen((was) => (was === widget.id ? null : widget.id))} style={{ flexDirection: "row", alignItems: "center", gap: 10, minHeight: TOUCH }}>
                        <ChatMark glyph={{ kind: "platform", platform: "WEB" }} size={17} width={22} />
                        <View style={{ flex: 1, minWidth: 0 }}>
                            <Text size={15.5} numberOfLines={1}>{widget.name}</Text>
                            <Text size={12.5} tone={color.ink3} numberOfLines={1}>{widget.allowedOrigins.length ? "On " + widget.allowedOrigins.join(", ") : "On any website"}</Text>
                        </View>
                        <Text size={13} tone={color.ink2}>{choices.find((one) => one.value === widget.answer)?.label}</Text>
                    </Press>
                    {open === widget.id ? (
                        <View style={{ gap: 10, paddingLeft: 32 }}>
                            <Choices label={"Whom " + widget.name + " answers"} options={choices} value={change.isPending && change.variables?.id === widget.id ? change.variables.answer : widget.answer} disabled={change.isPending} onPick={(answer) => change.mutate({ id: widget.id, answer })} />
                            <Text size={12.5} tone={color.ink3}>{choices.find((one) => one.value === widget.answer)?.note}</Text>
                            <Label>Share a link</Label>
                            <Text size={14} numberOfLines={1} selectable>{widget.pageUrl.replace(/^https?:\/\//, "")}</Text>
                            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                                <Button title="Copy the link" kind="outline" height={34} size={13.5} onPress={() => copyText(widget.pageUrl)} />
                                <Button title="Send it" kind="outline" height={34} size={13.5} onPress={() => void Share.share({ message: widget.pageUrl })} />
                                <Button title="Open the page" kind="outline" height={34} size={13.5} onPress={() => void Linking.openURL(widget.pageUrl)} />
                            </View>
                            <Label>Or put it on your website</Label>
                            <Text size={13} tone={color.ink2} selectable numberOfLines={3} style={{ fontFamily: Platform.OS === "ios" ? "Menlo" : "monospace" }}>{widget.embed}</Text>
                            <View style={{ flexDirection: "row" }}><Button title="Copy the code" kind="outline" height={34} size={13.5} onPress={() => copyText(widget.embed)} /></View>
                            <View style={{ flexDirection: "row", gap: 16 }}>
                                <TextAction label="New signing secret" busy={rotate.isPending} onPress={() => Alert.alert("A new secret for " + widget.name + "?", "The old one stops working at once: your site’s server has to sign with the new one, and customers it signed in with the old one start over.", [
                                    { text: "Keep it", style: "cancel" },
                                    { text: "Make a new secret", onPress: () => rotate.mutate(widget, { onSuccess: setRevealed }) },
                                ])} />
                                <TextAction label="Remove" tone={color.amber} busy={remove.isPending} onPress={() => Alert.alert("Remove " + widget.name + "?", "Pages with its code or link stop showing the chat, and anyone talking to it now is cut off. A new chat gets a new key.", [
                                    { text: "Keep it", style: "cancel" },
                                    { text: "Remove it", style: "destructive", onPress: () => remove.mutate(widget.id) },
                                ])} />
                            </View>
                        </View>
                    ) : null}
                </View>
            ))}
            <Trouble>{change.isError || remove.isError || rotate.isError ? "Couldn’t do that. Try again." : null}</Trouble>
            {making ? <WidgetSheet podId={podId} lemmaName={lemmaName} onClose={() => setMaking(false)} onMade={(made) => { setMaking(false); reread(); setTimeout(() => setRevealed({ widget: made, secret: made.signingSecret }), Platform.OS === "ios" ? 350 : 0); }} /> : null}
            {revealed ? <SecretSheet widget={revealed.widget} secret={revealed.secret} onClose={() => setRevealed(null)} /> : null}
        </View>
    );
}

function WidgetSheet({ podId, lemmaName, onClose, onMade }: { podId: string; lemmaName: string; onClose: () => void; onMade: (made: NewWebWidget) => void }) {
    const source = useChatsSource();
    const [draft, setDraft] = useState<WidgetDraft>(EMPTY_WIDGET_DRAFT);
    const [problem, setProblem] = useState<string | null>(null);
    const create = useMutation({ mutationFn: () => source.createWidget(podId, draft), onSuccess: onMade, onError: (error) => setProblem(isForbidden(error) ? "Only an editor of " + lemmaName + " can add a web chat." : problemOf(error, "Couldn’t make it.")) });
    const set = (change: Partial<WidgetDraft>) => setDraft((was) => ({ ...was, ...change }));
    return (
        <Sheet open onClose={onClose} title={"New web chat, answered by " + lemmaName} tall>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, gap: 10, paddingBottom: 8 }} keyboardShouldPersistTaps="handled">
                    <Label>Name</Label>
                    <Field accessibilityLabel="The web chat’s name" autoFocus value={draft.name} maxLength={255} placeholder="Shop chat" onChangeText={(name) => set({ name })} />
                    <Label>Who can chat</Label>
                    <Choices label="Who can chat" options={widgetAnswerChoices().filter((one) => one.value !== "off")} value={draft.answer} onPick={(answer) => set({ answer })} />
                    <Label>Websites it may be on</Label>
                    <Field accessibilityLabel="Websites it may be on" multiline value={draft.origins} placeholder="https://shop.example" autoCapitalize="none" keyboardType="url" onChangeText={(origins) => set({ origins })} style={{ minHeight: 64, textAlignVertical: "top" }} />
                    <Text size={12.5} tone={color.ink3}>{readOrigins(draft.origins).length ? "One per line." : "Leave empty for any website. The link works either way."}</Text>
                    <Trouble>{problem}</Trouble>
                    <View style={{ flexDirection: "row", gap: 8, justifyContent: "flex-end" }}>
                        <Button title="Cancel" kind="outline" onPress={onClose} />
                        <Button title="Make the chat" busy={create.isPending} onPress={() => {
                            const wrong = widgetProblem(draft);
                            setProblem(wrong);
                            if (!wrong) create.mutate();
                        }} />
                    </View>
                </ScrollView>
            </KeyboardAvoidingView>
        </Sheet>
    );
}

/** A web chat's signing secret, the one time it is shown. Only saying it has
 *  been copied closes it: a stray tap would lose it for good. */
function SecretSheet({ widget, secret, onClose }: { widget: WebWidget; secret: string; onClose: () => void }) {
    return (
        <Sheet open onClose={() => undefined} title={widget.name + ": copy the signing secret now"} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, gap: 10, paddingBottom: 8 }}>
                <Text size={14.5} style={{ lineHeight: 20 }}>Your site’s server uses this to sign in its own customers, so they are answered as contacts. It is shown only now; keep it off web pages.</Text>
                <Text size={14} selectable style={{ padding: 12, borderRadius: 12, backgroundColor: color.wash }}>{secret}</Text>
                <View style={{ flexDirection: "row" }}><Button title="Copy the secret" kind="outline" height={36} size={14} onPress={() => copyText(secret)} /></View>
                <Text size={14.5}>Then put this on the page:</Text>
                <Text size={13} selectable style={{ padding: 12, borderRadius: 12, backgroundColor: color.wash }}>{widget.embed}</Text>
                <View style={{ flexDirection: "row" }}><Button title="Copy the code" kind="outline" height={36} size={14} onPress={() => copyText(widget.embed)} /></View>
                <View style={{ flexDirection: "row", justifyContent: "flex-end" }}><Button title="I’ve copied the secret" onPress={onClose} /></View>
            </ScrollView>
        </Sheet>
    );
}

/** The line said once to a contact who writes while nobody is answering. */
function Acknowledge({ podId, lemmaName }: { podId: string; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const current = useQuery({ queryKey: chatKeys.ack(key, podId), queryFn: () => source.autoAcknowledge(podId), staleTime: 60_000 });
    const [draft, setDraft] = useState<string | null>(null);
    const set = useMutation({
        mutationFn: async (said: string | null) => { await source.setAutoAcknowledge(podId, said); return said; },
        onSuccess: (said) => { cache.setQueryData(chatKeys.ack(key, podId), said); setDraft(null); },
    });
    const value = draft ?? current.data ?? "";
    return (
        <View style={{ gap: 8 }}>
            <Label>Auto-reply</Label>
            <Text size={13.5} tone={color.ink3}>Sent once when someone writes and nobody is answering.</Text>
            <Field accessibilityLabel="Auto-reply" value={value} maxLength={500} editable={!current.isPending} placeholder={"Thanks — someone from " + lemmaName + " will reply."} onChangeText={setDraft} />
            <View style={{ flexDirection: "row", alignItems: "center", gap: 12 }}>
                <Button title="Save" kind="outline" height={36} size={14} busy={set.isPending} disabled={draft === null} onPress={() => set.mutate(value.trim() || null)} />
                {current.data && draft === null ? <TextAction label="Turn it off" disabled={set.isPending} onPress={() => set.mutate(null)} /> : null}
            </View>
            <Trouble>{set.isError ? (isForbidden(set.error) ? "Only an admin of " + lemmaName + " can change that." : "Couldn’t save it. Try again.") : current.isError ? "Couldn’t read it." : null}</Trouble>
        </View>
    );
}

/** What contacts can use of the Lemma besides what is Public: tables, each
 *  showing a contact only their own rows and the columns chosen, and
 *  functions (the web's `Reach`). A long list is cut short. */
function Reach({ podId, lemmaName }: { podId: string; lemmaName: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const reach = useQuery({ queryKey: chatKeys.reach(key, podId), queryFn: () => source.reach(podId), staleTime: 60_000 });
    const reread = () => void cache.invalidateQueries({ queryKey: chatKeys.reach(key, podId) });
    const change = useMutation({
        mutationFn: (one: { table: string; on: boolean; columns?: string[] } | { fn: string; on: boolean }) => ("table" in one ? source.setTableContactOwned(podId, one.table, one.on, one.columns) : source.setFunctionContactsInvoke(podId, one.fn, one.on)),
        onSuccess: () => { reread(); setPicking(null); },
    });
    const [picking, setPicking] = useState<string | null>(null);
    const [all, setAll] = useState(false);
    const data: ContactReach | undefined = reach.data;
    const note = (table: ContactReach["tables"][number]) => (table.perPerson ? "Each person’s own rows: can’t be shared" : table.contactOwned ? "Shows " + table.contactColumns.join(", ") : null);
    const tables = data?.tables ?? [];
    const shownTables = all ? tables : [...tables.filter((one) => one.contactOwned), ...tables.filter((one) => !one.contactOwned)].slice(0, Math.max(5, tables.filter((one) => one.contactOwned).length));
    return (
        <View style={{ gap: 6 }}>
            <Label>Shared with contacts</Label>
            <Text size={13.5} tone={color.ink3}>Tables and functions contacts can use, besides what is Public. Each contact sees only their own rows.</Text>
            {reach.isPending ? <Loading /> : !data ? <Trouble>Couldn’t load it.</Trouble> : (
                <>
                    <Label>Tables</Label>
                    {tables.length === 0 ? <Text size={14} tone={color.ink2}>No tables yet.</Text> : null}
                    {shownTables.map((table) => (
                        <View key={table.name}>
                            <SwitchRow label={table.name} note={note(table)} value={table.contactOwned || picking === table.name} disabled={change.isPending || table.perPerson} onChange={(on) => {
                                if (on) setPicking(table.name);
                                else if (picking === table.name) setPicking(null);
                                else change.mutate({ table: table.name, on: false });
                            }} />
                            {picking === table.name ? <Columns podId={podId} table={table.name} saving={change.isPending} onShare={(columns) => change.mutate({ table: table.name, on: true, columns })} onCancel={() => setPicking(null)} /> : null}
                        </View>
                    ))}
                    {tables.length > shownTables.length || all ? <TextAction label={all ? "Show fewer" : "Show " + (tables.length - shownTables.length) + " more tables"} onPress={() => setAll((was) => !was)} /> : null}
                    <Label>Functions</Label>
                    {data.functions.length === 0 ? <Text size={14} tone={color.ink2}>No functions yet.</Text> : null}
                    {data.functions.map((fn) => (
                        <SwitchRow key={fn.name} label={fn.name} note={fn.description} value={fn.contactsInvoke} disabled={change.isPending} onChange={(on) => change.mutate({ fn: fn.name, on })} />
                    ))}
                </>
            )}
            <Trouble>{change.isError ? (isForbidden(change.error) ? "Only an editor of " + lemmaName + " can change that." : "Couldn’t change it. Try again.") : null}</Trouble>
        </View>
    );
}

/** The columns a contact may read of their own rows: the person's own
 *  choice, so a column added later stays theirs alone. */
function Columns({ podId, table, saving, onShare, onCancel }: { podId: string; table: string; saving: boolean; onShare: (columns: string[]) => void; onCancel: () => void }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const columns = useQuery({ queryKey: chatKeys.columns(key, podId, table), queryFn: () => source.shareableColumns(podId, table) });
    const [chosen, setChosen] = useState<string[]>([]);
    if (columns.isPending) return <ActivityIndicator color={color.ink3} />;
    if (!columns.data) return <Trouble>Couldn’t load the columns.</Trouble>;
    const names = columns.data;
    return (
        <View style={{ gap: 8, paddingLeft: 12, paddingBottom: 8 }}>
            <Text size={13} tone={color.ink3}>What a contact may see of their own rows</Text>
            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                {names.map((name) => {
                    const on = chosen.includes(name);
                    return (
                        <Press key={name} role="button" label={name} accessibilityState={{ checked: on }} onPress={() => setChosen((was) => (on ? was.filter((one) => one !== name) : [...was, name]))} style={{ minHeight: 32, paddingHorizontal: 12, borderRadius: 999, justifyContent: "center", backgroundColor: on ? color.ink : "transparent", borderWidth: on ? 0 : 1, borderColor: color.line2 }}>
                            <Text size={14} tone={on ? color.onInk : color.ink}>{name}</Text>
                        </Press>
                    );
                })}
            </View>
            <View style={{ flexDirection: "row", gap: 8 }}>
                <Button title="Share these columns" height={36} size={14} busy={saving} disabled={chosen.length === 0} onPress={() => onShare(names.filter((name) => chosen.includes(name)))} />
                <Button title="Cancel" kind="outline" height={36} size={14} onPress={onCancel} />
            </View>
        </View>
    );
}

