"use client";

import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { useQueries } from "@tanstack/react-query";
import { source, type ConversationRef, type Org, type Pod } from "@/data";
import { readableName } from "@/library/reading";
import { badge, sinceShort } from "@/channels/model";
import { ChannelMark } from "@/channels/chat-mark";
import { ChannelIcon, channelName } from "@/shell/channels";
import { TeammateFace } from "@/space/teammate-face";
import { HumanProfile } from "@/session/human-profile";
import { useMe } from "@/session/use-me";
import { key } from "@/session/storage";
import {
    NEW_CHAT,
    chatItems, chatView, foldSection, inSidebar, outcomeView, podOfView, repeatedTitles, sectionRows, sidebarSections, workRows, writeView,
    type Go, type SideRow, type View,
} from "./model";
import { useChannelsByPod } from "./chats";
import { Bars, Icon, to, type IconName } from "./parts";

/** The organization picker's last choice, which makes a new one. */
const NEW_ORG = "__new-organization";

/** How many lines a section shows before "Show more". */
const FOLD = 3;
/** Sections the reader opened or folded themselves: theirs, on this
 *  browser only. Every other section opens or folds by how lively it is. */
const CHOSEN_KEY = key("outcomes-sections");

function readChosen(): Map<string, boolean> {
    try {
        const raw = localStorage.getItem(CHOSEN_KEY);
        return new Map(raw ? (Object.entries(JSON.parse(raw)) as [string, boolean][]) : []);
    } catch {
        return new Map();
    }
}

/** The frame's left side: a few places, then every Lemma -- its face and
 *  name -- with what is recent with it under it: chats, automations that
 *  ran, tables that changed. The busiest first, the quiet ones folded to
 *  their name. People outside who wrote in are in Needs you while they
 *  wait, and on the Lemma's Contacts tab. */
