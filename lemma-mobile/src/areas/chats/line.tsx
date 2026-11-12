import { memo, useState } from "react";
import { ActivityIndicator, Linking, View, type LayoutChangeEvent } from "react-native";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useCacheKey } from "../../data/hooks";
import { shareFile } from "../../data/io";
import { Glyph } from "../../thread/glyphs";
import { AudioRow, DocumentCard, PhotoGrid, documentLine, type Photo } from "../../ui/files-in-chat";
import { Markdown } from "../../ui/markdown";
import { Press } from "../../ui/press";
import { useIdentity } from "../../ui/identity";
import { Num, Text } from "../../ui/text";
import { color } from "../../ui/tokens";
import { BreathingDot, PlatformMark, SaidBy, Trouble } from "./bits";
import { chatKeys, lineKeys, useChatsSource } from "./hooks";
import { answeringWhom, removedNote, threadSaid, viaOf, whoSaid } from "./model";
import { arrivalOf, faceIdOf, saidOn } from "./room";
import { WaitingRow } from "./waiting";
import { sayReason } from "@web/channels/deliveries";
import type { Answering } from "@web/channels/live";
import {
    ASKER_ONLY_NOTE,
    answerSummary,
    asWaitingQuestion,
    clockTime,
    isMine,
    personName,
    questionState,
    sayQuestion,
    withheldNote,
    type ChannelDetail,
    type Line,
    type Part,
} from "@web/channels/model";
import { extraLinks, isInlineImage, pieces, sizeShown } from "@web/channels/parts-model";
import { problemOf } from "@web/channels/problem";
import { platformName } from "@web/channels/sources";

type FilePart = Extract<Part, { type: "file" }>;

export interface LineContext {
    podId: string;
    channel: ChannelDetail;
    lemma: { id: string; name: string; iconUrl: string | null } | null;
    frontId: string | null;
    me: string | null;
    mayWrite: boolean;
    agentName: (agentId: string | null) => string;
    onThread?: (line: Line) => void;
    onHold: (line: Line) => void;
    onQuote?: (lineId: string) => void;
}

/** One message of a chat (the web's `LineView`, for a phone): who said it
 *  and for whom, where and when, what it said -- or that it was kept to
 *  someone, or deleted -- what it shares, where one's own went outside, the
 *  question it asks when it waits on the reader, and its thread. Holding it
 *  offers reply, copy, change, delete and share (`actions.tsx`). */
