"use client";

import { useEffect, useRef, useState } from "react";
import type { ConversationRef, Pod } from "@/data";
import { useSearch } from "@/search/use-search";
import { highlight, type Hit } from "@/search/matching";
import { useResourceConversation } from "@/thread/use-resource-conversation";
import { chatView, isChat, outcomeView, type View } from "./model";
import { Bars, Icon } from "./parts";

/** How many of a Lemma's own things the palette lists for what was typed. */
const FOUND_SHOWN = 8;

/** ⌘K: ask a Lemma something, find anything in it -- chats, pages, files,
 *  rows, apps, agents, workflows, schedules, people, as the workspace's
 *  search finds them -- or jump to a Lemma or a chat. Asking starts a chat
 *  with the Lemma chosen and opens it here, where the reply streams in;
 *  nothing leaves the frame. */
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
    const search = useSearch(asking?.id ?? null, [...pods], text);
    const found = said ? search.hits.slice(0, FOUND_SHOWN) : [];
    const discussion = useResourceConversation(asking?.id ?? "");

    /* Where each kind of thing opens in the frame. A function has no page of
       its own, so its conversation is where it is changed and explained, as
       in the workspace's search. */
    const open = (hit: Hit) => {
        if (!asking) return;
        const podId = asking.id;
        switch (hit.kind) {
            case "teammate": return go(outcomeView(hit.id));
            case "conversation": return go(chatView({ kind: "chat", podId, id: hit.id }));
            case "doc": return go({ name: "file", podId, path: String((hit.payload as { path?: string } | undefined)?.path ?? hit.subtitle ?? "") });
            case "table": return go({ name: "table", podId, table: hit.title });
            case "record": {
                const row = hit.payload as { table?: string; row?: Record<string, unknown> } | undefined;
                const id = row?.row?.id;
                if (row?.table && id !== undefined && id !== null) return go({ name: "record", podId, table: row.table, recordId: String(id) });
                if (row?.table) return go({ name: "table", podId, table: row.table });
                return;
            }
            case "app": return go({ name: "app", podId, app: hit.title });
            case "agent": return go({ name: "agent", podId, agent: hit.title });
            case "workflow": return go({ name: "workflow", podId, workflow: hit.title });
            case "schedule": return go(outcomeView(podId, "automations", "schedules"));
            case "function":
                void discussion.open("function", hit.title).then((id) => { if (id) go(chatView({ kind: "chat", podId, id })); });
                return;
            default: return go(outcomeView(podId, "settings", "people"));
        }
    };

    const ask = () => {
        if (!asking || !text.trim()) return;
        onAsk(asking.id, text.trim());
    };

    return (
        <div className="ox-scrim" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
            <div className="ox-palette" role="dialog" aria-modal="true" aria-label="Ask, find, or jump to a Lemma" onKeyDown={(event) => { if (event.key === "Escape") onClose(); }}>
                <form className="ox-palette__in" onSubmit={(event) => { event.preventDefault(); ask(); }}>
                    <Icon name="search" />
                    <label className="ox-sr" htmlFor="palette-text">Ask, find, or jump to a Lemma</label>
                    <input
                        id="palette-text"
                        ref={input}
                        value={text}
                        placeholder="Ask anything, find anything, or type a Lemma’s name"
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
                {asking && said && (found.length > 0 || search.isSearching) && (
                    <>
                        <p className="ox-palette__h">In {asking.name}{search.isSearching ? " …" : ""}</p>
                        <ul className="ox-palette__list">
                            {found.map((hit) => (
                                <li key={hit.kind + hit.id}>
                                    <button type="button" onClick={() => open(hit)}>
                                        <span className="ox-palette__name">
                                            {highlight(hit.title, hit.ranges).map((piece, at) => (piece.hit ? <mark key={at}>{piece.text}</mark> : <span key={at}>{piece.text}</span>))}
                                        </span>
                                        <span className="ox-palette__desc">{KIND_WORDS[hit.kind]}{hit.subtitle ? " · " + hit.subtitle : ""}</span>
                                    </button>
                                </li>
                            ))}
                        </ul>
                    </>
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

/** What each kind of thing the search finds is called, beside it. */
const KIND_WORDS: Record<Hit["kind"], string> = {
    teammate: "Lemma",
    conversation: "Chat",
    doc: "Page or file",
    record: "Row",
    app: "App",
    agent: "Agent",
    workflow: "Workflow",
    function: "Function",
    table: "Table",
    person: "Person",
    schedule: "Schedule",
};
