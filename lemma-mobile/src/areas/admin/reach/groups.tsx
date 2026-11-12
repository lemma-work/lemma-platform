import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Linking, ScrollView, Share, View } from "react-native";
import { useRouter } from "expo-router";
import { useMe } from "../../../data/hooks";
import { routes } from "../../../data/routes";
import { copyText } from "../../../thread/clipboard";
import { Button, Press } from "../../../ui/press";
import { Sheet } from "../../../ui/sheet";
import { Num, Text } from "../../../ui/text";
import { color } from "../../../ui/tokens";
import { Copyable, Field, Group, ReadProblem, Reading, Row, Say, ToggleRow, isForbidden, problemOf } from "../ui";
import { useGroups, useJoinGroup, useReachSource, useRefreshReach } from "./hooks";
import { addGroupLabel, arrivedGroup, groupLine, groupsHeading, linkProblem, noGroupsYet, placeKey, startGroupProblem } from "./places";
import type { BotGroup, InviteLink, Surface } from "./types";
import { WHATSAPP_TITLE_MAX, answering, inviteText, linkShown, sayAnswering, slackInvite, whatsappShareUrl, type SourceSettings } from "@web/channels/sources";

/** The groups a place is in, under its settings (the web's `GroupsHere`,
 *  `BotGroupRow` and `source-sheets.tsx`): each opens its chat; a private one
 *  the reader is not in is offered to join; and the way to bring the Lemma
 *  into another, one sheet per platform, because each lets it in its own
 *  way. WhatsApp: its number starts the group and you share the link.
 *  Telegram: one tap in Telegram adds it to a group you pick. Slack: you
 *  invite it there. */
export function GroupsHere({ podId, space, surface, bot }: { podId: string; space: string; surface: Surface; bot: string }) {
    const router = useRouter();
    const [adding, setAdding] = useState(false);
    const groups = useGroups(podId, surface.name, true, false);
    const join = useJoinGroup(podId);
    const me = useMe();
    const list = groups.data ?? [];
    const open = (channelId: string) => router.push(routes.shared(podId, channelId) as never);

    return (
        <Group
            label={groupsHeading(surface.platform, bot)}
            right={(
                <Press role="link" label={`Every chat in ${space}`} onPress={() => router.push(routes.lemma(podId, "chats") as never)} style={{ minHeight: 36, justifyContent: "center", paddingLeft: 12 }}>
                    <Text size={13.5} weight="medium">All chats</Text>
                </Press>
            )}
        >
            {groups.isPending ? <Reading label="Reading its groups" /> : groups.isError ? (
                isForbidden(groups.error)
                    ? <Say>You may not see {bot}'s groups.</Say>
                    : <ReadProblem said={problemOf(groups.error, "Its groups could not be read.")} onRetry={() => void groups.refetch()} />
            ) : list.length === 0 ? (
                <Say>{noGroupsYet(surface.platform, bot)}</Say>
            ) : list.map((group, index) => {
                const title = group.title ?? `A group in ${space}`;
                const last = index === list.length - 1;
                if (!group.joined) {
                    const joining = join.isPending && join.variables === group.channelId;
                    return (
                        <View key={group.id}>
                            <Row
                                label={title}
                                hint="Private · you are not in it"
                                last={last}
                                end={<Button title={joining ? "Joining…" : "Join"} kind="outline" height={36} size={14} busy={joining} onPress={() => join.mutate(group.channelId, { onSuccess: () => open(group.channelId) })} />}
                            />
                            {join.isError && join.variables === group.channelId ? (
                                <Say tone="bad">{isForbidden(join.error) ? `Only an editor of ${space} can join it.` : problemOf(join.error, "Couldn't join it. Try again.")}</Say>
                            ) : null}
                        </View>
                    );
                }
                return (
                    <Row
                        key={group.id}
                        role="link"
                        label={title}
                        hint={groupLine(answering(group, me.data?.id ?? null), space)}
                        last={last}
                        onPress={() => open(group.channelId)}
                    />
                );
            })}
            {surface.active ? (
                <Press label={addGroupLabel(surface.platform, bot)} onPress={() => setAdding(true)} style={{ alignSelf: "flex-start", minHeight: 44, justifyContent: "center" }}>
                    <Text size={15} weight="medium">{addGroupLabel(surface.platform, bot)}</Text>
                </Press>
            ) : null}
            {adding ? (
                placeKey(surface.platform) === "WHATSAPP" ? <StartWhatsApp podId={podId} space={space} surface={surface} bot={bot} onClose={() => setAdding(false)} onOpen={(id) => { setAdding(false); open(id); }} />
                    : placeKey(surface.platform) === "SLACK" ? <AddSlack podId={podId} space={space} surface={surface} bot={bot} onClose={() => setAdding(false)} onOpen={(id) => { setAdding(false); open(id); }} />
                    : <AddTelegram podId={podId} space={space} surface={surface} bot={bot} onClose={() => setAdding(false)} onOpen={(id) => { setAdding(false); open(id); }} />
            ) : null}
        </Group>
    );
}

