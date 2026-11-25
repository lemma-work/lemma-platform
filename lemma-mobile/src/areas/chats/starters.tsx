import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, KeyboardAvoidingView, Linking, Platform, ScrollView, Share, View } from "react-native";
import { useRouter } from "expo-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../data/hooks";
import { routes } from "../../data/routes";
import { copyText } from "../../thread/clipboard";
import { Glyph } from "../../thread/glyphs";
import { Icon } from "../../ui/icons";
import { Button, Press } from "../../ui/press";
import { Sheet } from "../../ui/sheet";
import { Text } from "../../ui/text";
import { color, TOUCH } from "../../ui/tokens";
import { ChatMark, Field, SwitchRow, Trouble } from "./bits";
import { allChannels, refreshLists, useAbilities, useChannelList, useChatsSource, useMyId, useSurfaces } from "./hooks";
import { isForbidden, starterWords, statusOf } from "./model";
import { chatsRoutes } from "./routes";
import { mayStartChannel } from "@web/channels/abilities";
import { channelTitle, type Channel } from "@web/channels/model";
import { arrivedSince, answering, groupStarters, inviteMailto, inviteText, linkShown, sayAnswering, slackInvite, whatsappShareUrl, WHATSAPP_TITLE_MAX, type SourceSettings, type Starter } from "@web/channels/sources";
import type { Surface } from "@web/data/types";

/** The one place to start talking (the web's `StartChat`): a chat with the
 *  Lemma, a chat shared with people in it, or the Lemma brought into a group
 *  on a platform it is connected to. A platform it is not connected to is
 *  offered as connecting it, never as a dead option. */
export function StartSheet({ podId, lemmaName, open, onClose }: { podId: string; lemmaName: string; open: boolean; onClose: () => void }) {
    const router = useRouter();
    const surfaces = useSurfaces(podId);
    const mayShare = mayStartChannel(useAbilities(podId));
    const starters = useMemo(() => groupStarters(surfaces.data ?? []), [surfaces.data]);
    const [sheet, setSheet] = useState<{ kind: "WHATSAPP" | "TELEGRAM" | "SLACK"; surface: Surface } | null>(null);
    const go = (href: string) => { onClose(); router.push(href as never); };
    const start = (starter: Starter) => {
        if (!starter.surface || starter.state !== "ready") { go(routes.lemma(podId, "settings")); return; }
        onClose();
        const surface = starter.surface;
        setTimeout(() => setSheet({ kind: starter.platform, surface }), Platform.OS === "ios" ? 350 : 0);
    };
    const row = (key: string, title: string, note: string, glyph: React.ReactNode, onPress: () => void) => (
        <Press key={key} label={title} onPress={onPress} style={{ flexDirection: "row", alignItems: "center", gap: 14, minHeight: TOUCH + 12, paddingHorizontal: 20 }}>
            <View style={{ width: 28, alignItems: "center" }}>{glyph}</View>
            <View style={{ flex: 1, minWidth: 0 }}>
                <Text size={16}>{title}</Text>
                <Text size={13} tone={color.ink3} numberOfLines={2}>{note}</Text>
            </View>
        </Press>
    );
    return (
        <>
            <Sheet open={open} onClose={onClose} title="Start a chat">
                <ScrollView>
                    {row("chat", "New chat", "Just you and " + lemmaName + ".", <Icon name="chat" size={20} />, () => go(routes.newChat(podId)))}
                    {mayShare ? row("group", "New group", "With people in " + lemmaName + ". Anyone in it can ask " + lemmaName + ".", <Icon name="people" size={20} />, () => go(chatsRoutes.newGroup(podId))) : null}
                    {surfaces.isPending ? <View style={{ padding: 16 }}><ActivityIndicator color={color.ink3} /></View> : null}
                    {starters.map((starter) => {
                        const said = starterWords(starter, lemmaName);
                        return row(starter.platform, said.title, said.note, <ChatMark glyph={{ kind: "platform", platform: starter.platform }} size={19} />, () => start(starter));
                    })}
                </ScrollView>
            </Sheet>
            {sheet?.kind === "WHATSAPP" ? <StartWhatsApp podId={podId} lemmaName={lemmaName} surface={sheet.surface} onClose={() => setSheet(null)} /> : null}
            {sheet?.kind === "TELEGRAM" ? <AddTelegram podId={podId} lemmaName={lemmaName} surface={sheet.surface} onClose={() => setSheet(null)} /> : null}
            {sheet?.kind === "SLACK" ? <AddSlack podId={podId} lemmaName={lemmaName} surface={sheet.surface} onClose={() => setSheet(null)} /> : null}
        </>
    );
}

