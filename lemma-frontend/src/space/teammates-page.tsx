"use client";

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { initialsOf, source, type Org, type Pod, type PodDetail } from "@/data";
import { lemma } from "@/session/client";
import { AI_MATE, MATES, NEW_MATE } from "@/copy";
import { Mark, markTint } from "@/shell/mark";
import { unbound } from "@/thread/conversation-list";
import { CheckIcon, ChevronDownIcon, PlusIcon } from "@/ui/icons";
import { byNeed, sayOwed, type Owed } from "./teammates";

/* Layout effects run on the client only; on the server this is the plain
   effect, which does nothing there either. */
const useBeforePaint = typeof window === "undefined" ? useEffect : useLayoutEffect;

/** Whether a node has been on screen yet. Once is enough: a card that has
 *  been seen keeps what it fetched, so scrolling back up costs nothing. */
function useSeen(node: RefObject<HTMLElement | null>): boolean {
    const [seen, setSeen] = useState(false);
    useEffect(() => {
        const element = node.current;
        if (seen || !element) return;
        if (typeof IntersectionObserver === "undefined") { setSeen(true); return; }
        const watch = new IntersectionObserver((entries) => {
            if (entries.some((entry) => entry.isIntersecting)) setSeen(true);
        }, { rootMargin: "120px" });
        watch.observe(element);
        return () => watch.disconnect();
    }, [node, seen]);
    return seen;
}

/** Zoomed all the way out: every teammate in the organization, and what each
 *  needs from you.
 *
 *  The page the rail's mark opens, and the one a teammate's breadcrumb climbs
 *  back to. A card is the teammate's face, its job and one line of news —
 *  waiting on you, or what it did last — with the people who share its
 *  space. Pressing one goes into that space. */
export function TeammatesPage({ pods, pending, failed, onRetry, owed, orgName, orgs, orgId, cameFrom, lead, tools, onOpen, onHire, onPickOrg }: {
    pods: Pod[];
    pending: boolean;
    failed: boolean;
    onRetry: () => void;
    owed: ReadonlyMap<string, Owed>;
    orgName: string;
    orgs: Org[];
    orgId: string | null;
    /** The teammate just zoomed out of, whose card says so for a moment. */
    cameFrom: string | null;
    /** Before the organization's name: on a phone, the way to the rail. */
    lead?: ReactNode;
    /** The queue of work waiting on you, beside the organization's name. */
    tools?: ReactNode;
    onOpen: (podId: string, from: DOMRect) => void;
    onHire: (() => void) | null;
    onPickOrg: (orgId: string) => void;
}) {
    const page = useRef<HTMLDivElement>(null);
    const { needs, rest } = byNeed(pods, owed);

    /* Zooming out: the whole floor comes back into view, and the teammate
       you were in is marked so the eye lands where it left. */
    useBeforePaint(() => {
        if (!cameFrom || !page.current) return;
        const card = page.current.querySelector<HTMLElement>(`[data-pod="${CSS.escape(cameFrom)}"]`);
        card?.scrollIntoView({ block: "nearest" });
        if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
        page.current.animate(
            [{ transform: "scale(1.04)", opacity: 0 }, { transform: "none", opacity: 1 }],
            { duration: 280, easing: "cubic-bezier(.2,.8,.2,1)" },
        );
        card?.classList.add("tcard--was");
        const done = window.setTimeout(() => card?.classList.remove("tcard--was"), 1400);
        return () => window.clearTimeout(done);
    }, [cameFrom]);

    return (
        <div className="team" ref={page}>
            <div className="workspace-toolbar team__bar">
                {lead}
                <OrgMenu name={orgName} orgs={orgs} orgId={orgId} onPick={onPickOrg} />
                <span className="team__tools">{tools}</span>
            </div>
            <div className="team__scroll">
                <div className="team__column">
                    <header className="team__head">
                        <div>
                            <h1>{MATES}</h1>
                            <p>
                                {pods.length} in {orgName}
                                {needs.length > 0 && " · " + needs.length + (needs.length === 1 ? " needs you" : " need you")}
                            </p>
                        </div>
                        {onHire && <button className="pill-button" onClick={onHire}><PlusIcon size={15} /> {NEW_MATE}</button>}
                    </header>

                    {pending ? <p className="team__quiet" role="status">Opening {orgName}…</p>
                        : failed ? (
                            <p className="team__quiet" role="alert">
                                Couldn’t load who is in {orgName}.{" "}
                                <button className="linkish" onClick={onRetry}>Try again</button>
                            </p>
                        )
                        : pods.length === 0 ? (
                            <div className="team__empty">
                                <p>Nobody here yet. Hire your first {AI_MATE} and this is where it will be.</p>
                                {onHire && <button className="pill-button" onClick={onHire}><PlusIcon size={15} /> {NEW_MATE}</button>}
                            </div>
                        ) : (
                            <>
                                {needs.length > 0 && (
                                    <section aria-label="Needs you">
                                        <h2 className="team__group">Needs you</h2>
                                        <div className="team__grid">
                                            {needs.map((pod) => <Card key={pod.id} pod={pod} owed={owed.get(pod.id)} onOpen={onOpen} />)}
                                        </div>
                                    </section>
                                )}
                                <section aria-label={needs.length > 0 ? "Everyone else" : MATES}>
                                    {needs.length > 0 && rest.length > 0 && <h2 className="team__group">Everyone else</h2>}
                                    <div className="team__grid">
                                        {rest.map((pod) => <Card key={pod.id} pod={pod} owed={owed.get(pod.id)} onOpen={onOpen} />)}
                                        {onHire && (
                                            <button className="tcard tcard--new" onClick={onHire}>
                                                <span className="tcard__plus"><PlusIcon size={18} /></span>
                                                <span className="tcard__name">{NEW_MATE}</span>
                                                <small>Give it a job. It learns how your team works.</small>
                                            </button>
                                        )}
                                    </div>
                                </section>
                            </>
                        )}
                </div>
            </div>
        </div>
    );
}