export const LineView = memo(function LineView({ line, context, continued = false, quote = null, answering = [], highlighted = false, inThread = false, onLayout }: {
    line: Line;
    context: LineContext;
    continued?: boolean;
    quote?: Line | null;
    answering?: Answering[];
    highlighted?: boolean;
    inThread?: boolean;
    onLayout?: (event: LayoutChangeEvent) => void;
}) {
    const { podId, channel, me } = context;
    const agent = line.sender.kind === "AGENT";
    const withheld = withheldNote(line, me);
    const who = whoSaid(line, channel, me);
    const whom = answeringWhom(line, channel, me);
    const via = viaOf(line, channel);
    /* Where it was said, as a glyph by the name; the place's own name too
       only when the chat is fed from more than one. */
    const platform = saidOn(line.sourceId, channel.places);
    const places = channel.places.filter((one) => !one.pending).length;
    const time = clockTime(line.createdAt);
    const label = who + (platform ? ", on " + platformName(platform) : "") + ", " + time + ". " + (line.deleted ? removedNote(line, me) : withheld ?? line.text ?? "");
    /* A Lemma's message carries its colour as a thin bar at its edge. */
    const faceId = faceIdOf(line.sender, context.lemma, context.frontId);
    const identity = useIdentity(faceId ?? "");
    /* The chat's own colour, for what plays in it: its Lemma's. */
    const lemmaLook = useIdentity(context.lemma?.id ?? "");
    const place = context.lemma ? lemmaLook : { ink: color.ink, onInk: color.onInk };
    return (
        <Press
            role="button"
            label={label}
            accessibilityHint="Hold for reply, copy and more"
            onLongPress={() => context.onHold(line)}
            delayLongPress={300}
            onLayout={onLayout}
            style={{ flexDirection: "row", gap: 10, paddingHorizontal: 16, paddingTop: continued ? 2 : 10, paddingBottom: 2, backgroundColor: highlighted ? color.violetWash : "transparent" }}
        >
            {faceId ? <View pointerEvents="none" style={{ position: "absolute", left: 0, top: continued ? 0 : 8, bottom: 0, width: 2, backgroundColor: identity.ink }} /> : null}
            <View style={{ width: 30, alignItems: "center" }}>
                {continued ? null : <SaidBy person={line.sender} lemma={context.lemma} frontId={context.frontId} size={30} />}
            </View>
            <View style={{ flex: 1, minWidth: 0, gap: 4, opacity: line.deleted || line.withheld ? 0.85 : 1 }}>
                {!continued ? (
                    <View style={{ flexDirection: "row", alignItems: "baseline", flexWrap: "wrap", columnGap: 8, rowGap: 2 }}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 5, flexShrink: 1 }}>
                            <Text size={14.5} weight="medium" numberOfLines={1} style={{ flexShrink: 1 }}>{who}</Text>
                            {platform ? <PlatformMark platform={platform} size={13} /> : null}
                        </View>
                        {whom ? <Text size={12.5} tone={color.ink3}>{whom}</Text> : null}
                        {line.sender.kind === "CONTACT" && channel.kind !== "CONTACT" ? <Tag text="Contact" /> : null}
                        {line.membersOnly ? <Tag text="Only here" /> : null}
                        <Num size={12} tone={color.ink3}>{time}</Num>
                        {via && (!platform || places > 1) ? <Text size={12} tone={color.ink3} numberOfLines={1}>{platform ? via.replace(/ \([^)]*\)$/, "") : "via " + via}</Text> : null}
                    </View>
                ) : null}
                {quote ? (
                    <Press label={"Go to the message this answers, from " + personName(quote.sender, me)} onPress={() => context.onQuote?.(quote.id)} style={{ borderLeftWidth: 2, borderLeftColor: color.line2, paddingLeft: 8, paddingVertical: 2 }}>
                        <Text size={13} tone={color.ink3} numberOfLines={2}>
                            <Text size={13} tone={color.ink2}>{personName(quote.sender, me)} </Text>
                            {quote.deleted ? "This message was deleted." : (quote.text ?? "").replace(/\s+/g, " ").slice(0, 140)}
                        </Text>
                    </Press>
                ) : null}

                {line.deleted ? (
                    <Text size={14.5} tone={color.ink3}>{removedNote(line, me)}</Text>
                ) : withheld ? (
                    <Text size={14.5} tone={color.ink3}>{withheld}</Text>
                ) : line.text ? (
                    agent ? <Markdown text={line.text} size={15.5} copyable={false} /> : <LineText text={line.text} parts={line.parts} me={me} />
                ) : null}
                {line.editedAt && !line.deleted ? <Text size={12} tone={color.ink3}>(edited)</Text> : null}
                {!line.deleted && !withheld ? <LineParts podId={podId} channelId={channel.id} line={line} tone={place.ink} onTone={place.onInk} /> : null}
                {line.canShare ? <AskerOnly podId={podId} channelId={channel.id} line={line} /> : null}
                {line.delivered && isMine(line, me) ? <Deliveries podId={podId} channel={channel} line={line} canRetry={context.mayWrite} /> : null}
                {line.interaction ? <Question podId={podId} channel={channel} line={line} me={me} /> : null}
                {!inThread && context.onThread && line.replyCount > 0 && !line.threadRootId ? (
                    <Press role="link" label={"Open the thread: " + threadSaid(line.replyCount, line.lastReplyAt)} onPress={() => context.onThread?.(line)} style={{ flexDirection: "row", alignItems: "center", gap: 6, minHeight: 32, alignSelf: "flex-start" }}>
                        <Glyph name="tree" size={14} tone={color.ink2} />
                        <Text size={13.5} tone={color.ink}>{threadSaid(line.replyCount, line.lastReplyAt)}</Text>
                    </Press>
                ) : null}
                {answering.map((one) => (
                    <View key={one.messageId + (one.agentId ?? "")} style={{ gap: 4, paddingTop: 2 }}>
                        <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                            <BreathingDot tone={color.violet} />
                            <Text size={13} tone={color.ink2}>{context.agentName(one.agentId)} is answering…</Text>
                        </View>
                        {one.text ? <View style={{ opacity: 0.7 }}><Markdown text={one.text} size={15} copyable={false} /></View> : null}
                    </View>
                ))}
            </View>
        </Press>
    );
});