interface SheetProps {
    podId: string;
    space: string;
    surface: Surface;
    bot: string;
    onClose: () => void;
    /** The group's chat, by id. */
    onOpen: (channelId: string) => void;
}

/** A group that turns up after the sheet opened: what was there on first
 *  sight is the baseline, so a group already there never counts. Asked
 *  every few seconds for as long as the sheet is open. */
function useArrival(podId: string, surface: Surface, watching: boolean): BotGroup | null {
    const groups = useGroups(podId, surface.name, true, watching);
    const before = useRef<Set<string> | null>(null);
    if (before.current === null && groups.data) before.current = new Set(groups.data.map((one) => one.id));
    return arrivedGroup(before.current, groups.data ?? []);
}

function Waiting({ children }: { children: string }) {
    return (
        <View accessibilityLiveRegion="polite" style={{ flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 4 }}>
            <ActivityIndicator size="small" color={color.ink3} />
            <Text size={14} tone={color.ink2} style={{ flex: 1, lineHeight: 19 }}>{children}</Text>
        </View>
    );
}

/** A heading and the sentence it is about, with a rule under it. */
function Fact({ title, body, last = false }: { title: string; body: string; last?: boolean }) {
    return (
        <View style={{ gap: 2, paddingVertical: 10, borderBottomWidth: last ? 0 : 1, borderBottomColor: color.line }}>
            <Text size={15.5}>{title}</Text>
            <Text size={14} tone={color.ink2} style={{ lineHeight: 19 }}>{body}</Text>
        </View>
    );
}

function Steps({ steps }: { steps: string[] }) {
    return (
        <View style={{ gap: 6 }}>
            {steps.map((step, index) => (
                <View key={index} style={{ flexDirection: "row", gap: 8 }}>
                    <Num size={14.5} tone={color.ink3} style={{ minWidth: 16 }}>{index + 1}.</Num>
                    <Text size={14.5} style={{ flex: 1, lineHeight: 20 }}>{step}</Text>
                </View>
            ))}
        </View>
    );
}

function Arrived({ group, space, onOpen }: { group: BotGroup; space: string; onOpen: () => void }) {
    return (
        <View style={{ gap: 8 }}>
            <Say tone="done">{`${group.title ?? `A group in ${space}`} is connected, with its own chat here.`}</Say>
            <View style={{ flexDirection: "row" }}>
                <Button title="Open the chat" onPress={onOpen} />
            </View>
        </View>
    );
}

/* ── WhatsApp: the number starts the group ─────────────────────────── */

/** How long to keep asking WhatsApp for a group it has not confirmed. */
const CONFIRM_EVERY_MS = 2000;
const CONFIRM_FOR_MS = 60_000;

