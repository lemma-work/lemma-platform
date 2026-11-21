"use client";

import { useEffect, useMemo, useState } from "react";
import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { askerName, saidAbout, source, type JoinRequest, type Org, type Pod } from "@/data";
import { canManage, canSetJoinPolicy, memberName, roleLabel, type Member as OrgMember } from "@/org/membership";
import { WhoCanJoinOrg } from "@/org/who-can-join-org";
import { agoOf } from "@/schedule/schedules";
import { isForbidden } from "@/session/auth-state";
import { lemma } from "@/session/client";
import { TeammateFace } from "@/space/teammate-face";
import type { FrameActions } from "./actions";
import { usePodDetails } from "./data";
import { InviteDialog, LEMMA_ROLE_FIRST, givenName, lemmaRoleSaid, useOrgRoster, type OrgInvite } from "./invite-dialog";
import type { Go } from "./model";
import { Avatar, Icon } from "./parts";
import { Calm, Problem } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SkeletonRows } from "@/ui/skeleton";
import {
    PersonSheet, mayChange, roleChoices, statusOf, whyNot, withArticle,
    type Person, type PersonLemma, type Standing,
} from "./person-sheet";

type Filter = "all" | "admins" | "invited";

const DAY = 24 * 60 * 60_000;
/** How long a lapsed invitation stays on the page: long enough to notice
 *  and send it again, not so long that old asks pile up. It cannot be
 *  withdrawn once it has lapsed, so time is the only thing that clears it. */
const LAPSED_SHOWN = 30 * DAY;

type Listed = OrgMember & { created_at?: string | null };

/** The Lemmas somebody is in, read from each Lemma's own roster: by account
 *  where the roster says it, by address otherwise. */
function lemmasOf(pods: readonly Pod[], userId: string | null, email: string): PersonLemma[] {
    const lower = email.toLowerCase();
    return pods.flatMap((pod) => {
        const member = pod.members.find((one) => one.kind === "person"
            && ((userId && one.userId === userId) || (lower && (one.email ?? "").toLowerCase() === lower)));
        return member ? [{ pod, member, role: member.podRole ?? null, said: member.podRole ? lemmaRoleSaid(member.podRole) : member.role }] : [];
    });
}

/** Everyone the organization's roster and its invitations name, once each.
 *
 *  Members in the order they joined. Then invitations, newest first, one per
 *  address: a waiting one over a lapsed one, and none for an address that
 *  has since joined. Withdrawn and accepted ones are history, not people. */
function rosterPeople(orgId: string, members: readonly Listed[], invites: readonly OrgInvite[], pods: readonly Pod[], meId: string | null, now: number): Person[] {
    const joined = [...members].sort((a, b) => (Date.parse(a.created_at ?? "") || 0) - (Date.parse(b.created_at ?? "") || 0));
    const emails = new Set<string>();
    const people: Person[] = joined.map((member) => {
        const email = member.user?.email ?? member.email ?? "";
        if (email) emails.add(email.toLowerCase());
        const name = memberName(member);
        return {
            key: "m:" + member.id,
            kind: "member",
            name,
            named: Boolean(email) && name !== email,
            email,
            role: member.role ?? "ORG_MEMBER",
            memberId: member.id,
            since: member.created_at ?? null,
            expires: null,
            invite: null,
            mine: Boolean(meId) && member.user_id === meId,
            lemmas: lemmasOf(pods, member.user_id ?? null, email),
        };
    });
    const asked = new Map<string, Person>();
    const newest = [...invites].sort((a, b) => (Date.parse(b.created_at ?? "") || 0) - (Date.parse(a.created_at ?? "") || 0));
    for (const invite of newest) {
        const email = (invite.email ?? "").trim();
        const lower = email.toLowerCase();
        if (!email || emails.has(lower)) continue;
        if (invite.status !== "PENDING" && invite.status !== "EXPIRED") continue;
        const ends = Date.parse(invite.expires_at ?? "");
        const lapsed = invite.status === "EXPIRED" || (Number.isFinite(ends) && ends <= now);
        if (lapsed && Number.isFinite(ends) && now - ends > LAPSED_SHOWN) continue;
        const prior = asked.get(lower);
        if (prior && (prior.kind === "invited" || lapsed)) continue;
        const given = givenName(orgId, email);
        const pod = invite.pod_id ? pods.find((one) => one.id === invite.pod_id) : undefined;
        const podRole = invite.pod_role ?? LEMMA_ROLE_FIRST;
        asked.set(lower, {
            key: "i:" + lower,
            kind: lapsed ? "lapsed" : "invited",
            name: given ?? email,
            named: Boolean(given),
            email,
            role: invite.role ?? "ORG_MEMBER",
            memberId: null,
            since: invite.created_at ?? null,
            expires: invite.expires_at ?? null,
            invite,
            mine: false,
            lemmas: pod ? [{ pod, member: null, role: podRole, said: lemmaRoleSaid(podRole) }] : [],
        });
    }
    return [...people, ...asked.values()];
}