function Tag({ text }: { text: string }) {
    return (
        <View style={{ backgroundColor: color.wash, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 1 }}>
            <Text size={11.5} tone={color.ink2}>{text}</Text>
        </View>
    );
}

/** A person's message as typed, with who it names set apart and its
 *  addresses as links. A name of the reader's is violet: it is for them. */
export function LineText({ text, parts, me }: { text: string; parts: readonly Part[]; me: string | null }) {
    return (
        <Text size={15.5} selectable style={{ lineHeight: 22 }}>
            {pieces(text, parts).map((piece, index) => piece.kind === "mention" ? (
                <Text key={index} size={15.5} weight="medium" tone={piece.who.kind === "MEMBER" && piece.who.id === me ? color.violetInk : color.ink}>{piece.text}</Text>
            ) : piece.kind === "link" ? (
                <Text key={index} size={15.5} tone={color.ink} style={{ textDecorationLine: "underline" }} onPress={() => void Linking.openURL(piece.url)} accessibilityRole="link">{piece.text}</Text>
            ) : piece.text)}
        </Text>
    );
}

/** The files a message shares, each read through the message and drawn as a
 *  Lemma's chat draws them (`ui/files-in-chat`): pictures as one grid that
 *  opens whole screen, a recording played in place, anything else a card
 *  handed to another app -- and the links it carries that its words do not
 *  show. `tone` is the chat's colour, for the play button. */
function LineParts({ podId, channelId, line, tone, onTone }: { podId: string; channelId: string; line: Line; tone: string; onTone: string }) {
    const files = line.parts.filter((part): part is FilePart => part.type === "file");
    const pictures = files.filter((file) => file.fileId !== null && isInlineImage(file.mimeType));
    const others = files.filter((file) => !pictures.includes(file));
    const links = extraLinks(line.text ?? "", line.parts);
    if (!files.length && !links.length) return null;
    return (
        <View style={{ gap: 6, paddingTop: 2 }}>
            {pictures.length ? <LinePhotos podId={podId} channelId={channelId} lineId={line.id} files={pictures} /> : null}
            {others.map((file) => <LineFile key={file.fileId ?? file.path} podId={podId} channelId={channelId} lineId={line.id} file={file} tone={tone} onTone={onTone} />)}
            {links.map((link) => (
                <Press key={link.url} role="link" label={"Open " + (link.label ?? link.url)} onPress={() => void Linking.openURL(link.url)} style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 36, alignSelf: "flex-start", paddingHorizontal: 10, borderRadius: 10, borderWidth: 1, borderColor: color.line2 }}>
                    <Glyph name="external" size={14} tone={color.ink2} />
                    <Text size={14} numberOfLines={1} style={{ maxWidth: 240 }}>{link.label ?? link.url.replace(/^https?:\/\//, "")}</Text>
                </Press>
            ))}
        </View>
    );
}

const AUDIO = /\.(m4a|mp3|wav|aac|ogg|opus|caf|amr)$/i;
const SHEETS = /\.(csv|tsv|xlsx|xls|numbers|ods)$/i;

function isAudio(file: FilePart): boolean {
    return (file.mimeType?.toLowerCase().startsWith("audio/") ?? false) || AUDIO.test(file.name);
}

function lineFileQuery(source: ReturnType<typeof useChatsSource>, key: string, podId: string, channelId: string, lineId: string, file: FilePart) {
    return {
        queryKey: chatKeys.file(key, podId, file.fileId ?? file.path),
        enabled: file.fileId !== null,
        staleTime: Infinity,
        retry: false,
        queryFn: () => source.lineFile(podId, channelId, lineId, { fileId: file.fileId as string, name: file.name }),
    };
}