/** How long a sheet waits for a group to turn up before it stops asking. */
const ARRIVAL_FOR_MS = 10 * 60_000;
const LISTED = { archived: false };

/** A group fed from this platform that turns up after the sheet opened: the
 *  list read every few seconds while `watching`, against what was there on
 *  first sight (the web's `useArrival`). */
function useArrival(podId: string, platform: string, watching: boolean): Channel | null {
    const [found, setFound] = useState<Channel | null>(null);
    const [gaveUp, setGaveUp] = useState(false);
    useEffect(() => {
        if (!watching || found) return;
        const timer = setTimeout(() => setGaveUp(true), ARRIVAL_FOR_MS);
        return () => { clearTimeout(timer); setGaveUp(false); };
    }, [watching, found]);
    const list = useChannelList(podId, LISTED, watching && !found && !gaveUp ? 3_000 : null);
    const channels = list.data ? allChannels(list.data) : null;
    const before = useRef<Set<string> | null>(null);
    if (before.current === null && channels) before.current = new Set(channels.map((channel) => channel.id));
    const arrived = before.current && channels ? arrivedSince(before.current, channels, platform) : null;
    useEffect(() => { if (!found && arrived) setFound(arrived); }, [arrived, found]);
    return found;
}

function Waiting({ children }: { children: string }) {
    return (
        <View accessibilityRole="progressbar" style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 8 }}>
            <ActivityIndicator size="small" color={color.ink3} />
            <Text size={14} tone={color.ink2} style={{ flex: 1, lineHeight: 20 }}>{children}</Text>
        </View>
    );
}

function Arrived({ channel, lemmaName, onOpen }: { channel: Channel; lemmaName: string; onOpen: () => void }) {
    return (
        <View style={{ flexDirection: "row", alignItems: "center", gap: 10, paddingVertical: 8 }}>
            <Icon name="check" size={18} />
            <Text size={14.5} style={{ flex: 1 }}>{channelTitle(channel, lemmaName)} is connected, with its own chat here.</Text>
            <Button title="Open" height={36} size={14} onPress={onOpen} />
        </View>
    );
}

/* ── WhatsApp: the number makes the group ──────────────────────────── */

const CONFIRM_EVERY_MS = 2_000;
const CONFIRM_FOR_MS = 60_000;

function startProblem(error: unknown, lemmaName: string): string {
    const status = statusOf(error);
    if (status === 502) return "WhatsApp would not start the group. Try again shortly.";
    if (isForbidden(error)) return "Only someone who can change " + lemmaName + " can start a group on this number.";
    if ((status === 409 || status === 422) && error instanceof Error && error.message.trim()) return error.message.trim();
    return "That didn’t work. Try again.";
}

