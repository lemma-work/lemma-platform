"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { SearchIcon, ChatIcon, FileIcon, TableIcon, AppsIcon, AgentIcon, WorkflowIcon, CodeIcon, ClockIcon, UserIcon, ProfileIcon } from "@/ui/icons";
import type { Pod } from "@/data";
import { groupByKind, highlight, type Hit, type SearchKind } from "./matching";
import type { ResourceKind } from "@/thread/resource-conversation";
import { useSearch } from "./use-search";

/** One box for everything in a pod.
 *
 *  The library had a search field that filtered the rows already on screen,
 *  which is a filter rather than a search — it cannot find what has not been
 *  scrolled to, and a box that silently only knows about part of the thing is
 *  worse than no box, because people stop checking.
 */

const ICONS: Record<SearchKind, React.ComponentType<{ size?: number }>> = {
    teammate: ProfileIcon,
    conversation: ChatIcon,
    doc: FileIcon,
    record: TableIcon,
    app: AppsIcon,
    agent: AgentIcon,
    workflow: WorkflowIcon,
    function: CodeIcon,
    table: TableIcon,
    person: UserIcon,
    schedule: ClockIcon,
};

export interface SearchAction {
    openPod: (podId: string) => void;
    openConversation: (id: string) => void;
    openFile: (path: string) => void;
    openTable: (name: string) => void;
    openApp: (name: string) => void;
    openRecord: (tableName: string, recordId: string) => void;
    /** Land on one agent in the Agents view. Takes the row name, which is
     *  what the list keys on — not the label. */
    openAgent: (name: string) => void;
    openProfile: () => void;
    /** Open the conversation a resource carries, making it if it has none. */
    discuss: (kind: ResourceKind, name: string) => void;
}

