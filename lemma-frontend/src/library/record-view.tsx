"use client";

import { LoadingRows } from "@/ui/loading";

import { useMemo, useState } from "react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { ChevronRightIcon, TableIcon, PlusIcon } from "@/ui/icons";
import { cellText, rowLabel, type Row } from "./record-cache";
import { layoutRecord, profileTable, type ColumnProfile, type RecordLayout } from "./profile";
import { readableName, whenever } from "./reading";
import { source } from "@/data";
import { RecordEditor } from "./record-editor";
import {
    inwardLinks,
    linkLabel,
    LINKED_ROWS_SHOWN,
    outwardLinks,
    type TableShape,
} from "./relations";

/** One row, and what it is attached to.
 *
 *  A row in a grid is a line of cells; the same row on its own page is a thing
 *  with a shape. What makes the page worth having is not the fields — the grid
 *  has those — but the links: what this points at, and what points back at it.
 *
 *  Both directions are read from the schema. Outward is declared on this
 *  table's own columns; inward is declared on everybody else's, which is why
 *  the page needs every table's shape before it can say what references this
 *  one.
 */
export function RecordView({
    podId,
    tableName,
    recordId,
    onOpenTable,
    onOpenRecord,
}: {
    podId: string;
    tableName: string;
    recordId: string;
    onOpenTable: (name: string) => void;
    onOpenRecord: (tableName: string, recordId: string) => void;
}) {
    const [editing, setEditing] = useState(false);
    const [empties, setEmpties] = useState(false);

    const table = useQuery({
        queryKey: ["table", podId, tableName, "detail"],
        queryFn: () => source.tableShape(podId, tableName) as Promise<TableShape>,
        staleTime: 5 * 60_000,
        retry: false,
    });
    const primaryKey = table.data?.primary_key_column ?? "id";

    const record = useQuery({
        queryKey: ["record", podId, tableName, recordId],
        queryFn: () => source.tableRecord(podId, tableName, recordId) as Promise<Row>,
        staleTime: 30_000,
        retry: false,
    });

    /* Every table's shape, because what points *here* is declared over there.
       One request per table, cached for as long as a schema stays still. */
    const shapes = useQuery({
        queryKey: ["table-shapes", podId],
        queryFn: () => source.tableShapes(podId) as Promise<TableShape[]>,
        staleTime: 5 * 60_000,
        retry: false,
    });

    /* A page of the table's rows, read so this page can rank its own columns.
     *
     *  One row cannot say which of its columns is a status and which is free
     *  text — `identified` and `Warm welcome + ask what they are building` are
     *  both strings. Fifty rows can, and it is the same reading the table view
     *  makes, so the two agree about what a column is. Cheap, cached, and not
     *  worth blocking on: while it is in flight the page shows the row in the
     *  order it arrived, which is what it always did. */
    const sample = useQuery({
        queryKey: ["table", podId, tableName, "sample"],
        queryFn: () => source.tableRows(podId, tableName),
        staleTime: 5 * 60_000,
        retry: false,
    });

    const out = useMemo(() => outwardLinks(table.data), [table.data]);
    const back = useMemo(
        () => (shapes.data ? inwardLinks(shapes.data, tableName) : []),
        [shapes.data, tableName],
    );

    /* The row on the other end of each outward link, so a chip can say a name
       rather than a uuid. Skipped where the column is empty — a null foreign
       key is a link that is not there, not one that failed. */
    const pointedAt = useQueries({
        queries: out.map((link) => {
            const value = record.data?.[link.column];
            const id = value === null || value === undefined ? "" : String(value);
            return {
                queryKey: ["record", podId, link.table, id],
                queryFn: () => source.tableRecord(podId, link.table, id) as Promise<Row>,
                enabled: Boolean(id) && Boolean(record.data),
                staleTime: 60_000,
                retry: false,
            };
        }),
    });

    const referencing = useQueries({
        queries: back.map((link) => ({
            queryKey: ["referencing", podId, link.table, link.column, recordId],
            queryFn: () => source.referencing(podId, link.table, link.column, recordId, LINKED_ROWS_SHOWN + 1) as Promise<Row[]>,
            enabled: Boolean(recordId),
            staleTime: 60_000,
            retry: false,
        })),
    });

    if (record.isPending || table.isPending) {
        return <section className="library-view record-view"><p role="status">Reading the row…</p></section>;
    }
    if (record.isError) {
        return (
            <section className="library-view record-view">
                <p role="alert">That row could not be read. <button className="btn" onClick={() => void record.refetch()}>Try again</button></p>
            </section>
        );
    }

    const row = record.data ?? {};
    const title = rowLabel(row, primaryKey);

    /* Links have their own panel below; repeating them here as a uuid in a grey
       box is the page telling you the same thing twice, worse the first time. */
    const linked = out.map((link) => link.column);
    const profile = sample.data
        ? profileTable(
            Object.keys(row).map((name) => ({ name })),
            sample.data.items.length ? sample.data.items : [row],
            { primaryKey },
        )
        : null;
    const layout = profile ? layoutRecord(profile, linked) : null;
    const filled = (column: ColumnProfile) => {
        const value = row[column.name];
        return !(value === null || value === undefined || value === "");
    };

    return (
        <section className="library-view record-view" aria-label={"Row in " + readableName(tableName)}>
            <header className="library-heading">
                <div>
                    <h1>{title}</h1>
                    <p>
                        <button className="record-view__crumb" onClick={() => onOpenTable(tableName)}>
                            <TableIcon size={14} /> {readableName(tableName)}
                        </button>
                        <span className="record-view__id">{recordId}</span>
                    </p>
                </div>
                <div className="library-actions">
                    <button className="btn" onClick={() => setEditing(true)}><PlusIcon size={16} />Edit</button>
                </div>
            </header>

            <div className="record-view__panels">
                {layout ? (
                    <RecordFields row={row} layout={layout} filled={filled} empties={empties} onEmpties={setEmpties}/>
                ) : (
                    /* Before the sample lands there is nothing to rank by, so
                       the row shows in the order it arrived — which is what the
                       page did for every row until now. */
                    <section className="record-panel">
                        <h2>Fields</h2>
                        <dl className="library-row-details">
                            {Object.entries(row).map(([key, value]) => (
                                <div key={key}><dt>{key}</dt><dd>{cellText(value)}</dd></div>
                            ))}
                        </dl>
                    </section>
                )}

                {out.length > 0 && (
                    <section className="record-panel">
                        <h2>Linked records</h2>
                        {out.map((link, index) => {
                            const value = row[link.column];
                            const id = value === null || value === undefined ? "" : String(value);
                            const target = pointedAt[index];
                            return (
                                <div className="record-link" key={link.column}>
                                    <span className="record-link__why">{link.column}</span>
                                    {!id ? (
                                        /* Empty, not broken. A null foreign key is a row
                                           that has not been attached to anything. */
                                        <span className="record-link__none">not set</span>
                                    ) : target?.isError ? (
                                        <span className="record-link__none">{readableName(link.table)} · not readable</span>
                                    ) : (
                                        <button
                                            className="record-link__go"
                                            disabled={target?.isPending}
                                            onClick={() => onOpenRecord(link.table, id)}
                                        >
                                            <span>{linkLabel(target?.data, id, (other) => rowLabel(other, "id"))}</span>
                                            <small>{readableName(link.table)}</small>
                                            <ChevronRightIcon size={14} />
                                        </button>
                                    )}
                                </div>
                            );
                        })}
                    </section>
                )}

                {back.length > 0 && (
                    <section className="record-panel">
                        <h2>Referenced by</h2>
                        {back.map((link, index) => {
                            const found = referencing[index];
                            const rows = found?.data ?? [];
                            const more = rows.length > LINKED_ROWS_SHOWN;
                            const shown = rows.slice(0, LINKED_ROWS_SHOWN);
                            return (
                                <div className="record-back" key={link.table + "." + link.column}>
                                    <div className="record-back__head">
                                        <button className="record-view__crumb" onClick={() => onOpenTable(link.table)}>
                                            <TableIcon size={14} /> {readableName(link.table)}
                                        </button>
                                        <span className="record-link__why">via {link.column}</span>
                                    </div>
                                    {found?.isPending ? (
                                        <LoadingRows label="Loading linked records" rows={2} />
                                    ) : found?.isError ? (
                                        <p className="record-link__none">Couldn’t load linked records.</p>
                                    ) : shown.length === 0 ? (
                                        <p className="record-link__none">Nothing yet.</p>
                                    ) : (
                                        <>
                                            {shown.map((other, at) => {
                                                const otherId = String(other.id ?? at);
                                                return (
                                                    <button
                                                        className="record-link__go"
                                                        key={otherId}
                                                        onClick={() => onOpenRecord(link.table, otherId)}
                                                    >
                                                        <span>{rowLabel(other, "id")}</span>
                                                        <ChevronRightIcon size={14} />
                                                    </button>
                                                );
                                            })}
                                            {/* A page is a summary of what a row is
                                                attached to. Past a handful, the table
                                                is the right place and this says so. */}
                                            {more && (
                                                <button className="record-back__more" onClick={() => onOpenTable(link.table)}>
                                                    More in {readableName(link.table)}
                                                </button>
                                            )}
                                        </>
                                    )}
                                </div>
                            );
                        })}
                    </section>
                )}
            </div>

            {editing && (
                <RecordEditor
                    podId={podId}
                    tableName={tableName}
                    primaryKey={primaryKey}
                    row={row}
                    onClose={() => setEditing(false)}
                    onSaved={() => void record.refetch()}
                />
            )}
        </section>
    );
}