export function Sidebar({ org, orgs, onPickOrg, pods, view, go, activity, needs, me, onFind, onSettings, onNewOrg, open }: {
    org: Org | null;
    orgs: readonly Org[];
    onPickOrg: (id: string) => void;
    pods: readonly Pod[];
    view: View;
    go: Go;
    activity: Map<string, ConversationRef[]>;
    /** How many things wait on the reader, across outcomes. */
    needs: number;
    me: string;
    onFind: () => void;
    /** The reader's own settings and the organization's. */
    onSettings: () => void;
    onNewOrg: () => void;
    /** Shown on a narrow screen, over the page. */
    open: boolean;
}) {
    const reader = useMe();
    const [channels, feeds] = useChannelsByPod(pods);
    const [chosen, setChosen] = useState<Map<string, boolean>>(readChosen);
    const [now] = useState(() => Date.now());
    const choose = useCallback((podId: string, opened: boolean) => {
        setChosen((was) => new Map(was).set(podId, opened));
    }, []);
    useEffect(() => {
        try { localStorage.setItem(CHOSEN_KEY, JSON.stringify(Object.fromEntries(chosen))); } catch { /* kept for this visit only */ }
    }, [chosen]);
    const here = podOfView(view);
    /* Each Lemma's tables, under the key X-ray reads them by, for the ones
       changed this week. */
    const tableLists = useQueries({
        queries: pods.map((pod) => ({ queryKey: ["outcome-tables", pod.id], queryFn: () => source.listLibrary(pod.id, "tables", "/"), staleTime: 5 * 60_000, retry: false })),
    });
    const tables = new Map(pods.map((pod, index) => [pod.id, tableLists[index]?.data?.items ?? null]));
    /* `tables` is a new map each render; when each list was last read says
       when any of them changed. */
    const tableKey = tableLists.map((one) => one.dataUpdatedAt).join(",");
    const sections = useMemo(() => sidebarSections(
        pods.map((pod) => {
            const { needs: waiting, rest } = chatItems({ pods: [pod], conversations: activity, channels, me: reader });
            const work = workRows({
                podId: pod.id,
                conversations: activity.get(pod.id) ?? [],
                tables: (tables.get(pod.id) ?? []).filter((item) => item.kind === "table").map((item) => ({ name: item.name, title: readableName(item.name), updated: item.updated })),
                now,
            });
            return { pod, items: sectionRows([...waiting, ...rest].filter(inSidebar), work) };
        }),
        now,
        chosen,
        here,
    ), [pods, activity, channels, reader, now, chosen, here, tableKey]);

    const places: { view: View; label: string; icon: IconName; count?: number }[] = [
        { view: { name: "today" }, label: "Home", icon: "home" },
        { view: { name: "needs" }, label: "Needs you", icon: "inbox", count: needs },
        { view: { name: "outcomes" }, label: "Lemmas", icon: "grid" },
    ];

    return (
        <aside className={"ox-side" + (open ? " ox-side--open" : "")} aria-label="Lemma">
            {feeds}
            <div className="ox-side__top">
                <Bars label="Lemma" />
                <label className="ox-org">
                    <span className="ox-sr">Organization</span>
                    <select value={org?.id ?? ""} onChange={(event) => (event.target.value === NEW_ORG ? onNewOrg() : onPickOrg(event.target.value))}>
                        {orgs.map((one) => <option key={one.id} value={one.id}>{one.name}</option>)}
                        <option value={NEW_ORG}>New organization…</option>
                    </select>
                    <Icon name="chevron" size={12} />
                </label>
                <button type="button" className="ox-side__search" onClick={onFind} aria-label="Ask or find" title="Ask or find (⌘K)">
                    <Icon name="search" size={16} />
                </button>
            </div>
            <nav className="ox-side__places" aria-label="Places">
                {places.map((place) => (
                    <a key={place.label} className="ox-place" {...to(place.view, go)} aria-current={view.name === place.view.name ? "page" : undefined}>
                        <span className="ox-glyph"><Icon name={place.icon} size={15} /></span>
                        <span className="ox-place__t">{place.label}</span>
                        {place.count ? <span className="ox-count">{place.count}</span> : null}
                    </a>
                ))}
            </nav>
            <nav className="ox-side__chats" aria-label="Chats, by Lemma">
                {sections.map((section) => (
                    <Section
                        key={section.pod.id}
                        pod={section.pod}
                        items={section.items}
                        waiting={section.waiting}
                        unfolded={section.open}
                        view={view}
                        here={here === section.pod.id && view.name !== "chats"}
                        onFold={() => choose(section.pod.id, !section.open)}
                        go={go}
                    />
                ))}
            </nav>
            <div className="ox-side__foot" aria-label={me}>
                <HumanProfile compact onOpen={onSettings} />
                <a className="ox-classic" href="/t">Classic view</a>
            </div>
        </aside>
    );
}

/** One Lemma: its face and name, which open it, and -- unless it is
 *  folded -- its chats under it. Its controls sit at the row's end, where
 *  the chats' signs are: + on hover, and the fold, which a folded section
 *  always shows. The chats waiting on the reader and the open one always
 *  show; the rest fold past a few. */
