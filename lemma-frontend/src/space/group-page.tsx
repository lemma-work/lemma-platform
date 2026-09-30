"use client";

import { useEffect, useId, useMemo, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type GroupDetail, type GroupLine, type GroupPerson, type GroupQuestion, type Pod, type Surface } from "@/data";
import {
    answeredNote,
    answering,
    groupHeadline,
    howToAsk,
    howToRemove,
    keepsTimeline,
    lineAuthor,
    offersTakeOn,
    personInitials,
    platformName,
    sayAnswering,
    sayStanding,
    sinceShort,
    slackChannelUrl,
    standing,
    timelineDays,
    whomItAnswers,
} from "@/data/groups";
import { groupTitle } from "@/data/surface-groups";
import { isForbidden, isMissing } from "@/session/auth-state";
import { useMe } from "@/session/use-me";
import { ChannelIcon } from "@/shell/channels";
import { useSurfaces } from "@/shell/surfaces";
import { ExternalIcon } from "@/ui/icons";
import { AgentMark } from "./agent-mark";
import { groupKey, refreshGroups, timelineKey, useGroupChange } from "./group-queries";
import { TeammateFace } from "./teammate-face";

/** How often an open group's page reads it again: its people talk, and a
 *  question can arrive while you are looking. Only while it is on screen. */
const READ_EVERY_MS = 20_000;

/** One group, in one place: who is in it, what its people outside the space
 *  are waiting on you for, and everything said there — beside how the bot
 *  behaves in it and who answers for the people it does not know. */
export function GroupPage({ pod, groupId, visible, onNamed, onOpenGroups, onInvite }: {
    pod: Pod;
    groupId: string;
    /** On screen, so worth reading again every so often. */
    visible: boolean;
    /** The page knows the group's name before the tab does. */
    onNamed: (title: string) => void;
    onOpenGroups: () => void;
    /** The space's own way to add people. */
    onInvite: () => void;
}) {
    const detail = useQuery({
        queryKey: groupKey(pod.id, groupId),
        queryFn: () => source.getGroup(pod.id, groupId),
        refetchInterval: visible ? READ_EVERY_MS : false,
        staleTime: 10_000,
        retry: (count, error) => !isMissing(error) && !isForbidden(error) && count < 3,
    });
    const surfaces = useSurfaces(pod.id);
    const group = detail.data ?? null;
    const title = group ? groupTitle(group) : null;
    useEffect(() => { if (title) onNamed(title); }, [title, onNamed]);

    if (detail.isPending) return <div className="gpage"><p className="gpage__quiet" role="status">Loading the group…</p></div>;
    /* A read that fails after one worked keeps the page it had. */
    if (!group) {
        const gone = isMissing(detail.error);
        return (
            <div className="gpage">
                <div className="gpage__gone" role="alert">
                    <p>{gone ? "This group is not here any more." : isForbidden(detail.error) ? "You may not open this group." : "Couldn’t load this group."}</p>
                    <div className="gpage__gone-acts">
                        {!gone && !isForbidden(detail.error) && <button type="button" className="ghost-pill" onClick={() => void detail.refetch()}>Try again</button>}
                        <button type="button" className="ghost-pill" onClick={onOpenGroups}>All groups</button>
                    </div>
                </div>
            </div>
        );
    }

    const surface = (surfaces.data ?? []).find((candidate) => candidate.name === group.surfaceName) ?? null;
    /* Who the bot is here: the space itself, or an agent with a channel of
       its own. */
    const agent = surface && !surface.mine ? surface.agentName || null : null;
    return <GroupView pod={pod} group={group} surface={surface} agent={agent} visible={visible} onInvite={onInvite} />;
}

