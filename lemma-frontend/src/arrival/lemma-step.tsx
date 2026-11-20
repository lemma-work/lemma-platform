"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type FirstWorkspace, type Pod } from "@/data";
import { lemma } from "@/session/client";
import { captureEvent } from "@/site/analytics/client";
import { joinLemma, useJoinable, type JoinableLemma } from "@/outcomes/data";
import { AiIcon, AppsIcon, ArrivalRow, InMark, LemmaMark, PeopleIcon } from "./shell";
import { hello, lemmaIs } from "./words";

/** "Meet your Lemma": the Lemma made for them says hello, says what a Lemma
 *  is, and shows itself -- its AI, its apps to come, who is in it. Somebody
 *  who came into an organization that already has Lemmas is greeted by the
 *  one they came in by, and offered the rest to join. */
export type LemmaPart = "ai" | "apps" | "people";

export function LemmaStep({ name, made, failed, retry, pod, onContinue, onStartNew, onPart }: {
    name: string;
    made: FirstWorkspace | null;
    /** The organization could not be set up: what went wrong. */
    failed: string | null;
    retry: () => void;
    /** The Lemma they land in, once read. */
    pod: Pod | null;
    onContinue: (renamed: { org: string } | null) => void;
    onStartNew: () => void;
    /** The part of a Lemma the reader is pointing at, to light it on the left too. */
    onPart?: (part: LemmaPart | null) => void;
}) {
    if (!made) {
        return (
            <>
                <h1 className="ar__t">{failed ? "Your Lemma isn’t made yet" : `${hello(name)} I’m making your Lemma…`}</h1>
                {failed ? (
                    <>
                        <p className="ar__problem" role="alert">{failed || "Something went wrong on the way."}</p>
                        <div className="ar__acts"><span className="ar__sum" /><button type="button" className="ar__btn ar__btn--primary" onClick={retry}>Try again</button></div>
                    </>
                ) : (
                    <p className="ar__note" role="status">This takes a moment.</p>
                )}
            </>
        );
    }
    return made.entry === "new_org"
        ? <MadeForYou name={name} made={made} pod={pod} onContinue={onContinue} onPart={onPart} />
        : <CameInto name={name} made={made} pod={pod} onContinue={() => onContinue(null)} onStartNew={onStartNew} />;
}

function MadeForYou({ name, made, pod, onContinue, onPart }: { name: string; made: FirstWorkspace; pod: Pod | null; onContinue: (renamed: { org: string } | null) => void; onPart?: (part: LemmaPart | null) => void }) {
    const point = (part: LemmaPart) => ({ onMouseEnter: () => onPart?.(part), onMouseLeave: () => onPart?.(null) });
    const shared = Boolean(made.orgPodId);
    const called = pod?.name ?? made.podName ?? (shared ? made.orgName : "Personal");
    const [renaming, setRenaming] = useState<string | null>(null);
    const [saving, setSaving] = useState(false);
    const [problem, setProblem] = useState<string | null>(null);
    const queryClient = useQueryClient();

    /* A team's Lemma carries the organization's name, so renaming it renames
       both, the one thing a new organization most often gets wrong. */
    const go = async () => {
        const wanted = renaming?.trim();
        if (!wanted || wanted === called || !made.orgCreated || !shared) {
            onContinue(null);
            return;
        }
        setSaving(true);
        setProblem(null);
        try {
            await lemma().request("PATCH", `/organizations/${encodeURIComponent(made.orgId)}`, { body: { name: wanted } });
            if (made.podId) await source.renamePod(made.podId, wanted);
            void queryClient.invalidateQueries({ queryKey: ["orgs"] });
            void queryClient.invalidateQueries({ queryKey: ["pods", made.orgId] });
            onContinue({ org: wanted });
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : "That name didn’t save.");
            setSaving(false);
        }
    };

    const shown = renaming?.trim() || called;
    const canRename = shared && made.orgCreated;
    const people = shared
        ? (made.openToDomain ? `Anyone with an @${made.openToDomain} address can join` : `You, and everyone at ${made.orgName} as they join`)
        : `Only you, in ${made.orgName}`;
    return (
        <>
            <h1 className="ar__t">{hello(name)} We’re making you a Lemma called {shown}, and I’m its AI.</h1>
            <p className="ar__model">{lemmaIs(shared, renaming?.trim() || made.orgName)}</p>
            {renaming !== null && (
                <label className="arl__rename">
                    Its name, and your organization’s
                    <input value={renaming} autoFocus maxLength={60} onChange={(event) => setRenaming(event.target.value)} />
                </label>
            )}
            <ul className="arl" aria-label={`What ${shown} has`}>
                <li {...point("ai")}><span className="ar__pic"><AiIcon /></span><b>An AI to ask</b><span>That’s me</span></li>
                <li {...point("apps")}><span className="ar__pic"><AppsIcon /></span><b>Apps</b><span>We’ll pick them next</span></li>
                <li {...point("people")}><span className="ar__pic"><PeopleIcon /></span><b>{shared ? "People" : "Just you"}</b><span>{people}</span></li>
            </ul>
            {problem && <p className="ar__problem" role="alert">{problem}</p>}
            <div className="ar__acts">
                <span className="ar__sum">
                    {renaming !== null ? (
                        <button type="button" className="ar__quiet" onClick={() => setRenaming(null)}>Keep {called}</button>
                    ) : canRename ? (
                        <button type="button" className="ar__quiet" onClick={() => setRenaming(called)}>Rename it</button>
                    ) : (
                        <><kbd>↵</kbd> to continue</>
                    )}
                </span>
                <button type="button" className="ar__btn ar__btn--primary" autoFocus={renaming === null} disabled={saving} onClick={() => void go()}>{saving ? "Saving…" : "Continue"}</button>
            </div>
        </>
    );
}