function Section({ pod, items, waiting, unfolded, view, here, onFold, go }: {
    pod: Pod;
    items: readonly SideRow[];
    waiting: number;
    unfolded: boolean;
    view: View;
    here: boolean;
    onFold: () => void;
    go: Go;
}) {
    const [all, setAll] = useState(false);
    const { shown, hidden } = all ? { shown: [...items], hidden: 0 } : foldSection(items, view, FOLD);
    const repeated = repeatedTitles(shown);
    const at = writeView(view);
    return (
        <section className={"ox-group" + (unfolded ? "" : " ox-group--folded")} aria-label={pod.name}>
            <div className="ox-group__head">
                <a className="ox-group__name" {...to(outcomeView(pod.id), go)} aria-current={here ? "page" : undefined}>
                    <span className="ox-glyph"><TeammateFace pod={pod} size={18} /></span>
                    <span className="ox-group__t">{pod.name}</span>
                </a>
                {!unfolded && waiting > 0 && <i className="ox-sign ox-sign--ask" role="img" aria-label={`${waiting} waiting on you`} />}
                <a className="ox-group__add" {...to(chatView({ kind: "chat", podId: pod.id, id: NEW_CHAT }), go)} aria-label={`New in ${pod.name}`} title="New chat, table or automation">
                    <Icon name="plus" size={15} />
                </a>
                <button type="button" className="ox-group__fold" aria-expanded={unfolded} aria-label={unfolded ? `Fold ${pod.name}` : `Show what is recent in ${pod.name}`} onClick={onFold}>
                    <Icon name={unfolded ? "chevron" : "next"} size={13} />
                </button>
            </div>
            {unfolded && (
                <div className="ox-group__rows">
                    {shown.map((row) => <SideLine key={row.key} row={row} here={writeView(row.view) === at} stamped={repeated.has(row.key)} link={to(row.view, go)} />)}
                    {(hidden > 0 || all) && items.length > FOLD && (
                        <button type="button" className="ox-group__more" onClick={() => setAll((was) => !was)}>{all ? "Show fewer" : `Show ${hidden} more`}</button>
                    )}
                </div>
            )}
        </section>
    );
}

/** What a line is, in the glyph column: the platform a chat lives on
 *  (WhatsApp, Telegram, Slack, Teams, email), # for a chat shared with
 *  people here, a lock for a private one, a bell for the notices the Lemma
 *  sends, a plain ring for a chat with the Lemma -- and for its own work, an
 *  automation's mark or a table's. */
export function markOf(row: SideRow): ReactNode {
    if (row.kind === "automation") return <Icon name="flow" size={14} />;
    if (row.kind === "table") return <Icon name="table" size={14} />;
    if (row.channel) return <ChannelMark channel={row.channel} size={14} />;
    if (row.platforms[0]) return <ChannelIcon platform={row.platforms[0]} size={14} />;
    return <i className="ox-ring" aria-hidden="true" />;
}

export function kindOf(row: SideRow): string {
    if (row.kind === "automation") return row.runs && row.runs > 1 ? `Automation · ${row.runs} runs` : "Automation";
    if (row.kind === "table") return "Table, changed lately";
    if (row.kind === "line") return "Your own line";
    const places = row.platforms.map(channelName).join(", ");
    if (row.kind === "shared") return places ? "Group on " + places : "Shared chat";
    return places ? "Chat from " + places : "Chat";
}

/** A line: what it is in the glyph column, its name on the edge every name
 *  starts on, and at the end one sign at most -- a question waiting on the
 *  reader (a violet dot), new lines (a count), work going on in it (a violet
 *  ring), or, for a name its section repeats, its time. */
function SideLine({ row, here, stamped, link }: { row: SideRow; here: boolean; stamped: boolean; link: ReturnType<typeof to> }) {
    return (
        <a className="ox-srow" {...link} aria-current={here ? "page" : undefined} title={`${kindOf(row)}: ${row.title}${row.said ? " · " + row.said : ""}`}>
            <span className="ox-glyph">{markOf(row)}</span>
            <span className="ox-srow__t">{row.title}</span>
            {row.needsYou
                ? <i className="ox-sign ox-sign--ask" role="img" aria-label="Waiting on you" />
                : row.unread > 0
                    ? <span className="ox-srow__n" aria-label={`${row.unread} new`}>{badge(row.unread)}</span>
                    : row.running
                        ? <i className="ox-sign ox-sign--live" role="img" aria-label="Running now" />
                        : stamped && row.at !== null ? <span className="ox-srow__when">{sinceShort(new Date(row.at).toISOString())}</span> : null}
        </a>
    );
}
