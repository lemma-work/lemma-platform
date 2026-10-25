"use client";

import { useEffect, useRef, useState } from "react";
import type { ConversationRef, Pod } from "@/data";
import { chatView, isChat, outcomeView, type View } from "./model";
import { Bars, Icon } from "./parts";

/** ⌘K: ask a Lemma something, or jump to a Lemma or a chat. Asking starts
 *  a chat with the Lemma chosen and opens it here, where the reply streams
 *  in; nothing leaves the frame. */
export function Palette({ pods, podId, activity, onClose, go, onAsk }: {
    pods: readonly Pod[];
    /** The Lemma the reader is on, which asking defaults to. */
    podId: string | null;
    activity: Map<string, ConversationRef[]>;
    onClose: () => void;
    go: (view: View) => void;
    onAsk: (podId: string, text: string) => void;
}) {
    const [text, setText] = useState("");
    const [target, setTarget] = useState<string>(podId ?? pods[0]?.id ?? "");
    const input = useRef<HTMLInputElement>(null);
    useEffect(() => { input.current?.focus(); }, []);

    const said = text.trim().toLowerCase();
    const matches = said ? pods.filter((pod) => pod.name.toLowerCase().includes(said) || (pod.description ?? "").toLowerCase().includes(said)) : pods;
    const chats = said
        ? pods.flatMap((pod) => (activity.get(pod.id) ?? []).filter((one) => isChat(one) && one.title.toLowerCase().includes(said)).map((one) => ({ pod, chat: one }))).slice(0, 5)
        : [];
    const asking = pods.find((pod) => pod.id === target) ?? null;

    const ask = () => {
        if (!asking || !text.trim()) return;
        onAsk(asking.id, text.trim());
    };

    return (
        <div className="ox-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
            <div className="ox-palette" role="dialog" aria-modal="true" aria-label="Ask, or jump to a Lemma" onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
                <form className="ox-palette__in" onSubmit={(event) => { event.preventDefault(); ask(); }}>
                    <Icon name="search" />
                    <label className="ox-sr" htmlFor="palette-text">Ask, or jump to a Lemma</label>
                    <input
                        id="palette-text"
                        ref={input}
                        value={text}
                        placeholder="Ask anything, or type a Lemma’s name"
                        onChange={(event) => setText(event.target.value)}
                    />
                    <kbd>esc</kbd>
                </form>
                {text.trim() && asking && (
                    <div className="ox-palette__ask">
                        <Bars size={16} />
                        <span>Ask</span>
                        <label className="ox-sr" htmlFor="palette-pod">Lemma to ask</label>
                        <select id="palette-pod" value={target} onChange={(event) => setTarget(event.target.value)}>
                            {pods.map((pod) => <option key={pod.id} value={pod.id}>{pod.name}</option>)}
                        </select>
                        <button type="button" className="ox-btn ox-btn--lemma ox-btn--small" onClick={ask}>Ask ↵</button>
                    </div>
                )}
                {chats.length > 0 && (
                    <>
                        <p className="ox-palette__h">Chats</p>
                        <ul className="ox-palette__list">
                            {chats.map(({ pod, chat }) => (
                                <li key={pod.id + chat.id}>
                                    <button type="button" onClick={() => go(chatView({ kind: "chat", podId: pod.id, id: chat.id }))}>
                                        <span className="ox-palette__name">{chat.title}</span>
                                        <span className="ox-palette__desc">{pod.name}</span>
                                    </button>
                                </li>
                            ))}
                        </ul>
                    </>
                )}
                <p className="ox-palette__h">Lemmas</p>
                <ul className="ox-palette__list">
                    {matches.slice(0, 8).map((pod) => (
                        <li key={pod.id}>
                            <button type="button" onClick={() => go(outcomeView(pod.id))}>
                                <span className="ox-palette__name">{pod.name}</span>
                                {pod.description && <span className="ox-palette__desc">{pod.description}</span>}
                            </button>
                        </li>
                    ))}
                    {matches.length === 0 && <li className="ox-vs ox-palette__none">No Lemma by that name. Press ↵ to ask instead.</li>}
                </ul>
            </div>
        </div>
    );
}