function StartWhatsApp({ podId, space, surface, bot, onClose, onOpen }: SheetProps) {
    const source = useReachSource();
    const refresh = useRefreshReach(podId);
    const me = useMe();
    const [title, setTitle] = useState("");
    const [outsiders, setOutsiders] = useState(true);
    const [busy, setBusy] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const [group, setGroup] = useState<SourceSettings | null>(null);
    const [slow, setSlow] = useState(false);

    /* Asked every two seconds until WhatsApp confirms the group and its link
       arrives; after a minute, said calmly: the group is not lost. */
    useEffect(() => {
        if (!group || group.inviteLink) return;
        let stop = false;
        const since = Date.now();
        const tick = setInterval(() => {
            if (Date.now() - since > CONFIRM_FOR_MS) {
                stop = true;
                clearInterval(tick);
                setSlow(true);
                return;
            }
            source.group(podId, group.channelId, group.id).then((next) => {
                if (stop || !next.inviteLink) return;
                stop = true;
                clearInterval(tick);
                setGroup(next);
                refresh();
            }).catch(() => undefined);
        }, CONFIRM_EVERY_MS);
        return () => { stop = true; clearInterval(tick); };
    }, [group, podId, source, refresh]);

    const start = async () => {
        const name = title.trim();
        if (!name || busy) return;
        setBusy(true);
        setProblem(null);
        try {
            setGroup(await source.startWhatsAppGroup(podId, { surfaceName: surface.name, title: name, answersOutsiders: outsiders }));
            refresh();
        } catch (error) {
            setProblem(startGroupProblem(error, bot));
        } finally {
            setBusy(false);
        }
    };

    const name = group?.title ?? title.trim();
    return (
        <Sheet open onClose={onClose} title={group?.inviteLink ? `${name} is ready` : group ? `Starting ${name}` : "Start a WhatsApp group"} tall>
            <ScrollView keyboardShouldPersistTaps="handled" contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 16 }}>
                {group?.inviteLink ? (
                    <>
                        <Group label="Share this link with the people who should join">
                            <Copyable value={group.inviteLink} label="the link" />
                            <Text size={13} tone={color.ink3} style={{ paddingTop: 6 }}>{linkShown(group.inviteLink)}</Text>
                        </Group>
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Send on WhatsApp" kind="outline" grow onPress={() => void Linking.openURL(whatsappShareUrl(name, group.inviteLink!)).catch(() => undefined)} />
                            <Button title="Share" kind="outline" grow onPress={() => void Share.share({ message: inviteText(name, group.inviteLink!) }).catch(() => undefined)} />
                        </View>
                        <Group>
                            <Fact title={`${bot} answers`} body={`When someone says “${bot}” or replies to it.`} />
                            <Fact title={`People outside ${space}`} body={sayAnswering(answering(group, me.data?.id ?? null), space)} last />
                        </Group>
                        <Say>Anyone with the link can join, so send it only to the people you mean.</Say>
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Done" kind="outline" grow onPress={onClose} />
                            <Button title="Open the chat" grow onPress={() => onOpen(group.channelId)} />
                        </View>
                    </>
                ) : group ? (
                    <>
                        {slow
                            ? <Say>{`WhatsApp is taking longer than usual. ${name} shows up in ${space}'s chats once it is ready, so you can close this.`}</Say>
                            : <Waiting>WhatsApp is making the group. Its link appears here in a few seconds.</Waiting>}
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Close" kind="outline" grow onPress={onClose} />
                            <Button title="Open the chat" grow onPress={() => onOpen(group.channelId)} />
                        </View>
                    </>
                ) : (
                    <>
                        <Field
                            label="What is it for?"
                            value={title}
                            onChange={setTitle}
                            placeholder="Acme and Northwind"
                            maxLength={WHATSAPP_TITLE_MAX}
                            hint="The group's name in WhatsApp. People see it when they join."
                            autoFocus
                        />
                        <Steps steps={[
                            `${bot} starts the group on ${surface.handle || "its WhatsApp number"}.`,
                            "You get a link. Send it to whoever should join. A group holds up to eight people.",
                            `Anyone in the group can ask ${bot} by name.`,
                        ]} />
                        <ToggleRow label={`Answer people outside ${space}`} hint="From what is Public. Anything else comes to you." value={outsiders} onChange={setOutsiders} last />
                        {problem ? <Say tone="bad">{problem}</Say> : null}
                        <View style={{ flexDirection: "row", gap: 10 }}>
                            <Button title="Cancel" kind="outline" grow onPress={onClose} />
                            <Button title={busy ? "Starting…" : "Start the group"} grow busy={busy} disabled={!title.trim()} onPress={() => void start()} />
                        </View>
                    </>
                )}
            </ScrollView>
        </Sheet>
    );
}

