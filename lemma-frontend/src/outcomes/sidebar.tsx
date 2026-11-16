"use client";

import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useQueries } from "@tanstack/react-query";
import { source, type ConversationRef, type Org, type Pod } from "@/data";
import { readableName } from "@/library/reading";
import { badge, sinceShort, type Channel } from "@/channels/model";
import { ChannelMark } from "@/channels/chat-mark";
import { ChannelIcon, channelName } from "@/shell/channels";
import { TeammateFace } from "@/space/teammate-face";
import { HumanProfile } from "@/session/human-profile";
import { AllowanceNote } from "@/usage/allowance-note";
import { HelpMenu } from "@/tour/help-menu";
import { useMe } from "@/session/use-me";
import { key } from "@/session/storage";
import {
    NEW_CHAT, SIDE_DEFAULTS,
    chatItems, chatView, foldSection, inSidebar, outcomeView, podOfView, readSideSettings, rowOfChat, repeatedTitles, sectionRows, sidebarSections, workRows, writeView,
    type Go, type SideGrouping, type SideRow, type SideSettings, type SideShowing, type View,
} from "./model";
import { useChannelsByPod } from "./chats";
import { useApps } from "./ask";
import { appPinKey, toggleAppPin, usePins, type Pin } from "./pins";
import { prefetchOnIntent } from "@/thread/opened-transcripts";
import { Bars, Icon, to, type IconName } from "./parts";

/** The organization picker's last choice, which makes a new one. */
const NEW_ORG = "__new-organization";

/** How many lines a section shows before "Show more". */
const FOLD = 3;
/** How many the one list of every Lemma's lines shows before "Show more". */
const TOGETHER = 12;
/** How the reader asked the list to read: theirs, on this browser. */
const SETTINGS_KEY = key("outcomes-sidebar-view");

function readSettings(): SideSettings {
    try {
        const raw = localStorage.getItem(SETTINGS_KEY);
        return readSideSettings(raw ? JSON.parse(raw) : null);
    } catch {
        return SIDE_DEFAULTS;
    }
}
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

const NOTHING = () => undefined;

/** A pinned chat's line, from the chat as the sidebar last read it, or --
 *  once it has fallen off the recent list -- from the name kept with the
 *  pin; or a pinned app's, which opens it. */
function pinnedRow(pin: Pin, live: ReadonlyMap<string, SideRow>): SideRow | null {
    const known = live.get(pin.key);
    if (known) return known;
    const quiet = { key: pin.key, title: pin.title, said: null, at: null, needsYou: false, unread: 0, running: false, platforms: [], channel: null };
    if (pin.app) return { ...quiet, kind: "app", podId: pin.app.podId, view: { name: "app", podId: pin.app.podId, app: pin.app.name } };
    if (pin.open) return { ...quiet, kind: pin.open.kind === "shared" ? "shared" : "chat", podId: pin.open.podId, view: chatView(pin.open) };
    return null;
}

/** The frame's left side, the way a list of projects and their sessions
 *  reads: a few places, the chats and apps the reader pinned, then every
 *  Lemma -- its face and name -- folded to that name until the reader opens
 *  it, its chats under it when open. The list's own header sets how it
 *  reads: chats only (the default), or with each Lemma's automations and
 *  the tables it changed lately; under each Lemma, or all together, newest
 *  first. People outside who wrote in are in Needs you while they wait, and
 *  on the Lemma's Contacts tab. */
