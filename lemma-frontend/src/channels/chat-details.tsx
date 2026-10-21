"use client";

import { useEffect, useId, useRef, useState, type FormEvent, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { NOWHERE, writeAddress } from "@/shell/address";
import { ChannelIcon } from "@/shell/channels";
import { Modal } from "@/shell/modal";
import { PlaceLink } from "@/shell/place-link";
import { BellIcon, CheckIcon, ChevronDownIcon, PlusIcon, SearchIcon } from "@/ui/icons";
import type { ChannelAbilities } from "./abilities";
import { AgentSwitch } from "./agent-switch";
import { AnswersSwitch } from "./answers-switch";
import { channelApi, type ChannelChange, type MineChange } from "./api";
import { Avatar } from "./avatar";
import { answersHint, placeName } from "./chat-copy";
import { ChannelMark } from "./chat-mark";
import { ContactControls } from "./contact-controls";
import { problemOf } from "./problem";
import { addable, channelTitle, personName, sinceShort, type AgentAnswers, type ChannelDetail, type NotifyLevel } from "./model";
import { channelKey, keepChannel, refreshLists } from "./queries";
import { muteChoices, mutedSaid, NOTIFY_VIA, publicWarning } from "./settings-model";
import { SourceSettingsView } from "./source-settings";
import { isGroupPlatform, platformName } from "./sources";

/** The tabs of a chat's details, the way Slack splits a channel's. */
export type DetailsTab = "about" | "members" | "agents" | "connected" | "settings";

const LEVELS: { level: NotifyLevel; label: string; short: string }[] = [
    { level: "ALL", label: "Every new message", short: "Every message" },
    { level: "MENTIONS", label: "Only @mentions and questions for me", short: "Mentions" },
    { level: "NONE", label: "Nothing", short: "Off" },
];

/** A chat's details, as a dialog over it: what it is, who is in it, which
 *  agents answer and how, where it is mirrored, and -- for whoever may --
 *  what can be done to it. Your own notifications sit under its name. What
 *  the reader may not change is shown, not offered; the API decides again,
 *  and a refusal is said where it happened. */
export function ChatDetails({ pod, channel, may, at, onAt, onClose, onLeft }: {
    pod: Pod;
    channel: ChannelDetail;
    may: ChannelAbilities;
    at: DetailsTab;
    onAt: (tab: DetailsTab) => void;
    onClose: () => void;
    /** The reader left the chat, or deleted it. */
    onLeft: () => void;
}) {
    const ids = useId();
    const people = channel.members.filter((one) => one.person.kind !== "AGENT").length;
    const agentRows = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const frontId = agentRows.data?.find((agent) => agent.front)?.id ?? null;
    /* The teammate counts whether or not it was ever seated: it is in every chat. */
    const agents = channel.members.filter((one) => one.person.kind === "AGENT" && one.person.id !== frontId).length + (frontId ? 1 : 0);
    const tabs: { at: DetailsTab; label: string; count?: number }[] = [
        { at: "about", label: "About" },
        ...(channel.kind !== "DIRECT" ? [{ at: "members" as const, label: "Members", count: people }] : []),
        ...(channel.kind === "GROUP" ? [{ at: "agents" as const, label: "Agents", count: agents }] : []),
        ...(channel.places.length > 0 ? [{ at: "connected" as const, label: "Connected" }] : []),
        ...(channel.kind === "GROUP" && may.manage ? [{ at: "settings" as const, label: "Settings" }] : []),
    ];
    const current = tabs.some((tab) => tab.at === at) ? at : "about";
    const title = channelTitle(channel, pod.name);
    return (
        <Modal title={title} mark={<ChannelMark channel={channel} size={20} />} wide flush onClose={onClose}>
            <div className="cdet">
                {channel.me.joined && <div className="cdet__bar"><Notifications pod={pod} channel={channel} /></div>}
                <div className="cdet__tabs" role="tablist" aria-label="Chat details">
                    {tabs.map((tab) => (
                        <button
                            key={tab.at}
                            type="button"
                            role="tab"
                            id={ids + "-" + tab.at}
                            className="cdet__tab"
                            aria-selected={current === tab.at}
                            aria-controls={ids + "-panel"}
                            onClick={() => onAt(tab.at)}
                        >
                            {tab.label}{tab.count !== undefined && <span className="cdet__count">{tab.count}</span>}
                        </button>
                    ))}
                </div>
                <div className="cdet__body" role="tabpanel" id={ids + "-panel"} aria-labelledby={ids + "-" + current}>
                    {current === "about" && <About pod={pod} channel={channel} may={may} onLeft={onLeft} onSettings={() => onAt("settings")} />}
                    {current === "members" && <Members pod={pod} channel={channel} may={may} onLeft={onLeft} />}
                    {current === "agents" && <Agents pod={pod} channel={channel} may={may} />}
                    {current === "connected" && <Connected pod={pod} channel={channel} />}
                    {current === "settings" && <Settings pod={pod} channel={channel} onGone={onLeft} />}
                </div>
            </div>
        </Modal>
    );
}

/* ── pieces ────────────────────────────────────────────────────────── */

/** A white card of rows, the way Slack groups what belongs together. */
function Card({ children, label }: { children: ReactNode; label?: string }) {
    return <section className="cdet__card" aria-label={label}>{children}</section>;
}

/** One row of a card: what it is, what it says, and the one thing to do. */
function Row({ label, children, action }: { label: string; children: ReactNode; action?: ReactNode }) {
    return (
        <div className="cdet__row">
            <div className="cdet__row-text">
                <div className="cdet__label">{label}</div>
                <div className="cdet__value">{children}</div>
            </div>
            {action}
        </div>
    );
}

function refusal(error: unknown, pod: Pod, what: string): string {
    return isForbidden(error) ? "Only who started this chat, or an editor of " + pod.name + ", can " + what + "." : problemOf(error, "Couldn’t " + what + ".");
}

/* ── your notifications ────────────────────────────────────────────── */

function Notifications({ pod, channel }: { pod: Pod; channel: ChannelDetail }) {
    const cache = useQueryClient();
    const [open, setOpen] = useState(false);
    const box = useRef<HTMLDivElement>(null);
    const set = useMutation({
        mutationFn: (change: MineChange) => channelApi(pod.id).setMine(channel.id, change),
        onSuccess: (saved) => keepChannel(cache, pod.id, saved),
    });
    useEffect(() => {
        if (!open) return;
        const away = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false); };
        /* Escape closes the menu, not the dialog under it: heard on the
           window, before the dialog's own listener on the document. */
        const escape = (event: KeyboardEvent) => {
            if (event.key !== "Escape") return;
            event.preventDefault();
            event.stopPropagation();
            setOpen(false);
            box.current?.querySelector<HTMLButtonElement>(".cdet__pill")?.focus();
        };
        document.addEventListener("mousedown", away);
        window.addEventListener("keydown", escape, true);
        return () => {
            document.removeEventListener("mousedown", away);
            window.removeEventListener("keydown", escape, true);
        };
    }, [open]);
    const level = set.isPending && set.variables?.notify ? set.variables.notify : channel.me.notify;
    const muted = mutedSaid(channel.me.mutedUntil);
    const via = channel.me.notifyVia?.[0] ?? "";
    const shown = muted ? muted : "Notifications: " + (LEVELS.find((one) => one.level === level)?.short ?? "Mentions");
    return (
        <div className="cdet__notify" ref={box}>
            <button type="button" className="cdet__pill" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
                <BellIcon size={15} aria-hidden="true" /> {shown} <ChevronDownIcon size={13} aria-hidden="true" />
            </button>
            {open && (
                <div className="cdet__menu" role="menu" aria-label="Notifications">
                    <p className="cdet__menu-label">Tell me about</p>
                    {LEVELS.map((one) => (
                        <button key={one.level} type="button" role="menuitemradio" aria-checked={level === one.level} className="cdet__menu-item" disabled={set.isPending} onClick={() => { set.mutate({ notify: one.level }); setOpen(false); }}>
                            <span className="cdet__tick" aria-hidden="true">{level === one.level && <CheckIcon size={14} />}</span>{one.label}
                        </button>
                    ))}
                    <span className="cdet__menu-sep" role="separator" />
                    {muted ? (
                        <button type="button" role="menuitem" className="cdet__menu-item" disabled={set.isPending} onClick={() => { set.mutate({ mutedUntil: null }); setOpen(false); }}>
                            <span className="cdet__tick" aria-hidden="true" />Unmute
                        </button>
                    ) : muteChoices().map((one) => (
                        <button key={one.label} type="button" role="menuitem" className="cdet__menu-item" disabled={set.isPending} onClick={() => { set.mutate({ mutedUntil: one.until }); setOpen(false); }}>
                            <span className="cdet__tick" aria-hidden="true" />Mute {one.label.toLowerCase()}
                        </button>
                    ))}
                    <span className="cdet__menu-sep" role="separator" />
                    <label className="cdet__menu-field">
                        <span>Outside the app, on</span>
                        <select value={via} disabled={set.isPending} onChange={(event) => set.mutate({ notifyVia: event.target.value ? [event.target.value] : null })}>
                            <option value="">Wherever {pod.name} reaches me</option>
                            {NOTIFY_VIA.map((one) => <option key={one.value} value={one.value}>{one.label}</option>)}
                        </select>
                    </label>
                </div>
            )}
            {set.isError && <p className="cdet__problem" role="alert">{problemOf(set.error, "Couldn’t change that.")}</p>}
        </div>
    );
}

