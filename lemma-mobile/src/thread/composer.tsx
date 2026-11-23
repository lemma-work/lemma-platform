import { memo, useEffect, useRef, useState, type ReactNode } from "react";
import { ActivityIndicator, Alert, Keyboard, ScrollView, TextInput, View } from "react-native";
import { Image } from "expo-image";
import Animated, { FadeOutDown, ReduceMotion, SlideInDown } from "react-native-reanimated";
import { useIsMutating } from "@tanstack/react-query";
import type { PickedFile } from "../data/io";
import { MicButton, SendButton } from "../ui/ask-box";
import { AttachToggle, AttachTray } from "../ui/files-in-chat";
import { Icon } from "../ui/icons";
import { spring } from "../ui/motion";
import { Press } from "../ui/press";
import { Text } from "../ui/text";
import { color, font } from "../ui/tokens";
import { canSend, offered, type Attachment } from "./attachments";
import type { ReplyMode } from "@web/thread/channel-reply";
import { readDraft, writeDraft } from "@web/thread/drafts";
import { Glyph } from "./glyphs";
import { isKept, kept, whenKept } from "./kept";
import { composerActions, type Queued } from "@web/thread/queued";

/** The box at the foot of a chat: the web's `composer.tsx`, for a phone.
 *
 *  Above the box, what the run has not heard yet (each one can be taken
 *  back while nobody has delivered it) and the files going with the next
 *  message. In a chat that also lives on a chat platform, where the next
 *  message goes. In the box, the words; under them the +, the model, the
 *  mic and Send -- or Stop while a run works and nothing is typed. Under
 *  the box, one line about what the chat is waiting on.
 *
 *  The + turns into an × and the attach tray slides up above the box: the
 *  camera, the latest photos (one tap each), files. Send is the Lemma's
 *  colour (`tone`).
 *
 *  Unsent words are kept per chat on this phone (`drafts.ts`). A widget's
 *  ask fills the box and never sends: the person reads it, and sends. */

/** About four lines: past this a draft is a paragraph, and the card docked
 *  above gives way to it. */
const TALL = 110;
const DRAFT_SAVE_MS = 300;

export interface ComposerChoice {
    mode: ReplyMode;
    label: string;
    hint: string;
}

/** The tray, up from behind the box on the one spring. */
const trayIn = SlideInDown.springify().damping(spring.damping ?? 20).stiffness(spring.stiffness ?? 190).mass(spring.mass ?? 1).reduceMotion(ReduceMotion.System);
const trayOut = FadeOutDown.duration(200).reduceMotion(ReduceMotion.System);