function CameInto({ name, made, pod, onContinue, onStartNew }: { name: string; made: FirstWorkspace; pod: Pod | null; onContinue: () => void; onStartNew: () => void }) {
    const queryClient = useQueryClient();
    const orgId = made.orgId;
    const invitedPodId = made.entry === "invitation" ? made.podId : null;
    const mine = useQuery({ queryKey: ["pods", orgId], queryFn: () => source.listPods(orgId) });
    const joinable = useJoinable(orgId);
    const [joining, setJoining] = useState<string | null>(null);
    const [joined, setJoined] = useState<ReadonlySet<string>>(new Set());
    const [problem, setProblem] = useState<string | null>(null);

    const rank = (one: { id: string; everyone?: boolean }) => (one.id === invitedPodId ? 0 : one.everyone ? 1 : 2);
    const yours = [...(mine.data ?? [])].sort((a, b) => rank(a) - rank(b));
    const inAlready = new Set(yours.map((one) => one.id));
    const open = (joinable.data ?? []).filter((one) => !inAlready.has(one.id));
    const lead = pod ?? yours[0] ?? null;

    const join = async (one: JoinableLemma) => {
        setJoining(one.id);
        setProblem(null);
        try {
            await joinLemma(one.id);
            setJoined((was) => new Set(was).add(one.id));
            captureEvent("onboarding.lemma_joined");
            void queryClient.invalidateQueries({ queryKey: ["pods", orgId] });
        } catch (failure) {
            setProblem(failure instanceof Error ? failure.message : `Couldn’t join ${one.name}.`);
        } finally {
            setJoining(null);
        }
    };

    const others = [...yours.filter((one) => one.id !== lead?.id), ...open];
    return (
        <>
            <h1 className="ar__t">{hello(name)} You’re in {lead?.name ?? made.orgName}, and I’m its AI.</h1>
            <p className="ar__model">
                A Lemma is a shared place for one part of your work: an AI you can ask, apps for the work, and the people in it.
                {others.length > 0 ? ` ${made.orgName} has ${others.length === 1 ? "one more" : "a few more"}; join the ones you’ll work in.` : ""}
            </p>
            {(mine.isLoading || joinable.isLoading) ? (
                <p className="ar__note" role="status">Reading {made.orgName}’s Lemmas…</p>
            ) : others.length > 0 && (
                <ul className="ar__rows ar__rows--boxed" aria-label={`Lemmas at ${made.orgName}`}>
                    {others.map((one) => {
                        const isIn = inAlready.has(one.id) || joined.has(one.id);
                        const people = "people" in one ? (one as JoinableLemma).people : 0;
                        return (
                            <ArrivalRow
                                key={one.id}
                                mark={<LemmaMark id={one.id} name={one.name} iconUrl={one.iconUrl} />}
                                name={one.name}
                                tag={"everyone" in one && one.everyone ? "Everyone" : undefined}
                                line={[one.description, people > 0 ? (people === 1 ? "1 person" : `${people} people`) : ""].filter(Boolean).join(" · ") || (isIn ? "You’re in this one" : "Open to everyone here")}
                            >
                                {isIn ? <InMark /> : (
                                    <button type="button" className="ar__btn ar__btn--small" disabled={joining !== null} onClick={() => void join(one as JoinableLemma)}>
                                        {joining === one.id ? "Joining…" : "Join"}
                                    </button>
                                )}
                            </ArrivalRow>
                        );
                    })}
                </ul>
            )}
            {problem && <p className="ar__problem" role="alert">{problem}</p>}
            <div className="ar__acts">
                <span className="ar__sum"><button type="button" className="ar__quiet" onClick={onStartNew}>Start a new Lemma</button></span>
                <button type="button" className="ar__btn ar__btn--primary" disabled={joining !== null} onClick={onContinue}>Continue</button>
            </div>
        </>
    );
}