function GroupView({ pod, group, surface, agent, visible, onInvite }: {
    pod: Pod;
    group: GroupDetail;
    surface: Surface | null;
    agent: string | null;
    visible: boolean;
    onInvite: () => void;
}) {
    const title = groupTitle(group);
    const bot = agent ?? pod.name;
    const open = group.inviteLink ?? (group.platform === "SLACK" ? slackChannelUrl(group.externalId) : null);
    /* On Slack the bot is removed by its handle there. */
    const remove = howToRemove(group.platform, group.platform === "SLACK" ? surface?.handle || bot : bot);
    /* The question leaves the page once it is answered — or once somebody
       else has — so what became of it is said here, where it was. */
    const [notice, setNotice] = useState<string | null>(null);
    useEffect(() => {
        if (!notice) return;
        const done = window.setTimeout(() => setNotice(null), 6_000);
        return () => window.clearTimeout(done);
    }, [notice]);
    return (
        <div className="gpage">
            <div className="gpage__main">
                <header className="gpage__head">
                    <span className="gpage__mark"><ChannelIcon platform={group.platform} size={44} /></span>
                    <div className="gpage__who">
                        <h1>{title}</h1>
                        <p>{groupHeadline(group, agent)}</p>
                    </div>
                    {open && (
                        <a className="ghost-pill gpage__open" href={open} target="_blank" rel="noreferrer">
                            Open in {platformName(group.platform)} <ExternalIcon size={13} aria-hidden="true" />
                        </a>
                    )}
                </header>
                {group.pending && (
                    <p className="gpage__note" role="status">WhatsApp is still making this group. Its link appears here in a few seconds.</p>
                )}
                {notice && <p className="gpage__note" role="status">{notice}</p>}
                {group.waiting.map((question) => (
                    <Asked key={question.notificationId} pod={pod} group={group} question={question} bot={bot} onGone={setNotice} />
                ))}
                <Timeline pod={pod} group={group} surface={surface} bot={bot} visible={visible} slackUrl={open} />
            </div>
            <aside className="gpage__side" aria-label="About this group">
                <People pod={pod} group={group} onInvite={onInvite} />
                <Outsiders pod={pod} group={group} bot={bot} />
                <section className="gside">
                    <h2 className="gside__head">{bot} in this group</h2>
                    <p className="gside__text">{howToAsk(group.platform, bot)} {whomItAnswers(group, pod.name)}</p>
                </section>
                {remove && <p className="gside__fine">{remove}</p>}
            </aside>
        </div>
    );
}

/* ── a question waiting on you ─────────────────────────────────────── */

function answerProblem(error: unknown): string {
    const status = (error as { statusCode?: unknown } | null)?.statusCode;
    if (status === 409) return "Someone has already answered this.";
    if (isForbidden(error)) return "This question was passed to someone else.";
    return "Couldn’t send that. Try again.";
}

function Asked({ pod, group, question, bot, onGone }: {
    pod: Pod;
    group: GroupDetail;
    question: GroupQuestion;
    bot: string;
    /** It is leaving the page, and this is why. */
    onGone: (why: string) => void;
}) {
    const cache = useQueryClient();
    const ids = useId();
    const title = groupTitle(group);
    const [answer, setAnswer] = useState("");
    const send = useMutation({
        mutationFn: (text: string) => source.answerGroupQuestion(pod.id, question.notificationId, text),
        onSuccess: () => {
            setAnswer("");
            onGone("Sent. " + bot + " passes it on in " + title + ".");
            refreshGroups(cache, pod.id);
            void cache.invalidateQueries({ queryKey: timelineKey(pod.id, group.id) });
        },
        /* Answered elsewhere: the question is not waiting any more, and the
           page should stop saying it is. */
        onError: (error) => {
            if ((error as { statusCode?: unknown }).statusCode !== 409) return;
            onGone("Someone had already answered that question.");
            refreshGroups(cache, pod.id);
        },
    });
    const submit = (event: FormEvent) => {
        event.preventDefault();
        const text = answer.trim();
        if (text && !send.isPending) send.mutate(text);
    };
    const when = sinceShort(question.askedAt);
    return (
        <section className="gask" aria-labelledby={ids + "-head"}>
            <div className="gask__head" id={ids + "-head"}>
                <span className="gask__dot" aria-hidden="true" />
                <span className="gask__title">Waiting on you</span>
                {when && <span className="gask__when">· {when}</span>}
            </div>
            <p className="gask__question">{question.question}</p>
            <p className="gask__note">People outside {pod.name} asked this in {title}, and nothing Public answers it. {bot} passes your answer on there.</p>
            <form className="gask__form" onSubmit={submit}>
                <label className="sr-only" htmlFor={ids + "-answer"}>Your answer</label>
                <input
                    id={ids + "-answer"}
                    value={answer}
                    onChange={(event) => setAnswer(event.target.value)}
                    placeholder={"Your answer. " + bot + " passes it on in the group."}
                    disabled={send.isPending}
                    autoComplete="off"
                />
                <button type="submit" className="pill-button" disabled={!answer.trim() || send.isPending}>
                    {send.isPending ? "Sending…" : "Send to " + title}
                </button>
            </form>
            {send.isError && <p className="gask__problem" role="alert">{answerProblem(send.error)}</p>}
        </section>
    );
}