/** A photo going with the next message, as itself. */
function Thumb({ uri, label, onRemove, removeLabel, busy, failed }: { uri: string; label: string; onRemove?: () => void; removeLabel?: string; busy: boolean; failed: boolean }) {
    return (
        <View accessibilityLabel={label} style={{ width: 56, height: 56 }}>
            <Image source={{ uri }} style={{ width: 52, height: 52, borderRadius: 10, marginTop: 4, backgroundColor: color.wash, opacity: busy ? 0.55 : 1, borderWidth: failed ? 2 : 0, borderColor: color.amber }} contentFit="cover" />
            {busy ? <View style={{ position: "absolute", left: 0, top: 4, width: 52, height: 52, alignItems: "center", justifyContent: "center" }}><ActivityIndicator size="small" color={color.ink} /></View> : null}
            {onRemove ? (
                <Press label={removeLabel} onPress={onRemove} hitSlop={8} style={{ position: "absolute", right: 0, top: 0, width: 20, height: 20, borderRadius: 10, backgroundColor: color.ink, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="close" size={10} width={2.4} tone={color.onInk} />
                </Press>
            ) : null}
        </View>
    );
}

function Chip({ children, label, onRemove, removeLabel }: { children: ReactNode; label: string; onRemove?: () => void; removeLabel?: string }) {
    return (
        <View accessibilityLabel={label} style={{ flexDirection: "row", alignItems: "center", gap: 6, maxWidth: 260, height: 32, paddingLeft: 10, paddingRight: onRemove ? 2 : 10, borderRadius: 999, borderWidth: 1, borderColor: color.line2, backgroundColor: color.paper }}>
            {children}
            {onRemove ? (
                <Press label={removeLabel} onPress={onRemove} hitSlop={6} style={{ width: 28, height: 28, alignItems: "center", justifyContent: "center" }}>
                    <Icon name="close" size={13} width={2} tone={color.ink2} />
                </Press>
            ) : null}
        </View>
    );
}

export const Composer = memo(function Composer({
    placeholder,
    note,
    busy,
    canStop,
    fill,
    onFilled,
    onSend,
    onStop,
    attachments,
    onAttach,
    onRemoveAttachment,
    queued,
    queuedNote,
    onWithdraw,
    choices,
    choice,
    onChoose,
    onTall,
    draftKey,
    model,
    onCall,
    tone = color.ink,
    onTone = color.onInk,
}: {
    placeholder: string;
    note?: string;
    busy: boolean;
    canStop: boolean;
    /** Words to put in the box -- a widget's ask, or a refused send to edit --
     *  and the ask they came from, so the same words asked twice fill twice. */
    fill: { text: string; id: number } | null;
    onFilled: () => void;
    onSend: (text: string) => Promise<unknown>;
    onStop: () => void;
    attachments: Attachment[];
    /** Absent where the box cannot take files. */
    onAttach?: (files: PickedFile[]) => void;
    onRemoveAttachment: (key: string) => void;
    queued: Queued[];
    queuedNote?: string;
    onWithdraw: (id: string) => void;
    choices: ComposerChoice[];
    choice: ReplyMode;
    onChoose: (mode: ReplyMode) => void;
    onTall?: (tall: boolean) => void;
    draftKey: string | null;
    model?: ReactNode;
    /** A voice call about this chat; none where the server cannot carry one. */
    onCall?: (() => void) | null;
    /** Send, and what is chosen in the tray, in the Lemma's colour. */
    tone?: string;
    onTone?: string;
}) {
    const [draft, setDraft] = useState(() => (draftKey && isKept() ? readDraft(kept, draftKey) : ""));
    const edited = useRef(false);
    const input = useRef<TextInput>(null);
    const [tray, setTray] = useState(false);
    const [refused, setRefused] = useState<string | null>(null);
    const savingModel = useIsMutating({ mutationKey: ["chat-runtime", "save"] }) > 0;

    /* The launch's read of the kept drafts may land after the box is drawn;
       an untouched box then takes its draft. */
    useEffect(() => {
        if (isKept() || !draftKey) return;
        let gone = false;
        void whenKept().then(() => {
            if (gone || edited.current) return;
            const held = readDraft(kept, draftKey);
            if (held) setDraft(held);
        });
        return () => { gone = true; };
    }, [draftKey]);

    /* Written a moment after typing stops, and at once when the box empties.
       A key that changes under the draft is a chat that came into being:
       words typed before it existed are its words, so they move with it. */
    const savedUnder = useRef(draftKey);
    const latest = useRef(draft);
    latest.current = draft;
    useEffect(() => {
        const here = draftKey;
        if (savedUnder.current !== here) {
            const moves = edited.current;
            if (moves && savedUnder.current) writeDraft(kept, savedUnder.current, "");
            savedUnder.current = here;
            edited.current = false;
            if (!moves && here) {
                const held = readDraft(kept, here);
                if (held !== draft) {
                    setDraft(held);
                    return;
                }
            }
        }
        if (!here) return;
        if (!draft.trim()) {
            if (edited.current) writeDraft(kept, here, "");
            return;
        }
        const timer = setTimeout(() => writeDraft(kept, here, draft), DRAFT_SAVE_MS);
        return () => clearTimeout(timer);
    }, [draft, draftKey]);
    useEffect(() => () => {
        if (savedUnder.current) writeDraft(kept, savedUnder.current, latest.current);
    }, []);

    /* Replaces the draft rather than joining it: two sentences nobody wrote
       together are not a message. Focus follows, so the next tap is theirs. */
    useEffect(() => {
        if (!fill) return;
        edited.current = true;
        setDraft(fill.text);
        setTimeout(() => input.current?.focus(), 50);
        onFilled();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [fill?.id]);

    const submitting = useRef(false);
    const send = async () => {
        const text = draft.trim();
        if (!canSend(text, attachments) || busy || savingModel || submitting.current) return;
        edited.current = true;
        setDraft("");
        setRefused(null);
        submitting.current = true;
        try {
            await onSend(text);
        } catch {
            /* Given back: the answer to a question did not go through. */
            setDraft((current) => current || text);
        } finally {
            submitting.current = false;
        }
    };

    const offer = (files: PickedFile[]) => {
        if (files.length === 0) return;
        const { take, refused: why } = offered(files);
        setRefused(why);
        if (take.length > 0) onAttach?.(take);
    };

    const attachedUris = new Set(attachments.map((one) => one.file.uri));
    const toggleTray = () => {
        if (!tray) Keyboard.dismiss();
        setTray((was) => !was);
    };
    const uploading = attachments.some((one) => one.status === "uploading");
    const ready = canSend(draft, attachments);
    const actions = composerActions(canStop, ready);
    const chosen = choices.find((one) => one.mode === choice) ?? null;
    const footNote = refused ?? (uploading ? "attaching…" : note);

    return (
        <View style={{ paddingHorizontal: 12, paddingTop: 6, paddingBottom: 10, gap: 6, backgroundColor: color.paper }}>
            {queued.length > 0 ? (
                <View accessibilityLabel="Waiting to be heard" style={{ gap: 6 }}>
                    {queuedNote ? <Text size={12.5} tone={color.ink3}>{queuedNote}</Text> : null}
                    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6 }} keyboardShouldPersistTaps="handled">
                        {queued.map((one) => (
                            <Chip key={one.id} label={one.text} onRemove={one.withdrawable ? () => onWithdraw(one.id) : undefined} removeLabel={"Take back: " + one.text}>
                                <Text size={13.5} numberOfLines={1} style={{ flexShrink: 1 }}>{one.text}</Text>
                                {one.withdrawable ? null : <Text size={12} tone={color.ink3}>sending…</Text>}
                            </Chip>
                        ))}
                    </ScrollView>
                </View>
            ) : null}
            {attachments.length > 0 ? (
                <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={{ gap: 6 }} keyboardShouldPersistTaps="handled">
                    {attachments.map((one) => (one.file.type.startsWith("image/") ? (
                        <Thumb key={one.key} uri={one.file.uri} label={one.file.name + (one.status === "failed" ? ". Not uploaded" : "")} busy={one.status === "uploading"} failed={one.status === "failed"} onRemove={one.status === "uploading" ? undefined : () => onRemoveAttachment(one.key)} removeLabel={"Remove " + one.file.name} />
                    ) : (
                        <Chip key={one.key} label={one.file.name + (one.status === "failed" ? ". Not uploaded" : "")} onRemove={one.status === "uploading" ? undefined : () => onRemoveAttachment(one.key)} removeLabel={"Remove " + one.file.name}>
                            {one.status === "uploading" ? <ActivityIndicator size="small" color={color.ink3} /> : <Glyph name="text" size={14} tone={one.status === "failed" ? color.amber : color.ink2} />}
                            <Text size={13.5} tone={one.status === "failed" ? color.amber : color.ink} numberOfLines={1} style={{ flexShrink: 1 }}>{one.file.name}</Text>
                        </Chip>
                    )))}
                </ScrollView>
            ) : null}
            {choices.length > 0 ? (
                <View style={{ gap: 4 }}>
                    <View accessibilityRole="radiogroup" accessibilityLabel="Where this goes" style={{ flexDirection: "row", gap: 6 }}>
                        {choices.map((one) => {
                            const on = one.mode === choice;
                            return (
                                <Press
                                    key={one.mode}
                                    role="radio"
                                    label={one.label}
                                    accessibilityState={{ checked: on }}
                                    onPress={() => onChoose(one.mode)}
                                    style={{ flexDirection: "row", alignItems: "center", gap: 5, height: 30, paddingHorizontal: 10, borderRadius: 999, borderWidth: 1, borderColor: on ? color.ink : color.line2, backgroundColor: on ? color.ink : "transparent" }}
                                >
                                    {one.mode === "note" ? <Glyph name="lock" size={12} tone={on ? color.onInk : color.ink2} /> : null}
                                    <Text size={13} tone={on ? color.onInk : color.ink2} numberOfLines={1}>{one.label}</Text>
                                </Press>
                            );
                        })}
                    </View>
                    {chosen ? <Text size={12.5} tone={color.ink3}>{chosen.hint}</Text> : null}
                </View>
            ) : null}
            {tray && onAttach ? (
                <Animated.View entering={trayIn} exiting={trayOut}>
                    <AttachTray
                        chosen={attachedUris}
                        onPicked={offer}
                        onUnpicked={(uri) => {
                            const one = attachments.find((held) => held.file.uri === uri);
                            if (one) onRemoveAttachment(one.key);
                        }}
                        onRefused={(why) => Alert.alert("Not attached", why)}
                        onDone={() => setTray(false)}
                        tone={tone}
                        onTone={onTone}
                    />
                </Animated.View>
            ) : null}
            <View style={{ borderWidth: 1, borderColor: color.line2, borderRadius: 22, paddingTop: 12, paddingBottom: 8, paddingLeft: 14, paddingRight: 10, gap: 8, backgroundColor: color.paper }}>
                <TextInput
                    ref={input}
                    accessibilityLabel={placeholder}
                    value={draft}
                    onChangeText={(value) => {
                        edited.current = true;
                        setDraft(value);
                    }}
                    onFocus={() => setTray(false)}
                    onContentSizeChange={(event) => onTall?.(event.nativeEvent.contentSize.height > TALL)}
                    placeholder={placeholder}
                    placeholderTextColor={color.ink3}
                    multiline
                    style={{ fontFamily: font.regular, fontSize: 16, color: color.ink, padding: 0, maxHeight: 140, minHeight: 22 }}
                />
                <View style={{ flexDirection: "row", alignItems: "center", gap: 2 }}>
                    {onAttach ? <View style={{ marginLeft: -6 }}><AttachToggle open={tray} onPress={toggleTray} /></View> : null}
                    {model}
                    <View style={{ flex: 1 }} />
                    {actions.stop ? (
                        <Press label="Stop this run" onPress={onStop} style={{ width: 34, height: 34, borderRadius: 17, backgroundColor: color.ink, alignItems: "center", justifyContent: "center" }}>
                            <Glyph name="stop" size={14} tone={color.onInk} />
                        </Press>
                    ) : null}
                    {actions.send ? (
                        <>
                            {!actions.stop ? <MicButton onPress={onCall} /> : null}
                            <SendButton ready={ready && !busy && !savingModel} onPress={() => { setTray(false); void send(); }} label={canStop ? "Send. Heard as soon as the work in progress allows" : "Send"} tone={tone} onTone={onTone} />
                        </>
                    ) : null}
                </View>
            </View>
            {footNote ? (
                <View style={{ flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 6 }}>
                    <View style={{ width: 6, height: 6, borderRadius: 3, backgroundColor: refused ? color.amber : note === "waiting on you" ? color.violet : color.ink3 }} />
                    <Text size={12.5} tone={refused ? color.amber : color.ink2} style={{ flex: 1 }}>{footNote}</Text>
                </View>
            ) : null}
        </View>
    );
});
