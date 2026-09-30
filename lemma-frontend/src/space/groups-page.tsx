"use client";

import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { useQueries } from "@tanstack/react-query";
import { source, type Group, type Pod, type Surface } from "@/data";
import {
    answering,
    byActivity,
    groupStarters,
    groupSubline,
    lastActive,
    offersTakeOn,
    platformName,
    sayAnswering,
    sinceShort,
    slackInvite,
    waitingGroups,
    type Starter,
} from "@/data/groups";
import { groupTitle } from "@/data/surface-groups";
import { surfaceStatus } from "@/data/surface-settings";
import { isForbidden } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { ChannelIcon } from "@/shell/channels";
import { useSurfaces } from "@/shell/surfaces";
import { groupKey, useGroupChange, useGroups } from "./group-queries";
import { GroupSheets, openTelegramLink, type GroupSheet } from "./group-sheets";

/** A space's groups: every WhatsApp group, Telegram group and Slack channel
 *  its bots are in, as one place in the space.
 *
 *  Each row says what it is, who has been in it, who answers its people from
 *  outside the space, and when it last moved. What is waiting on you sits
 *  above the list, and New group offers only what the space can actually do
 *  — a platform it is not connected to is offered as connecting, never as a
 *  dead option. With no groups yet, the page is the first run: the ways in,
 *  and what happens once there. */
export function GroupsPage({ pod, onOpenGroup, onConnect }: {
    pod: Pod;
    onOpenGroup: (group: Group) => void;
    /** The space's channels, to connect or fix one. */
    onConnect: () => void;
}) {
    const groups = useGroups(pod.id);
    const surfaces = useSurfaces(pod.id);
    const [sheet, setSheet] = useState<GroupSheet | null>(null);
    const starters = useMemo(() => groupStarters(surfaces.data ?? []), [surfaces.data]);
    const list = useMemo(() => byActivity(groups.data ?? []), [groups.data]);

    /* Which agent answers a group, when it is not the space's own: said on
       its row, so a bot's group is never read as the space's. */
    const agentOf = useMemo(() => {
        const bySurface = new Map((surfaces.data ?? []).map((surface) => [surface.name, surface]));
        return (group: Group) => {
            const surface = bySurface.get(group.surfaceName);
            return surface && !surface.mine ? surface.agentName || null : null;
        };
    }, [surfaces.data]);

    const start = (starter: Starter) => {
        if (!starter.surface || starter.state !== "ready") { onConnect(); return; }
        if (starter.platform === "WHATSAPP") setSheet({ kind: "whatsapp", surface: starter.surface });
        else if (starter.platform === "SLACK") setSheet({ kind: "slack", surface: starter.surface });
        else setSheet({ kind: "telegram", surface: starter.surface });
    };

    return (
        <div className="all groups">
            {groups.isPending ? (
                <p className="all__empty" role="status">Loading groups…</p>
            ) : !groups.data ? (
                <div className="groups__trouble" role="alert">
                    <p>{isForbidden(groups.error) ? "You may not see " + pod.name + "’s groups." : "Couldn’t load the groups."}</p>
                    {!isForbidden(groups.error) && <button type="button" className="ghost-pill" onClick={() => void groups.refetch()}>Try again</button>}
                </div>
            ) : list.length === 0 ? (
                <FirstRun
                    pod={pod}
                    starters={starters}
                    loading={surfaces.isPending}
                    onStart={start}
                    onTelegramNow={(surface) => setSheet({ kind: "telegram", surface, started: openTelegramLink(pod.id, surface.name) })}
                    onConnect={onConnect}
                />
            ) : (
                <>
                    <header className="groups__head">
                        <div className="groups__title">
                            <h1>Groups</h1>
                            <p>Chats on WhatsApp, Telegram and Slack that {pod.name} is in.</p>
                        </div>
                        <NewGroup pod={pod} starters={starters} loading={surfaces.isPending} onStart={start} />
                    </header>
                    <Waiting pod={pod} groups={list} onOpenGroup={onOpenGroup} />
                    <GroupList pod={pod} groups={list} agentOf={agentOf} onOpenGroup={onOpenGroup} />
                    <p className="groups__fine">
                        In every group, people in {pod.name} are answered as themselves. People outside {pod.name} get answers from what is Public, and anything more comes to whoever answers for them.
                    </p>
                </>
            )}
            {sheet && <GroupSheets pod={pod} sheet={sheet} onClose={() => setSheet(null)} onOpenGroup={onOpenGroup} />}
        </div>
    );
}

