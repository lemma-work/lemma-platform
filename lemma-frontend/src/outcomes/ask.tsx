"use client";

import { useEffect, useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { source, type Pod, type Tab } from "@/data";
import { AppCover } from "@/stage/app-cover";
import { key } from "@/session/storage";
import type { Go } from "./model";
import { Icon, to } from "./parts";

/** The Lemma the reader last asked from Home, to ask again. */
const ASK_IN_KEY = key("outcomes-ask-in");

/** A box to ask a Lemma something, with which Lemma at its foot. Home lets
 *  the reader pick and remembers the pick; a new chat names its Lemma in
 *  the address, and picking another moves the chat there. */
export function AskBox({ pods, podId, onPod, onAsk, autoFocus = false, placeholder = "Ask anything, or say what to do", seed = null, fixed = false }: {
    pods: readonly Pod[];
    /** The Lemma asked, when where the box sits decides it. */
    podId?: string;
    onPod?: (podId: string) => void;
    onAsk: (podId: string, text: string) => void;
    autoFocus?: boolean;
    placeholder?: string;
    /** The start of a request put in the box -- "Make a table to track" --
     *  for the reader to finish. A new `id` puts it there again. */
    seed?: { text: string; id: number } | null;
    /** On a Lemma's own page the box asks that Lemma, and says nothing of
     *  which. */
    fixed?: boolean;
}) {
    const [text, setText] = useState("");
    const field = useRef<HTMLTextAreaElement>(null);
    useEffect(() => {
        if (!seed) return;
        setText(seed.text);
        const box = field.current;
        if (box) {
            box.focus();
            requestAnimationFrame(() => box.setSelectionRange(seed.text.length, seed.text.length));
        }
    }, [seed]);
    const [chosen, setChosen] = useState<string | null>(() => {
        try {
            return localStorage.getItem(ASK_IN_KEY);
        } catch {
            return null;
        }
    });
    const target = pods.find((pod) => pod.id === (podId ?? chosen)) ?? pods[0] ?? null;
    const pick = (id: string) => {
        if (onPod) onPod(id);
        else setChosen(id);
    };
    const send = () => {
        const said = text.trim();
        if (!said || !target) return;
        try { localStorage.setItem(ASK_IN_KEY, target.id); } catch { /* asked here this once */ }
        onAsk(target.id, said);
    };
    return (
        <form className="ox-askbox" onSubmit={(event) => { event.preventDefault(); send(); }}>
            <label className="ox-sr" htmlFor="ask-text">Ask {target?.name ?? "a Lemma"}</label>
            <textarea
                ref={field}
                id="ask-text"
                rows={2}
                value={text}
                placeholder={placeholder}
                autoFocus={autoFocus}
                onChange={(event) => setText(event.target.value)}
                onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                        event.preventDefault();
                        send();
                    }
                }}
            />
            <div className="ox-askbox__bar">
                {target && !fixed && (
                    <label className="ox-askbox__in">
                        <span>To</span>
                        <select value={target.id} onChange={(event) => pick(event.target.value)}>
                            {pods.map((pod) => <option key={pod.id} value={pod.id}>{pod.name}</option>)}
                        </select>
                    </label>
                )}
                <button type="submit" className="ox-askbox__send" disabled={!text.trim() || !target} aria-label={`Ask ${target?.name ?? ""}`.trim()}><Icon name="send" size={16} /></button>
            </div>
        </form>
    );
}

export interface PodApp {
    pod: Pod;
    app: Extract<Tab, { kind: "app" }>;
}

/** The apps of the Lemmas given, newest first, read under the key every
 *  view of them shares. */
export function useApps(pods: readonly Pod[]): { apps: PodApp[]; pending: boolean } {
    const results = useQueries({
        queries: pods.map((pod) => ({ queryKey: ["outcome-apps", pod.id], queryFn: () => source.listTabs(pod.id), staleTime: 5 * 60_000 })),
    });
    const apps = pods
        .flatMap((pod, index) => (results[index]?.data ?? []).flatMap((tab) => (tab.kind === "app" ? [{ pod, app: tab }] : [])))
        .sort((a, b) => (Date.parse(b.app.updated ?? "") || 0) - (Date.parse(a.app.updated ?? "") || 0));
    return { apps, pending: results.some((result) => result.isPending) };
}

/** Apps as tiles: a cover, a name and -- across Lemmas -- whose it is. Each
 *  opens here, framed, in the Lemma that runs it. */
export function AppTiles({ apps, go, showWhose = true }: { apps: readonly PodApp[]; go: Go; showWhose?: boolean }) {
    return (
        <div className="ox-apps ox-apps--home">
            {apps.map(({ pod, app }) => (
                <a key={pod.id + app.id} className="ox-apptile" {...to({ name: "app", podId: pod.id, app: app.id.replace(/^app:/, "") }, go)}>
                    <span className="ox-app__cover"><AppCover url={app.url} fallback={<span className="ox-app__blank"><Icon name="app" size={20} /></span>} /></span>
                    <span className="ox-apptile__name">{app.label}</span>
                    {showWhose && <span className="ox-apptile__pod">{pod.name}</span>}
                </a>
            ))}
        </div>
    );
}
