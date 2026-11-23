"use client";

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { PAGE_TEMPLATES, makePage } from "@/docpages/templates";
import { readableName } from "@/library/reading";
import { sinceShort } from "@/channels/model";
import { AddPeople } from "@/shell/add-people";
import { WhoCanJoin } from "@/shell/who-can-join";
import type { FrameActions } from "./actions";
import { closeMenu } from "./lemma-bar";
import { useLemmaPages, useLemmaTables } from "./lemma-head";
import { pageTitle, type Go } from "./model";
import { Empty, Icon, to } from "./parts";
import { Calm, Problem } from "./calm";
import { TryAgain, codeOf } from "./read-again";
import { SkeletonRows } from "@/ui/skeleton";

/** A page made and opened with the caret in it, named the way a person
 *  would -- Untitled, then Untitled 2 -- against what /pages already holds.
 *  Create-only, so it never writes over a page. */
export function useNewPage(pod: Pod, go: Go) {
    const cache = useQueryClient();
    const [making, setMaking] = useState<string | null>(null);
    /* The page that couldn't be made, to try again with. */
    const [failed, setFailed] = useState<{ id: string; base: string; text: string } | null>(null);
    const make = async (id: string, base: string, text: string) => {
        setMaking(id);
        setFailed(null);
        try {
            /* With no listing, names are found by trying. */
            const taken = await source.listLibrary(pod.id, "files", "/pages")
                .then((listed) => new Set(listed.items.map((item) => item.name.toLowerCase())))
                .catch(() => new Set<string>());
            const path = await makePage((at, body) => source.createFile(pod.id, at, body), base, text, taken);
            void cache.invalidateQueries({ queryKey: ["library", pod.id] });
            void cache.invalidateQueries({ queryKey: ["lemma-pages", pod.id] });
            go({ name: "file", podId: pod.id, path });
        } catch {
            setFailed({ id, base, text });
        } finally {
            setMaking(null);
        }
    };
    return { make, making, failed };
}

/** New page: a blank one, or one from a template. */
function NewPageMenu({ pod, go }: { pod: Pod; go: Go }) {
    const { make, making, failed } = useNewPage(pod, go);
    const mate = pod.teammate?.name || pod.name;
    return (
        <div className="ox-make">
            {failed && (
                <div className="ox-make__note">
                    <Problem retrying={Boolean(making)} onRetry={() => void make(failed.id, failed.base, failed.text)}>Couldn’t make that page.</Problem>
                </div>
            )}
            <details className="ox-more">
                <summary className="ox-btn ox-btn--small" aria-label="New page">{making ? "Making…" : "New page"}<Icon name="chevron" size={14} /></summary>
                <div className="ox-more__menu" role="menu">
                    <button type="button" role="menuitem" disabled={Boolean(making)} onClick={(event) => { closeMenu(event); void make("page", "Untitled", "# Untitled\n\n"); }}>Blank page</button>
                    {PAGE_TEMPLATES.map((template) => (
                        <button key={template.id} type="button" role="menuitem" disabled={Boolean(making)} onClick={(event) => { closeMenu(event); void make(template.id, template.file, template.body(mate, new Date())); }}>
                            {template.title}<span className="ox-make__blurb">{template.blurb}</span>
                        </button>
                    ))}
                </div>
            </details>
        </div>
    );
}