/* ── New group ─────────────────────────────────────────────────────── */

/** One entry of New group, as its platform stands: the thing to do where it
 *  can be done, and what has to happen first where it cannot. */
function starterWords(starter: Starter, bot: string): { title: string; note: string } {
    const name = platformName(starter.platform);
    if (starter.state === "missing") {
        return {
            title: "Connect " + name + " first",
            note: starter.platform === "WHATSAPP" ? "Then " + bot + " can start groups on it."
                : starter.platform === "TELEGRAM" ? "Then add " + bot + " to any group."
                : "Then invite @" + bot + " to any channel.",
        };
    }
    if (starter.state === "unwell") {
        return { title: name + " needs attention", note: surfaceStatus(starter.surface?.status, false) + ". Open its settings first." };
    }
    if (starter.platform === "WHATSAPP") return { title: "Start a WhatsApp group", note: bot + " creates it. You share the link." };
    if (starter.platform === "TELEGRAM") return { title: "Add " + bot + " to a Telegram group", note: "Pick the group in Telegram." };
    return { title: "Add " + bot + " to a Slack channel", note: "Invite " + slackInvite(starter.surface?.handle ?? "", bot).replace(/^\/invite /, "") + " in the channel." };
}

function NewGroup({ pod, starters, loading, onStart }: {
    pod: Pod;
    starters: Starter[];
    loading: boolean;
    onStart: (starter: Starter) => void;
}) {
    const [open, setOpen] = useState(false);
    const menuId = useId();
    const button = useRef<HTMLButtonElement>(null);
    const menu = useRef<HTMLDivElement>(null);

    /* Open: the first entry has the keyboard, a click anywhere else or Escape
       closes it, and focus goes back to the button it came from. */
    useEffect(() => {
        if (!open) return;
        menu.current?.querySelector<HTMLElement>("[role=menuitem]")?.focus();
        const away = (event: MouseEvent) => {
            const target = event.target as Node;
            if (menu.current?.contains(target) || button.current?.contains(target)) return;
            setOpen(false);
        };
        document.addEventListener("mousedown", away);
        return () => document.removeEventListener("mousedown", away);
    }, [open]);

    const keys = (event: KeyboardEvent<HTMLDivElement>) => {
        const items = Array.from(menu.current?.querySelectorAll<HTMLElement>("[role=menuitem]") ?? []);
        const at = items.indexOf(document.activeElement as HTMLElement);
        if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); setOpen(false); button.current?.focus(); }
        else if (event.key === "ArrowDown") { event.preventDefault(); items[(at + 1) % items.length]?.focus(); }
        else if (event.key === "ArrowUp") { event.preventDefault(); items[(at - 1 + items.length) % items.length]?.focus(); }
        else if (event.key === "Home") { event.preventDefault(); items[0]?.focus(); }
        else if (event.key === "End") { event.preventDefault(); items.at(-1)?.focus(); }
        else if (event.key === "Tab") setOpen(false);
    };

    return (
        <div className="gnew">
            <button
                ref={button}
                type="button"
                className="pill-button gnew__button"
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                onClick={() => setOpen((was) => !was)}
            >
                New group
            </button>
            {open && (
                <div className="gnew__menu" role="menu" id={menuId} aria-label="New group" ref={menu} onKeyDown={keys}>
                    {loading && <p className="gnew__quiet" role="status">Reading the channels…</p>}
                    {!loading && starters.map((starter) => {
                        const words = starterWords(starter, pod.name);
                        return (
                            <button
                                key={starter.platform}
                                type="button"
                                role="menuitem"
                                className="gnew__item"
                                data-ready={starter.state === "ready" || undefined}
                                onClick={() => { setOpen(false); onStart(starter); }}
                            >
                                <span className="gnew__mark"><ChannelIcon platform={starter.platform} size={24} /></span>
                                <span className="gnew__text">
                                    <span className="gnew__title">{words.title}</span>
                                    <span className="gnew__note">{words.note}</span>
                                </span>
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}

/* ── waiting on you ────────────────────────────────────────────────── */

/** The groups with a question waiting on you, above the list. The question
 *  itself is read off each one's page — only the few that have any. */
function Waiting({ pod, groups, onOpenGroup }: { pod: Pod; groups: Group[]; onOpenGroup: (group: Group) => void }) {
    const owed = waitingGroups(groups).slice(0, 3);
    const details = useQueries({
        queries: owed.map((group) => ({
            queryKey: groupKey(pod.id, group.id),
            queryFn: () => source.getGroup(pod.id, group.id),
            staleTime: 30_000,
        })),
    });
    if (owed.length === 0) return null;
    return (
        <ul className="gwaits" aria-label="Waiting on you">
            {owed.map((group, index) => {
                const title = groupTitle(group);
                const question = details[index]?.data?.waiting[0]?.question ?? null;
                return (
                    <li key={group.id}>
                        <button type="button" className="gwait" onClick={() => onOpenGroup(group)}>
                            <span className="gwait__dot" aria-hidden="true" />
                            <span className="gwait__text">
                                <span className="gwait__who">
                                    {group.waitingForYou === 1 ? title + " is waiting on you." : title + " has " + group.waitingForYou + " questions for you."}
                                </span>{" "}
                                <span className="gwait__what">{question ?? "People outside " + pod.name + " asked something only you can answer."}</span>
                            </span>
                            <span className="gwait__go">Answer</span>
                        </button>
                    </li>
                );
            })}
        </ul>
    );
}

/* ── the list ──────────────────────────────────────────────────────── */

function GroupList({ pod, groups, agentOf, onOpenGroup }: {
    pod: Pod;
    groups: Group[];
    agentOf: (group: Group) => string | null;
    onOpenGroup: (group: Group) => void;
}) {
    const me = useMe();
    const change = useGroupChange(pod.id);
    const [problem, setProblem] = useState<{ id: string; text: string } | null>(null);
    const now = new Date();
    return (
        <div className="glist">
            <div className="glist__cols" aria-hidden="true">
                <span />
                <span>Group</span>
                <span>People outside {pod.name}</span>
                <span>Last message</span>
            </div>
            <ul className="glist__rows" aria-label={pod.name + "’s groups"}>
                {groups.map((group) => {
                    const title = groupTitle(group);
                    const state = answering(group, me, pod.members);
                    const taking = change.isPending && change.variables?.groupId === group.id;
                    const agent = agentOf(group);
                    const when = sinceShort(lastActive(group), now);
                    return (
                        <li key={group.id} className="grow" data-pending={group.pending || undefined}>
                            <span className="grow__mark"><ChannelIcon platform={group.platform} size={30} /></span>
                            <span className="grow__main">
                                <button type="button" className="grow__open" onClick={() => onOpenGroup(group)}>{title}</button>
                                <span className="grow__sub">{groupSubline(group, pod.name, agent)}</span>
                            </span>
                            <span className="grow__who" data-quiet={state.kind === "pending" || state.kind === "invited" || state.kind === "off" || undefined}>
                                <span className="sr-only">People outside {pod.name}: </span>
                                <span className="grow__who-text">{sayAnswering(state, pod.name)}</span>
                                {state.kind === "nobody" && offersTakeOn(state) && (
                                    <button
                                        type="button"
                                        className="grow__take"
                                        disabled={taking}
                                        aria-label={"Take on " + title + ": answer its people from outside " + pod.name}
                                        onClick={() => {
                                            setProblem(null);
                                            change.mutate({ groupId: group.id, change: { take_over: true } }, {
                                                onError: (error) => setProblem({
                                                    id: group.id,
                                                    text: isForbidden(error)
                                                        ? "Only someone who can change " + (agent ?? pod.name) + " can take this on."
                                                        : "Couldn’t take it on. Try again.",
                                                }),
                                            });
                                        }}
                                    >
                                        {taking ? "Taking it on…" : "Take it on"}
                                    </button>
                                )}
                            </span>
                            <span className="grow__when">
                                {when && <span className="sr-only">Last message </span>}
                                {when || "—"}
                            </span>
                            {problem?.id === group.id && <span className="grow__problem" role="alert">{problem.text}</span>}
                        </li>
                    );
                })}
            </ul>
        </div>
    );
}

/* ── before there are any ──────────────────────────────────────────── */

function FirstRun({ pod, starters, loading, onStart, onTelegramNow, onConnect }: {
    pod: Pod;
    starters: Starter[];
    loading: boolean;
    onStart: (starter: Starter) => void;
    /** Telegram's card opens Telegram itself, inside this click. */
    onTelegramNow: (surface: Surface) => void;
    onConnect: () => void;
}) {
    const bot = pod.name;
    const find = (platform: Starter["platform"]) => starters.find((starter) => starter.platform === platform);
    const order = [find("TELEGRAM"), find("WHATSAPP"), find("SLACK")].filter((starter): starter is Starter => Boolean(starter));
    return (
        <div className="gfirst">
            <header className="gfirst__head">
                <h1>Bring {bot} into a group chat</h1>
                <p>It answers people in {pod.name} as themselves, and everyone else from what you have marked Public.</p>
            </header>
            {loading ? (
                <p className="all__empty" role="status">Reading the channels…</p>
            ) : (
                <ul className="gfirst__ways">
                    {order.map((starter) => (
                        <FirstWay
                            key={starter.platform}
                            pod={pod}
                            starter={starter}
                            onGo={() => {
                                if (starter.state === "ready" && starter.surface && starter.platform === "TELEGRAM") onTelegramNow(starter.surface);
                                else if (starter.state === "ready") onStart(starter);
                                else onConnect();
                            }}
                        />
                    ))}
                </ul>
            )}
            <dl className="gfirst__how">
                <div>
                    <dt>Your questions</dt>
                    <dd>@mention {bot} or reply to it. It answers with your access.</dd>
                </div>
                <div>
                    <dt>Their questions</dt>
                    <dd>People outside {pod.name} get what is Public, nothing else.</dd>
                </div>
                <div>
                    <dt>Anything more</dt>
                    <dd>Comes to you here. Your answer goes back to the group.</dd>
                </div>
            </dl>
        </div>
    );
}

function FirstWay({ pod, starter, onGo }: { pod: Pod; starter: Starter; onGo: () => void }) {
    const bot = pod.name;
    const name = platformName(starter.platform);
    const ready = starter.state === "ready";
    const words = !ready
        ? {
            title: starter.platform === "SLACK" ? "Slack channels" : name + " groups",
            note: starter.state === "missing"
                ? "Connect " + name + " first. Then " + (starter.platform === "WHATSAPP" ? bot + " can start groups on it." : starter.platform === "TELEGRAM" ? "add " + bot + " to any group." : "invite @" + bot + " to any channel.")
                : name + ": " + surfaceStatus(starter.surface?.status, false).toLowerCase() + ". Open its settings first.",
            action: starter.state === "missing" ? "Connect " + name : "Open settings",
        }
        : starter.platform === "TELEGRAM"
            ? { title: "Add " + bot + " to a Telegram group", note: "One tap in Telegram. The group shows up here.", action: "Open Telegram" }
            : starter.platform === "WHATSAPP"
                ? { title: "Start a WhatsApp group", note: bot + " creates it on " + (starter.surface?.handle || "its number") + " and gives you a link to share.", action: "Start" }
                : { title: "Add " + bot + " to a Slack channel", note: "Invite " + slackInvite(starter.surface?.handle ?? "", bot).replace(/^\/invite /, "") + " in any channel. It shows up here.", action: "Add" };
    return (
        <li className="gway" data-ready={ready || undefined}>
            <span className="gway__mark"><ChannelIcon platform={starter.platform} size={40} /></span>
            <span className="gway__text">
                <span className="gway__title">{words.title}</span>
                <span className="gway__note">{words.note}</span>
            </span>
            <button
                type="button"
                className={ready && starter.platform === "TELEGRAM" ? "pill-button gway__go" : "ghost-pill gway__go"}
                onClick={onGo}
                aria-label={words.action === "Start" || words.action === "Add" ? words.title : undefined}
            >
                {words.action}
            </button>
        </li>
    );
}