export function SearchPalette({
    podId,
    pods,
    actions,
    onClose,
}: {
    podId: string | null;
    pods: Pod[];
    actions: SearchAction;
    onClose: () => void;
}) {
    const [query, setQuery] = useState("");
    const [cursor, setCursor] = useState(0);
    const box = useRef<HTMLDivElement | null>(null);
    const listRef = useRef<HTMLDivElement | null>(null);
    const { hits, isSearching, failed, skippedTables } = useSearch(podId, pods, query);

    /* A new query starts at the top. Without this the selection stays on
       whatever index it was, which after a keystroke is a different result
       entirely — and Enter then opens something nobody looked at. */
    useEffect(() => { setCursor(0); }, [query]);

    useEffect(() => {
        const keys = (event: KeyboardEvent) => {
            if (event.key === "Escape") { event.preventDefault(); onClose(); return; }
            if (event.key === "ArrowDown") { event.preventDefault(); setCursor(at => Math.min(at + 1, hits.length - 1)); }
            if (event.key === "ArrowUp") { event.preventDefault(); setCursor(at => Math.max(at - 1, 0)); }
            if (event.key === "Enter") {
                const hit = hits[cursor];
                if (hit) { event.preventDefault(); open(hit); }
            }
        };
        document.addEventListener("keydown", keys);
        return () => document.removeEventListener("keydown", keys);
    });

    /* Keep the selected row on screen when it is moved by the keyboard. */
    useEffect(() => {
        listRef.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
    }, [cursor, hits.length]);

    useEffect(() => {
        const away = (event: MouseEvent) => {
            if (!box.current?.contains(event.target as Node)) onClose();
        };
        document.addEventListener("mousedown", away);
        return () => document.removeEventListener("mousedown", away);
    }, [onClose]);

    function open(hit: Hit) {
        switch (hit.kind) {
            case "teammate": actions.openPod(hit.id); break;
            case "conversation": actions.openConversation(hit.id); break;
            case "doc": actions.openFile(String((hit.payload as { path?: string })?.path ?? hit.subtitle ?? "")); break;
            case "table": actions.openTable(hit.title); break;
            case "record": {
                /* The row itself, not the table it happens to live in. Landing
                   on a table after searching for a row means finding it twice. */
                const found = hit.payload as { table?: string; row?: Record<string, unknown> } | undefined;
                const id = found?.row?.id;
                if (found?.table && id !== undefined && id !== null) actions.openRecord(found.table, String(id));
                else if (found?.table) actions.openTable(found.table);
                break;
            }
            case "app": actions.openApp(hit.title); break;
            /* An agent does now have a view of its own, so it goes there —
               with its instruction, what it may reach and what you may do to
               it. Talking to it is a button on that page rather than the only
               way in, which is what it was when searching for an agent could
               only ever open a conversation about it. */
            case "agent": actions.openAgent(hit.title); break;
            /* A workflow, a function and a schedule still have no view of
               their own here — and for those, the conversation bound to them is
               not a consolation prize, it is the thing. It is where they get
               changed, and where the reasons live. The Profile tab is the
               nearest true thing to drop somebody on instead, and still a
               small lie. */
            case "workflow":
            case "function":
            case "schedule": actions.discuss(hit.kind, hit.title); break;
            /* A person is not a resource you edit. The profile is genuinely
               where a pod says who is in it. */
            default: actions.openProfile(); break;
        }
        onClose();
    }

    const grouped = useMemo(() => groupByKind(hits), [hits]);

    let index = -1;

    return (
        <div className="palette-scrim">
            <div className="palette" ref={box} role="dialog" aria-label="Search">
                <div className="palette__field">
                    <SearchIcon size={18} />
                    <input
                        autoFocus
                        aria-label="Search this teammate"
                        placeholder="Search teammates, conversations, documents, records…"
                        value={query}
                        onChange={(event) => setQuery(event.target.value)}
                    />
                    {isSearching && <span className="palette__working" aria-hidden="true" />}
                </div>

                <div className="palette__list" ref={listRef} role="listbox" aria-label="Results">
                    {grouped.map((group) => (
                        <div key={group.label + ":" + group.kind} className="palette__group">
                            <div className="palette__kind">{group.label}</div>
                            {group.hits.map((hit) => {
                                index += 1;
                                const mine = index;
                                const Icon = ICONS[hit.kind];
                                return (
                                    <button
                                        key={hit.kind + hit.id}
                                        className="palette__hit"
                                        role="option"
                                        aria-selected={mine === cursor}
                                        onMouseMove={() => setCursor(mine)}
                                        onClick={() => open(hit)}
                                    >
                                        <span className="palette__icon"><Icon size={16} /></span>
                                        <span className="palette__text">
                                            <span className="palette__title">
                                                {highlight(hit.title, hit.ranges).map((piece, at) =>
                                                    piece.hit ? <mark key={at}>{piece.text}</mark> : <span key={at}>{piece.text}</span>,
                                                )}
                                            </span>
                                            {hit.subtitle && hit.subtitle !== group.label && <small>{hit.subtitle}</small>}
                                        </span>
                                    </button>
                                );
                            })}
                        </div>
                    ))}

                    {query.trim() && hits.length === 0 && !isSearching && (
                        <p className="palette__quiet">Nothing here matches that.</p>
                    )}
                    {!query.trim() && (
                        <p className="palette__quiet">
                            Teammates, conversations, documents, records, apps, agents, workflows, functions, tables,
                            people and schedules.
                        </p>
                    )}
                </div>

                {/* What the list could not cover, said rather than hidden. A
                    search that quietly returns less than it claims is the one
                    failure people cannot detect from the results. */}
                {(failed.length > 0 || skippedTables > 0) && (
                    <p className="palette__partial" role="status">
                        {failed.length > 0 && <>Could not reach {failed.join(", ")}. </>}
                        {skippedTables > 0 && <>{skippedTables} more table{skippedTables === 1 ? "" : "s"} not searched.</>}
                    </p>
                )}
            </div>
        </div>
    );
}