/* ── about ─────────────────────────────────────────────────────────── */

function About({ pod, channel, may, onLeft, onSettings }: { pod: Pod; channel: ChannelDetail; may: ChannelAbilities; onLeft: () => void; onSettings: () => void }) {
    const me = useMe();
    const cache = useQueryClient();
    const [editing, setEditing] = useState<"title" | "topic" | null>(null);
    const [draft, setDraft] = useState("");
    const teammate = pod.teammate?.name || pod.name;
    const update = useMutation({
        mutationFn: (body: ChannelChange) => channelApi(pod.id).update(channel.id, body),
        onSuccess: (saved) => { keepChannel(cache, pod.id, saved); setEditing(null); },
    });
    const leave = useMutation({
        mutationFn: () => channelApi(pod.id).leave(channel.id),
        onSuccess: () => { refreshLists(cache, pod.id); onLeft(); },
    });
    const start = (field: "title" | "topic") => { setDraft((field === "title" ? channel.title : channel.topic) ?? ""); setEditing(field); };
    const save = (event: FormEvent) => {
        event.preventDefault();
        if (editing === "title" && draft.trim()) update.mutate({ title: draft.trim() });
        if (editing === "topic") update.mutate({ topic: draft.trim() || null });
    };
    const editor = (field: "title" | "topic") => (
        <form className="cdet__edit" onSubmit={save}>
            <input autoFocus value={draft} maxLength={field === "title" ? 200 : 500} required={field === "title"} placeholder={field === "title" ? "Launch plan" : "What this chat is for"} onChange={(event) => setDraft(event.target.value)} />
            <button type="button" className="ghost-pill" onClick={() => setEditing(null)}>Cancel</button>
            <button type="submit" className="pill-button" disabled={update.isPending || (field === "title" && !draft.trim())}>{update.isPending ? "Saving…" : "Save"}</button>
        </form>
    );
    const edit = (field: "title" | "topic") => may.manage && editing !== field ? <button type="button" className="cdet__link" onClick={() => start(field)}>Edit</button> : null;

    if (channel.kind === "DIRECT") {
        return (
            <Card>
                <Row label="What it is">Your private line with {teammate}. {answersHint(channel, teammate)} Nobody else reads it.</Row>
                {channel.places.length > 0 && <Row label="Reached on">{channel.places.map((place) => platformName(place.platform)).join(", ")}</Row>}
            </Card>
        );
    }
    if (channel.kind === "CONTACT") {
        return (
            <>
                <Card>
                    <Row label="Who" action={<PlaceLink className="cdet__link" href={writeAddress({ ...NOWHERE, podId: pod.id, tabId: "space:contacts" })}>Open in Contacts</PlaceLink>}>
                        {personName(channel.person, me)}, outside {pod.name}. Only its editors and admins read this chat.
                    </Row>
                    {channel.attention && !channel.assignee && (
                        <Row label="Needs a person">{channel.attention.reason ?? "They asked for one."} <span className="cdet__muted">{sinceShort(channel.attention.since)}</span></Row>
                    )}
                </Card>
                {may.contactOps && (
                    <Card label="Who answers them">
                        <Row label={teammate + " answers them"} action={<AgentSwitch podId={pod.id} channel={channel} />}>
                            Off, only people answer them until you switch it back on.
                        </Row>
                        <div className="cdet__row"><ContactControls pod={pod} channel={channel} /></div>
                    </Card>
                )}
            </>
        );
    }
    return (
        <>
            <Card>
                <Row label="Chat name" action={edit("title")}>{editing === "title" ? editor("title") : channelTitle(channel, pod.name)}</Row>
                <Row label="Topic" action={edit("topic")}>{editing === "topic" ? editor("topic") : channel.topic ?? <span className="cdet__muted">{may.manage ? "Add a topic" : "No topic"}</span>}</Row>
                <Row label="Who answers">{answersHint(channel, teammate)}</Row>
                <div className="cdet__row"><AnswersSwitch podId={pod.id} channel={channel} manage={may.manage} /></div>
                <Row label="Who can find it" action={may.manage ? <button type="button" className="cdet__link" onClick={onSettings}>Change</button> : null}>
                    {channel.visibility === "PRIVATE" ? "Only the people in it." : "Anyone in " + pod.name + " can find it and join."}
                </Row>
                <Row label="Started">{new Date(channel.createdAt).toLocaleDateString([], { day: "numeric", month: "long", year: "numeric" })}</Row>
                {update.isError && <p className="cdet__problem" role="alert">{refusal(update.error, pod, "change it")}</p>}
            </Card>
            {channel.me.joined && (
                <Card>
                    <button type="button" className="cdet__danger-row" disabled={leave.isPending} onClick={() => leave.mutate()}>{leave.isPending ? "Leaving…" : "Leave chat"}</button>
                    {leave.isError && <p className="cdet__problem" role="alert">{problemOf(leave.error, "Couldn’t leave it.")}</p>}
                </Card>
            )}
        </>
    );
}