/** Its pages, newest first, the way a doc app lists them. */
export function PagesTab({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const pages = useLemmaPages(pod.id);
    const list = [...(pages.data ?? [])].sort((a, b) => b.updated.localeCompare(a.updated));
    return (
        <div className="ox-held">
            <div className="ox-held__bar">
                <span className="ox-vs">{pages.isSuccess ? `${list.length} ${list.length === 1 ? "page" : "pages"}` : ""}</span>
                <NewPageMenu pod={pod} go={go} />
            </div>
            {pages.isPending ? <SkeletonRows label={`Reading ${pod.name}’s pages`} rows={5} slow={`Still reading ${pod.name}’s pages. It’s slower than usual.`} />
                : pages.isError ? (
                    <Calm tone="warn" title={`Couldn’t read ${pod.name}’s pages`} note={codeOf(pages.error)} actions={<TryAgain busy={pages.isFetching} onClick={() => void pages.refetch()} />}>
                        Nothing in them changed. You can still make a new page.
                    </Calm>
                )
                : list.length === 0 ? (
                    <Empty title="No pages yet.">
                        <p>Write one, bring your Notion pages in, or ask {pod.name} to write one.</p>
                        <button type="button" className="ox-btn" onClick={() => actions.prompt(pod.id, "Write a page about ")}>Ask {pod.name}</button>
                    </Empty>
                ) : (
                    <ul className="ox-heldrows" aria-label={`Pages in ${pod.name}`}>
                        {list.map((item) => (
                            <li key={item.path}>
                                <a className="ox-heldrow" {...to({ name: "file", podId: pod.id, path: item.path }, go)}>
                                    <Icon name="page" size={16} />
                                    <span className="ox-heldrow__t">{pageTitle(item.name)}</span>
                                    <span className="ox-heldrow__when">{item.updated ? sinceShort(item.updated) : ""}</span>
                                </a>
                            </li>
                        ))}
                    </ul>
                )}
        </div>
    );
}

/** Its tables, as a base lists them. A table is made by the Lemma, from a
 *  sentence, so "New table" starts the sentence. */
export function TablesTab({ pod, go, actions }: { pod: Pod; go: Go; actions: FrameActions }) {
    const tables = useLemmaTables(pod.id);
    const list = [...(tables.data ?? [])].sort((a, b) => b.updated.localeCompare(a.updated));
    return (
        <div className="ox-held">
            <div className="ox-held__bar">
                <span className="ox-vs">{tables.isSuccess ? `${list.length} ${list.length === 1 ? "table" : "tables"}` : ""}</span>
                <button type="button" className="ox-btn ox-btn--small" onClick={() => actions.prompt(pod.id, "Set up a table to track ")}>New table</button>
            </div>
            {tables.isPending ? <SkeletonRows label={`Reading ${pod.name}’s tables`} rows={5} slow={`Still reading ${pod.name}’s tables. It’s slower than usual.`} />
                : tables.isError ? (
                    <Calm tone="warn" title={`Couldn’t read ${pod.name}’s tables`} note={codeOf(tables.error)} actions={<TryAgain busy={tables.isFetching} onClick={() => void tables.refetch()} />}>
                        Nothing in them changed. The list of them didn’t come through just now.
                    </Calm>
                )
                : list.length === 0 ? (
                    <Empty title="No tables yet.">
                        <p>Bring one in from Airtable or a sheet, or ask {pod.name} to make one.</p>
                        <button type="button" className="ox-btn" onClick={() => actions.prompt(pod.id, "Set up a table to track ")}>New table</button>
                    </Empty>
                ) : (
                    <ul className="ox-heldrows" aria-label={`Tables in ${pod.name}`}>
                        {list.map((item) => (
                            <li key={item.path || item.name}>
                                <a className="ox-heldrow" {...to({ name: "table", podId: pod.id, table: item.name }, go)}>
                                    <Icon name="table" size={16} />
                                    <span className="ox-heldrow__t">{readableName(item.name)}</span>
                                    {item.detail && <span className="ox-heldrow__d">{item.detail}</span>}
                                    <span className="ox-heldrow__when">{item.updated ? sinceShort(item.updated) : ""}</span>
                                </a>
                            </li>
                        ))}
                    </ul>
                )}
        </div>
    );
}

/** Who is in it, how to add someone, and who else can find it -- beside the
 *  people it lets in, where a rule like that gets read. */
export function PeopleTab({ pod, orgName }: { pod: Pod; orgName: string }) {
    return (
        <div className="ox-held ox-held--people">
            <AddPeople pod={pod} orgId={pod.orgId} />
            <WhoCanJoin podId={pod.id} orgName={orgName} />
        </div>
    );
}
