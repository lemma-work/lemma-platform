import { VoiceIcon, StopIcon, SendIcon, AttachIcon, CloseIcon, FileIcon } from "@/ui/icons";
import { memo, useEffect, useId, useLayoutEffect, useRef, useState, type DragEvent, type KeyboardEvent, type ReactNode } from "react";
import {
    canSend,
    describeSize,
    tooLarge,
    type Attachment,
} from "./attachments";
import { composerActions, type Queued } from "./queued";
import { draftStore, readDraft, writeDraft } from "./drafts";

/** Where the next message goes, when there is more than one place: a note to
 *  the bot alone, or a reply that goes out on the chat platform. Each option
 *  says what it does in one line, shown under the box while it is chosen. */
export interface ComposerChoices {
    /** Names the group for assistive technology. */
    label: string;
    value: string;
    options: { id: string; label: string; hint: string; icon?: ReactNode }[];
    onChange: (id: string) => void;
}

/** A radio group of buttons: one tab stop, arrows move between them. */
function Choices({ choices }: { choices: ComposerChoices }) {
    const move = (event: KeyboardEvent<HTMLButtonElement>, at: number) => {
        const step = event.key === "ArrowRight" || event.key === "ArrowDown" ? 1
            : event.key === "ArrowLeft" || event.key === "ArrowUp" ? -1 : 0;
        if (!step) return;
        event.preventDefault();
        const next = (at + step + choices.options.length) % choices.options.length;
        choices.onChange(choices.options[next].id);
        (event.currentTarget.parentElement?.children[next] as HTMLElement | undefined)?.focus();
    };
    return (
        <div className="composer__modes" role="radiogroup" aria-label={choices.label}>
            {choices.options.map((option, at) => {
                const chosen = option.id === choices.value;
                return (
                    <button
                        key={option.id}
                        type="button"
                        role="radio"
                        className="composer__mode"
                        aria-checked={chosen}
                        tabIndex={chosen ? 0 : -1}
                        title={option.label}
                        onClick={() => choices.onChange(option.id)}
                        onKeyDown={(event) => move(event, at)}
                    >
                        {option.icon}
                        <span>{option.label}</span>
                    </button>
                );
            })}
        </div>
    );
}

/** About four lines at the chat size: past this a draft is a paragraph, and the
 *  card docked above the composer gives way to it. */
const TALL_PX = 110;

/** How long typing has to pause before the draft is written down. */
const DRAFT_SAVE_MS = 300;

/** Memoised: the pane around it re-renders for every few tokens of a reply,
 *  and a box redrawn that often is a box that lags behind typing. Everything
 *  the pane hands it is stable for that reason. */
