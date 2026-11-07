import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Alert, Keyboard, ScrollView, TextInput, View } from "react-native";
import Animated, { FadeOutDown, ReduceMotion, SlideInDown } from "react-native-reanimated";
import * as Crypto from "expo-crypto";
import { useMutation } from "@tanstack/react-query";
import { useLemma } from "../../data/hooks";
import type { PickedFile } from "../../data/io";
import { offered } from "../../thread/attachments";
import { Glyph } from "../../thread/glyphs";
import { AttachToggle, AttachTray } from "../../ui/files-in-chat";
import { haptics } from "../../ui/haptics";
import { Icon } from "../../ui/icons";
import { useIdentity } from "../../ui/identity";
import { spring } from "../../ui/motion";
import { Press } from "../../ui/press";
import { Text } from "../../ui/text";
import { color, font } from "../../ui/tokens";
import { SaidBy } from "./bits";
import { useAgents, useChatsSource } from "./hooks";
import { useMentionMenu } from "./mention-menu";
import { askingSaid, askingText, faceIdOf } from "./room";
import type { FilePart } from "./source";
import { goesTo, latestPlace, placeName } from "@web/channels/chat-copy";
import { attachmentSaid, uniqueNames, uploadProgress, type AttachmentState } from "@web/channels/compose-model";
import { draftKey, keepDraft, readDraft } from "@web/channels/drafts";
import { mentionsIn, pickedIn, removeMention, type Mentionable } from "@web/channels/mentions";
import { composerChoices, type ChannelDetail, type Line } from "@web/channels/model";
import { problemOf } from "@web/channels/problem";

/** One file on its way into a message: picked, then uploaded when the
 *  message is sent, and kept uploaded if the send fails so trying again does
 *  not upload it twice. */
interface Attached {
    key: string;
    file: PickedFile;
    uploaded: FilePart | null;
    uploading: boolean;
    failed: string | null;
}

let attachedCount = 0;

/** The Lemma each box was asking, kept beside its draft while the reader is
 *  elsewhere (the web's drafts keep only the words and the names in them). */
const askingKept = new Map<string, Mentionable>();

/** Send, in the colour of the Lemma being asked when one is: faintly
 *  before there is anything to send, fully once there is. */
function Send({ ready, onPress, label, tone, onTone, idle = color.idle, idleTone = color.idleInk }: { ready: boolean; onPress: () => void; label: string; tone: string; onTone: string; idle?: string; idleTone?: string }) {
    return (
        <Press
            label={label}
            onPress={onPress}
            disabled={!ready}
            accessibilityState={{ disabled: !ready }}
            hitSlop={8}
            style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: ready ? tone : idle, alignItems: "center", justifyContent: "center" }}
        >
            <Icon name="send" size={16} width={2} tone={ready ? onTone : idleTone} />
        </Press>
    );
}

function stateOf(one: Attached): AttachmentState {
    return one.uploaded ? "uploaded" : one.uploading ? "uploading" : one.failed ? "failed" : "waiting";
}