/** The sample has no organization behind it, so its people are the ones its
 *  Lemmas list, once each, most-placed first. */
function samplePeople(pods: readonly Pod[]): Person[] {
    const people = new Map<string, Person>();
    for (const pod of pods) {
        for (const member of pod.members) {
            if (member.kind !== "person") continue;
            const key = "m:" + (member.userId ?? member.email ?? member.id);
            const email = member.email ?? "";
            const known = people.get(key) ?? {
                key, kind: "member", name: member.name, named: Boolean(email) && member.name !== email, email,
                role: null, memberId: null, since: null, expires: null, invite: null, mine: false, lemmas: [],
            } satisfies Person;
            known.lemmas.push({ pod, member, role: member.podRole ?? null, said: member.podRole ? lemmaRoleSaid(member.podRole) : member.role });
            people.set(key, known);
        }
    }
    return [...people.values()].sort((a, b) => b.lemmas.length - a.lemmas.length || a.name.localeCompare(b.name));
}

/** The organization's People page: everyone in it, what they can do, and
 *  the Lemmas each one is in, as one dense table.
 *
 *  It replaces a read-only list built from the Lemmas' rosters, which could
 *  not say who was in the organization but in no Lemma, who had been asked
 *  and not come, or let anybody change any of it. Everything here was already
 *  on the server: the organization's members and invitations, each Lemma's
 *  roster, and who is asking to be let into a Lemma.
 *
 *  The order is the mock's: the header and its one button, anybody asking
 *  to join a Lemma (they are waiting on somebody), the filters and search,
 *  the table, and who else may join. A row opens the person in a sheet,
 *  where everything about them can be changed. Each control is offered only
 *  where the server will allow it, and where it will not, the page says who
 *  can; the sample, which has no organization behind it, says it is one. */