/* ── what was said ─────────────────────────────────────────────────── */

function Timeline({ pod, group, surface, bot, visible, slackUrl }: {
    pod: Pod;
    group: GroupDetail;
    surface: Surface | null;
    bot: string;
    visible: boolean;
    slackUrl: string | null;
}) {
    const me = useMe();
    const kept = keepsTimeline(group.platform);
    const lines = useQuery({
        queryKey: timelineKey(pod.id, group.id),
        queryFn: () => source.groupTimeline(pod.id, group.id),
        enabled: kept && !group.pending,
        refetchInterval: visible && kept ? READ_EVERY_MS : false,
        staleTime: 10_000,
    });
    const days = useMemo(() => timelineDays(lines.data ?? []), [lines.data]);

    if (!kept) {
        return (
            <section className="gtimeline" aria-label={"What was said in " + groupTitle(group)}>
                <p className="gtimeline__quiet">
                    Slack keeps this channel’s history.{" "}
                    {slackUrl ? <a href={slackUrl} target="_blank" rel="noreferrer">Open it in Slack</a> : "Open it in Slack."}
                </p>
            </section>
        );
    }
    if (group.pending) return null;
    return (
        <section className="gtimeline" aria-label={"What was said in " + groupTitle(group)}>
            {lines.isPending && <p className="gtimeline__quiet" role="status">Loading what was said…</p>}
            {lines.isError && (
                <p className="gtimeline__quiet" role="alert">
                    Couldn’t load what was said. <button type="button" className="gsheet__inline" onClick={() => void lines.refetch()}>Try again</button>
                </p>
            )}
            {lines.isSuccess && days.length === 0 && <p className="gtimeline__quiet">Nothing said here yet.</p>}
            {days.map((day) => (
                <div key={day.key} className="gday">
                    <h2 className="gday__label">{day.label}</h2>
                    <ol className="gday__lines">
                        {day.lines.map((line, index) => (
                            <Line key={line.at + ":" + index} pod={pod} line={line} people={group.people} me={me} bot={bot} surface={surface} />
                        ))}
                    </ol>
                </div>
            ))}
        </section>
    );
}