/* ── members ───────────────────────────────────────────────────────── */

function Members({ pod, channel, may, onLeft }: { pod: Pod; channel: ChannelDetail; may: ChannelAbilities; onLeft: () => void }) {
    const me = useMe();
    const cache = useQueryClient();
    const [query, setQuery] = useState("");
    const [adding, setAdding] = useState(false);
    const needle = query.trim().toLowerCase();
    const matches = (name: string) => !needle || name.toLowerCase().includes(needle);
    const people = channel.members.filter((one) => one.person.kind !== "AGENT" && matches(personName(one.person, me) + " " + (one.person.name ?? "")));
    const candidates = may.manage ? addable(pod.members, channel.members).filter((member) => matches(member.name)) : [];
    const reread = () => void cache.invalidateQueries({ queryKey: channelKey(pod.id, channel.id) });
    const add = useMutation({
        mutationFn: (userId: string) => channelApi(pod.id).addMembers(channel.id, { people: [userId] }),
        onSuccess: (saved) => keepChannel(cache, pod.id, saved),
    });
    const role = useMutation({
        mutationFn: ({ id, owner }: { id: string; owner: boolean }) => channelApi(pod.id).updateMember(channel.id, { kind: "MEMBER", id }, { role: owner ? "owner" : "member" }),
        onSuccess: reread,
    });
    const remove = useMutation({
        mutationFn: (id: string) => id === me ? channelApi(pod.id).leave(channel.id) : channelApi(pod.id).removeMember(channel.id, { kind: "MEMBER", id }),
        onSuccess: (_done, id) => {
            if (id === me) { refreshLists(cache, pod.id); onLeft(); return; }
            reread();
        },
    });
    const join = useMutation({
        mutationFn: () => channelApi(pod.id).join(channel.id),
        onSuccess: (saved) => keepChannel(cache, pod.id, saved),
    });
    const group = channel.kind === "GROUP";
    return (
        <>
            <label className="cdet__search">
                <SearchIcon size={15} aria-hidden="true" />
                <span className="sr-only">Find people</span>
                <input type="search" value={query} placeholder="Find people" onChange={(event) => setQuery(event.target.value)} />
            </label>
            <Card>
                {group && !channel.me.joined && (
                    <div className="cdet__person">
                        <span className="cdet__person-text">You are not in it.</span>
                        <button type="button" className="pill-button" disabled={join.isPending} onClick={() => join.mutate()}>{join.isPending ? "Joining…" : "Join"}</button>
                    </div>
                )}
                {may.manage && (
                    <button type="button" className="cdet__person cdet__person--add" aria-expanded={adding} onClick={() => setAdding((was) => !was)}>
                        <span className="cdet__add-mark" aria-hidden="true"><PlusIcon size={18} /></span>
                        <span className="cdet__person-text">Add people</span>
                    </button>
                )}
                {adding && (candidates.length === 0 ? (
                    <p className="cdet__note">{needle ? "Nobody else in " + pod.name + " matches." : "Everyone in " + pod.name + " is in it already."}</p>
                ) : candidates.map((member) => (
                    <div key={member.userId} className="cdet__person cdet__person--candidate">
                        <Avatar person={{ kind: "MEMBER", id: member.userId ?? null, name: member.name }} size={32} />
                        <span className="cdet__person-text"><span className="cdet__name">{member.name}</span></span>
                        <button type="button" className="ghost-pill" disabled={add.isPending} onClick={() => member.userId && add.mutate(member.userId)}>Add</button>
                    </div>
                )))}
                {people.map((one) => {
                    const id = one.person.id;
                    const you = one.person.kind === "MEMBER" && id === me;
                    const full = one.person.name ?? personName(one.person, me);
                    const what = one.person.kind === "CONTACT" ? "Outside " + pod.name : one.role === "owner" ? "Owner" : null;
                    return (
                        <div key={one.person.kind + (id ?? full)} className="cdet__person">
                            <Avatar person={one.person} size={32} />
                            <span className="cdet__person-text">
                                <span className="cdet__name" title={full}>{full}{you && <span className="cdet__muted"> (you)</span>}</span>
                                {what && <span className="cdet__muted">{what}</span>}
                            </span>
                            {group && one.person.kind === "MEMBER" && id && (
                                <span className="cdet__acts">
                                    {may.manage && !you && (
                                        <button type="button" className="cdet__link" disabled={role.isPending} onClick={() => role.mutate({ id, owner: one.role !== "owner" })}>
                                            {one.role === "owner" ? "Remove as owner" : "Make owner"}
                                        </button>
                                    )}
                                    {(you || may.manage) && (
                                        <button type="button" className="cdet__link cdet__link--bad" disabled={remove.isPending} aria-label={you ? "Leave this chat" : "Take " + full + " out of this chat"} onClick={() => remove.mutate(id)}>
                                            {you ? "Leave" : "Remove"}
                                        </button>
                                    )}
                                </span>
                            )}
                        </div>
                    );
                })}
                {people.length === 0 && needle && <p className="cdet__note">Nobody in it matches.</p>}
            </Card>
            {(add.isError || role.isError || remove.isError || join.isError) && (
                <p className="cdet__problem" role="alert">
                    {isForbidden(add.error ?? role.error ?? remove.error) ? "Only who started this chat, or an editor, can change who is in it." : problemOf(add.error ?? role.error ?? remove.error ?? join.error, "Couldn’t do that.")}
                </p>
            )}
        </>
    );
}

