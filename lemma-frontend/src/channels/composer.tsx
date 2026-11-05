"use client";

import { useEffect, useId, useRef, useState, useSyncExternalStore, type DragEvent, type KeyboardEvent } from "react";
import { useMutation } from "@tanstack/react-query";
import { AttachIcon, CloseIcon, LockIcon, SendIcon } from "@/ui/icons";
import { channelApi } from "./api";
import { attachmentSaid, carriedFiles, dragHasFiles, enterSends, uniqueNames, uploadProgress, type AttachmentState } from "./compose-model";
import { draftKey, keepDraft, readDraft } from "./drafts";
import { problemOf } from "./problem";
import { useMentionPicker } from "./mention-picker";
import { mentionsIn, pickedIn, removeMention, type Mentionable } from "./mentions";
import { composerChoices, type ChannelDetail, type Line, type Part } from "./model";
import { goesTo, latestPlace, placeName } from "./chat-copy";

type FilePart = Extract<Part, { type: "file" }>;

/** One file on its way into a line: picked, pasted or dropped, then
 *  uploaded when the line is sent, and kept uploaded if the send fails so a
 *  retry does not upload it twice. */
interface Attached {
    key: string;
    file: File;
    uploaded: FilePart | null;
    uploading: boolean;
    failed: string | null;
}

let attachedCount = 0;

function stateOf(one: Attached): AttachmentState {
    return one.uploaded ? "uploaded" : one.uploading ? "uploading" : one.failed ? "failed" : "waiting";
}

/** A screen with no hover and a coarse pointer: a phone's or a tablet's,
 *  where Enter is the keyboard's return key. */
function subscribeTouch(notify: () => void): () => void {
    if (typeof window === "undefined" || !window.matchMedia) return () => {};
    const query = window.matchMedia("(hover: none) and (pointer: coarse)");
    query.addEventListener?.("change", notify);
    return () => query.removeEventListener?.("change", notify);
}

function touchNow(): boolean {
    return typeof window !== "undefined" && Boolean(window.matchMedia?.("(hover: none) and (pointer: coarse)").matches);
}

/** Where a message sent here goes besides this chat, said plainly under the
 *  box -- "Also goes to #launch on Slack", "Goes to Dana Ruiz by email" --
 *  with the place to pick where there are several, and one switch to keep
 *  it inside Lemma, for the people in the space only. */
function Destination({ channel, place, onPlace, only, onOnly, disabled }: {
    channel: ChannelDetail;
    place: string;
    onPlace: (sourceId: string) => void;
    only: boolean;
    onOnly: (only: boolean) => void;
    disabled: boolean;
}) {
    const live = channel.places.filter((one) => !one.pending);
    const latest = latestPlace(channel.places);
    const chosen = live.find((one) => one.id === place) ?? latest;
    return (
        <span className="chcompose__dest">
            <button
                type="button"
                className="chcompose__only"
                aria-pressed={only}
                disabled={disabled}
                title={only ? "Only people in Lemma see this. Click to send it out too." : "Keep this inside Lemma: nothing outside sees it."}
                onClick={() => onOnly(!only)}
            >
                <LockIcon size={13} aria-hidden="true" /> Only in Lemma
            </button>
            {!only && chosen && (live.length > 1 ? (
                <label className="chcompose__where">
                    <span>{channel.kind === "CONTACT" ? "Goes" : "Also goes"} to</span>
                    <select value={place} disabled={disabled} onChange={(event) => onPlace(event.target.value)}>
                        {latest && <option value="">{placeName(latest)}</option>}
                        {live.filter((one) => one.id !== latest?.id).map((one) => <option key={one.id} value={one.id}>{placeName(one)}</option>)}
                    </select>
                </label>
            ) : (
                <span className="chcompose__where">{goesTo(channel, chosen)}</span>
            ))}
            {only && <span className="chcompose__where">Only people in Lemma see this</span>}
        </span>
    );
}

/** Writing in a channel, or in one thread of it.
 *
 *  Enter sends and Shift+Enter breaks the line; on a touch screen Enter
 *  breaks it and the send button sends. `@` opens the people and agents who
 *  can be named -- the channel's agents first -- and each one picked shows
 *  as a chip while their name is in the text; a named agent answers. Files
 *  -- picked, pasted or dropped on it -- go up when the line is sent, each
 *  saying how it is getting on, and travel as file parts. A send that fails
 *  keeps everything typed and attached, and sending again is the same line
 *  (`client_message_id`). What is typed, and who was picked, is kept per
 *  thread while the reader is elsewhere. */