/** Pictures sent together, as one grid. */
function LinePhotos({ podId, channelId, lineId, files }: { podId: string; channelId: string; lineId: string; files: FilePart[] }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const read = useQueries({ queries: files.map((file) => lineFileQuery(source, key, podId, channelId, lineId, file)) });
    const photos: Photo[] = files.map((file, index) => ({ key: file.fileId ?? file.path, uri: read[index]?.data ?? null, name: file.name, type: file.mimeType ?? undefined }));
    const broke = read.filter((one) => one.isError).length;
    return (
        <View style={{ alignSelf: "stretch", maxWidth: 320, gap: 4 }}>
            <PhotoGrid photos={photos} label={files.length === 1 ? files[0].name : "Pictures"} />
            {broke ? <Trouble>{broke === files.length ? "Couldn’t show " + (files.length === 1 ? "this picture." : "these pictures.") : "Couldn’t show " + broke + " of them."}</Trouble> : null}
        </View>
    );
}

/** A recording, played in place; anything else, a card that hands it to
 *  another app. One the server no longer has is only named. */
function LineFile({ podId, channelId, lineId, file, tone, onTone }: { podId: string; channelId: string; lineId: string; file: FilePart; tone: string; onTone: string }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const audio = isAudio(file);
    const played = useQuery({ ...lineFileQuery(source, key, podId, channelId, lineId, file), enabled: audio && file.fileId !== null });
    const hand = useMutation({
        mutationFn: async () => {
            const uri = await source.lineFile(podId, channelId, lineId, { fileId: file.fileId as string, name: file.name });
            await shareFile(uri, file.mimeType ?? undefined);
        },
    });
    if (!file.fileId) {
        return (
            <View style={{ flexDirection: "row", alignItems: "center", gap: 8, minHeight: 36, alignSelf: "flex-start" }}>
                <Glyph name="text" size={14} tone={color.ink3} />
                <Text size={14} tone={color.ink2}>{file.name}</Text>
            </View>
        );
    }
    if (audio) {
        return (
            <View style={{ alignSelf: "stretch", maxWidth: 320, gap: 4 }}>
                <AudioRow uri={played.data ?? null} name={file.name} seed={file.fileId} tone={tone} onTone={onTone} />
                {played.isError ? <Trouble>{problemOf(played.error, "Couldn’t get the recording.")}</Trouble> : null}
            </View>
        );
    }
    return (
        <View style={{ alignSelf: "stretch", maxWidth: 320, gap: 4 }}>
            <DocumentCard
                name={file.name}
                line={documentLine(file.name, null, sizeShown(file.size) ?? "")}
                preview={null}
                loading={hand.isPending}
                glyph={SHEETS.test(file.name) ? "sheet" : "page"}
                grows={false}
                onOpen={() => hand.mutate()}
            />
            <Trouble>{hand.isError ? problemOf(hand.error, "Couldn’t get the file.") : null}</Trouble>
        </View>
    );
}

/** An answer made with the reader's own access, which only they read in full
 *  until they share it with everyone here. */
function AskerOnly({ podId, channelId, line }: { podId: string; channelId: string; line: Line }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const [asking, setAsking] = useState(false);
    const share = useMutation({
        mutationFn: () => source.share(podId, channelId, line.id),
        onSuccess: () => {
            setAsking(false);
            for (const held of lineKeys(key, podId, channelId, line)) void cache.invalidateQueries({ queryKey: held });
        },
    });
    return (
        <View style={{ gap: 6, paddingTop: 2 }}>
            <View style={{ flexDirection: "row", alignItems: "center", gap: 6 }}>
                <Glyph name="lock" size={13} tone={color.ink3} />
                <Text size={13} tone={color.ink3} style={{ flex: 1 }}>{ASKER_ONLY_NOTE}</Text>
            </View>
            {asking ? (
                <View style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
                    <Text size={13.5} tone={color.ink2}>Everyone in this chat will read it.</Text>
                    <Press label="Share it" disabled={share.isPending} onPress={() => share.mutate()} style={{ minHeight: 32, justifyContent: "center" }}>
                        {share.isPending ? <ActivityIndicator size="small" color={color.ink3} /> : <Text size={14} weight="medium">Share</Text>}
                    </Press>
                    <Press label="Keep it to me" onPress={() => setAsking(false)} style={{ minHeight: 32, justifyContent: "center" }}>
                        <Text size={14} tone={color.ink2}>Keep it to me</Text>
                    </Press>
                </View>
            ) : (
                <Press label="Share with everyone here" onPress={() => { share.reset(); setAsking(true); }} style={{ minHeight: 32, justifyContent: "center", alignSelf: "flex-start" }}>
                    <Text size={14} tone={color.ink}>Share with everyone here</Text>
                </Press>
            )}
            <Trouble>{share.isError ? problemOf(share.error, "Couldn’t share it.") : null}</Trouble>
        </View>
    );
}