export function Sidebar({ org, orgs, onPickOrg, pods, view, go, activity, needs, waitingIn, onChannels, me, onFind, onSettings, onPlan, onNewOrg, onNewLemma, open, closed, onToggle }: {
    org: Org | null;
    orgs: readonly Org[];
    onPickOrg: (id: string) => void;
    pods: readonly Pod[];
    view: View;
    go: Go;
    activity: Map<string, ConversationRef[]>;
    /** How many things wait on the reader, across outcomes. */
    needs: number;
    /** How many of them are in each Lemma: what puts the dot on a folded
     *  one, whose own chats are not read until it is opened. */
    waitingIn: ReadonlyMap<string, number>;
    /** The shared chats read here, handed up for Home to list from. */
    onChannels: (channels: ReadonlyMap<string, readonly Channel[]>) => void;
    me: string;
    onFind: () => void;
    /** The reader's own settings and the organization's. */
    onSettings: () => void;
    /** The plan, from a warning that the allowance is close or spent. */
    onPlan: () => void;
    onNewOrg: () => void;
    /** A new Lemma, made in a dialog over the page. */
    onNewLemma: () => void;
    /** Shown on a narrow screen, over the page. */
    open: boolean;
    /** Put away by the reader: only a rail of its places shows. */
    closed: boolean;
    onToggle: () => void;
}) {
    const reader = useMe();
    const [chosen, setChosen] = useState<Map<string, boolean>>(readChosen);
    const choose = useCallback((podId: string, opened: boolean) => {
        setChosen((was) => new Map(was).set(podId, opened));
    }, []);
    useEffect(() => {
        try { localStorage.setItem(CHOSEN_KEY, JSON.stringify(Object.fromEntries(chosen))); } catch { /* kept for this visit only */ }
    }, [chosen]);
    const here = podOfView(view);
    const pins = usePins();
    const [settings, setSettings] = useState<SideSettings>(readSettings);
    useEffect(() => {
        try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(settings)); } catch { /* kept for this visit only */ }
    }, [settings]);
    const [now] = useState(() => Date.now());
    /* A Lemma's shared chats are read, and kept live, only while it shows
       them: unfolded, open in the main area, or holding a pinned chat. A
       folded Lemma is a name, and reading every Lemma's to draw a list of
       names cost two requests and a socket apiece. One list for everything
       needs every Lemma's. */
    const showsChats = useMemo(() => {
        if (settings.grouping === "together") return pods;
        const pinnedIn = new Set(pins.flatMap((pin) => (pin.open ? [pin.open.podId] : [])));
        return pods.filter((pod) => (chosen.get(pod.id) ?? pod.id === here) || pinnedIn.has(pod.id));
    }, [pods, settings.grouping, pins, chosen, here]);
    const [channels, feeds] = useChannelsByPod(showsChats, { live: true });
    useEffect(() => { onChannels(channels); }, [channels, onChannels]);
    /* Each Lemma's tables, read only once the reader asks to see the ones
       that changed, under the key its own page reads them by. */
    const tableLists = useQueries({
        queries: pods.map((pod) => ({
            queryKey: ["outcome-tables", pod.id],
            queryFn: () => source.listLibrary(pod.id, "tables", "/"),
            staleTime: 5 * 60_000,
            retry: false,
            enabled: settings.showing === "everything",
        })),
    });
    /* A new array each render; when each list was last read says when any
       of them changed. */
    const tablesAt = tableLists.map((one) => one.dataUpdatedAt).join(",");
    const { sections, together, pinned } = useMemo(() => {
        const rows = pods.map((pod, index) => {
            const { needs: waiting, rest } = chatItems({ pods: [pod], conversations: activity, channels, me: reader });
            const work = settings.showing === "chats" ? [] : workRows({
                podId: pod.id,
                conversations: activity.get(pod.id) ?? [],
                tables: settings.showing === "everything"
                    ? (tableLists[index]?.data?.items ?? []).filter((item) => item.kind === "table").map((item) => ({ name: item.name, title: readableName(item.name), updated: item.updated }))
                    : [],
                now,
            });
            return { pod, chats: [...waiting, ...rest].filter(inSidebar), work };
        });
        const live = new Map(rows.flatMap(({ chats }) => chats.map((chat) => [chat.key, rowOfChat(chat)] as const)));
        const held = new Set(pins.map((pin) => pin.key));
        /* A pinned chat is listed once, up top, not again under its Lemma. */
        const unpinned = rows.map(({ pod, chats, work }) => ({ pod, items: sectionRows(chats.filter((chat) => !held.has(chat.key)), work) }));
        return {
            sections: sidebarSections(unpinned, chosen, here),
            together: sectionRows(rows.flatMap(({ chats }) => chats.filter((chat) => !held.has(chat.key))), rows.flatMap(({ work }) => work)),
            pinned: pins.flatMap((pin) => pinnedRow(pin, live) ?? []),
        };
        /* `tableLists` is read through `tablesAt`, which changes when it does. */
    }, [pods, activity, channels, reader, pins, chosen, here, settings.showing, now, tablesAt]);
    const faces = useMemo(() => new Map(pods.map((pod) => [pod.id, pod])), [pods]);
    const at = writeView(view);

    const places: { view: View; label: string; icon: IconName; count?: number }[] = [
        { view: { name: "today" }, label: "New chat", icon: "plus" },
        { view: { name: "needs" }, label: "Needs you", icon: "inbox", count: needs },
        { view: { name: "apps" }, label: "Apps", icon: "app" },
        { view: { name: "connectors" }, label: "Connectors", icon: "plug" },
        { view: { name: "outcomes" }, label: "Lemmas", icon: "grid" },
        { view: { name: "history", podId: null }, label: "History", icon: "clock" },
    ];

    /* Put away: the places as icons on a rail, and the way to bring the
       rest back. A narrow screen's sidebar slides over the page instead, and
       opening it there shows all of it. */
    if (closed && !open) {
        return (
            <aside className="ox-side ox-side--rail" aria-label="Lemma">
                <button type="button" className="ox-rail__b" onClick={onToggle} aria-label="Open the sidebar" title="Open the sidebar (⌘\)">
                    <Icon name="sidebar" size={18} />
                </button>
                <button type="button" className="ox-rail__b" onClick={onFind} aria-label="Ask or find" title="Ask or find (⌘K)">
                    <Icon name="search" size={17} />
                </button>
                {places.map((place) => (
                    <a key={place.label} className="ox-rail__b" {...to(place.view, go)} aria-current={view.name === place.view.name ? "page" : undefined} aria-label={place.count ? `${place.label}, ${place.count}` : place.label} title={place.label}>
                        <Icon name={place.icon} size={17} />
                        {place.count ? <i className="ox-rail__dot" aria-hidden="true" /> : null}
                    </a>
                ))}
            </aside>
        );
    }

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
                <button type="button" className="ox-side__search ox-side__close" onClick={onToggle} aria-label="Close the sidebar" title="Close the sidebar (⌘\)">
                    <Icon name="sidebar" size={16} />
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
                {pinned.length > 0 && (
                    <section className="ox-pinned" aria-label="Pinned">
                        <p className="ox-pinned__t">Pinned</p>
                        {pinned.map((row) => <SideLine key={row.key} row={row} here={writeView(row.view) === at} stamped={false} link={to(row.view, go)} />)}
                    </section>
                )}
                <div className="ox-listhead">
                    <span className="ox-listhead__t">{settings.grouping === "together" ? "Recent" : "Lemmas"}</span>
                    <ListFilter pods={pods} settings={settings} onChange={setSettings} />
                    <button type="button" className="ox-listhead__b" onClick={onNewLemma} aria-label="New Lemma" title="New Lemma">
                        <Icon name="plus" size={15} />
                    </button>
                </div>
                {settings.grouping === "together" ? (
                    <Together rows={together} faces={faces} view={view} go={go} />
                ) : sections.map((section) => (
                    <Section
                        key={section.pod.id}
                        pod={section.pod}
                        items={section.items}
                        waiting={Math.max(section.waiting, waitingIn.get(section.pod.id) ?? 0)}
                        unfolded={section.open}
                        view={view}
                        here={here === section.pod.id && view.name !== "chats"}
                        onFold={() => choose(section.pod.id, !section.open)}
                        go={go}
                    />
                ))}
            </nav>
            {/* Silent unless the allowance is close or spent; above the
                account, because it is about the account. */}
            <AllowanceNote orgId={org?.id ?? null} onOpenPlan={onPlan} />
            <div className="ox-side__foot" aria-label={me}>
                <HumanProfile compact onOpen={onSettings} />
                <HelpMenu nudge={false} onNudged={NOTHING} />
            </div>
        </aside>
    );
}