function Line({ pod, line, people, me, bot, surface }: {
    pod: Pod;
    line: GroupLine;
    people: GroupPerson[];
    me: string | null;
    bot: string;
    surface: Surface | null;
}) {
    const who = lineAuthor(line, people, me, bot);
    const note = answeredNote(line);
    const at = new Date(line.at);
    const person = !line.fromBot ? people.find((one) => one.externalId && one.externalId === line.authorExternalId) : undefined;
    return (
        <li className="gline" data-bot={line.fromBot || undefined}>
            <span className="gline__face">
                {line.fromBot
                    ? (surface && !surface.mine
                        ? <AgentMark pod={pod} agent={{ name: surface.agentKey ?? surface.agentName, label: surface.agentName || bot }} size={32} />
                        : <TeammateFace pod={pod} size={32} />)
                    : <span className="gface" aria-hidden="true">{personInitials(person?.name ?? line.authorName ?? who.name, person ? standing(person) : line.inSpace ? "in" : "outside")}</span>}
            </span>
            <div className="gline__body">
                <div className="gline__head">
                    <span className="gline__who">{who.name}</span>
                    {!line.inSpace && <span className="gline__chip">Not in {pod.name}</span>}
                    {!Number.isNaN(at.getTime()) && (
                        <time className="gline__at" dateTime={line.at}>{at.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</time>
                    )}
                </div>
                <p className="gline__text">{line.text}</p>
                {note && <span className="gline__note">{note}</span>}
            </div>
        </li>
    );
}

/* ── beside it ─────────────────────────────────────────────────────── */

const STANDING_ORDER = { in: 0, outside: 1, unknown: 2 } as const;

function People({ pod, group, onInvite }: { pod: Pod; group: GroupDetail; onInvite: () => void }) {
    const me = useMe();
    const people = [...group.people].sort((a, b) => STANDING_ORDER[standing(a)] - STANDING_ORDER[standing(b)]);
    const strangers = people.some((person) => standing(person) === "unknown");
    return (
        <section className="gside">
            <h2 className="gside__head">People in this group</h2>
            {group.platform === "SLACK" ? (
                <p className="gside__text">Slack keeps who is in the channel.</p>
            ) : people.length === 0 ? (
                <p className="gside__text">Nobody has spoken here yet.</p>
            ) : (
                <ul className="gpeople">
                    {people.map((person) => {
                        const state = standing(person);
                        const you = Boolean(me && person.userId === me);
                        return (
                            <li key={person.externalId ?? person.name}>
                                <span className="gface" aria-hidden="true">{personInitials(person.name, state)}</span>
                                <span className="gpeople__who">
                                    <span>{you ? "You" : person.name}</span>
                                    <small>{sayStanding(state, pod.name)}</small>
                                </span>
                                {state === "outside" && (
                                    <button type="button" className="ghost-pill gpeople__invite" onClick={onInvite} aria-label={"Invite " + person.name + " to " + pod.name}>
                                        Invite
                                    </button>
                                )}
                            </li>
                        );
                    })}
                </ul>
            )}
            {strangers && (
                <p className="gside__fine">Not recognised: Lemma can’t tell who they are, so {pod.name} answers them from what is Public.</p>
            )}
        </section>
    );
}

function Outsiders({ pod, group, bot }: { pod: Pod; group: GroupDetail; bot: string }) {
    const me = useMe();
    const ids = useId();
    const change = useGroupChange(pod.id);
    const asked = change.isPending ? change.variables?.change : null;
    const on = asked?.answers_outsiders ?? group.answersOutsiders;
    const state = answering(group, me, pod.members);
    const refused = "Only someone who can change " + bot + " can change this.";
    return (
        <section className="gside">
            <h2 className="gside__head" id={ids + "-head"}>People outside {pod.name}</h2>
            {state.kind === "invited" ? (
                <p className="gside__text">People here who are not in {pod.name} get a private note inviting them in. Nobody is answered from what is Public in this channel.</p>
            ) : (
                <>
                    <label className="gswitch">
                        <input
                            type="checkbox"
                            role="switch"
                            checked={on}
                            disabled={change.isPending || group.pending}
                            aria-describedby={ids + "-head"}
                            onChange={(event) => change.mutate({ groupId: group.id, change: { answers_outsiders: event.target.checked } })}
                        />
                        <span className="gswitch__text">
                            <span>Answer them</span>
                            <small>From what is Public: the tables and pages you mark Public.</small>
                        </span>
                    </label>
                    {state.kind !== "off" && state.kind !== "pending" && (
                        <div className="gowner">
                            <span>{sayAnswering(state, pod.name)}</span>
                            {offersTakeOn(state) && (
                                <button
                                    type="button"
                                    className="gsheet__inline"
                                    disabled={change.isPending}
                                    onClick={() => change.mutate({ groupId: group.id, change: { take_over: true } })}
                                >
                                    {change.isPending && change.variables?.change.take_over ? "Taking it on…" : "Take it on"}
                                </button>
                            )}
                        </div>
                    )}
                    {state.kind === "you" && (
                        <p className="gside__fine">To hand over, someone else in {pod.name} takes it on from here.</p>
                    )}
                    {change.isError && (
                        <p className="gside__problem" role="alert">{isForbidden(change.error) ? refused : "Couldn’t change that. Try again."}</p>
                    )}
                </>
            )}
        </section>
    );
}
