import { VoiceIcon, StopIcon, SendIcon, AttachIcon, CloseIcon, FileIcon } from "@/ui/icons";
import { useEffect, useLayoutEffect, useRef, useState, type DragEvent } from "react";
import {
    canSend,
    describeSize,
    tooLarge,
    type Attachment,
} from "./attachments";

export function Composer({
    placeholder,
    note,
    busy,
    canStop,
    fill,
    onFilled,
    onSend,
    onStop,
    onVoice,
    attachments,
    onAttach,
    onRemoveAttachment,
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
    /** Files waiting to go with the next message. Held by the pane rather than
     *  here, because the pane is what uploads them: it clears them once the
     *  message carrying them has gone, and leaves them alone when it has not,
     *  so a retry still has something to send. */
    attachments?: Attachment[];
    /** Absent means this surface cannot take files at all — the sample pane,
     *  which has nowhere to put them. */
    onAttach?: (files: File[]) => void;
    onRemoveAttachment?: (key: string) => void;
}) {
    const [draft, setDraft] = useState("");
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

    useLayoutEffect(() => {
        const field = input.current;
        if (!field) return;
        const resize = () => {
            field.style.height = "auto";
            field.style.height = Math.min(field.scrollHeight, 200) + "px";
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

    return (
        <div
            className={"composer" + (over ? " composer--over" : "")}
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
                        title="Enter to send · Shift+Enter for a new line"
                        ref={input}
                        className="composer__input"
                        aria-label={placeholder}
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
                        title={onVoice ? "Start a call" : "Voice is not configured"}
                        disabled={!onVoice}
                        onClick={onVoice}
                    >
                        <VoiceIcon size={21} />
                    </button>
                    {canStop ? (
                        <button className="composer__stop" onClick={onStop} title="Stop this run">
                            <StopIcon size={18} weight="fill" />
                        </button>
                    ) : (
                        <button
                            className="composer__send"
                            aria-label="Send message"
                            title="Send message"
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
                    </span>
                )}
            </div>
        </div>
    );
}