/** One Lemma: its face and name, which open it, the chevron after the name,
 *  which folds and unfolds it, and -- unless it is folded -- its chats under
 *  it. A + at the row's end, waiting for a pointer, starts a chat with it.
 *  The chats waiting on the reader and the open one always show; the rest
 *  fold past a few. */
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
    /* Only a Lemma that is showing chats stands apart from its neighbours.
       One with none yet has nothing to unfold, so it reads as a name in the
       list and offers no chevron. */
    const showing = unfolded && items.length > 0;
    return (
        <section className={"ox-group" + (unfolded ? "" : " ox-group--folded") + (showing ? " ox-group--showing" : "")} aria-label={pod.name}>
            <div className="ox-group__head">
                <a className="ox-group__name" {...to(outcomeView(pod.id), go)} aria-current={here ? "page" : undefined}>
                    <span className="ox-group__face"><TeammateFace pod={pod} size={26} /></span>
                    <span className="ox-group__t">{pod.name}</span>
                </a>
                {items.length > 0 && (
                    <button type="button" className="ox-group__chev" aria-expanded={unfolded} aria-label={unfolded ? `Fold ${pod.name}` : `Show ${pod.name}’s chats`} onClick={onFold}>
                        <Icon name="chevron" size={12} />
                    </button>
                )}
                <span className="ox-group__fill" />
                {!unfolded && waiting > 0 && <i className="ox-sign ox-sign--ask" role="img" aria-label={`${waiting} waiting on you`} />}
                <a className="ox-group__add" {...to(outcomeView(pod.id), go)} aria-label={`New chat with ${pod.name}`} title="New chat">
                    <Icon name="plus" size={15} />
                </a>
            </div>
            {showing && (
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

/** Every Lemma's lines in one list, newest first -- what waits on the
 *  reader before the rest -- each with its Lemma's face where the mark of
 *  what it is would be, since that is what tells them apart here. */
function Together({ rows, faces, view, go }: {
    rows: readonly SideRow[];
    faces: ReadonlyMap<string, Pod>;
    view: View;
    go: Go;
}) {
    const [all, setAll] = useState(false);
    const { shown, hidden } = all ? { shown: [...rows], hidden: 0 } : foldSection(rows, view, TOGETHER);
    const at = writeView(view);
    if (rows.length === 0) return <p className="ox-listhead__none">Nothing recent yet.</p>;
    return (
        <div className="ox-group__rows">
            {shown.map((row) => {
                const pod = faces.get(row.podId);
                return <SideLine key={row.key} row={row} here={writeView(row.view) === at} stamped={false} link={to(row.view, go)} lead={pod ? <TeammateFace pod={pod} size={16} /> : undefined} />;
            })}
            {(hidden > 0 || all) && rows.length > TOGETHER && (
                <button type="button" className="ox-group__more" onClick={() => setAll((was) => !was)}>{all ? "Show fewer" : `Show ${hidden} more`}</button>
            )}
        </div>
    );
}

const SHOWING: readonly { value: SideShowing; label: string }[] = [
    { value: "chats", label: "Chats only" },
    { value: "automations", label: "Chats and automations" },
    { value: "everything", label: "Chats, automations and tables" },
];

const GROUPING: readonly { value: SideGrouping; label: string }[] = [
    { value: "lemma", label: "By Lemma" },
    { value: "together", label: "All together, newest first" },
];

/** The list header's filter: what each Lemma lists, how the list is
 *  grouped, and which apps sit pinned at the top. A menu set over the page
 *  rather than inside the scrolling list, so nothing in it is cut off; the
 *  button wears a dot while the list reads differently from the default. */
function ListFilter({ pods, settings, onChange }: { pods: readonly Pod[]; settings: SideSettings; onChange: (next: SideSettings) => void }) {
    const button = useRef<HTMLButtonElement>(null);
    const panel = useRef<HTMLDivElement>(null);
    const [at, setAt] = useState<{ top: number; left: number } | null>(null);
    const close = useCallback(() => setAt(null), []);
    useEffect(() => {
        if (!at) return;
        const away = (event: PointerEvent) => {
            const target = event.target as Node;
            if (!panel.current?.contains(target) && !button.current?.contains(target)) close();
        };
        const keys = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            close();
            button.current?.focus();
        };
        window.addEventListener("pointerdown", away);
        window.addEventListener("keydown", keys);
        window.addEventListener("resize", close);
        return () => {
            window.removeEventListener("pointerdown", away);
            window.removeEventListener("keydown", keys);
            window.removeEventListener("resize", close);
        };
    }, [at, close]);
    const toggle = () => {
        if (at) return close();
        const box = button.current?.getBoundingClientRect();
        if (box) setAt({ top: box.bottom + 6, left: Math.max(8, Math.min(box.left - 8, window.innerWidth - 276)) });
    };
    const changed = settings.showing !== SIDE_DEFAULTS.showing || settings.grouping !== SIDE_DEFAULTS.grouping;
    return (
        <>
            <button
                ref={button}
                type="button"
                className={"ox-listhead__b" + (changed ? " ox-listhead__b--set" : "")}
                aria-haspopup="menu"
                aria-expanded={at !== null}
                aria-label="What the list shows"
                title="What the list shows"
                onClick={toggle}
            >
                <Icon name="filter" size={15} />
            </button>
            {at && (
                <div ref={panel} className="ox-menu" role="menu" aria-label="What the list shows" style={{ top: at.top, left: at.left, maxHeight: `calc(100dvh - ${at.top + 12}px)` }}>
                    <p className="ox-menu__h">Show</p>
                    {SHOWING.map((one) => (
                        <button key={one.value} type="button" role="menuitemradio" aria-checked={settings.showing === one.value} className="ox-menu__i" onClick={() => onChange({ ...settings, showing: one.value })}>
                            <span className="ox-menu__mark">{settings.showing === one.value && <Icon name="check" size={14} />}</span>
                            {one.label}
                        </button>
                    ))}
                    <p className="ox-menu__h">Group</p>
                    {GROUPING.map((one) => (
                        <button key={one.value} type="button" role="menuitemradio" aria-checked={settings.grouping === one.value} className="ox-menu__i" onClick={() => onChange({ ...settings, grouping: one.value })}>
                            <span className="ox-menu__mark">{settings.grouping === one.value && <Icon name="check" size={14} />}</span>
                            {one.label}
                        </button>
                    ))}
                    <p className="ox-menu__h">Pin apps</p>
                    <PinApps pods={pods} />
                </div>
            )}
        </>
    );
}

/** Every app across the Lemmas, each pinned to the top of the sidebar or
 *  not with a click. Read only while the menu is open. */
function PinApps({ pods }: { pods: readonly Pod[] }) {
    const { apps, pending } = useApps(pods);
    const pinned = new Set(usePins().flatMap((pin) => (pin.app ? [pin.key] : [])));
    if (apps.length === 0) return <p className="ox-menu__none">{pending ? "Loading apps…" : "No apps yet."}</p>;
    return (
        <>
            {apps.map(({ pod, app }) => {
                const name = app.id.replace(/^app:/, "");
                const on = pinned.has(appPinKey(pod.id, name));
                return (
                    <button key={pod.id + app.id} type="button" role="menuitemcheckbox" aria-checked={on} className="ox-menu__i" onClick={() => toggleAppPin(pod.id, name, app.label)}>
                        <span className="ox-menu__mark">{on && <Icon name="check" size={14} />}</span>
                        <span className="ox-menu__t">{app.label}</span>
                        {pods.length > 1 && <span className="ox-menu__sub">{pod.name}</span>}
                    </button>
                );
            })}
        </>
    );
}

/** For a line that opens a chat with a Lemma: the handlers that read the
 *  chat ahead, once a pointer rests on the line or it takes focus, so it
 *  opens drawn. Anything else is opened as it is. */
export function readsAhead(view: View): ReturnType<typeof prefetchOnIntent> | undefined {
    if (view.name !== "chats" || view.open.kind !== "chat" || view.open.id === NEW_CHAT) return undefined;
    return prefetchOnIntent(view.open.podId, view.open.id);
}

/** What a line is, in the glyph column: the platform a chat lives on
 *  (WhatsApp, Telegram, Slack, Teams, email), # for a chat shared with
 *  people here, a lock for a private one, a bell for the notices the Lemma
 *  sends, a plain ring for a chat with the Lemma -- and for its own work, an
 *  automation's mark or a table's. */
export function markOf(row: SideRow): ReactNode {
    if (row.kind === "app") return <Icon name="app" size={14} />;
    if (row.kind === "automation") return <Icon name="flow" size={14} />;
    if (row.kind === "table") return <Icon name="table" size={14} />;
    if (row.channel) return <ChannelMark channel={row.channel} size={14} />;
    if (row.platforms[0]) return <ChannelIcon platform={row.platforms[0]} size={14} />;
    return <i className="ox-ring" aria-hidden="true" />;
}

export function kindOf(row: SideRow): string {
    if (row.kind === "app") return "App";
    if (row.kind === "automation") return row.runs && row.runs > 1 ? `Automation · ${row.runs} runs` : "Automation";
    if (row.kind === "table") return "Table, changed lately";
    if (row.kind === "line") return "Your own chat";
    const places = row.platforms.map(channelName).join(", ");
    if (row.kind === "shared") return places ? "Group on " + places : "Shared chat";
    return places ? "Chat from " + places : "Chat";
}

/** A line: what it is in the glyph column, its name on the edge every name
 *  starts on, and at the end one sign at most -- a question waiting on the
 *  reader (a violet dot), new lines (a count), work going on in it (a violet
 *  ring), or, for a name its section repeats, its time. */
function SideLine({ row, here, stamped, link, lead }: {
    row: SideRow;
    here: boolean;
    stamped: boolean;
    link: ReturnType<typeof to>;
    /** In place of the mark of what it is: whose it is, in a list that
     *  mixes Lemmas. */
    lead?: ReactNode;
}) {
    return (
        <a className="ox-srow" {...link} {...readsAhead(row.view)} aria-current={here ? "page" : undefined} title={`${kindOf(row)}: ${row.title}${row.said ? " · " + row.said : ""}`}>
            <span className="ox-glyph">{lead ?? markOf(row)}</span>
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
