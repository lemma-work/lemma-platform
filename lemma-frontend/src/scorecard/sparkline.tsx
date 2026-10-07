"use client";

import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { CheckIcon, CloseIcon, ExternalIcon } from "@/ui/icons";
import { barsFor, lastWeek, type WeekScore } from "./history";
import { hasRows, type Measure } from "./measures";
import { measureRows } from "./store";
import { sayDate } from "./review";

/** Four weeks as four bars, the target as a dashed line, and the newest week
 *  in words. Met is drawn in ink, missed in the waiting colour, and a week
 *  that could not be judged as a faint stub — never as a zero. */
export function Sparkline({ measure, weeks }: { measure: Measure; weeks: WeekScore[] }) {
    const { bars, target } = barsFor(measure, weeks);
    const words = weeks.map((week) => week.shown || "nothing to count").join(", ");
    return (
        <span className="spark" role="img" aria-label={"Last " + weeks.length + " weeks: " + words}>
            <span className="spark__bars">
                {bars.map((bar, at) => (
                    <i key={at} data-state={bar.state} style={{ height: Math.round(4 + bar.height * 28) + "px" }} />
                ))}
                <span className="spark__target" style={{ bottom: Math.round(4 + target * 28) + "px" }} />
            </span>
        </span>
    );
}

/** The newest week's number, which opens to the units behind it when the
 *  measure counts rows of work. */
export function WeekNumber({ podId, measure, weeks }: { podId: string; measure: Measure; weeks: WeekScore[] }) {
    const said = lastWeek(weeks);
    if (!said) return null;
    return <RowsButton podId={podId} measure={measure} label={"Last 7 days: " + said} />;
}

/** A number that opens to the rows it was counted from — which reels missed
 *  6 pm, which conversations waited — for the week ending `end` (the last
 *  seven days when absent). Plain text for a measure with no rows to show. */
export function RowsButton({ podId, measure, label, end }: { podId: string; measure: Measure; label: string; end?: string }) {
    const [open, setOpen] = useState(false);
    if (!hasRows(measure)) return <span className="spark__now">{label}</span>;
    return (
        <span className="spark__wrap">
            <button className="spark__now spark__now--open" aria-expanded={open} onClick={() => setOpen((was) => !was)}>{label}</button>
            {open && <UnitRows podId={podId} measure={measure} end={end} onClose={() => setOpen(false)} />}
        </span>
    );
}

function UnitRows({ podId, measure, end, onClose }: { podId: string; measure: Measure; end?: string; onClose: () => void }) {
    /* Escape closes it, as it closes every other panel over the page. */
    useEffect(() => {
        const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
        window.addEventListener("keydown", close);
        return () => window.removeEventListener("keydown", close);
    }, [onClose]);
    const rows = useQuery({ queryKey: ["scorecard-rows", podId, measure.key, end ?? "now"], queryFn: () => measureRows(podId, measure.key, end), staleTime: 60_000 });
    return (
        <div className="unitrows" role="dialog" aria-label={"What counted for " + measure.measure}>
            <div className="unitrows__head">
                <span>What counted{end ? ", week to " + sayDate(end) : ", last 7 days"}</span>
                <button className="unitrows__close" aria-label="Close" onClick={onClose}><CloseIcon size={14} /></button>
            </div>
            {rows.isPending && <p className="aboutpage__quiet">Loading…</p>}
            {rows.isError && <p className="aboutpage__quiet" role="alert">Couldn’t load the rows. <button className="linkish" onClick={() => void rows.refetch()}>Try again</button></p>}
            {rows.isSuccess && rows.data.length === 0 && <p className="aboutpage__quiet">Nothing in these seven days.</p>}
            {rows.isSuccess && rows.data.length > 0 && (
                <ul>
                    {rows.data.map((row) => (
                        <li key={row.id} data-passed={row.passed === null ? undefined : String(row.passed)}>
                            <span className="unitrows__mark" aria-label={row.passed === null ? "" : row.passed ? "Passed" : "Missed"}>
                                {row.passed === null ? null : row.passed ? <CheckIcon size={13} /> : <CloseIcon size={13} />}
                            </span>
                            <span className="unitrows__label">{row.label}</span>
                            {row.link && /^https?:\/\//.test(row.link) && (
                                <a href={row.link} target="_blank" rel="noreferrer" aria-label={"Open " + row.label}><ExternalIcon size={13} /></a>
                            )}
                        </li>
                    ))}
                </ul>
            )}
        </div>
    );
}
