"use client";

import { useEffect, useRef, useState } from "react";
import { SendIcon } from "@/ui/icons";

/** A box to start a conversation from somewhere that is not one — Home, a
 *  bot's page. It holds no conversation of its own: the words are handed to
 *  the Chat tab, which creates the conversation and sends them, so the pane
 *  that shows the reply is the pane that holds its stream. A box here that
 *  sent by itself and then navigated away was cut off mid-send. */
export function AskBox({ placeholder, fill, onFilled, onAsk }: {
    placeholder: string;
    /** Words put in the box from outside, e.g. a starter on Home. */
    fill?: { text: string; id: number } | null;
    onFilled?: () => void;
    onAsk: (text: string) => void;
}) {
    const [text, setText] = useState("");
    const area = useRef<HTMLTextAreaElement>(null);

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

    /* Grows with what is typed, up to a few lines, then scrolls. */
    useEffect(() => {
        const box = area.current;
        if (!box) return;
        box.style.height = "auto";
        box.style.height = Math.min(box.scrollHeight, 200) + "px";
    }, [text]);

    const ask = () => {
        const said = text.trim();
        if (!said) return;
        setText("");
        onAsk(said);
    };

    return (
        <form className="askbox" onSubmit={(event) => { event.preventDefault(); ask(); }}>
            <textarea
                ref={area}
                rows={1}
                value={text}
                placeholder={placeholder}
                aria-label={placeholder}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                        event.preventDefault();
                        ask();
                    }
                }}
            />
            <button type="submit" className="askbox__send" disabled={!text.trim()} aria-label="Send">
                <SendIcon size={17} weight="bold" />
            </button>
        </form>
    );
}