/** The faces on a card: who is in the pod, and nothing else.
 *
 *  Not `getPodDetail`, which reads the pod's agents as well, to find the
 *  teammate's picture — a picture the card already has from the listed pod.
 *  That was a second request per card on screen for nothing the card draws.
 *  Under the detail's key, one level down, so everything that invalidates a
 *  pod's detail after a membership change reaches this too. The sample has no
 *  client to ask, so it reads its own detail. */
async function cardMembers(pod: Pod): Promise<{ id: string; name: string; initials: string }[]> {
    if (source.label === "sample") return peopleIn((await source.getPodDetail(pod.id, pod.name, pod.iconUrl)).members);
    try {
        const listed = (await lemma(pod.id).podMembers.list(pod.id)) as { items?: unknown[] } | unknown[];
        const items = Array.isArray(listed) ? listed : listed.items ?? [];
        return items.map((raw) => {
            const m = raw as { pod_member_id?: string; user_id?: string; user_name?: string | null; email?: string; user_email?: string };
            const name = m.user_name?.trim() || m.email || m.user_email || "Member";
            return { id: m.pod_member_id ?? m.user_id ?? name, name, initials: initialsOf(name) };
        });
    } catch {
        /* As the detail does: a pod whose members you may not list still
           has a card. */
        return [];
    }
}

/** The people in a detail's roster — the bots in it are drawn elsewhere. */
function peopleIn(members: PodDetail["members"]) {
    return members.filter((member) => member.kind === "person");
}

/** One teammate on the floor.
 *
 *  Its people and its latest conversation, each read once the card is on
 *  screen: an owner sees every pod in the organization, and a request per pod
 *  up front is the cost `listPods` was written to avoid. The conversations are
 *  the space's own list under its key, so a card that has been seen opens its
 *  space with the history already filled; the people come from the space's
 *  detail when that was read first. */