/** The row, in the four bands `layoutRecord` sorts it into.
 *
 *  What changed from a flat list of every column in server order:
 *
 *  - the state is a chip at the top, because it is what somebody came to see;
 *  - the facts are a compact list, ranked, dates said in words;
 *  - prose gets its own block and the full reading width, rather than a `dd`
 *    the same size as the one holding `owner`;
 *  - the key and the timestamps go last, behind a summary, because a uuid
 *    at the top of a page is the least useful line on it;
 *  - and a column that is empty *on this row* is folded away, with a count, so
 *    a table of twenty-three columns does not show fourteen dashes.
 */
function RecordFields({ row, layout, filled, empties, onEmpties }: {
    row: Row;
    layout: RecordLayout;
    filled: (column: ColumnProfile) => boolean;
    empties: boolean;
    onEmpties: (next: boolean) => void;
}) {
    const said = (column: ColumnProfile): string => {
        const value = row[column.name];
        if (column.role !== "date") return cellText(value);
        const when = whenever(value);
        return when ? cellText(value).slice(0, 10) + " · " + when.text : cellText(value);
    };

    const facts = layout.facts.filter(filled);
    const blank = [...layout.facts, ...layout.prose].filter((c) => !filled(c));

    return <>
        <section className="record-panel">
            <h2>Fields</h2>
            {layout.state && filled(layout.state) && (
                <p className="record-state"><span className="form-tag">{cellText(row[layout.state.name])}</span>
                    <small>{layout.state.name}</small></p>
            )}
            <dl className="library-row-details">
                {facts.map((column) => (
                    <div key={column.name}>
                        <dt>{column.name}</dt>
                        <dd>{said(column)}</dd>
                    </div>
                ))}
            </dl>
            {blank.length > 0 && (
                <button className="record-empties" aria-expanded={empties} onClick={() => onEmpties(!empties)}>
                    {empties ? "Hide" : "Show"} {blank.length} empty {blank.length === 1 ? "field" : "fields"}
                </button>
            )}
            {empties && (
                <dl className="library-row-details record-blank">
                    {blank.map((column) => <div key={column.name}><dt>{column.name}</dt><dd>—</dd></div>)}
                </dl>
            )}
        </section>

        {layout.prose.filter(filled).map((column) => (
            <section className="record-panel record-prose" key={column.name}>
                <h2>{column.name}</h2>
                <p>{String(row[column.name] ?? "")}</p>
            </section>
        ))}

        {layout.quiet.length > 0 && (
            <details className="record-panel record-quiet">
                <summary>Identifiers and timestamps</summary>
                <dl className="library-row-details">
                    {layout.quiet.map((column) => (
                        <div key={column.name}><dt>{column.name}</dt><dd>{said(column)}</dd></div>
                    ))}
                </dl>
            </details>
        )}
    </>;
}