export function People({ org, pods, go, embedded = false }: {
    org: Org;
    pods: readonly Pod[];
    go: Go;
    actions: FrameActions;
    /** Inside Settings, under its own heading: no page or title of its own. */
    embedded?: boolean;
}) {
    const live = source.label === "live";
    const queryClient = useQueryClient();
    const roster = useOrgRoster(org.id, live);
    const details = usePodDetails(pods);
    const rostersIn = pods.every((pod) => details.get(pod.id) !== undefined);
    const [now] = useState(() => Date.now());
    const [filter, setFilter] = useState<Filter>("all");
    const [query, setQuery] = useState("");
    const [open, setOpen] = useState<string | null>(null);
    const [inviting, setInviting] = useState(false);
    const [fresh, setFresh] = useState<string | null>(null);
    const [said, setSaid] = useState<string | null>(null);

    /* What just happened is said once, then goes: the row it changed is the
       lasting answer. */
    useEffect(() => {
        if (!said) return;
        const timer = setTimeout(() => setSaid(null), 6000);
        return () => clearTimeout(timer);
    }, [said]);
    useEffect(() => {
        if (!fresh) return;
        const timer = setTimeout(() => setFresh(null), 2400);
        return () => clearTimeout(timer);
    }, [fresh]);

    const meId = roster.me.data?.id ?? null;
    /* Each Lemma with its roster, read above: the list the frame passes in
       may not carry them, and who is in which Lemma is read from them. */
    const rostered = useMemo(() => pods.map((pod) => {
        const detail = details.get(pod.id);
        return detail ? { ...pod, ...detail } : pod;
    }), [pods, details]);
    const people = useMemo(
        () => (live ? rosterPeople(org.id, roster.list as Listed[], roster.invites, rostered, meId, now) : samplePeople(rostered)),
        [live, org.id, roster.list, roster.invites, rostered, meId, now],
    );
    const standing: Standing = {
        live,
        myRole: roster.myRole,
        owners: roster.list.filter((member) => member.role === "ORG_OWNER").length,
        orgName: org.name,
    };
    const manage = live && canManage(roster.myRole);

    const setRole = useMutation({
        mutationFn: ({ person, next }: { person: Person; next: string }) =>
            lemma().organizations.members.updateRole(org.id, person.memberId as string, next as never),
        onSuccess: async (_answer, { person, next }) => {
            await queryClient.invalidateQueries({ queryKey: ["org-members", org.id] });
            setSaid(`${person.name} is ${withArticle(roleLabel(next))} in ${org.name} now.`);
        },
    });

    const joined = people.filter((person) => person.kind === "member");
    const counts: Record<Filter, number> = {
        all: joined.length,
        admins: joined.filter((person) => person.role === "ORG_OWNER" || person.role === "ORG_EDITOR").length,
        invited: people.length - joined.length,
    };
    const chips: [Filter, string][] = [
        ["all", "Everyone"],
        ...(live ? [["admins", "Owners and editors"] as [Filter, string]] : []),
        ...(roster.invitations.isSuccess || counts.invited > 0 ? [["invited", "Invited"] as [Filter, string]] : []),
    ];

    const words = query.trim().toLowerCase();
    const rows = people.filter((person) => {
        if (filter === "invited" ? person.kind === "member" : person.kind !== "member") return false;
        if (filter === "admins" && person.role !== "ORG_OWNER" && person.role !== "ORG_EDITOR") return false;
        return !words || person.name.toLowerCase().includes(words) || person.email.toLowerCase().includes(words);
    });
    const opened = open ? people.find((person) => person.key === open) ?? null : null;

    const empty = words ? `Nobody here matches “${query.trim()}”.`
        : !live && !rostersIn ? "Reading who’s where…"
        : filter === "invited" ? "Nobody is waiting on an invitation."
        : filter === "admins" ? "No owners or editors yet."
        : "Nobody here yet.";

    return (
        <div className={embedded ? "ox-people ox-people--embedded" : "kpage ox-people"}>
            <header className={embedded ? "ph ox-people__head" : "ph"}>
                <div className="ph__row">
                    <div className="ph__words">
                        {embedded ? <h2 className="ox-people__t">People</h2> : <h1 className="ph__t">People</h1>}
                        <p className="ph__line">Everyone in {org.name}, what they can do, and the Lemmas each one is in.</p>
                    </div>
                    {live && (roster.members.isPending || manage) && (
                        <div className="ph__acts">
                            <button type="button" className="b b--m b--primary" disabled={!manage} onClick={() => setInviting(true)}>
                                <Icon name="plus" size={16} />
                                Invite people
                            </button>
                        </div>
                    )}
                </div>
            </header>

            {!live && <p className="ox-people__notice">This is a sample, so nobody can be invited or changed from here.</p>}
            {live && roster.members.isSuccess && !manage && (
                <p className="ox-people__notice">Only owners and editors of {org.name} can invite people or change roles.</p>
            )}

            <Asks
                org={org}
                pods={pods}
                people={people}
                onAnswered={(email, words2) => {
                    if (email) setFresh(email.toLowerCase());
                    setSaid(words2);
                }}
            />

            <div className="ph__tools">
                <div className="ox-people__chips" role="group" aria-label="Show">
                    {chips.map(([key, label]) => (
                        <button key={key} type="button" className="ox-people__chip" aria-pressed={filter === key} onClick={() => setFilter(key)}>
                            {label} <span className="ox-people__count">{counts[key]}</span>
                        </button>
                    ))}
                </div>
                <span className="ph__grow" />
                <label className="ox-people__find">
                    <Icon name="search" size={15} />
                    <input type="search" placeholder="Find someone" aria-label="Find someone" value={query} onChange={(event) => setQuery(event.target.value)} />
                </label>
            </div>

            <p className="ox-people__said" role="status">{said ?? ""}</p>

            {live && roster.members.isPending ? (
                <SkeletonRows label={`Reading who’s in ${org.name}`} rows={6} slow={`Still reading who’s in ${org.name}. It’s slower than usual.`} />
            ) : live && roster.members.isError && isForbidden(roster.members.error) ? (
                <Calm icon="lock" title={`You’re not a member of ${org.name}`}>So its people aren’t shown here.</Calm>
            ) : live && roster.members.isError ? (
                <Calm
                    tone="warn"
                    title={`Couldn’t read the people in ${org.name}`}
                    note={codeOf(roster.members.error)}
                    actions={<TryAgain busy={roster.members.isFetching} onClick={() => void roster.members.refetch()} />}
                >
                    Nothing about them changed. Everyone keeps the access they have.
                </Calm>
            ) : (
                <div>
                    <div className="ox-people__table" role="table" aria-label={"People in " + org.name}>
                        <div className="ox-people__row ox-people__row--head" role="row">
                            <span role="columnheader">Person</span>
                            <span role="columnheader">In {org.name}</span>
                            <span role="columnheader">Lemmas</span>
                            <span role="columnheader">Status</span>
                            <span role="columnheader"><span className="ox-sr">More</span></span>
                        </div>
                        {rows.map((person) => (
                            <Row
                                key={person.key}
                                person={person}
                                org={org}
                                standing={standing}
                                now={now}
                                fresh={fresh !== null && person.email.toLowerCase() === fresh}
                                rostersIn={rostersIn}
                                busy={setRole.isPending}
                                pendingRole={setRole.isPending && setRole.variables?.person.key === person.key ? setRole.variables.next : null}
                                onRole={(next) => setRole.mutate({ person, next })}
                                onOpen={() => setOpen(person.key)}
                            />
                        ))}
                    </div>
                    {rows.length === 0 && <p className="ox-people__state">{empty}</p>}
                </div>
            )}

            {setRole.isError && (
                <div className="ox-people__problem">
                    <Problem retrying={setRole.isPending} onRetry={() => { if (setRole.variables) setRole.mutate(setRole.variables); }}>
                        {saidAbout(setRole.error, `${setRole.variables?.person.name ?? "Their"} role didn’t change.`)}
                    </Problem>
                </div>
            )}
            {manage && roster.invitations.isError && !isForbidden(roster.invitations.error) && (
                <div className="ox-people__state">
                    <Problem retrying={roster.invitations.isFetching} onRetry={() => void roster.invitations.refetch()}>Couldn’t read the invitations.</Problem>
                </div>
            )}

            <section className="ox-people__door" aria-label={"Who can join " + org.name}>
                <WhoCanJoinOrg
                    orgId={org.id}
                    mayChange={live ? (roster.members.isSuccess ? canSetJoinPolicy(roster.myRole) : null) : true}
                />
            </section>

            {opened && (
                <PersonSheet
                    key={opened.key}
                    person={opened}
                    org={org}
                    pods={pods}
                    standing={standing}
                    go={go}
                    onClose={() => setOpen(null)}
                    onGone={(words2) => { setOpen(null); setSaid(words2); }}
                />
            )}
            {inviting && (
                <InviteDialog
                    org={org}
                    pods={pods}
                    onClose={() => setInviting(false)}
                    onInvited={({ email, emailed }) => {
                        setInviting(false);
                        setQuery("");
                        setFilter("invited");
                        setFresh(email.toLowerCase());
                        setSaid(emailed ? `Invite sent to ${email}.` : `The invitation to ${email} is ready. Open their row to copy its link.`);
                    }}
                />
            )}
        </div>
    );
}

