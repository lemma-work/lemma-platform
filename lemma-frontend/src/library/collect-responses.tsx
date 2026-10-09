"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source } from "@/data";
import { EMPTY_WIDGET_DRAFT, type NewWebWidget } from "@/data/contacts";
import {
    audienceChoices,
    columnQuestion,
    customFormAsk,
    doorFor,
    formEmbed,
    formLink,
    htmlFormSnippet,
    openingProblem,
    startingColumns,
    widgetAnswerFor,
    type Audience,
    type TableOpening,
} from "@/data/public-rows";
import { isForbidden } from "@/session/auth-state";
import { Modal } from "@/shell/modal";
import { CopyButton } from "@/thread/copy-button";
import { useCreateWidget, useWidgets } from "@/space/contact-queries";
import { SecretSheet, WidgetPlaces } from "@/space/widget-places";

const openingKey = (podId: string, table: string) => ["table-opening", podId, table] as const;

/** Collect responses: a table's rows, added by people outside the space.
 *
 *  There is no form to build. Opening the table says who may add rows and
 *  which columns they may write; any page can then be the form — the one Lemma
 *  hosts, a website with the script, or an app the teammate designs. The
 *  space's web chat is the door, so the chat sits beside the form to answer
 *  questions and fill it in. */
export function CollectResponses({ podId, table, teammate, onAsk, onClose }: {
    podId: string;
    table: string;
    teammate: string;
    onAsk?: (text: string) => void;
    onClose: () => void;
}) {
    const cache = useQueryClient();
    const opening = useQuery({ queryKey: openingKey(podId, table), queryFn: () => source.tableOpening(podId, table) });
    const widgets = useWidgets(podId);
    const [audience, setAudience] = useState<Audience | "off">("anyone");
    const [columns, setColumns] = useState<string[] | null>(null);
    const [problem, setProblem] = useState<string | null>(null);
    const [made, setMade] = useState<NewWebWidget | null>(null);

    useEffect(() => {
        if (!opening.data || columns !== null) return;
        setColumns(startingColumns(opening.data));
        setAudience(opening.data.audience ?? "anyone");
    }, [opening.data, columns]);

    const save = useMutation({
        mutationFn: async ({ to, chosen }: { to: Audience | "off"; chosen: string[] }) => {
            if (to === "off") {
                await source.closeTable(podId, table);
                return null;
            }
            return source.openTable(podId, table, to, chosen);
        },
        onSuccess: () => void cache.invalidateQueries({ queryKey: openingKey(podId, table) }),
        onError: (error) => setProblem(isForbidden(error) ? "Only someone who can change this table can open it." : error instanceof Error && error.message ? error.message : "Couldn’t save it. Try again."),
    });
    const create = useCreateWidget(podId);

    const data = opening.data;
    const open = Boolean(data?.audience);
    // Read only once the widgets have loaded: a guess made while they load
    // would offer to make a chat the space already has.
    const door = data?.audience && widgets.data ? doorFor(widgets.data, data.audience) : null;

    return (
        <Modal title="Collect responses" subtitle={"People outside " + teammate + "’s space add rows to " + table} onClose={onClose}>
            {opening.isPending || widgets.isPending ? (
                <p className="all__empty" role="status">Loading…</p>
            ) : !data ? (
                <p role="alert">Couldn’t read this table. <button type="button" className="linkish" onClick={() => void opening.refetch()}>Try again</button></p>
            ) : !widgets.data ? (
                <p role="alert">Couldn’t read the space’s web chats. <button type="button" className="linkish" onClick={() => void widgets.refetch()}>Try again</button></p>
            ) : data.perUser ? (
                <p className="contacts__quiet">Each member sees only their own rows in this table, so it can’t take rows from outside. Make a shared table for responses.</p>
            ) : (
                <div className="collect csheet">
                    <fieldset className="collect__who" disabled={save.isPending}>
                        <legend className="smanage__label">Who can answer</legend>
                        {audienceChoices().map((choice) => (
                            <label key={choice.value} className="collect__choice">
                                <input type="radio" name="audience" checked={audience === choice.value} onChange={() => setAudience(choice.value)} />
                                <span><span>{choice.label}</span><small>{choice.note}</small></span>
                            </label>
                        ))}
                    </fieldset>
                    {audience !== "off" && (
                        <Columns opening={data} chosen={columns ?? []} disabled={save.isPending} onChange={setColumns} />
                    )}
                    {problem && <p role="alert">{problem}</p>}
                    <div className="csheet__actions">
                        <button
                            type="button"
                            className="pill-button"
                            disabled={save.isPending}
                            onClick={() => {
                                const chosen = columns ?? [];
                                const wrong = audience === "off" ? null : openingProblem(data, chosen);
                                setProblem(wrong);
                                if (!wrong) save.mutate({ to: audience, chosen });
                            }}
                        >
                            {save.isPending ? "Saving…" : audience === "off" ? "Stop taking responses" : open ? "Save" : "Start taking responses"}
                        </button>
                        <button type="button" className="ghost-pill" onClick={onClose}>Close</button>
                    </div>
                    {data.audience && !door && (
                        <section className="collect__share" aria-label="Where people answer">
                            <p className="contacts__quiet">
                                People reach a form through {teammate}’s web chat, and none of its chats answers {data.audience === "contacts" ? "people who confirm their email" : "anyone"} yet.
                                {widgets.data.length ? " Switch one on in Contacts, or make one for this form." : " Make one for this form."}
                            </p>
                            {create.isError && <p role="alert">Couldn’t make the chat. Try again.</p>}
                            <div className="csheet__actions">
                                <button
                                    type="button"
                                    className="pill-button"
                                    disabled={create.isPending}
                                    onClick={() => create.mutate(
                                        { ...EMPTY_WIDGET_DRAFT, name: "Website", answer: widgetAnswerFor(data.audience ?? "anyone") },
                                        { onSuccess: setMade },
                                    )}
                                >
                                    {create.isPending ? "Making…" : "Make a web chat for it"}
                                </button>
                            </div>
                        </section>
                    )}
                    {open && door && data.audience && (
                        <section className="collect__share" aria-label="Where people answer">
                            <WidgetPlaces link={formLink(door.pageUrl, table)} embed={formEmbed(door.embed, table)} linkLabel="Share the form" />
                            <details className="collect__own">
                                <summary>Use your own HTML</summary>
                                <p className="contacts__quiet">Any form with these field names works; the table still decides what is written.</p>
                                <div className="cwidget__embed">
                                    <code>{htmlFormSnippet(door.embed, table, data.offered.filter((column) => data.columns.includes(column.name)))}</code>
                                    <CopyButton text={htmlFormSnippet(door.embed, table, data.offered.filter((column) => data.columns.includes(column.name)))} label="Copy the HTML" />
                                </div>
                            </details>
                            {onAsk && (
                                <button type="button" className="ghost-pill" onClick={() => { onAsk(customFormAsk(table)); onClose(); }}>
                                    Ask {teammate} for a custom design
                                </button>
                            )}
                        </section>
                    )}
                </div>
            )}
            {made && <SecretSheet widget={made} secret={made.signingSecret} onClose={() => setMade(null)} />}
        </Modal>
    );
}

function Columns({ opening, chosen, disabled, onChange }: { opening: TableOpening; chosen: string[]; disabled: boolean; onChange: (next: string[]) => void }) {
    if (!opening.offered.length) return <p className="contacts__quiet">This table has nothing a person outside could fill in.</p>;
    return (
        <fieldset className="collect__columns" disabled={disabled}>
            <legend className="smanage__label">What they fill in</legend>
            <ul>
                {opening.offered.map((column) => (
                    <li key={column.name}>
                        <label>
                            <input
                                type="checkbox"
                                checked={column.required || chosen.includes(column.name)}
                                disabled={column.required}
                                onChange={(event) => onChange(event.target.checked ? [...chosen, column.name] : chosen.filter((name) => name !== column.name))}
                            />
                            <span className="collect__column">
                                <span>{columnQuestion(column)}</span>
                                <small>{column.name}{column.required ? " · the table needs it" : ""}</small>
                            </span>
                        </label>
                    </li>
                ))}
            </ul>
            <small>A column’s description is the question people see. Edit it in the table to reword it.</small>
        </fieldset>
    );
}