/* ── agents ────────────────────────────────────────────────────────── */

const ANSWERS: { answers: AgentAnswers; label: string }[] = [
    { answers: "MENTIONED", label: "When @mentioned" },
    { answers: "EVERY_LINE", label: "Every message" },
];

function Agents({ pod, channel, may }: { pod: Pod; channel: ChannelDetail; may: ChannelAbilities }) {
    const cache = useQueryClient();
    const [adding, setAdding] = useState(false);
    const rows = useQuery({ queryKey: ["agents", pod.id], queryFn: () => source.listAgents(pod.id), staleTime: 5 * 60_000 });
    const teammate = pod.teammate?.name || pod.name;
    /* The space's own teammate is in every chat here, seated or not: named,
       it answers. So it is listed first and never offered as something to
       add -- only when it answers can change. */
    const front = rows.data?.find((agent) => agent.front && agent.id) ?? null;
    const seated = channel.members.filter((one) => one.person.kind === "AGENT");
    const frontSeat = front ? seated.find((one) => one.person.id === front.id) ?? null : null;
    const others = seated.filter((one) => !front || one.person.id !== front.id);
    const seatedIds = new Set(seated.map((one) => one.person.id));
    const candidates = (rows.data ?? []).filter((agent) => agent.id && !agent.front && !seatedIds.has(agent.id));
    const rowOf = (id: string | null) => rows.data?.find((agent) => agent.id === id);
    const reread = () => void cache.invalidateQueries({ queryKey: channelKey(pod.id, channel.id) });
    const add = useMutation({
        mutationFn: ({ id, answers }: { id: string; answers: AgentAnswers }) => channelApi(pod.id).addMembers(channel.id, { agents: [{ id, answers }] }),
        onSuccess: (saved) => { keepChannel(cache, pod.id, saved); setAdding(false); },
    });
    const change = useMutation({
        mutationFn: ({ id, answers }: { id: string; answers: AgentAnswers }) => channelApi(pod.id).updateMember(channel.id, { kind: "AGENT", id }, { answers }),
        onSuccess: reread,
    });
    const remove = useMutation({
        mutationFn: (id: string) => channelApi(pod.id).removeMember(channel.id, { kind: "AGENT", id }),
        onSuccess: reread,
    });
    /* When an agent answers: a seated one changes its seat; the teammate,
       not seated yet, is seated answering that way. */
    const answerWith = (id: string, answers: AgentAnswers, isSeated: boolean) =>
        isSeated ? change.mutate({ id, answers }) : add.mutate({ id, answers });
    const pending = add.isPending || change.isPending || remove.isPending;
    const row = (id: string, name: string, answers: AgentAnswers, note: string, isSeated: boolean, removable: boolean) => (
        <div key={"agent" + id} className="cdet__person">
            <Avatar person={{ kind: "AGENT", id, name }} size={32} />
            <span className="cdet__person-text">
                <span className="cdet__name">{name}</span>
                <span className="cdet__muted">{note}</span>
            </span>
            {may.manage ? (
                <span className="cdet__acts cdet__acts--shown">
                    <label className="cdet__select">
                        <span className="sr-only">When {name} answers</span>
                        <select value={answers} disabled={pending} onChange={(event) => answerWith(id, event.target.value as AgentAnswers, isSeated)}>
                            {ANSWERS.map((choice) => <option key={choice.answers} value={choice.answers}>{choice.label}</option>)}
                        </select>
                    </label>
                    {removable && <button type="button" className="cdet__link cdet__link--bad" disabled={pending} aria-label={"Take " + name + " out of this chat"} onClick={() => remove.mutate(id)}>Remove</button>}
                </span>
            ) : null}
        </div>
    );
    return (
        <>
            <p className="cdet__lead">{teammate} is in every chat here. Other agents answer only where they are added. Each one answers with the access of whoever asked.</p>
            <Card>
                {front && row(
                    front.id,
                    front.label,
                    frontSeat?.answers ?? "MENTIONED",
                    (frontSeat?.answers === "EVERY_LINE" ? "Answers every message" : "Answers when @mentioned") + " · always here",
                    Boolean(frontSeat),
                    false,
                )}
                {others.map((one) => {
                    const id = one.person.id;
                    if (!id) return null;
                    const name = rowOf(id)?.label ?? one.person.name ?? "An agent";
                    return row(id, name, one.answers ?? "MENTIONED", one.answers === "EVERY_LINE" ? "Answers every message" : "Answers when @mentioned", true, true);
                })}
                {may.manage && candidates.length > 0 && (
                    <button type="button" className="cdet__person cdet__person--add" aria-expanded={adding} onClick={() => setAdding((was) => !was)}>
                        <span className="cdet__add-mark" aria-hidden="true"><PlusIcon size={18} /></span>
                        <span className="cdet__person-text">Add another agent</span>
                    </button>
                )}
                {adding && candidates.map((agent) => (
                    <div key={agent.id} className="cdet__person cdet__person--candidate">
                        <Avatar person={{ kind: "AGENT", id: agent.id, name: agent.label }} size={32} />
                        <span className="cdet__person-text"><span className="cdet__name">{agent.label}</span>{agent.blurb && <span className="cdet__muted cdet__clip">{agent.blurb}</span>}</span>
                        <button type="button" className="ghost-pill" disabled={pending} onClick={() => add.mutate({ id: agent.id, answers: "MENTIONED" })}>Add</button>
                    </div>
                ))}
            </Card>
            {(add.isError || change.isError || remove.isError) && <p className="cdet__problem" role="alert">{problemOf(add.error ?? change.error ?? remove.error, "Couldn’t do that.")}</p>}
        </>
    );
}