function Card({ pod, owed, onOpen }: { pod: Pod; owed: Owed | undefined; onOpen: (podId: string, from: DOMRect) => void }) {
    const node = useRef<HTMLButtonElement>(null);
    const seen = useSeen(node);
    const cache = useQueryClient();
    const members = useQuery({
        queryKey: ["pod-detail", pod.id, "members"],
        queryFn: () => cardMembers(pod),
        enabled: seen,
        staleTime: 5 * 60_000,
        initialData: () => {
            const known = cache.getQueryData<PodDetail>(["pod-detail", pod.id]);
            return known ? peopleIn(known.members) : undefined;
        },
        initialDataUpdatedAt: () => cache.getQueryState(["pod-detail", pod.id])?.dataUpdatedAt,
    });
    const handWritten = pod.waiting.trim();
    const chats = useQuery({
        queryKey: ["conversations", pod.id],
        queryFn: () => source.listConversations(pod.id),
        enabled: seen && !owed && !handWritten,
        staleTime: 60_000,
    });
    const latest = unbound(chats.data)[0];
    const people = members.data ?? [];

    const status = owed ? { tone: "needs", text: sayOwed(owed) }
        : handWritten ? { tone: "needs", text: handWritten }
        : latest ? { tone: "quiet", text: latest.title + " · " + latest.at }
        : chats.isSuccess ? { tone: "quiet", text: "Nothing yet" }
        : null;

    return (
        <button
            ref={node}
            className="tcard"
            data-pod={pod.id}
            onClick={(event) => onOpen(pod.id, event.currentTarget.getBoundingClientRect())}
        >
            <span className="tcard__stage" style={{ background: markTint(pod.id).background }}>
                <Mark seed={pod.id} name={pod.name} icon={pod.iconUrl} size={116} still />
            </span>
            <span className="tcard__body">
                <span className="tcard__name">{pod.name}</span>
                {pod.description && <span className="tcard__job">{pod.description}</span>}
                <span className="tcard__status">
                    {status && <><span className={"tcard__dot tcard__dot--" + status.tone} aria-hidden="true" /><span>{status.text}</span></>}
                </span>
                <span className="tcard__foot">
                    {people.length > 0 && (
                        <span className="tcard__people" aria-label={people.map((person) => person.name).join(", ")}>
                            {people.slice(0, 4).map((person) => <span key={person.id} className="tcard__person">{person.initials}</span>)}
                            {people.length > 4 && <span className="tcard__more">+{people.length - 4}</span>}
                        </span>
                    )}
                </span>
            </span>
        </button>
    );
}

/** The organization's name, and — with more than one — the way to another.
 *  The only place that asks, now that no space switcher holds the list. */
function OrgMenu({ name, orgs, orgId, onPick }: { name: string; orgs: Org[]; orgId: string | null; onPick: (orgId: string) => void }) {
    const [open, setOpen] = useState(false);
    const box = useRef<HTMLDivElement>(null);
    useEffect(() => {
        if (!open) return;
        const away = (event: MouseEvent) => { if (!box.current?.contains(event.target as Node)) setOpen(false); };
        const escape = (event: KeyboardEvent) => { if (event.key === "Escape") setOpen(false); };
        document.addEventListener("mousedown", away);
        document.addEventListener("keydown", escape);
        return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", escape); };
    }, [open]);

    if (orgs.length < 2) return <span className="crumb"><span className="crumb__here">{name}</span></span>;
    return (
        <div className="orgmenu" ref={box}>
            <button className="orgmenu__button" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((was) => !was)}>
                {name}<ChevronDownIcon size={15} />
            </button>
            {open && (
                <div className="orgmenu__list" role="menu">
                    {orgs.map((org) => (
                        <button key={org.id} role="menuitem" className="orgmenu__item" aria-current={org.id === orgId}
                            onClick={() => { setOpen(false); if (org.id !== orgId) onPick(org.id); }}>
                            <span className="orgmenu__avatar">{org.name.slice(0, 1).toUpperCase()}</span>
                            <span>{org.name}</span>
                            {org.id === orgId && <CheckIcon size={15} />}
                        </button>
                    ))}
                </div>
            )}
        </div>
    );
}
