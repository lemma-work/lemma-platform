"use client";

import { useRef, useState } from "react";
import { useQueries } from "@tanstack/react-query";
import { source, type Pod, type Tab } from "@/data";
import { AppCover } from "@/stage/app-cover";
import { key } from "@/session/storage";
import type { Choice } from "@/data/runtimes";
import { PlusIcon } from "@/ui/icons";
import { canSend, namePasted, offered, toAttachments, type Attachment } from "@/thread/attachments";
import { AttachedFiles } from "@/thread/attached";
import { ModelPicker } from "@/thread/model-picker";
import { useConversationFolder } from "@/desktop/folders";
import { FolderChip } from "@/desktop/folder-chip";
import { RepositoryChip } from "@/thread/repository-chip";
import { TeammateFace } from "@/space/teammate-face";
import type { Ask, Go } from "./model";
import { Icon, to } from "./parts";

/** The Lemma the reader last asked from Home, to ask again. */
const ASK_IN_KEY = key("outcomes-ask-in");

/** The model a new chat runs on, as the conversation is made with it. */
function runtimeOf(model: Choice | null): Record<string, unknown> | undefined {
    return model ? { agent_runtime: { profile_id: model.runtimeId, model_name: model.model || null } } : undefined;
}

/** What the chat a box starts is created with: the model picked under it,
 *  and the GitHub repository it works in. */
function createWithOf(model: Choice | null, repository: string | null): Record<string, unknown> | undefined {
    const made = { ...runtimeOf(model), ...(repository ? { repository } : {}) };
    return Object.keys(made).length > 0 ? made : undefined;
}

/** The one box to ask a Lemma something, with which Lemma at its foot.
 *  Home lets the reader pick and remembers the pick; a new chat names its
 *  Lemma in the address, and picking another moves the chat there. Files go
 *  with the words -- attached, pasted or dropped -- and the model picked
 *  under it is the one the chat it starts runs on, as in the workspace's own
 *  box. */
export function AskBox({ pods, podId, onPod, onAsk, autoFocus = false, placeholder = "Ask anything, or say what to do" }: {
    pods: readonly Pod[];
    /** The Lemma asked, when where the box sits decides it. */
    podId?: string;
    onPod?: (podId: string) => void;
    onAsk: Ask;
    autoFocus?: boolean;
    placeholder?: string;
}) {
    const [text, setText] = useState("");
    const [held, setHeld] = useState<Attachment[]>([]);
    const [refused, setRefused] = useState<string | null>(null);
    const [over, setOver] = useState(false);
    const [model, setModel] = useState<Choice | null>(null);
    const [repository, setRepository] = useState<string | null>(null);
    const picker = useRef<HTMLInputElement>(null);
    /* A folder on this computer for the chat to work in: the desktop app's,
       on a local install, and nothing anywhere else. Parked until the chat
       exists, then adopted by it. */
    const folder = useConversationFolder(null);
    const [chosen, setChosen] = useState<string | null>(() => {
        try {
            return localStorage.getItem(ASK_IN_KEY);
        } catch {
            return null;
        }
    });
    const target = pods.find((pod) => pod.id === (podId ?? chosen)) ?? pods[0] ?? null;
    const pick = (id: string) => {
        /* A model is one of a Lemma's own; another Lemma starts on its own
           default. So is a repository: its GitHub connector decides them. */
        setModel(null);
        setRepository(null);
        if (onPod) onPod(id);
        else setChosen(id);
    };
    /** Refuse what the server would refuse, before an upload is spent on
     *  finding out -- the composer's rule, in its words. */
    const offer = (files: File[]) => {
        if (files.length === 0) return;
        const { take, refused: why } = offered(files);
        setRefused(why);
        if (take.length > 0) setHeld((was) => [...was, ...toAttachments(take)]);
    };
    const send = () => {
        const said = text.trim();
        if (!canSend(said, held) || !target) return;
        try { localStorage.setItem(ASK_IN_KEY, target.id); } catch { /* asked here this once */ }
        const files = held.map((one) => one.file);
        setText("");
        setHeld([]);
        setRefused(null);
        onAsk(target.id, said, { files, createWith: createWithOf(model, repository), folder: folder.folder ? folder.pendingId : undefined });
        setModel(null);
        setRepository(null);
    };
    return (
        <form
            className={"ox-askbox" + (over ? " ox-askbox--over" : "")}
            onSubmit={(event) => { event.preventDefault(); send(); }}
            onDragOver={(event) => {
                /* A file being dragged, not selected text. */
                if (!Array.from(event.dataTransfer.types).includes("Files")) return;
                event.preventDefault();
                setOver(true);
            }}
            onDragLeave={() => setOver(false)}
            onDrop={(event) => {
                event.preventDefault();
                setOver(false);
                offer(Array.from(event.dataTransfer.files));
            }}
        >
            <AttachedFiles className="ox-askbox__files" files={held} onRemove={(gone) => setHeld((was) => was.filter((kept) => kept.key !== gone))} />
            <label className="ox-sr" htmlFor="ask-text">Ask {target?.name ?? "a Lemma"}</label>
            <textarea
                id="ask-text"
                rows={2}
                value={text}
                placeholder={placeholder}
                autoFocus={autoFocus}
                onChange={(event) => setText(event.target.value)}
                onPaste={(event) => {
                    /* A screenshot on the clipboard is a file; ordinary text
                       is left to paste as text. */
                    const files = Array.from(event.clipboardData.files);
                    if (files.length === 0) return;
                    event.preventDefault();
                    offer(namePasted(files));
                }}
                onKeyDown={(event) => {
                    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
                        event.preventDefault();
                        send();
                    }
                }}
            />
            <div className="ox-askbox__bar">
                <input
                    ref={picker}
                    className="ox-sr"
                    type="file"
                    multiple
                    tabIndex={-1}
                    aria-hidden="true"
                    onChange={(event) => {
                        offer(Array.from(event.target.files ?? []));
                        /* Cleared, so the same file chosen twice still counts. */
                        event.target.value = "";
                    }}
                />
                <button type="button" className="ox-askbox__attach" title="Attach a file" aria-label="Attach a file" onClick={() => picker.current?.click()}>
                    <PlusIcon size={18} />
                </button>
                {/* Who is asked, as a face and a name rather than a form
                    field: the native list underneath, for the keyboard. */}
                {/* Not on a Lemma's own page, which is the one asked. */}
                {target && pods.length > 1 && (
                    <label className="ox-askbox__in" title="Who to ask">
                        <span className="ox-sr">To</span>
                        <TeammateFace pod={target} size={18} />
                        <select value={target.id} onChange={(event) => pick(event.target.value)}>
                            {pods.map((pod) => <option key={pod.id} value={pod.id}>{pod.name}</option>)}
                        </select>
                        <Icon name="chevron" size={12} />
                    </label>
                )}
                <FolderChip folder={folder} />
                {target && source.label === "live" && (
                    <RepositoryChip key={target.id} podId={target.id} picked={repository} onPick={setRepository} className="ox-askbox__in" chevron={<Icon name="chevron" size={12} />} />
                )}
                {refused && <span className="ox-askbox__note">{refused}</span>}
                <span className="ox-askbox__model">
                    {target && source.label === "live" && <ModelPicker key={target.id} podId={target.id} orgId={target.orgId} conversationId={null} picked={model} onPicked={setModel} />}
                </span>
                <button type="submit" className="ox-askbox__send" disabled={!canSend(text.trim(), held) || !target} aria-label={`Ask ${target?.name ?? ""}`.trim()}><Icon name="send" size={16} /></button>
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