function Chip({ label, removeLabel, onRemove, tone = color.ink, children }: { label: string; removeLabel: string; onRemove?: () => void; tone?: string; children?: React.ReactNode }) {
    return (
        <View accessibilityLabel={label} style={{ flexDirection: "row", alignItems: "center", gap: 6, maxWidth: 240, height: 30, paddingLeft: 10, paddingRight: onRemove ? 2 : 10, borderRadius: 999, borderWidth: 1, borderColor: color.line2 }}>
            {children}
            <Text size={13.5} tone={tone} numberOfLines={1} style={{ flexShrink: 1 }}>{label}</Text>
            {onRemove ? (
                <Press label={removeLabel} onPress={onRemove} style={{ width: 26, height: 26, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="close" size={12} width={2} tone={color.ink2} />
                </Press>
            ) : null}
        </View>
    );
}

/** Writing in a chat, or in one thread of it (the web's channel `Composer`,
 *  for a phone). `@` -- typed, or the button -- offers the people and agents
 *  who can be named, and each one picked shows as a chip while their name is
 *  in the words; a named agent answers. Files go up when the message is
 *  sent, each saying how it is getting on. Where the chat is fed from
 *  outside, what is sent goes there too, unless it is kept here. A send that
 *  fails keeps everything typed and attached, and sending again is the same
 *  message. What is typed, and who was picked, is kept per chat and thread
 *  while the reader is elsewhere. */
/** The attach tray, up from behind the box on the one spring, as in a
 *  chat with a Lemma (`thread/composer.tsx`). */
const trayIn = SlideInDown.springify().damping(spring.damping ?? 20).stiffness(spring.stiffness ?? 190).mass(spring.mass ?? 1).reduceMotion(ReduceMotion.System);
const trayOut = FadeOutDown.duration(200).reduceMotion(ReduceMotion.System);

export function ChatComposer({ podId, channel, people, threadRootId, placeholder, disabledReason, onPosted, onTyping }: {
    podId: string;
    channel: ChannelDetail;
    people: readonly Mentionable[];
    threadRootId?: string;
    placeholder: string;
    disabledReason?: string | null;
    onPosted: (line: Line) => void;
    onTyping?: () => void;
}) {
    const source = useChatsSource();
    const draft = draftKey(podId, channel.id, threadRootId);
    const [kept] = useState(() => readDraft(draft));
    const [text, setText] = useState(kept.text);
    const [picked, setPicked] = useState<Mentionable[]>(kept.picked);
    const [files, setFiles] = useState<Attached[]>([]);
    const [tray, setTray] = useState(false);
    const [only, setOnly] = useState(false);
    const [place, setPlace] = useState("");
    const box = useRef<TextInput>(null);
    const lineId = useRef<string | null>(null);
    const choices = composerChoices(channel);
    const offersPlaces = choices.notes && channel.kind !== "DIRECT";
    const membersOnly = offersPlaces && only;

    useEffect(() => { keepDraft(draft, { text, picked: pickedIn(text, picked) }); }, [draft, text, picked]);

    const changeText = (value: string) => {
        lineId.current = null;
        setText(value);
        if (value.trim()) onTyping?.();
    };
    const lemma = useLemma(podId);
    const frontId = useAgents(podId).data?.find((agent) => agent.front)?.id ?? null;
    /* A Lemma picked from the strip is asked by the whole box: outlined and
       sent in its colour, its name leading the words when they go. */
    const [asking, setAskingNow] = useState<Mentionable | null>(() => askingKept.get(draft) ?? null);
    const setAsking = (who: Mentionable | null) => {
        lineId.current = null;
        if (who) askingKept.set(draft, who);
        else askingKept.delete(draft);
        setAskingNow(who);
    };
    const lemmaFace = lemma ? { id: lemma.id, iconUrl: lemma.iconUrl } : null;
    const askedId = asking ? faceIdOf({ kind: "AGENT", id: asking.id }, lemmaFace, frontId) : null;
    const asked = useIdentity(askedId ?? "");
    const mention = useMentionMenu({
        people,
        text,
        setText: changeText,
        onPicked: (who) => { lineId.current = null; setPicked((was) => [...was, who]); },
        onAsk: (who) => { setAsking(who); box.current?.focus(); },
        lemma: lemmaFace,
        frontId,
    });
    const chips = pickedIn(text, picked);

    const send = useMutation({
        mutationFn: async (body: { text: string; mentions: ReturnType<typeof mentionsIn> }) => {
            /* Named before anything uploads: the message's files go in a
               folder of its own, under names unique within it. */
            const clientMessageId = (lineId.current ??= Crypto.randomUUID());
            const names = uniqueNames(files.map((one) => one.file.name));
            const parts: FilePart[] = [];
            for (let index = 0; index < files.length; index += 1) {
                const one = files[index];
                if (one.uploaded) { parts.push(one.uploaded); continue; }
                one.uploading = true;
                one.failed = null;
                setFiles((was) => [...was]);
                try {
                    const uploaded = await source.attach(podId, channel.id, one.file, { clientMessageId, name: names[index] });
                    one.uploaded = uploaded;
                    parts.push(uploaded);
                } catch (problem) {
                    one.failed = problemOf(problem, "Couldn’t upload it.");
                    throw new Error("“" + one.file.name + "” didn’t upload. Remove it or try again.");
                } finally {
                    one.uploading = false;
                    setFiles((was) => [...was]);
                }
            }
            return source.post(podId, channel.id, {
                clientMessageId,
                text: body.text,
                mentions: body.mentions,
                files: parts,
                membersOnly: choices.notes && membersOnly,
                sourceId: membersOnly ? null : place || null,
                threadRootId: threadRootId ?? null,
            });
        },
        onSuccess: (line) => {
            lineId.current = null;
            setText("");
            setPicked([]);
            setFiles([]);
            setAsking(null);
            haptics.done();
            onPosted(line);
        },
    });

    const submit = () => {
        const typed = text.trim();
        if ((!typed && files.length === 0) || send.isPending || disabledReason) return;
        const said = asking ? askingText(typed, asking.name) : typed;
        setTray(false);
        send.mutate({ text: said, mentions: mentionsIn(said, asking ? [asking, ...picked] : picked) });
    };

    const attach = (chosen: PickedFile[]) => {
        const { take, refused } = offered(chosen);
        if (refused) Alert.alert("Not attached", refused);
        if (!take.length) return;
        lineId.current = null;
        setFiles((was) => [...was, ...take.map((file) => ({ key: String(++attachedCount), file, uploaded: null, uploading: false, failed: null }))]);
    };

    if (disabledReason) {
        return (
            <View accessibilityRole="text" style={{ paddingHorizontal: 16, paddingVertical: 14, borderTopWidth: 1, borderTopColor: color.line }}>
                <Text size={14} tone={color.ink2} style={{ lineHeight: 20 }}>{disabledReason}</Text>
            </View>
        );
    }

    const sending = send.isPending;
    const progress = uploadProgress(files.map(stateOf));
    const live = channel.places.filter((one) => !one.pending);
    const latest = latestPlace(channel.places);
    const chosen = live.find((one) => one.id === place) ?? latest;
    return (
        <View style={{ paddingHorizontal: 12, paddingTop: 6, paddingBottom: 10, gap: 6, backgroundColor: color.paper }}>
            {mention.menu}
            {asking || chips.length > 0 || files.length > 0 ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6 }} keyboardShouldPersistTaps="handled">
                    {asking ? (
                        <View accessibilityLabel={"Asking " + asking.name} style={{ flexDirection: "row", alignItems: "center", gap: 6, height: 30, paddingLeft: 4, paddingRight: 2, borderRadius: 999, backgroundColor: asked.soft }}>
                            <SaidBy person={{ kind: "AGENT", id: asking.id, name: asking.name }} lemma={lemmaFace} frontId={frontId} size={22} />
                            <Text size={13.5} tone={asked.ink} numberOfLines={1} style={{ maxWidth: 180 }}>{asking.name}</Text>
                            {sending ? <View style={{ width: 8 }} /> : (
                                <Press label={"Don’t ask " + asking.name} onPress={() => { haptics.tick(); setAsking(null); }} style={{ width: 26, height: 26, alignItems: "center", justifyContent: "center" }}>
                                    <Icon name="close" size={12} width={2} tone={asked.ink} />
                                </Press>
                            )}
                        </View>
                    ) : null}
                    {chips.map((who) => (
                        <Chip key={"m" + who.kind + who.id} label={"@" + who.name} removeLabel={"Don’t name " + who.name} onRemove={sending ? undefined : () => {
                            lineId.current = null;
                            setText(removeMention(text, who));
                            setPicked((was) => was.filter((one) => !(one.kind === who.kind && one.id === who.id)));
                        }} />
                    ))}
                    {files.map((one) => {
                        const state = stateOf(one);
                        const said = attachmentSaid(state);
                        return (
                            <Chip key={one.key} label={one.file.name + (said ? " · " + said : "")} tone={one.failed ? color.amber : color.ink} removeLabel={"Remove " + one.file.name} onRemove={sending ? undefined : () => { lineId.current = null; setFiles((was) => was.filter((other) => other.key !== one.key)); }}>
                                {one.uploading ? <ActivityIndicator size="small" color={color.ink3} /> : <Glyph name={one.file.type.startsWith("image/") ? "image" : "text"} size={13} tone={one.failed ? color.amber : color.ink2} />}
                            </Chip>
                        );
                    })}
                </ScrollView>
            ) : null}
            {progress ? <Text size={12.5} tone={color.ink3}>{progress}</Text> : null}
            {tray ? (
                <Animated.View entering={trayIn} exiting={trayOut}>
                    <AttachTray
                        chosen={new Set(files.map((one) => one.file.uri))}
                        onPicked={attach}
                        onUnpicked={(uri) => { lineId.current = null; setFiles((was) => was.filter((one) => one.file.uri !== uri)); }}
                        onRefused={(why) => Alert.alert("Not attached", why)}
                        onDone={() => setTray(false)}
                    />
                </Animated.View>
            ) : null}
            {offersPlaces ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6, alignItems: "center" }} keyboardShouldPersistTaps="handled">
                    <Press role="radio" label="Only here: nothing outside sees it" accessibilityState={{ checked: only }} disabled={sending} onPress={() => setOnly((was) => !was)} style={{ flexDirection: "row", alignItems: "center", gap: 5, height: 30, paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: only ? color.ink : color.line2, backgroundColor: only ? color.ink : "transparent" }}>
                        <Glyph name="lock" size={12} tone={only ? color.onInk : color.ink2} />
                        <Text size={13} tone={only ? color.onInk : color.ink2}>Only here</Text>
                    </Press>
                    {only ? <Text size={12.5} tone={color.ink3}>Only people here see this</Text>
                        : live.length > 1 ? live.map((one) => {
                            const on = (place || latest?.id) === one.id;
                            return (
                                <Press key={one.id} role="radio" label={(channel.kind === "CONTACT" ? "Goes to " : "Also goes to ") + placeName(one)} accessibilityState={{ checked: on }} disabled={sending} onPress={() => setPlace(one.id === latest?.id ? "" : one.id)} style={{ height: 30, paddingHorizontal: 10, borderRadius: 999, justifyContent: "center", borderWidth: 1, borderColor: on ? color.ink : color.line2 }}>
                                    <Text size={13} tone={on ? color.ink : color.ink2} numberOfLines={1}>{placeName(one)}</Text>
                                </Press>
                            );
                        })
                        : chosen ? <Text size={12.5} tone={color.ink3} numberOfLines={1}>{goesTo(channel, chosen)}</Text> : null}
                </ScrollView>
            ) : null}
            <View style={{ borderWidth: asking ? 1.5 : 1, borderColor: asking ? asked.ink : membersOnly ? color.ink2 : color.line2, borderStyle: membersOnly && !asking ? "dashed" : "solid", borderRadius: 22, paddingTop: asking ? 9.5 : 10, paddingBottom: asking ? 5.5 : 6, paddingLeft: asking ? 13.5 : 14, paddingRight: asking ? 7.5 : 8, gap: 6 }}>
                <TextInput
                    ref={box}
                    accessibilityLabel={asking ? askingSaid(asking.name) : placeholder}
                    value={text}
                    selection={mention.selection}
                    onSelectionChange={mention.onSelectionChange}
                    onChangeText={mention.onChangeText}
                    placeholder={asking ? askingSaid(asking.name) : placeholder}
                    placeholderTextColor={color.ink3}
                    multiline
                    maxLength={8000}
                    editable={!sending}
                    onFocus={() => setTray(false)}
                    style={{ fontFamily: font.regular, fontSize: 16, color: color.ink, padding: 0, maxHeight: 140, minHeight: 22 }}
                />
                <View style={{ flexDirection: "row", alignItems: "center", gap: 2 }}>
                    <View pointerEvents={sending ? "none" : "auto"} style={{ marginLeft: -6, opacity: sending ? 0.4 : 1 }}>
                        <AttachToggle open={tray} onPress={() => { if (!tray) Keyboard.dismiss(); setTray((was) => !was); }} />
                    </View>
                    <Press label="Name someone" disabled={sending} onPress={() => { mention.begin(); box.current?.focus(); }} style={{ width: 36, height: 36, alignItems: "center", justifyContent: "center" }}>
                        <Text size={18} tone={color.ink2}>@</Text>
                    </Press>
                    <View style={{ flex: 1 }} />
                    {sending ? <ActivityIndicator size="small" color={color.ink3} style={{ marginRight: 8 }} /> : null}
                    <Send ready={!sending && (text.trim().length > 0 || files.length > 0)} onPress={submit} label={asking ? "Ask " + asking.name : membersOnly ? "Send, only here" : "Send"} tone={asking ? asked.ink : color.ink} onTone={asking ? asked.onInk : color.onInk} idle={asking ? asked.soft : color.idle} idleTone={asking ? asked.ink : color.idleInk} />
                </View>
            </View>
            {send.isError ? <Text accessibilityRole="alert" size={12.5} tone={color.amber}>{problemOf(send.error, "Couldn’t send that.")} What you wrote is still here.</Text> : null}
        </View>
    );
}