/** One person in the table. The whole row opens them; the role select and
 *  the ⋯ answer for themselves, and ⋯ is the way in from the keyboard. */
function Row({ person, org, standing, now, fresh, rostersIn, busy, pendingRole, onRole, onOpen }: {
    person: Person;
    org: Org;
    standing: Standing;
    now: number;
    fresh: boolean;
    rostersIn: boolean;
    busy: boolean;
    pendingRole: string | null;
    onRole: (next: string) => void;
    onOpen: () => void;
}) {
    const status = statusOf(person, now);
    const changeable = mayChange(person, standing);
    const sub = person.named ? person.email : person.kind !== "member" ? "Hasn’t joined yet" : "";
    const shown = person.lemmas.slice(0, 3);
    return (
        <div
            className="ox-people__row"
            role="row"
            data-new={fresh ? "" : undefined}
            onClick={(event) => {
                if ((event.target as HTMLElement).closest("select, button, a, input")) return;
                onOpen();
            }}
        >
            <span className="ox-people__who" role="cell">
                <Avatar name={person.name} size={30} />
                <span className="ox-people__words">
                    <span className="ox-people__name">
                        <span className="ox-people__nm">{person.name}</span>
                        {person.mine && <span className="ox-people__you">You</span>}
                    </span>
                    {sub && <span className="ox-people__mail">{sub}</span>}
                </span>
            </span>
            <span className="ox-people__rolecell" role="cell">
                {changeable ? (
                    <span className="ox-people__pick">
                        <select
                            aria-label={`${person.name}’s role in ${org.name}`}
                            value={pendingRole ?? person.role ?? "ORG_MEMBER"}
                            disabled={busy}
                            onChange={(event) => onRole(event.target.value)}
                        >
                            {roleChoices(standing, person.role).map((entry) => <option key={entry.value} value={entry.value}>{entry.label}</option>)}
                        </select>
                        <Icon name="chevron" size={12} />
                    </span>
                ) : (
                    <span className="ox-people__fixed" title={whyNot(person, standing, "role") ?? undefined}>
                        {person.role ? roleLabel(person.role) : "—"}
                    </span>
                )}
            </span>
            <span className="ox-people__lemmas" role="cell">
                {person.lemmas.length > 0 ? (
                    <>
                        <span className="ox-people__faces" aria-hidden="true">
                            {shown.map((one) => <TeammateFace key={one.pod.id} pod={one.pod} size={24} className="ox-people__face" />)}
                        </span>
                        <span className="ox-people__names">{person.lemmas.map((one) => one.pod.name).join(", ")}</span>
                    </>
                ) : (
                    <span className="ox-people__none">{rostersIn ? "None yet" : "Reading…"}</span>
                )}
            </span>
            <span className="ox-people__status" role="cell" data-tone={status.tone}>
                {status.tone !== "plain" && <i aria-hidden="true" />}
                {status.said}
            </span>
            <span className="ox-people__end" role="cell">
                <button type="button" className="b b--s b--quiet b--icon" aria-label={"More for " + person.name} onClick={onOpen}>
                    <Icon name="more" size={16} />
                </button>
            </span>
        </div>
    );
}