function StartWhatsApp({ podId, lemmaName, surface, onClose }: { podId: string; lemmaName: string; surface: Surface; onClose: () => void }) {
    const router = useRouter();
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const me = useMyId();
    const [title, setTitle] = useState("");
    const [outsiders, setOutsiders] = useState(true);
    const [group, setGroup] = useState<SourceSettings | null>(null);
    const [slow, setSlow] = useState(false);
    const start = useMutation({
        mutationFn: () => source.startWhatsAppGroup(podId, { surfaceName: surface.name, title: title.trim(), answersOutsiders: outsiders }),
        onSuccess: (made) => { setGroup(made); refreshLists(cache, key, podId); },
    });
    /* Asked every two seconds until WhatsApp confirms the group and its link
       arrives; after a minute, said calmly: the group is in the list. */
    useEffect(() => {
        if (!group || group.inviteLink) return;
        let stop = false;
        const since = Date.now();
        const tick = setInterval(() => {
            if (Date.now() - since > CONFIRM_FOR_MS) { stop = true; clearInterval(tick); setSlow(true); return; }
            source.source(podId, group.channelId, group.id).then((next) => {
                if (stop || !next.inviteLink) return;
                stop = true;
                clearInterval(tick);
                setGroup(next);
                refreshLists(cache, key, podId);
            }).catch(() => undefined);
        }, CONFIRM_EVERY_MS);
        return () => { stop = true; clearInterval(tick); };
    }, [group, podId, source, cache, key]);
    const openGroup = (channelId: string) => { onClose(); router.push(chatsRoutes.shared(podId, channelId) as never); };
    const name = group?.title ?? title.trim();
    return (
        <Sheet open onClose={onClose} title={group?.inviteLink ? name + " is ready" : group ? "Creating " + name : "Start a WhatsApp group"} tall>
            <KeyboardAvoidingView behavior={Platform.OS === "ios" ? "padding" : undefined}>
                <ScrollView contentContainerStyle={{ paddingHorizontal: 20, gap: 12, paddingBottom: 8 }} keyboardShouldPersistTaps="handled">
                    {group?.inviteLink ? (
                        <>
                            <Text size={13} tone={color.ink3}>Share this link with the people who should join</Text>
                            <Text size={15} numberOfLines={2}>{linkShown(group.inviteLink)}</Text>
                            <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
                                <Button title="Copy the link" height={38} size={14} onPress={() => copyText(group.inviteLink ?? "")} />
                                <Button title="Send it" kind="outline" height={38} size={14} onPress={() => void Share.share({ message: inviteText(name, group.inviteLink ?? "") })} />
                                <Button title="On WhatsApp" kind="outline" height={38} size={14} onPress={() => void Linking.openURL(whatsappShareUrl(name, group.inviteLink ?? ""))} />
                                <Button title="By email" kind="outline" height={38} size={14} onPress={() => void Linking.openURL(inviteMailto(name, group.inviteLink ?? ""))} />
                            </View>
                            <View style={{ gap: 4, paddingTop: 4 }}>
                                <Text size={14.5}>{lemmaName} answers when someone says “{lemmaName}” or replies to it.</Text>
                                <Text size={14.5} tone={color.ink2}>People outside {lemmaName}: {sayAnswering(answering(group, me), lemmaName)}.</Text>
                            </View>
                            <Text size={13} tone={color.ink3}>Anyone with the link can join, so send it only to the people you mean.</Text>
                            <View style={{ flexDirection: "row", gap: 8, justifyContent: "flex-end" }}>
                                <Button title="Done" kind="outline" onPress={onClose} />
                                <Button title="Open the chat" onPress={() => openGroup(group.channelId)} />
                            </View>
                        </>
                    ) : group ? (
                        <>
                            {slow ? <Text size={14.5} tone={color.ink2}>WhatsApp is taking longer than usual. {name} shows up in the Lemma’s chats once it is ready, so you can close this.</Text>
                                : <Waiting>WhatsApp is making the group. Its link appears here in a few seconds.</Waiting>}
                            <View style={{ flexDirection: "row", gap: 8, justifyContent: "flex-end" }}>
                                <Button title="Close" kind="outline" onPress={onClose} />
                                <Button title="Open the chat" onPress={() => openGroup(group.channelId)} />
                            </View>
                        </>
                    ) : (
                        <>
                            <Text size={13} tone={color.ink3}>What is it for?</Text>
                            <Field accessibilityLabel="The group’s name" autoFocus value={title} maxLength={WHATSAPP_TITLE_MAX} placeholder="Acme × Northwind" onChangeText={setTitle} />
                            <Text size={13} tone={color.ink3}>This is the group’s name in WhatsApp. People see it when they join.</Text>
                            <View style={{ gap: 6, paddingTop: 4 }}>
                                <Text size={14.5} style={{ lineHeight: 20 }}>1. {lemmaName} creates the group on {surface.handle || "its WhatsApp number"}.</Text>
                                <Text size={14.5} style={{ lineHeight: 20 }}>2. You get a link. Send it to whoever should join. A group holds up to eight people.</Text>
                                <Text size={14.5} style={{ lineHeight: 20 }}>3. Anyone in the group can ask {lemmaName} by name.</Text>
                            </View>
                            <SwitchRow label={"Answer people outside " + lemmaName} note="From what is Public. Anything else comes to you." value={outsiders} onChange={setOutsiders} />
                            <Trouble>{start.isError ? startProblem(start.error, lemmaName) : null}</Trouble>
                            <View style={{ flexDirection: "row", gap: 8, justifyContent: "flex-end" }}>
                                <Button title="Cancel" kind="outline" onPress={onClose} />
                                <Button title="Start the group" busy={start.isPending} disabled={!title.trim()} onPress={() => start.mutate()} />
                            </View>
                        </>
                    )}
                </ScrollView>
            </KeyboardAvoidingView>
        </Sheet>
    );
}

/* ── Telegram: one tap in Telegram adds the Lemma ──────────────────── */

function linkProblem(error: unknown, lemmaName: string): string {
    if (isForbidden(error)) return "Only someone who can change " + lemmaName + " can add it to a group.";
    const status = statusOf(error);
    if ((status === 409 || status === 422) && error instanceof Error && error.message.trim()) return error.message.trim() + ".";
    return "Couldn’t make the link. Try again.";
}