/* ── where it is mirrored ──────────────────────────────────────────── */

function Connected({ pod, channel }: { pod: Pod; channel: ChannelDetail }) {
    const [open, setOpen] = useState<string | null>(null);
    return (
        <>
            <p className="cdet__lead">
                {channel.kind === "GROUP"
                    ? "Messages in these places show up here, and what you send here goes back to the place it answers."
                    : "Where messages in this chat come from, and go back to."}
            </p>
            <Card>
                {channel.places.map((place) => {
                    const settled = channel.kind === "GROUP" && isGroupPlatform(place.platform);
                    return (
                        <div key={place.id} className="cdet__place">
                            <div className="cdet__person">
                                <span className="cdet__add-mark cdet__add-mark--plain" aria-hidden="true"><ChannelIcon platform={place.platform} size={20} /></span>
                                <span className="cdet__person-text">
                                    <span className="cdet__name">{place.title ?? platformName(place.platform)}</span>
                                    <span className="cdet__muted">{placeName({ platform: place.platform, title: null })}{place.pending ? " · being created" : place.lastInboundAt ? " · last message " + sinceShort(place.lastInboundAt) + " ago" : ""}</span>
                                </span>
                                {settled && (
                                    <button type="button" className="cdet__link" aria-expanded={open === place.id} onClick={() => setOpen((was) => (was === place.id ? null : place.id))}>
                                        {open === place.id ? "Hide settings" : "Settings"}
                                    </button>
                                )}
                            </div>
                            {open === place.id && <div className="cdet__place-settings"><SourceSettingsView pod={pod} channelId={channel.id} sourceId={place.id} /></div>}
                        </div>
                    );
                })}
            </Card>
        </>
    );
}

