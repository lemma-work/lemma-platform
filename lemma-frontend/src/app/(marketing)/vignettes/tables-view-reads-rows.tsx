import type { CSSProperties } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./tables-view-reads-rows.module.css";

/* Remy's follow_ups table (marketing/sample-spaces.ts), row for row: what is
   owed, whether it is done, and how many days off it is due, today being
   Oct 1. The grid lists them in no useful order. */
type Row = { task: string; done: boolean; days: number };
const ROWS: Row[] = [
    { task: "Harbor: intro Mira to June", done: true, days: 0 },
    { task: "Oak & Ivy: SSO answer for Lena", done: false, days: 3 },
    { task: "Redwing: walkthrough video", done: true, days: 1 },
    { task: "Northstar: security overview to Anita", done: false, days: 1 },
    { task: "Fern Works: case study", done: false, days: 4 },
    { task: "Saltbox: chase the order form", done: false, days: 2 },
];
/* How the grid writes the due value (library.tsx `gridText`), and how the
   checklist says it (library/reading.ts `whenever`). */
const date = (days: number) => `Oct ${1 + days}, 2026`;
const when = (days: number) => (days === 0 ? "today" : days === 1 ? "tomorrow" : `in ${days} days`);
/* The checklist's order (library/forms.tsx `ChecklistForm`), shown in the
   two steps it is made of: the done rows sink below the open ones, then the
   open ones go soonest due first. */
const SUNK = [...ROWS.filter((row) => !row.done), ...ROWS.filter((row) => row.done)];
const SORTED = [...SUNK].sort((a, b) => Number(a.done) - Number(b.done) || a.days - b.days);
/* Rows sit under the 32 px header strip, one every 34 px. */
const TOP = 32;
const ROW = 34;
const NOTE = "Checklist, sorted by due.";

const vars = (values: Record<string, string | number>) => values as CSSProperties;

/** A table that reads its own values: Remy's Follow Ups opens as a plain
 *  grid; the screen reads the done column and the due column, sorts the rows
 *  by them, and draws them as a checklist, then says what it chose.
 *
 *  Beats: 1 the grid arrives; 2 a highlight runs down done, then down due;
 *  3 the rows re-sort: the done ones sink, then the rest go soonest due
 *  first; 4 the grid becomes a checklist, ticks at the left, dates said as
 *  days, done rows ticked and struck through; 5 "Checklist, sorted by due." types in beside
 *  Filter and Sort, and "4 left of 6" sits above the list.
 *
 *  On a phone it shows the follow-ups and the done column beside them: the
 *  done ones sink, and the note says what the rest were sorted by. */
export function TablesViewReadsRows() {
    return (
        <Vignette className={v.root} phone={{ x: 8, width: 400 }} beats={[800, 1500, 2300, 2300, 2200]} hold={3400}
            label="Remy's Follow Ups table opens as a plain grid of six follow-ups, with a done column reading true or false and a due column of dates, in no useful order. The screen reads the done column, then the due column, sorts the rows soonest due first with the two done ones at the bottom, and draws them as a checklist: a tick box on each row, due dates said as tomorrow, in 2 days, in 3 days and in 4 days, and the done rows for Harbor and Redwing ticked and struck through. Beside Filter and Sort it says Checklist, sorted by due, with 4 left of 6 above the list. Nobody chose the view.">
            <div className={k.bar}><img className={k.face} src={FACES.Remy} alt="" /><span>Remy / Tables /</span><b>Follow Ups</b><i /><i /><i /></div>
            <div className={k.body}>
                <header className={v.head}>
                    <div>
                        <p className={k.title}>Follow Ups</p>
                        <p className={`${k.muted} ${v.loaded}`}>6 rows loaded</p>
                    </div>
                    <span className={k.btn}><span className={v.plus}>+</span>New row</span>
                </header>

                <div className={v.tools}>
                    <span className={v.filter}>Filter</span>
                    <span className={v.sort}>Sort<span className={v.select}>Default order
                        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.8 5 6.8 8 3.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
                    </span></span>
                    <span className={v.note}>{[...NOTE].map((char, index) => <span key={index} style={vars({ "--n": index })}>{char}</span>)}</span>
                    <span className={v.actions}><span className={v.asTable}>Show as table</span><span>Full screen</span></span>
                </div>

                <div className={v.sheet}>
                    <div className={v.cols}>
                        <span className={v.colTask}>follow_up</span>
                        <span className={v.colDone}>done</span>
                        <span className={v.colDue}>due</span>
                    </div>
                    <p className={v.count}>4 left of 6<span>+ Add</span></p>

                    {ROWS.map((row, index) => {
                        const sunk = SUNK.indexOf(row);
                        const sorted = SORTED.indexOf(row);
                        return (
                            <div key={row.task} className={`${v.row} ${row.done ? v.isDone : sorted < sunk ? v.rises : ""}`}
                                style={vars({ top: TOP + index * ROW, "--i": index, "--n": sorted, "--sink": (sunk - index) * ROW + "px", "--sort": (sorted - sunk) * ROW + "px" })}>
                                <div className={v.line}>
                                    <span className={`${k.check} ${row.done ? k.checkOn : ""} ${v.tick}`} />
                                    <span className={v.task}>{row.task}</span>
                                    <span className={v.flag}>{String(row.done)}</span>
                                    <span className={v.date}>{date(row.days)}</span>
                                    <span className={v.when}>{when(row.days)}</span>
                                </div>
                            </div>
                        );
                    })}
                    <div className={v.frame} />
                </div>
            </div>
        </Vignette>
    );
}
