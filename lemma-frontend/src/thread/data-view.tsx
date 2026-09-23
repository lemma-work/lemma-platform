import { useQuery } from "@tanstack/react-query";
import { source } from "@/data";
import { TableIcon } from "@/ui/icons";

/** How much of a result is a preview rather than the thing itself. Past this
 *  a reader is scrolling a card instead of reading an answer, and the table
 *  tab is where scrolling belongs. */
const ROWS = 8;
const COLUMNS = 6;

function cell(value: unknown): string {
    if (value === null || value === undefined) return "";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
}

/** A table or a query, with its rows in it.
 *
 *  Not a card with a name and a type on it. The agent answers "here are the
 *  Q1 totals" and what appears is the word "table", while the data is one
 *  request away — and on a call, where the widget is the only thing the person
 *  can look at, a title is nothing at all.
 *
 *  A preview, not the table: the first rows and the first columns, with the
 *  full thing one click away. `TableView` already exists for the full thing
 *  and brings a heading, a search box and a column picker with it — right on
 *  a tab of its own, far too much inside a card. */
export function DataView({ podId, name, sql, onOpenTable }: {
    podId: string;
    /** The table to read. Ignored when `sql` is given. */
    name?: string;
    /** A read-only SELECT, when the agent showed a query rather than a table. */
    sql?: string;
    onOpenTable?: (name: string) => void;
}) {
    const query = useQuery({
        queryKey: sql ? ["query", podId, sql] : ["table-preview", podId, name],
        queryFn: () =>
            sql
                ? source.runQuery(podId, sql)
                : source.tableRows(podId, name as string).then((page) => ({ items: page.items, truncated: false })),
        enabled: Boolean(sql || name),
        staleTime: 60_000,
        retry: false,
    });

    const label = name ?? "Query result";
    const rows = query.data?.items ?? [];
    const fields = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
    const shown = fields.slice(0, COLUMNS);
    const hiddenColumns = fields.length - shown.length;
    const visible = rows.slice(0, ROWS);

    const truncated = query.data?.truncated === true;
    const footnotes = [
        rows.length > visible.length ? `${visible.length} of ${rows.length} rows` : "",
        hiddenColumns > 0 ? `${hiddenColumns} more column${hiddenColumns === 1 ? "" : "s"}` : "",
    ].filter(Boolean);

    return (
        <figure className="filecard dataview">
            <figcaption className="filecard__head">
                <span className="filecard__name">{label}</span>
                {/* Not the type twice. An unnamed query was headed
                    "query · query", which is a label and its own echo. */}
                <span className="filecard__meta">{sql ? "read-only query" : "table"}</span>
                {name && onOpenTable && (
                    <button className="filecard__open" onClick={() => onOpenTable(name)}>Open</button>
                )}
            </figcaption>

            {/* The SQL is the question that was asked. Shown for a query and
                not for a table, where the name already is the question. */}
            {sql && <pre className="dataview__sql">{sql}</pre>}

            {query.isPending && <p className="dataview__note" role="status">Reading {label}…</p>}
            {query.isError && (
                <p className="dataview__note" role="alert">
                    Could not read {label}. <button onClick={() => void query.refetch()}>Retry</button>
                </p>
            )}

            {!query.isPending && !query.isError && rows.length === 0 && (
                <p className="dataview__note">
                    <TableIcon size={16} /> No rows came back.
                </p>
            )}

            {visible.length > 0 && (
                <div className="library-grid dataview__grid">
                    <table>
                        <thead>
                            <tr>{shown.map((field) => <th key={field}>{field}</th>)}</tr>
                        </thead>
                        <tbody>
                            {visible.map((row, index) => (
                                <tr key={index}>
                                    {shown.map((field) => (
                                        <td key={field} title={cell(row[field])}>{cell(row[field])}</td>
                                    ))}
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            )}

            {/* Everything the preview is not showing, said plainly — and
                nothing at all when it is showing everything, or this is an
                empty bordered strip under every small result. A capped result
                that looks complete is the one failure here that ends with
                somebody quoting a wrong number out loud. */}
            {(footnotes.length > 0 || truncated) && (
                <p className="dataview__note dataview__note--foot">
                    {footnotes.join(" · ")}
                    {truncated && (
                        <span className="dataview__capped">
                            {footnotes.length > 0 ? " · " : ""}cut short by the row cap — there are more rows than this
                        </span>
                    )}
                </p>
            )}
        </figure>
    );
}