/* ── settings ──────────────────────────────────────────────────────── */

function Settings({ pod, channel, onGone }: { pod: Pod; channel: ChannelDetail; onGone: () => void }) {
    const cache = useQueryClient();
    const [asking, setAsking] = useState<"public" | "delete" | null>(null);
    const update = useMutation({
        mutationFn: (body: ChannelChange) => channelApi(pod.id).update(channel.id, body),
        onSuccess: (saved) => { keepChannel(cache, pod.id, saved); setAsking(null); },
    });
    const destroy = useMutation({
        mutationFn: () => channelApi(pod.id).destroy(channel.id),
        onSuccess: () => { refreshLists(cache, pod.id); cache.removeQueries({ queryKey: channelKey(pod.id, channel.id) }); onGone(); },
    });
    const isPrivate = channel.visibility === "PRIVATE";
    const deletable = channel.places.length === 0;
    const action = (label: string, onClick: () => void, bad = false) => (
        <button type="button" className={bad ? "ghost-pill cdet__bad" : "ghost-pill"} disabled={update.isPending || destroy.isPending} onClick={onClick}>{label}</button>
    );
    return (
        <>
            <Card>
                <Row label={isPrivate ? "Make it public" : "Make it private"} action={action(isPrivate ? "Make public" : "Make private", () => isPrivate ? setAsking("public") : update.mutate({ visibility: "PRIVATE" }))}>
                    {isPrivate ? "Anyone in " + pod.name + " could find it, join, and read everything said in it." : "Only the people in it will see it. Nobody else can find it or join."}
                </Row>
                {asking === "public" && (
                    <div className="cdet__confirm" role="group" aria-label="Make this chat public">
                        <p>{publicWarning(channel.title, pod.name)}</p>
                        <button type="button" className="ghost-pill" onClick={() => setAsking(null)}>Keep it private</button>
                        <button type="button" className="pill-button" disabled={update.isPending} onClick={() => update.mutate({ visibility: "PUBLIC" })}>{update.isPending ? "Saving…" : "Make it public"}</button>
                    </div>
                )}
                <Row label={channel.archived ? "Bring it back" : "Archive this chat"} action={action(channel.archived ? "Bring back" : "Archive", () => update.mutate({ archived: !channel.archived }))}>
                    {channel.archived ? "It is listed again, and people can write in it." : "Nobody can write in it, and it leaves everyone’s list. You can bring it back."}
                </Row>
                {deletable && (
                    <Row label="Delete this chat" action={action("Delete", () => setAsking("delete"), true)}>
                        Everything said in it goes, for everyone. This cannot be undone.
                    </Row>
                )}
                {asking === "delete" && (
                    <div className="cdet__confirm" role="group" aria-label="Delete this chat">
                        <p>Delete {channel.title ?? "this chat"} and everything said in it?</p>
                        <button type="button" className="ghost-pill" onClick={() => setAsking(null)}>Keep it</button>
                        <button type="button" className="pill-button pill-button--bad" disabled={destroy.isPending} onClick={() => destroy.mutate()}>{destroy.isPending ? "Deleting…" : "Delete"}</button>
                    </div>
                )}
            </Card>
            {update.isError && <p className="cdet__problem" role="alert">{refusal(update.error, pod, "change it")}</p>}
            {destroy.isError && <p className="cdet__problem" role="alert">{refusal(destroy.error, pod, "delete it")}</p>}
        </>
    );
}
