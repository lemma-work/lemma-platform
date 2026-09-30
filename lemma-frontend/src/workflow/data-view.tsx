"use client";

import { useState } from "react";

/** A value, read as a document rather than as JSON.
 *
 *  A step's output is whatever its node produced — an agent's structured
 *  answer, a function's return, a form's answers — and most of it is a flat
 *  object of short values with the occasional paragraph. Printed as JSON it is
 *  braces and quotes around the thing somebody wanted to read. So: one row per
 *  key, the key said as words, prose kept as prose, and the raw payload one
 *  click away for the times the shape itself is the question. */
export function DataView({ value, lead, showLead = false }: {
    value: unknown;
    /** The sentence already pulled out of this value; its key is not repeated. */
    lead?: string | null;
    /** Print that sentence here too, above the fields. */
    showLead?: boolean;
}) {
    const [raw, setRaw] = useState(false);
    if (value === undefined || value === null || (typeof value === "object" && Object.keys(value as object).length === 0)) {
        return null;
    }
    return (
        <div className="dataview">
            {raw ? <pre className="dataview__raw">{json(value)}</pre> : <Fields value={value} lead={lead ?? null} showLead={showLead} />}
            <button className="dataview__toggle" onClick={() => setRaw((v) => !v)}>{raw ? "Show as fields" : "Show data"}</button>
        </div>
    );
}

function Fields({ value, lead, showLead }: { value: unknown; lead: string | null; showLead: boolean }) {
    if (typeof value !== "object" || value === null) return <Prose text={String(value)} />;
    if (Array.isArray(value)) return <Scalar value={value} />;
    const entries = Object.entries(value as Record<string, unknown>).filter(([, one]) => one !== undefined && one !== null && !(typeof one === "string" && one === lead));
    return (
        <>
            {showLead && lead && <Prose text={lead} />}
            {entries.length > 0 && (
                <dl className="dataview__fields">
                    {entries.map(([key, one]) => (
                        <div key={key}>
                            <dt>{words(key)}</dt>
                            <dd><Scalar value={one} /></dd>
                        </div>
                    ))}
                </dl>
            )}
        </>
    );
}

function Scalar({ value }: { value: unknown }) {
    if (typeof value === "boolean") return <>{value ? "Yes" : "No"}</>;
    if (typeof value === "number") return <>{value.toLocaleString()}</>;
    if (typeof value === "string") return value.length > 160 || value.includes("\n") ? <Prose text={value} /> : <>{value}</>;
    if (Array.isArray(value) && value.every((one) => ["string", "number", "boolean"].includes(typeof one))) {
        return <>{value.length === 0 ? "None" : value.map(String).join(", ")}</>;
    }
    return <pre className="dataview__nested">{json(value)}</pre>;
}

function Prose({ text }: { text: string }) {
    const [open, setOpen] = useState(false);
    const long = text.length > 480 || text.split("\n").length > 8;
    return (
        <div className="dataview__prose-wrap">
            <p className="dataview__prose" data-open={open || !long || undefined}>{text}</p>
            {long && <button className="dataview__toggle" onClick={() => setOpen((v) => !v)}>{open ? "Show less" : "Show all"}</button>}
        </div>
    );
}

function words(key: string): string {
    const spaced = key.replace(/[_-]+/g, " ").replace(/([a-z])([A-Z])/g, "$1 $2").trim();
    return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

function json(value: unknown): string {
    try {
        return typeof value === "string" ? value : JSON.stringify(value, null, 2);
    } catch {
        return String(value);
    }
}