function AddTelegram({ podId, lemmaName, surface, onClose }: { podId: string; lemmaName: string; surface: Surface; onClose: () => void }) {
    const router = useRouter();
    const source = useChatsSource();
    const [shown, setShown] = useState(false);
    const link = useMutation({ mutationFn: () => source.telegramLink(podId, surface.name) });
    const found = useArrival(podId, "TELEGRAM", link.isSuccess || link.isPending);
    const open = () => link.mutate(undefined, { onSuccess: (made) => void Linking.openURL(made.url) });
    const copyInstead = () => {
        const made = link.data;
        if (made) { setShown(true); copyText(made.url); return; }
        link.mutate(undefined, { onSuccess: (got) => { setShown(true); copyText(got.url); } });
    };
    const steps = [
        "Telegram asks which group. Pick one you run, or make a new one there.",
        lemmaName + " joins and says hello, so everyone knows it is there.",
        "The group shows up in " + lemmaName + "’s chats, with you answering for anyone outside it.",
    ];
    return (
        <Sheet open onClose={onClose} title={"Add " + lemmaName + " to a Telegram group"} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, gap: 12, paddingBottom: 8 }}>
                <Button title={link.isPending ? "Opening Telegram…" : "Open Telegram and pick a group"} busy={link.isPending} onPress={open} />
                {steps.map((step, index) => (
                    <View key={index} style={{ flexDirection: "row", gap: 10 }}>
                        {found || (index === 0 && link.isSuccess) ? <Icon name="check" size={16} tone={color.ink2} /> : <Text size={14.5} tone={color.ink3}>{index + 1}.</Text>}
                        <Text size={14.5} style={{ flex: 1, lineHeight: 20 }}>{step}</Text>
                    </View>
                ))}
                <Trouble>{link.isError ? linkProblem(link.error, lemmaName) : null}</Trouble>
                {found ? <Arrived channel={found} lemmaName={lemmaName} onOpen={() => { onClose(); router.push(chatsRoutes.shared(podId, found.id) as never); }} />
                    : link.isSuccess ? <Waiting>Waiting for Telegram. Keep this open, or come back later.</Waiting> : null}
                {shown && link.data ? <Text size={14} tone={color.ink2} selectable>{linkShown(link.data.url)} · copied</Text> : null}
                <Text size={13} tone={color.ink3} style={{ lineHeight: 18 }}>On Telegram, {lemmaName} can join a group but not start one. It hears the messages that name it or reply to it.</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
                    <Press label="Copy the link instead" onPress={copyInstead} style={{ minHeight: 40, justifyContent: "center", flex: 1 }}>
                        <Text size={14} tone={color.ink2}>Copy the link instead</Text>
                    </Press>
                    <Button title="Done" kind="outline" onPress={onClose} />
                </View>
            </ScrollView>
        </Sheet>
    );
}

/* ── Slack: invite it in the channel ───────────────────────────────── */

function AddSlack({ podId, lemmaName, surface, onClose }: { podId: string; lemmaName: string; surface: Surface; onClose: () => void }) {
    const router = useRouter();
    const command = slackInvite(surface.handle, lemmaName);
    const found = useArrival(podId, "SLACK", true);
    const [copied, setCopied] = useState(false);
    return (
        <Sheet open onClose={onClose} title={"Add " + lemmaName + " to a Slack channel"} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, gap: 12, paddingBottom: 8 }}>
                <Text size={13} tone={color.ink3}>In the Slack channel, send this</Text>
                <View style={{ flexDirection: "row", alignItems: "center", gap: 10, padding: 12, borderRadius: 12, backgroundColor: color.wash }}>
                    <Glyph name="terminal" size={16} tone={color.ink2} />
                    <Text size={15} selectable style={{ flex: 1 }}>{command}</Text>
                    <Button title={copied ? "Copied" : "Copy"} kind="outline" height={34} size={13.5} onPress={() => { copyText(command); setCopied(true); }} />
                </View>
                <Text size={13} tone={color.ink3}>If Slack asks who answers there, pick {lemmaName}.</Text>
                <View style={{ gap: 4 }}>
                    <Text size={14.5}>A channel inside your company</Text>
                    <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>People who are not in {lemmaName} get a private note inviting them in. The channel sees nothing.</Text>
                </View>
                <View style={{ gap: 4 }}>
                    <Text size={14.5}>A channel shared with another company</Text>
                    <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>Their people are answered from what is Public, like any group, once someone here takes it on.</Text>
                </View>
                {found ? <Arrived channel={found} lemmaName={lemmaName} onOpen={() => { onClose(); router.push(chatsRoutes.shared(podId, found.id) as never); }} />
                    : <Waiting>Waiting for Slack. The channel shows up here after the next message in it.</Waiting>}
                <View style={{ flexDirection: "row", justifyContent: "flex-end" }}>
                    <Button title="Done" kind="outline" onPress={onClose} />
                </View>
            </ScrollView>
        </Sheet>
    );
}