export function Composer({ podId, channel, people, threadRootId, placeholder, disabledReason, onPosted, onTyping }: {
    podId: string;
    channel: ChannelDetail;
    people: Mentionable[];
    threadRootId?: string;
    placeholder: string;
    /** Why the reader may not write here, when they may not. */
    disabledReason?: string | null;
    onPosted: (line: Line) => void;
    /** Somebody is typing: the socket's typing note, throttled by the caller. */
    onTyping?: () => void;
}) {
    const ids = useId();
    const box = useRef<HTMLTextAreaElement>(null);
    const picker = useRef<HTMLInputElement>(null);
    const draft = draftKey(podId, channel.id, threadRootId);
    const [kept] = useState(() => readDraft(draft));
    const [text, setText] = useState(kept.text);
    const [picked, setPicked] = useState<Mentionable[]>(kept.picked);
    const [files, setFiles] = useState<Attached[]>([]);
    const [dragging, setDragging] = useState(false);
    const touch = useSyncExternalStore(subscribeTouch, touchNow, () => false);
    const choices = composerChoices(channel);
    /* A message here can go outside -- to a mirrored group, or to a contact
       -- unless it is kept inside Lemma. Your default chat has nobody else in
       it: nothing to choose. */
    const offersPlaces = choices.notes && channel.kind !== "DIRECT";
    const [only, setOnly] = useState(false);
    const [place, setPlace] = useState("");
    const membersOnly = offersPlaces && only;
    /* One name per line being written: sending it again after a failure is
       the same line, not a second one. Anything changed is a new line. */
    const lineId = useRef<string | null>(null);

    /* The words and the people picked in them, kept together. */
    useEffect(() => { keepDraft(draft, { text, picked: pickedIn(text, picked) }); }, [draft, text, picked]);

    const attach = (chosen: readonly File[]) => {
        if (!chosen.length) return;
        lineId.current = null;
        setFiles((was) => [...was, ...chosen.map((file) => ({ key: String(++attachedCount), file, uploaded: null, uploading: false, failed: null }))]);
    };

    const mention = useMentionPicker({
        people,
        text,
        setText,
        box,
        onPicked: (who) => { lineId.current = null; setPicked((was) => [...was, who]); },
    });
    const chips = pickedIn(text, picked);

    const send = useMutation({
        mutationFn: async (body: { text: string; mentions: ReturnType<typeof mentionsIn> }) => {
            const api = channelApi(podId);
            /* Named before anything uploads: the line's files go in a folder
               of its own, under names unique within it. */
            const clientMessageId = (lineId.current ??= crypto.randomUUID());
            const names = uniqueNames(files.map((one) => one.file.name));
            const parts: FilePart[] = [];
            for (let index = 0; index < files.length; index += 1) {
                const one = files[index];
                if (one.uploaded) { parts.push(one.uploaded); continue; }
                one.uploading = true;
                one.failed = null;
                setFiles((was) => [...was]);
                try {
                    const uploaded = await api.attach(channel.id, one.file, { clientMessageId, name: names[index] });
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
            return api.post(channel.id, {
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
            onPosted(line);
            box.current?.focus({ preventScroll: true });
        },
    });

    const submit = () => {
        const said = text.trim();
        if ((!said && files.length === 0) || send.isPending || disabledReason) return;
        send.mutate({ text: said, mentions: mentionsIn(said, picked) });
    };

    const unpick = (who: Mentionable) => {
        lineId.current = null;
        setText(removeMention(text, who));
        setPicked((was) => was.filter((one) => !(one.kind === who.kind && one.id === who.id)));
        box.current?.focus({ preventScroll: true });
    };

    const keys = (event: KeyboardEvent<HTMLTextAreaElement>) => {
        if (event.nativeEvent.isComposing) return;
        if (mention.keyDown(event)) return;
        if (enterSends({ key: event.key, shiftKey: event.shiftKey, isComposing: event.nativeEvent.isComposing }, touch)) { event.preventDefault(); submit(); }
    };

    const dragOver = (event: DragEvent<HTMLFormElement>) => {
        if (!dragHasFiles(event.dataTransfer?.types)) return;
        event.preventDefault();
        if (!dragging) setDragging(true);
    };
    const dropped = (event: DragEvent<HTMLFormElement>) => {
        setDragging(false);
        const chosen = carriedFiles(event.dataTransfer);
        if (!chosen.length) return;
        event.preventDefault();
        if (!send.isPending) attach(chosen);
    };

    if (disabledReason) {
        return <p className="chcompose chcompose--off" role="note">{disabledReason}</p>;
    }

    const sending = send.isPending;
    const progress = uploadProgress(files.map(stateOf));
    return (
        <form
            className="chcompose"
            aria-busy={sending || undefined}
            data-note={(choices.notes && membersOnly) || undefined}
            data-dragging={dragging || undefined}
            onSubmit={(event) => { event.preventDefault(); submit(); }}
            onDragOver={dragOver}
            onDragLeave={(event) => { if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false); }}
            onDrop={dropped}
        >
            {mention.menu}
            {(chips.length > 0 || files.length > 0) && (
                <ul className="chcompose__chips" aria-label="With this line">
                    {chips.map((who) => (
                        <li key={"m" + who.kind + who.id} className="chchip" data-agent={who.kind === "AGENT" || undefined}>
                            <span>@{who.name}</span>
                            <button type="button" className="chchip__x" aria-label={"Don’t name " + who.name} disabled={sending} onClick={() => unpick(who)}><CloseIcon size={12} /></button>
                        </li>
                    ))}
                    {files.map((one) => (
                        <li key={one.key} className="chchip chchip--file" data-failed={one.failed ? "" : undefined} data-state={stateOf(one)} title={one.failed ?? undefined}>
                            <span>{one.file.name}{attachmentSaid(stateOf(one)) ? " — " + attachmentSaid(stateOf(one)) : ""}</span>
                            <button type="button" className="chchip__x" aria-label={"Remove " + one.file.name} disabled={sending} onClick={() => { lineId.current = null; setFiles((was) => was.filter((other) => other.key !== one.key)); }}><CloseIcon size={12} /></button>
                        </li>
                    ))}
                </ul>
            )}
            {progress && <p className="chcompose__progress" role="status">{progress}</p>}
            <label className="sr-only" htmlFor={ids + "-text"}>{placeholder}</label>
            <textarea
                ref={box}
                id={ids + "-text"}
                className="chcompose__text"
                rows={Math.min(8, Math.max(1, text.split("\n").length))}
                value={text}
                placeholder={placeholder}
                maxLength={8000}
                readOnly={sending}
                {...mention.boxProps}
                onChange={(event) => {
                    if (!mention.changed(event.target.value, event.target.selectionStart)) return;
                    lineId.current = null;
                    if (event.target.value.trim()) onTyping?.();
                }}
                onKeyDown={keys}
                onPaste={(event) => {
                    const pasted = carriedFiles(event.clipboardData);
                    if (!pasted.length || sending) return;
                    event.preventDefault();
                    attach(pasted);
                }}
                enterKeyHint={touch ? "enter" : "send"}
            />
            <div className="chcompose__bar">
                <input
                    ref={picker}
                    type="file"
                    multiple
                    hidden
                    onChange={(event) => {
                        const chosen = Array.from(event.target.files ?? []);
                        event.target.value = "";
                        attach(chosen);
                    }}
                />
                <button type="button" className="icon-button chcompose__attach" aria-label="Attach files" title="Attach files" disabled={sending} onClick={() => picker.current?.click()}>
                    <AttachIcon size={18} />
                </button>
                <button
                    type="button"
                    className="icon-button chcompose__at"
                    aria-label="Mention someone"
                    title="Mention someone"
                    disabled={sending}
                    onClick={() => {
                        /* An "@" where the caret is, after a space when it
                           follows a word: the same as typing it. */
                        const element = box.current;
                        const at = element?.selectionStart ?? text.length;
                        const before = text.slice(0, at);
                        const insert = before && !/\s$/.test(before) ? " @" : "@";
                        const next = before + insert + text.slice(element?.selectionEnd ?? at);
                        const caret = before.length + insert.length;
                        if (!mention.changed(next, caret)) return;
                        requestAnimationFrame(() => { element?.focus({ preventScroll: true }); element?.setSelectionRange(caret, caret); });
                    }}
                >
                    <span aria-hidden="true">@</span>
                </button>
                {offersPlaces && <Destination channel={channel} place={place} onPlace={setPlace} only={only} onOnly={setOnly} disabled={sending} />}
                <button type="submit" className="icon-button chcompose__send" aria-label={sending ? "Sending" : membersOnly ? "Send, only in Lemma" : "Send"} disabled={sending || (!text.trim() && files.length === 0)}>
                    <SendIcon size={18} />
                </button>
            </div>
            {send.isError && <p className="cline__problem" role="alert">{problemOf(send.error, "Couldn’t send that.")} What you wrote is still here.</p>}
        </form>
    );
}
