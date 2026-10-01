import type { CSSProperties } from "react";
import { ShieldCheck, Table } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./tables-own-rows.module.css";

/* Follow Ups as the sample seeds it, split by owner: [follow-up, due, done].
   Each list is drawn the way the checklist draws a narrow table, soonest
   first with the due date under the name, finished rows struck through at
   the bottom. */
type Row = [string, string, boolean];
const PRIYA: Row[] = [
    ["Northstar: security overview to Anita", "tomorrow", false],
    ["Saltbox: chase the order form", "in 2 days", false],
];
const ADITI: Row[] = [
    ["Fern Works: case study", "in 4 days", false],
    ["Harbor: intro Mira to June", "today", true],
    ["Redwing: walkthrough video", "tomorrow", true],
];
/* The one checklist before it is opened as anyone: rows not yet read. */
const GHOSTS = 5;

const n = (index: number) => ({ ["--n" as string]: index }) as CSSProperties;

/** One person's Follow Ups: the count line, then their rows. Under them,
 *  the outline of rows not yet read, the same for everyone. */
function Checklist({ rows, className }: { rows: Row[]; className: string }) {
    const left = rows.filter(([, , done]) => !done).length;
    return (
        <div className={`${k.card} ${v.list} ${className}`}>
            <div className={v.ghost} aria-hidden="true">
                <span className={v.ghostCount}><span className={k.skel} style={{ width: 74 }} /></span>
                {Array.from({ length: GHOSTS }, (_, index) => (
                    <span key={index} className={v.ghostRow}>
                        <i className={k.check} />
                        <span><span className={k.skel} style={{ width: 52 + ((index * 17) % 30) + "%" }} /><span className={k.skel} style={{ width: "18%" }} /></span>
                    </span>
                ))}
            </div>
            <p className={v.count}>{left} left of {rows.length}</p>
            <ul className={v.rows}>{rows.map(([name, due, done], index) => (
                <li key={name} className={`${v.row} ${done ? v.done : ""}`} style={n(index)}>
                    <i className={`${k.check} ${done ? k.checkOn : ""}`} />
                    <span className={v.name}>{name}</span>
                    <span className={v.due}>{due}</span>
                </li>
            ))}</ul>
        </div>
    );
}

/** Each person, their own rows: Priya and Aditi open the same Follow Ups in
 *  Remy's space, and Postgres hands each of them only the rows they own.
 *  When Priya asks in Slack what's waiting on her, Remy reads the table as
 *  her, so its answer holds her two follow-ups and none of Aditi's.
 *
 *  Beats: 1 the one checklist splits into two, Priya's and Aditi's; 2 each
 *  fills with its owner's rows; 3 Priya asks in Slack; 4 Remy's answer lands
 *  under it, naming her two, and those two rows light; 5 the line that says
 *  who checks it.
 *
 *  On a phone it shows Priya's side: her rows, her question, Remy's answer,
 *  and the first half of the line along the bottom, which ends there. */
export function TablesOwnRows() {
    return (
        <Vignette className={v.root} phone={{ x: 8, width: 318 }} beats={[1400, 1200, 1500, 1300, 1900]} hold={3400} height={460}
            label="In Remy's space, the Follow Ups table is marked RLS, own rows each. Priya and Aditi open the same table and each sees a shorter list of their own: Priya's holds the Northstar security overview to Anita, due tomorrow, and chasing the Saltbox order form, in 2 days; Aditi's holds the Fern Works case study, in 4 days, with Harbor and Redwing ticked off. Priya asks in Slack what's waiting on her, and Remy answers with exactly her two follow-ups and nothing of Aditi's. A line along the bottom says Postgres checks every read and write, and admins of Remy's space see every row.">
            <div className={k.bar}><img className={k.face} src={FACES.Remy} alt="" /><b>Remy’s space</b><span>· Tables</span><i /><i /><i /></div>

            <div className={v.head}>
                <span className={v.glyph}><Table size={18} /></span>
                <span className={v.label}>
                    <span className={v.tableName}>Follow Ups<em className={v.rls}>RLS</em></span>
                    <small>What each buyer is waiting on us for</small>
                </span>
                <span className={v.access}>
                    <span className={v.faces}><span className={k.person}>P</span><span className={k.person}>A</span></span>
                    Own rows each
                </span>
            </div>

            <p className={`${v.who} ${v.whoPriya}`}><span className={k.person}>P</span>Priya</p>
            <p className={`${v.who} ${v.whoAditi}`}><span className={k.person}>A</span>Aditi</p>
            <Checklist rows={ADITI} className={v.aditi} />
            <Checklist rows={PRIYA} className={v.priya} />

            <div className={`${k.card} ${v.slack}`}>
                <img className={v.slackMark} src="/connector-logos/slack.svg" alt="" />
                <div className={v.message}>
                    <span className={k.person}>P</span>
                    <p><b>Priya</b>what’s waiting on me?</p>
                </div>
                <div className={`${v.message} ${v.reply}`}>
                    <img className={k.face} src={FACES.Remy} alt="" />
                    <p><b>Remy</b>Two: the Northstar security overview to Anita, due tomorrow, and the Saltbox order form, in 2 days.</p>
                </div>
            </div>

            <p className={v.rule}><ShieldCheck size={15} /><span>Checked by Postgres on every read and write.</span><span className={v.admins}>Admins of Remy’s space see every row.</span></p>
        </Vignette>
    );
}
