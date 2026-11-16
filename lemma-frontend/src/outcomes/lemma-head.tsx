"use client";

import { useQuery } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { TeammateFace } from "@/space/teammate-face";
import { listIfThere } from "@/thread/memory-notes";
import type { FrameActions } from "./actions";
import { useMemo } from "react";
import { useApps } from "./ask";
import { LemmaActions, useLemmaSections, type LemmaSection } from "./lemma-bar";
import { outcomeView, partOfAbout, type Go, type OutcomeTab } from "./model";
import { Avatar, to } from "./parts";

/** How many faces the header shows before it counts the rest. */
const FACES_SHOWN = 3;

/** The pages a Lemma holds: what its Pages tab lists and its header counts.
 *  A page is a `.md` file under /pages; a page with pages under it keeps
 *  them in a folder of the same name, beside it. */
export function useLemmaPages(podId: string) {
    return useQuery({
        queryKey: ["lemma-pages", podId],
        /* A Lemma nobody has written a page in has no /pages folder, and
           listing a folder that is not there is an error, not an empty list. */
        queryFn: async () => ((await listIfThere((directory, page) => source.listLibrary(podId, "files", directory, page), "/pages", 1)) ?? [])
            .filter((item) => item.kind !== "folder" && item.name.toLowerCase().endsWith(".md")),
        staleTime: 60_000,
        retry: false,
    });
}

/** The tables a Lemma holds, under the key its home reads them by. */
export function useLemmaTables(podId: string) {
    return useQuery({
        queryKey: ["outcome-tables", podId],
        queryFn: () => source.listLibrary(podId, "tables", "/"),
        staleTime: 5 * 60_000,
        retry: false,
        select: (listed) => listed.items.filter((item) => item.kind === "table"),
    });
}

/** The top of every page a Lemma is itself: its character, alive, beside its
 *  name and what it says about itself right now (the way back to its home),
 *  the people in it, and its tabs: its home, what is in it, each named for
 *  the tool it stands in for, and About. One header, and no tab draws a
 *  title of its own under it. */
export function LemmaHead({ pod, says, tab, go, actions }: {
    pod: Pod;
    /** How it is doing and whether it needs the reader, in its own words
     *  (`lemmaSays`); what it is for while that is still being read. */
    says: string;
    tab: OutcomeTab;
    go: Go;
    actions: FrameActions;
}) {
    const sections = useLemmaSections(pod.id).filter((one) => !partOfAbout(one.tab));
    const pages = useLemmaPages(pod.id);
    const tables = useLemmaTables(pod.id);
    const solo = useMemo(() => [pod], [pod]);
    const apps = useApps(solo);
    const people = pod.members.filter((member) => member.kind === "person");
    const counts: Partial<Record<LemmaSection, number>> = {
        apps: apps.apps.length,
        pages: pages.data?.length,
        tables: tables.data?.length,
    };
    /* A part of About keeps About lit: the row is where you are, and those
       pages are inside it. */
    const lit: OutcomeTab = partOfAbout(tab) ? "about" : tab;

    return (
        <header className="ox-lhead">
            <div className="ox-lhead__row">
                <a className="ox-lhead__id" {...to(outcomeView(pod.id), go)}>
                    <TeammateFace pod={pod} size={56} live />
                    <span className="ox-lhead__t">
                        <span className="ox-lhead__name">{pod.name}</span>
                        {says && <span className="ox-lhead__says">{says}</span>}
                    </span>
                </a>
                <div className="ox-lhead__acts">
                    {people.length > 0 && (
                        <a className="ox-lhead__faces" {...to(outcomeView(pod.id, "people"), go)} aria-label={`People in ${pod.name}: ${people.map((member) => member.name).join(", ")}`}>
                            {people.slice(0, FACES_SHOWN).map((member) => <Avatar key={member.id} name={member.name} size={26} />)}
                            {people.length > FACES_SHOWN && <span className="ox-lhead__more ox-num">+{people.length - FACES_SHOWN}</span>}
                        </a>
                    )}
                    <button type="button" className="ox-btn ox-btn--small" onClick={() => actions.addPeople(pod.id)}>Add people</button>
                    <LemmaActions pod={pod} go={go} actions={actions} newChat={tab !== "home"} />
                </div>
            </div>
            <nav className="ox-tabs" aria-label={`In ${pod.name}`}>
                <a className="ox-tab" {...to(outcomeView(pod.id), go)} aria-current={lit === "home" ? "page" : undefined}>Home</a>
                {sections.map((one) => {
                    const count = counts[one.tab];
                    return (
                        <a key={one.tab} className="ox-tab" {...to(outcomeView(pod.id, one.tab), go)} aria-current={lit === one.tab ? "page" : undefined}>
                            {one.label}
                            {count ? <span className="ox-tab__n ox-num">{count}</span> : null}
                        </a>
                    );
                })}
            </nav>
        </header>
    );
}
