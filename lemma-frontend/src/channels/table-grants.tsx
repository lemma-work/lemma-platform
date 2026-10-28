"use client";

/* What a group's people outside the space may do with its tables: read the
   columns given, and add a row with the columns given. Given one table at a
   time by the group's owners or an admin, through their own access -- and
   everybody in the group reads what is given, so the section says so. */

import { useId, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { source, type Pod } from "@/data";
import { isForbidden } from "@/session/auth-state";
import { channelApi, type TableGrant } from "./api";
import { problemOf } from "./problem";

export function TableGrants({ pod, channelId, may }: { pod: Pod; channelId: string; may: boolean }) {
    const ids = useId();
    const cache = useQueryClient();
    const key = ["table-grants", pod.id, channelId];
    const grants = useQuery({ queryKey: key, queryFn: () => channelApi(pod.id).tableGrants(channelId) });
    const [giving, setGiving] = useState(false);
    const remove = useMutation({
        mutationFn: (table: string) => channelApi(pod.id).removeTableGrant(channelId, table),
        onSuccess: () => void cache.invalidateQueries({ queryKey: key }),
    });
    const given = grants.data ?? [];
    return (
        <section aria-labelledby={ids + "-head"}>
            <p className="gside__label" id={ids + "-head"}>What they can do with {pod.name}’s tables</p>
            {given.length === 0 ? (
                <p className="gside__text">Nothing yet: they read only what is Public, and the bot writes nothing down for them.</p>
            ) : (
                <ul className="tgrants">
                    {given.map((grant) => (
                        <li key={grant.table} className="tgrants__row">
                            <span className="tgrants__text">
                                <strong>{grant.table}</strong>
                                <small>{said(grant)}</small>
                            </span>
                            {may && (
                                <button type="button" className="gsheet__inline gsheet__inline--bad" disabled={remove.isPending} onClick={() => remove.mutate(grant.table)}>Take back</button>
                            )}
                        </li>
                    ))}
                </ul>
            )}
            {may && !giving && <button type="button" className="gsheet__inline" onClick={() => setGiving(true)}>Give them a table</button>}
            {may && giving && <GiveTable pod={pod} channelId={channelId} onDone={() => { setGiving(false); void cache.invalidateQueries({ queryKey: key }); }} />}
            {given.length > 0 && <p className="gside__fine">Everyone in this group reads what you give it, people outside {pod.name} included.</p>}
            {remove.isError && <p className="gside__problem" role="alert">{problemOf(remove.error, "Couldn’t take it back.")}</p>}
        </section>
    );
}

function said(grant: TableGrant): string {
    const parts = [];
    if (grant.readColumns.length) parts.push("read " + grant.readColumns.join(", "));
    if (grant.addColumns.length) parts.push("add a row with " + grant.addColumns.join(", "));
    return parts.join(" · ");
}

function GiveTable({ pod, channelId, onDone }: { pod: Pod; channelId: string; onDone: () => void }) {
    const ids = useId();
    const [table, setTable] = useState("");
    const [read, setRead] = useState<Set<string>>(new Set());
    const [add, setAdd] = useState<Set<string>>(new Set());
    const tables = useQuery({
        queryKey: ["grantable-tables", pod.id],
        queryFn: async () => (await source.listLibrary(pod.id, "tables", "", undefined)).items.filter((item) => !item.rls),
    });
    const columns = useQuery({
        queryKey: ["table-columns", pod.id, table],
        queryFn: async () => (await source.tableColumns(pod.id, table)).filter((column) => !column.system),
        enabled: Boolean(table),
    });
    const save = useMutation({
        mutationFn: () => channelApi(pod.id).setTableGrant(channelId, table, { readColumns: [...read], addColumns: [...add] }),
        onSuccess: onDone,
    });
    const toggle = (set: Set<string>, name: string) => {
        const next = new Set(set);
        if (next.has(name)) next.delete(name); else next.add(name);
        return next;
    };
    return (
        <div className="tgrants__give">
            <label className="tgrants__pick">
                <span id={ids + "-table"}>Table</span>
                <select aria-labelledby={ids + "-table"} value={table} onChange={(event) => { setTable(event.target.value); setRead(new Set()); setAdd(new Set()); }}>
                    <option value="">Pick one</option>
                    {(tables.data ?? []).map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
                </select>
            </label>
            {table && (
                <table className="tgrants__columns">
                    <thead>
                        <tr><th scope="col">Column</th><th scope="col">Read</th><th scope="col">Add</th></tr>
                    </thead>
                    <tbody>
                        {(columns.data ?? []).map((column) => (
                            <tr key={column.name}>
                                <th scope="row">{column.name}</th>
                                <td><input type="checkbox" aria-label={"Read " + column.name} checked={read.has(column.name)} onChange={() => setRead((was) => toggle(was, column.name))} /></td>
                                <td><input type="checkbox" aria-label={"Add " + column.name} checked={add.has(column.name)} onChange={() => setAdd((was) => toggle(was, column.name))} /></td>
                            </tr>
                        ))}
                    </tbody>
                </table>
            )}
            <div className="gsheet__foot gsheet__foot--split">
                <button type="button" className="ghost-pill" onClick={onDone}>Cancel</button>
                <button type="button" className="pill-button" disabled={!table || (read.size === 0 && add.size === 0) || save.isPending} onClick={() => save.mutate()}>
                    {save.isPending ? "Giving…" : "Give it"}
                </button>
            </div>
            {save.isError && (
                <p className="gside__problem" role="alert">
                    {isForbidden(save.error) ? "Only the group’s owners or an admin can give it a table, and only one they can use." : problemOf(save.error, "Couldn’t give it that table.")}
                </p>
            )}
        </div>
    );
}
