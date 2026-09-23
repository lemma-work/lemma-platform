"use client";

import { useState, type ReactNode } from "react";
import { filterable, isAsking, NO_FILTERS, orderable, type Filters, type Ordering, type Span } from "./filters";
import type { TableProfile } from "./profile";

/** Asking a table something.
 *
 *  Every control here is a column the table was read as having, so a table
 *  with no status offers no status filter rather than a disabled one. The values in
 *  a status filter are the values that table actually holds — the same reading
 *  that lets a card's status be a picker.
 *
 *  Folded away until asked for, because the common case is looking at the whole
 *  table, and a row of controls above a table nobody is narrowing is a row of
 *  controls in the way. The count of what is on is on the button, so a filter
 *  left on cannot hide behind the fold.
 *
 *  One strip, not two. What narrows the table, what the automatic layout
 *  thought it saw, and the way back out of it were three separate bars stacked
 *  over the rows, which is three rules and three paddings before anybody
 *  reaches the data. They are one line here, and `note` and `actions` are how
 *  the caller puts its half in it.
 */
const SPANS: { span: Span; label: string }[] = [
    { span: "any", label: "Any time" },
    { span: "overdue", label: "Overdue" },
    { span: "today", label: "Today" },
    { span: "week", label: "Next 7 days" },
    { span: "later", label: "Later" },
];

export function FilterBar({ profile, filters, onFilters, order, onOrder, note, actions }: {
    profile: TableProfile;
    filters: Filters;
    onFilters: (next: Filters) => void;
    order: Ordering | null;
    onOrder: (next: Ordering | null) => void;
    /** What this table is being shown as, and why. */
    note?: ReactNode;
    /** The way back to the grid, full screen — whatever acts on the view. */
    actions?: ReactNode;
}) {
    const [open, setOpen] = useState(false);
    const columns = filterable(profile);
    const sortable = orderable(profile);
    /* The strip carries the caller's half too, so it survives a table that
       offers nothing to filter or sort by. */
    if (columns.length === 0 && sortable.length === 0 && !note && !actions) return null;

    const on = Object.values(filters.values).filter((keep) => keep.length > 0).length
        + Object.values(filters.spans).filter((span) => span !== "any").length;

    const toggle = (column: string, value: string) => {
        const keep = filters.values[column] ?? [];
        onFilters({
            ...filters,
            values: {
                ...filters.values,
                [column]: keep.includes(value) ? keep.filter((v) => v !== value) : [...keep, value],
            },
        });
    };

    return <div className="table-ask">
        <div className="table-ask__bar">
            {/* The fold only opens onto per-column controls, so a table with
                none of those gets no opener. Offering one meant a click that
                unfolded an empty tray. */}
            {columns.length > 0 && <button className="table-ask__toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
                {open ? "Hide filters" : "Filter"}{on > 0 ? " (" + on + ")" : ""}
            </button>}
            {sortable.length > 0 && <label className="table-ask__sort">
                Sort
                <select value={order?.column ?? ""} onChange={(event) => onOrder(
                    event.target.value ? { column: event.target.value, down: order?.down ?? true } : null,
                )}>
                    <option value="">Default order</option>
                    {sortable.map((c) => <option key={c.name} value={c.name}>{c.name}</option>)}
                </select>
                {order && <button className="table-ask__dir" onClick={() => onOrder({ ...order, down: !order.down })}>
                    {order.down ? "↓ newest first" : "↑ oldest first"}
                </button>}
            </label>}
            {/* Only when there is something to clear: a permanent "Clear" is a
                control that does nothing most of the time it is looked at. */}
            {isAsking(filters) && <button className="table-ask__clear" onClick={() => onFilters(NO_FILTERS)}>Clear</button>}
            {/* A layout nobody asked for has to say what it thought it saw, and
                the way back has to sit next to the sentence that explains it. */}
            {note && <span className="table-ask__note">{note}</span>}
            {actions && <span className="table-ask__actions">{actions}</span>}
        </div>

        {open && columns.length > 0 && <div className="table-ask__body">
            {columns.map((column) => <div className="table-ask__row" key={column.name}>
                <span className="table-ask__label">{column.name}</span>
                {column.role === "date" ? (
                    <div className="table-ask__chips">
                        {SPANS.map(({ span, label }) => <button key={span}
                            aria-pressed={(filters.spans[column.name] ?? "any") === span}
                            onClick={() => onFilters({ ...filters, spans: { ...filters.spans, [column.name]: span } })}
                        >{label}</button>)}
                    </div>
                ) : column.role === "boolean" ? (
                    <div className="table-ask__chips">
                        {["true", "false"].map((value) => <button key={value}
                            aria-pressed={(filters.values[column.name] ?? []).includes(value)}
                            onClick={() => toggle(column.name, value)}
                        >{value === "true" ? "Done" : "Not done"}</button>)}
                    </div>
                ) : (
                    <div className="table-ask__chips">
                        {column.values.map((value) => <button key={value}
                            aria-pressed={(filters.values[column.name] ?? []).includes(value)}
                            onClick={() => toggle(column.name, value)}
                        >{value}</button>)}
                    </div>
                )}
            </div>)}
        </div>}
    </div>;
}