export const Composer = memo(function Composer({
    placeholder,
    note,
    busy,
    canStop,
    fill,
    onFilled,
    onSend,
    onStop,
    onVoice,
    onDismissNote,
    attachments,
    onAttach,
    onRemoveAttachment,
    queued,
    queuedNote,
    onWithdraw,
    choices,
    onTall,
    draftKey,
}: {
    placeholder: string;
    note?: string;
    busy: boolean;
    canStop: boolean;
    /** Text a widget or an app asked the app to put here, and the ask it came
     *  from — two people clicking the same button want the box filled twice,
     *  and the text alone cannot tell the second ask from the first. */
    fill?: { text: string; id: number } | null;
    onFilled?: () => void;
    onSend: (text: string) => void | Promise<unknown>;
    onStop?: () => void;
    onVoice?: () => void;
    /** Makes `note` dismissible. For notes that are the reader's to clear —
     *  a call that failed to start — not for ones that describe the run. */
    onDismissNote?: () => void;
    /** Files waiting to go with the next message. Held by the pane rather than
     *  here, because the pane is what uploads them: it clears them once the
     *  message carrying them has gone, and leaves them alone when it has not,
     *  so a retry still has something to send. */
    attachments?: Attachment[];
    /** Absent means this surface cannot take files at all — the sample pane,
     *  which has nowhere to put them. */
    onAttach?: (files: File[]) => void;
    onRemoveAttachment?: (key: string) => void;
    /** What the person said while the run was working that it has not heard
     *  yet. Drawn here rather than in the transcript; see `queued.ts`. */
    queued?: Queued[];
    /** Says when they will be heard, which depends on the teammate. */
    queuedNote?: string;
    onWithdraw?: (id: string) => void;
    /** Where the message goes, in a conversation that also lives on a chat
     *  platform. Absent everywhere else, and then nothing is drawn. */
    choices?: ComposerChoices;
    /** Told when the draft grows past a few lines, and when it is back under.
     *  A long draft and a docked card together left the conversation a line
     *  tall; the card folds on this. */
    onTall?: (tall: boolean) => void;
    /** Where an unsent draft is kept in this browser, so it is still here
     *  after switching away and back (`drafts.ts`). Null keeps it only as long
     *  as the box. */
    draftKey?: string | null;
}) {
    const hintId = useId();
    const chosen = choices?.options.find((option) => option.id === choices.value) ?? null;
    const [draft, setDraft] = useState(() => {
        const store = draftStore();
        return store && draftKey ? readDraft(store, draftKey) : "";
    });

    /* Written a moment after typing stops, and at once when the box empties —
       a message just sent must not come back as a draft because the page
       closed inside that moment. A key that changes under the draft is a new
       conversation getting its id: the draft moves with it. */
    const savedUnder = useRef(draftKey ?? null);
    const latestDraft = useRef(draft);
    latestDraft.current = draft;
    useEffect(() => {
        const store = draftStore();
        if (!store) return;
        const previous = savedUnder.current;
        if (previous !== (draftKey ?? null)) {
            if (previous) writeDraft(store, previous, "");
            savedUnder.current = draftKey ?? null;
        }
        if (!draftKey) return;
        if (!draft.trim()) {
            writeDraft(store, draftKey, "");
            return;
        }
        const timer = setTimeout(() => writeDraft(store, draftKey, draft), DRAFT_SAVE_MS);
        return () => clearTimeout(timer);
    }, [draft, draftKey]);
    useEffect(() => () => {
        const store = draftStore();
        if (store && savedUnder.current) writeDraft(store, savedUnder.current, latestDraft.current);
    }, []);
    const [over, setOver] = useState(false);
    const [refused, setRefused] = useState<string | null>(null);
    const input = useRef<HTMLTextAreaElement | null>(null);
    const picker = useRef<HTMLInputElement | null>(null);
    const held = attachments ?? [];
    const takesFiles = Boolean(onAttach);

    /* Replaces the draft rather than appending to it. Appending would join two
       sentences nobody wrote together, and the person can still see and edit
       what arrived — which is the whole reason this fills the box instead of
       sending. Focus follows it so the next keystroke is theirs. */
    useEffect(() => {
        if (!fill) return;
        setDraft(fill.text);
        input.current?.focus();
        onFilled?.();
        // Keyed on the ask, so the same text asked for twice fills twice.
    }, [fill?.id]);

    const tallNow = useRef(false);
    const onTallRef = useRef(onTall);
    onTallRef.current = onTall;
    useLayoutEffect(() => {
        const field = input.current;
        if (!field) return;
        const resize = () => {
            field.style.height = "auto";
            field.style.height = Math.min(field.scrollHeight, 200) + "px";
            const tall = field.scrollHeight > TALL_PX;
            if (tall !== tallNow.current) {
                tallNow.current = tall;
                onTallRef.current?.(tall);
            }
        };
        resize();
        const observer = new ResizeObserver(resize);
        observer.observe(field.parentElement!);
        return () => observer.disconnect();
    }, [draft]);

    const submitting = useRef(false);

    /** Refuse what the server would refuse, before spending somebody's upload
     *  on finding out. Everything else goes up; a wrong guess at the ceiling is
     *  only ever a refusal that says exactly what it did. */
    function offer(files: File[]) {
        if (files.length === 0) return;
        const big = files.filter(tooLarge);
        const rest = files.filter((file) => !tooLarge(file));
        setRefused(
            big.length === 0
                ? null
                : big.length === 1
                  ? big[0].name + " is too large to attach (" + describeSize(big[0].size) + ")."
                  : big.length + " files are too large to attach.",
        );
        if (rest.length > 0) onAttach?.(rest);
    }

    async function send() {
        const text = draft.trim();
        if (!canSend(text, held) || busy || submitting.current) return;
        setDraft("");
        setRefused(null);
        submitting.current = true;
        try { await onSend(text); }
        catch { setDraft(current => current || text); }
        finally { submitting.current = false; }
    }

    const uploading = held.some((one) => one.status === "uploading");
    const actions = composerActions(canStop, canSend(draft, held));

    return (
        <div
            className={"composer" + (over ? " composer--over" : "")}
            data-choice={choices?.value}
            onDragOver={takesFiles ? (event: DragEvent) => {
                /* Only for an actual file drag. Without the check, dragging
                   selected text across the page lights the whole composer up
                   as though it were about to accept it. */
                if (!Array.from(event.dataTransfer.types).includes("Files")) return;
                event.preventDefault();
                setOver(true);
            } : undefined}
            onDragLeave={takesFiles ? () => setOver(false) : undefined}
            onDrop={takesFiles ? (event: DragEvent) => {
                event.preventDefault();
                setOver(false);
                offer(Array.from(event.dataTransfer.files));
            } : undefined}
        >
            {queued && queued.length > 0 && (
                <div className="queued" aria-label="Waiting to be heard">
                    {queuedNote && <span className="queued__note">{queuedNote}</span>}
                    {queued.map((one) => (
                        <span key={one.id} className="queued__item" data-withdrawable={one.withdrawable ? "" : undefined}>
                            <span className="queued__text" title={one.text}>{one.text}</span>
                            {one.withdrawable ? (
                                <button
                                    className="attached__drop"
                                    aria-label={"Take back: " + one.text}
                                    title="Take this back"
                                    onClick={() => onWithdraw?.(one.id)}
                                >
                                    <CloseIcon size={12} />
                                </button>
                            ) : (
                                <span className="queued__state">sending…</span>
                            )}
                        </span>
                    ))}
                </div>
            )}
            {held.length > 0 && (
                <div className="attached" aria-label="Attached files">
                    {held.map((one) => (
                        <span key={one.key} className="attached__chip" data-status={one.status}>
                            <FileIcon size={14} />
                            <span className="attached__name" title={one.error ?? one.file.name}>{one.file.name}</span>
                            <span className="attached__size">
                                {one.status === "uploading" ? "sending…"
                                    : one.status === "failed" ? "failed"
                                    : describeSize(one.file.size)}
                            </span>
                            <button
                                className="attached__drop"
                                aria-label={"Remove " + one.file.name}
                                title={"Remove " + one.file.name}
                                onClick={() => onRemoveAttachment?.(one.key)}
                            >
                                <CloseIcon size={12} />
                            </button>
                        </span>
                    ))}
                </div>
            )}
            <div className="composer__row">
                {choices && <Choices choices={choices} />}
                <div className="composer__box">
                    {takesFiles && (
                        <>
                            <input
                                ref={picker}
                                className="composer__picker"
                                type="file"
                                multiple
                                tabIndex={-1}
                                aria-hidden="true"
                                onChange={(event) => {
                                    offer(Array.from(event.target.files ?? []));
                                    /* Cleared so choosing the same file twice
                                       in a row still fires a change event. */
                                    event.target.value = "";
                                }}
                            />
                            <button
                                className="composer__attach"
                                title="Attach a file"
                                aria-label="Attach a file"
                                onClick={() => picker.current?.click()}
                            >
                                <AttachIcon size={19} />
                            </button>
                        </>
                    )}
                    <textarea
                        rows={1}
                        ref={input}
                        className="composer__input"
                        aria-label={placeholder}
                        aria-describedby={chosen ? hintId : undefined}
                        placeholder={placeholder}
                        value={draft}
                        onChange={(event) => setDraft(event.target.value)}
                        onPaste={takesFiles ? (event) => {
                            /* A screenshot on the clipboard is a file, and
                               pasting one is how people send them. Only
                               intercepted when there is one, or this swallows
                               ordinary text. */
                            const files = Array.from(event.clipboardData.files);
                            if (files.length === 0) return;
                            event.preventDefault();
                            offer(files);
                        } : undefined}
                        onKeyDown={(event) => {
                            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                                event.preventDefault();
                                send();
                            }
                        }}
                    />
                    <button
                        className="composer__wave"
                        title={onVoice ? "Start a call" : "Voice calls aren’t set up on this install."}
                        aria-label={onVoice ? "Start a call" : "Voice calls aren’t set up on this install."}
                        disabled={!onVoice}
                        onClick={onVoice}
                    >
                        <VoiceIcon size={21} />
                    </button>
                    {actions.stop && (
                        <button className="composer__stop" onClick={onStop} title="Stop this run" aria-label="Stop this run">
                            <StopIcon size={18} weight="fill" />
                        </button>
                    )}
                    {actions.send && (
                        <button
                            className="composer__send"
                            aria-label="Send message"
                            title={canStop ? "Send — heard as soon as the work in progress allows" : "Send message"}
                            onClick={send}
                            disabled={!canSend(draft, held) || busy}
                        >
                            <SendIcon size={20} />
                        </button>
                    )}
                </div>
                {(refused || note || uploading) && (
                    <span className="composer__note" data-bad={refused ? "" : undefined}>
                        <i />
                        {refused ?? (uploading ? "attaching…" : note)}
                        {!refused && !uploading && note && onDismissNote && (
                            <button type="button" className="linkish" aria-label="Dismiss" onClick={onDismissNote}>
                                <CloseIcon size={12} />
                            </button>
                        )}
                    </span>
                )}
                {chosen && <p className="composer__hint" id={hintId}>{chosen.hint}</p>}
            </div>
        </div>
    );
});