/* ── Telegram: one tap in Telegram adds it ─────────────────────────── */

function AddTelegram({ podId, space, surface, bot, onClose, onOpen }: SheetProps) {
    const source = useReachSource();
    const [link, setLink] = useState<InviteLink | null>(null);
    const [asking, setAsking] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const [shown, setShown] = useState(false);
    const found = useArrival(podId, surface, Boolean(link));

    const ask = async (): Promise<InviteLink | null> => {
        if (link) return link;
        setAsking(true);
        setProblem(null);
        try {
            const made = await source.telegramLink(podId, surface.name);
            setLink(made);
            return made;
        } catch (error) {
            setProblem(linkProblem(error, bot));
            return null;
        } finally {
            setAsking(false);
        }
    };

    return (
        <Sheet open onClose={onClose} title={`Add ${bot} to a Telegram group`} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 16 }}>
                <Button
                    title={asking ? "Opening Telegram…" : "Open Telegram and pick a group"}
                    height={48}
                    busy={asking}
                    onPress={() => void ask().then((made) => { if (made) void Linking.openURL(made.url).catch(() => setShown(true)); })}
                />
                <Steps steps={[
                    "Telegram asks which group. Pick one you run, or make a new one there.",
                    `${bot} joins and says hello, so everyone knows it is there.`,
                    `The group shows up here, with you answering for anyone outside ${space}.`,
                ]} />
                {problem ? <Say tone="bad">{problem}</Say> : null}
                {found ? <Arrived group={found} space={space} onOpen={() => onOpen(found.channelId)} />
                    : link ? <Waiting>Waiting for Telegram. Keep this open, or come back later.</Waiting> : null}
                {shown && link ? <Copyable value={link.url} label="the link" /> : null}
                <Say>{`On Telegram, ${bot} can join a group but not start one. It hears the messages that mention it or reply to it.`}</Say>
                <View style={{ flexDirection: "row", gap: 10 }}>
                    <Button title="Copy the link instead" kind="outline" grow onPress={() => void ask().then((made) => { if (made) { copyText(made.url); setShown(true); } })} />
                    <Button title="Done" kind="outline" grow onPress={onClose} />
                </View>
            </ScrollView>
        </Sheet>
    );
}

/* ── Slack: invite it there ────────────────────────────────────────── */

function AddSlack({ podId, space, surface, bot, onClose, onOpen }: SheetProps) {
    const command = slackInvite(surface.handle, bot);
    const found = useArrival(podId, surface, true);
    return (
        <Sheet open onClose={onClose} title={`Add ${bot} in Slack`} tall>
            <ScrollView contentContainerStyle={{ paddingHorizontal: 20, paddingBottom: 8, gap: 16 }}>
                <Group label="Send this in Slack, where it should answer" foot={`If Slack asks who answers there, pick ${bot}.`}>
                    <Copyable value={command} label="the command" mono />
                </Group>
                <Group>
                    <Fact title="Inside your company" body={`People who are not in ${space} get a private note inviting them in. Nobody else sees it.`} />
                    <Fact title="Shared with another company" body="Their people are answered from what is Public, like any group, once someone here takes it on." last />
                </Group>
                {found ? <Arrived group={found} space={space} onOpen={() => onOpen(found.channelId)} />
                    : <Waiting>Waiting for Slack. It shows up here after the next message there.</Waiting>}
                <View style={{ flexDirection: "row" }}>
                    <Button title="Done" kind="outline" grow onPress={onClose} />
                </View>
            </ScrollView>
        </Sheet>
    );
}
