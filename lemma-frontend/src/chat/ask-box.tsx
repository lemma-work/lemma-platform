"use client";

import { useEffect, useRef, useState, type DragEvent } from "react";
import { AttachIcon, CloseIcon, FileIcon, SendIcon } from "@/ui/icons";
import { canSend, describeSize, offered, toAttachments, type Attachment } from "@/thread/attachments";

/** A box to start a conversation from somewhere that is not one — Home, a
 *  bot's page. It holds no conversation of its own: the words and the files
 *  are handed to the Chat tab, which creates the conversation and sends them,
 *  so the pane that shows the reply is the pane that holds its stream. A box
 *  here that sent by itself and then navigated away was cut off mid-send.
 *
 *  Files take the same road as the words rather than being uploaded here:
 *  there is no `pod_cwd` to put them in until the conversation exists, and
 *  reading it off the conversation is the Chat tab's job. */
export function AskBox({ placeholder, fill, onFilled, onAsk }: {
    placeholder: string;
    /** Words put in the box from outside, e.g. a starter on Home. */
    fill?: { text: string; id: number } | null;
    onFilled?: () => void;
    /** Start a conversation with these words and files, in the Chat tab. */
    onAsk: (text: string, files: File[]) => void;
}) {
    const [text, setText] = useState("");
    /* Listed and removable here until the send that carries them, the same
       thing the composer's chips do — in a box with no pane above it to hold
       them, because this box is not in the pane they are going to. */
    const [held, setHeld] = useState<Attachment[]>([]);
    const [refused, setRefused] = useState<string | null>(null);
    const [over, setOver] = useState(false);
    const area = useRef<HTMLTextAreaElement>(null);
    const picker = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!fill) return;
        setText(fill.text);
        onFilled?.();
        requestAnimationFrame(() => {
            const box = area.current;
            if (!box) return;
            box.focus();
            box.setSelectionRange(box.value.length, box.value.length);
        });
    }, [fill, onFilled]);

    /* Grows with what is typed, up to a few lines, then scrolls — and is
       measured again whenever its width changes. Measured once, a box first
       drawn narrow (a window opening small, a pane not yet on screen) kept the
       height its placeholder needed at that width: eight empty lines. */
    useEffect(() => {
        const box = area.current;
        if (!box) return;
        const fit = () => {
            box.style.height = "auto";
            box.style.height = Math.min(box.scrollHeight, 200) + "px";
        };
        fit();
        if (typeof ResizeObserver === "undefined") return;
        let width = box.clientWidth;
        const watch = new ResizeObserver(() => {
            if (box.clientWidth === width) return;
            width = box.clientWidth;
            fit();
        });
        watch.observe(box);
        return () => watch.disconnect();
    }, [text]);

    /** Refuse what the server would refuse, before spending somebody's upload
     *  on finding out — the same rule, in the same words, as the composer. */
    function offer(files: File[]) {
        if (files.length === 0) return;
        const { take, refused: why } = offered(files);
        setRefused(why);
        if (take.length > 0) setHeld(was => [...was, ...toAttachments(take)]);
    }

    const ask = () => {
        const said = text.trim();
        /* Either half is a message: a file with nothing typed is "here, look
           at this", and the references the upload appends are what the agent
           is given. */
        if (!canSend(said, held)) return;
        const files = held.map(one => one.file);
        setText("");
        setHeld([]);
        setRefused(null);
        onAsk(said, files);
    };

    return (
        <form
            className={"askbox" + (over ? " askbox--over" : "")}
            onSubmit={(event) => { event.preventDefault(); ask(); }}
            onDragOver={(event: DragEvent) => {
                /* Only for an actual file drag. Without the check, dragging
                   selected text across the page lights the box up as though it
                   were about to accept it. */
                if (!Array.from(event.dataTransfer.types).includes("Files")) return;
                event.preventDefault();
                setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(event: DragEvent) => {
                event.preventDefault();
                setOver(false);
                offer(Array.from(event.dataTransfer.files));
            }}
        >
            {held.length > 0 && (
                <div className="askbox__files" aria-label="Attached files">
                    {held.map((one) => (
                        <span key={one.key} className="attached__chip">
                            <FileIcon size={14} />
                            <span className="attached__name" title={one.file.name}>{one.file.name}</span>
                            <span className="attached__size">{describeSize(one.file.size)}</span>
                            <button
                                type="button"
                                className="attached__drop"
                                aria-label={"Remove " + one.file.name}
                                title={"Remove " + one.file.name}
                                onClick={() => setHeld(was => was.filter(kept => kept.key !== one.key))}
                            >
                                <CloseIcon size={12} />
                            </button>
                        </span>
                    ))}
                </div>
            )}
            <div className="askbox__row">
                <input
                    ref={picker}
                    className="askbox__picker"
                    type="file"
                    multiple
                    tabIndex={-1}
                    aria-hidden="true"
                    onChange={(event) => {
                        offer(Array.from(event.target.files ?? []));
                        /* Cleared so choosing the same file twice in a row
                           still fires a change event. */
                        event.target.value = "";
                    }}
                />
                <button
                    type="button"
                    className="askbox__attach"
                    title="Attach a file"
                    aria-label="Attach a file"
                    onClick={() => picker.current?.click()}
                >
                    <AttachIcon size={19} />
                </button>
                <textarea
                    ref={area}
                    rows={1}
                    value={text}
                    placeholder={placeholder}
                    aria-label={placeholder}
                    onChange={(event) => setText(event.target.value)}
                    onPaste={(event) => {
                        /* A screenshot on the clipboard is a file, and pasting
                           one is how people send them. Only intercepted when
                           there is one, or this swallows ordinary text. */
                        const files = Array.from(event.clipboardData.files);
                        if (files.length === 0) return;
                        event.preventDefault();
                        offer(files);
                    }}
                    onKeyDown={(event) => {
                        if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                            event.preventDefault();
                            ask();
                        }
                    }}
                />
                <button type="submit" className="askbox__send" disabled={!canSend(text.trim(), held)} aria-label="Send">
                    <SendIcon size={17} weight="bold" />
                </button>
            </div>
            {refused && (
                <span className="askbox__note" data-bad="">{refused}</span>
            )}
        </form>
    );
}