/** Whether the reader's own message reached the places outside. One that
 *  did says nothing; one on its way says so quietly; one that didn't says
 *  so in amber under the message -- "Not delivered to WhatsApp · Try
 *  again" -- with why for a screen reader and a tap on the words. */
function Deliveries({ podId, channel, line, canRetry }: { podId: string; channel: ChannelDetail; line: Line; canRetry: boolean }) {
    const source = useChatsSource();
    const key = useCacheKey();
    const cache = useQueryClient();
    const [why, setWhy] = useState<string | null>(null);
    const retry = useMutation({
        mutationFn: (deliveryId: string) => source.retryDelivery(podId, channel.id, line.id, deliveryId),
        onSettled: () => { for (const held of lineKeys(key, podId, channel.id, line)) void cache.invalidateQueries({ queryKey: held }); },
    });
    const arrival = arrivalOf(line.delivered, channel.places, sayReason);
    if (!arrival.failed.length && !arrival.pending && !arrival.skipped.length) return null;
    return (
        <View accessibilityLabel="Where it went" style={{ gap: 2 }}>
            {arrival.pending ? <Text size={12.5} tone={color.ink3}>{arrival.pending}</Text> : null}
            {arrival.skipped.map((said) => <Text key={said} size={12.5} tone={color.ink3}>{said}</Text>)}
            {arrival.failed.map((one) => (
                <View key={one.id} style={{ gap: 2 }}>
                    <View style={{ flexDirection: "row", alignItems: "center", flexWrap: "wrap" }}>
                        <Press label={one.said + (one.why ? ": " + one.why : "")} accessibilityHint={one.why ? "Shows why" : undefined} disabled={!one.why} onPress={() => setWhy((was) => (was === one.id ? null : one.id))} style={{ minHeight: 28, justifyContent: "center" }}>
                            <Text size={12.5} tone={color.amber}>{one.said}</Text>
                        </Press>
                        {one.retry && canRetry ? (
                            <>
                                <Text size={12.5} tone={color.amber}> · </Text>
                                <Press label="Try sending it again" disabled={retry.isPending} onPress={() => retry.mutate(one.id)} style={{ minHeight: 28, justifyContent: "center" }}>
                                    {retry.isPending && retry.variables === one.id ? <ActivityIndicator size="small" color={color.amber} /> : <Text size={12.5} weight="medium" tone={color.amber}>Try again</Text>}
                                </Press>
                            </>
                        ) : null}
                    </View>
                    {why === one.id && one.why ? <Text size={12.5} tone={color.ink3} style={{ lineHeight: 17 }}>{one.why.charAt(0).toUpperCase() + one.why.slice(1)}.</Text> : null}
                </View>
            ))}
            <Trouble>{retry.isError ? problemOf(retry.error, "Couldn’t try it again.") : null}</Trouble>
        </View>
    );
}

/** The question a message carries: answered here when it waits on the
 *  reader, else where it stands. */
function Question({ podId, channel, line, me }: { podId: string; channel: ChannelDetail; line: Line; me: string | null }) {
    const interaction = line.interaction;
    if (!interaction) return null;
    const state = questionState(interaction, me);
    if (state.kind === "yours") {
        const question = asWaitingQuestion(line, channel, me);
        return question ? (
            <View style={{ gap: 6, paddingTop: 4 }}>
                {state.answering ? <Text size={13} tone={color.ink3}>Being answered… If that answer doesn’t arrive, answer it again here.</Text> : null}
                <WaitingRow podId={podId} question={question} bare />
            </View>
        ) : null;
    }
    const said = sayQuestion(state);
    const answer = state.kind === "answered" ? answerSummary(interaction.answer) : null;
    return (
        <Text size={13.5} tone={state.kind === "waiting" ? color.ink2 : color.ink3}>
            {said}{answer ? <Text size={13.5} tone={color.ink}>: {answer}</Text> : null}
        </Text>
    );
}