/** Who is asking to be let into a Lemma, for whoever can let them: first
 *  on the page, because they are waiting on somebody.
 *
 *  The same queue each Lemma shows at its own door, under the same key, so
 *  an answer here clears it there. Listing is an admin's call and most
 *  people are not one, so a refusal draws nothing; any other failure gets a
 *  line and a retry. Let in, they start able to use it, and their role can
 *  be changed from their row. Either answer reaches them by email. */
function Asks({ org, pods, people, onAnswered }: {
    org: Org;
    pods: readonly Pod[];
    people: readonly Person[];
    onAnswered: (email: string | null, said: string) => void;
}) {
    const queryClient = useQueryClient();
    const doors = useMemo(() => pods.filter((pod) => !pod.everyone), [pods]);
    const lists = useQueries({
        queries: doors.map((pod) => ({
            queryKey: ["pod-knocking", pod.id],
            queryFn: () => source.listJoinRequests(pod.id),
            retry: false,
        })),
    });

    const answer = useMutation({
        mutationFn: ({ pod, request, admit }: { pod: Pod; request: JoinRequest; admit: boolean }) =>
            admit ? source.admitToPod(pod.id, request.id, LEMMA_ROLE_FIRST) : source.declineJoinRequest(pod.id, request.id),
        onSuccess: (_answer, { pod, request, admit }) => {
            queryClient.setQueryData<JoinRequest[]>(["pod-knocking", pod.id], (before) => (before ?? []).filter((one) => one.id !== request.id));
            if (!admit) {
                onAnswered(null, `Declined. ${askerName(request)} will hear by email.`);
                return;
            }
            void queryClient.invalidateQueries({ queryKey: ["pod-detail", pod.id] });
            void queryClient.invalidateQueries({ queryKey: ["pods", org.id] });
            void queryClient.invalidateQueries({ queryKey: ["org-members", org.id] });
            onAnswered(request.email || null, `${askerName(request)} is in ${pod.name} now.`);
        },
    });

    const waiting = doors.flatMap((pod, index) => (lists[index]?.data ?? []).map((request) => ({ pod, request })));
    const failed = doors.flatMap((pod, index) => {
        const error = lists[index]?.error;
        return error && !isForbidden(error) ? [{ pod, index }] : [];
    });
    if (waiting.length === 0 && failed.length === 0) return null;

    return (
        <section className="ox-people__asks" aria-label="Asking to join">
            {waiting.map(({ pod, request }) => {
                const busy = answer.isPending && answer.variables?.request.id === request.id;
                const lower = request.email.toLowerCase();
                const inOrg = Boolean(lower) && people.some((person) => person.kind === "member" && person.email.toLowerCase() === lower);
                const sub = [request.name && request.email ? request.email : "", inOrg ? "already in " + org.name : "", request.askedAt ? agoOf(request.askedAt) : ""]
                    .filter(Boolean)
                    .join(" · ");
                return (
                    <div key={pod.id + ":" + request.id} className="ox-people__ask">
                        <Avatar name={askerName(request)} size={30} />
                        <span className="ox-people__askw">
                            <span><span className="ox-people__strong">{askerName(request)}</span> asked to join <span className="ox-people__strong">{pod.name}</span></span>
                            {sub && <span className="ox-people__asksub">{sub}</span>}
                        </span>
                        <span className="ox-people__askacts">
                            <button type="button" className="b b--s b--secondary" disabled={answer.isPending} onClick={() => answer.mutate({ pod, request, admit: false })}>
                                {busy && !answer.variables?.admit ? "Declining…" : "Decline"}
                            </button>
                            <button type="button" className="b b--s b--primary" disabled={answer.isPending} onClick={() => answer.mutate({ pod, request, admit: true })}>
                                {busy && answer.variables?.admit ? "Letting them in…" : "Let them in"}
                            </button>
                        </span>
                    </div>
                );
            })}
            {failed.map(({ pod, index }) => (
                <div key={pod.id} className="ox-people__state">
                    <Problem retrying={Boolean(lists[index]?.isFetching)} onRetry={() => void lists[index]?.refetch()}>Couldn’t read who’s asking to join {pod.name}.</Problem>
                </div>
            ))}
            {answer.isError && (
                <div className="ox-people__problem">
                    <Problem retrying={answer.isPending} onRetry={() => { if (answer.variables) answer.mutate(answer.variables); }}>
                        {saidAbout(answer.error, "That didn’t go through.")}
                    </Problem>
                </div>
            )}
        </section>
    );
}
