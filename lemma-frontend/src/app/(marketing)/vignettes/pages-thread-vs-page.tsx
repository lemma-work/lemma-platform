"use client";

import type { CSSProperties, ReactNode } from "react";
import { FACES } from "@/marketing/product-pages";
import { ChatIcon, RefreshIcon, SendIcon, TableIcon } from "@/ui/icons";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./pages-thread-vs-page.module.css";

/* One message in the channel. `from` is a person's name, or Remy. */
function Post({ from, at, children }: { from: string; at: string; children: ReactNode }) {
    return (
        <div className={v.post}>
            {from === "Remy" ? <img className={k.face} src={FACES.Remy} alt="" /> : <span className={k.person}>{from[0]}</span>}
            <div><p className={v.name}>{from} <time>{at}</time></p><p className={v.text}>{children}</p></div>
        </div>
    );
}

/* A line of the channel. `beat` is the beat it arrives on (none: already
   there); `n` staggers two arrivals within one beat. */
function Line({ beat, n = 0, children }: { beat?: number; n?: number; children: ReactNode }) {
    return (
        <div className={`${v.line} ${beat ? v["b" + beat] : ""}`} style={{ ["--n" as string]: n } as CSSProperties}>
            <div>{children}</div>
        </div>
    );
}

const DEALS: [string, string, string][] = [
    ["Northstar", "Procurement", "Send the approved security overview"],
    ["Cedar Health", "Evaluating", "Hold: Priya spoke to them yesterday"],
    ["Birch", "Discovery", "Book a call with their ops lead"],
    ["Oak & Ivy", "Evaluating", "Answer the SSO question"],
];

/** A thread and a page, side by side: Remy answers in a team-chat channel,
 *  the same answer is the opening line of its page, and the channel keeps
 *  talking until the answer has scrolled away. The page stays where it was,
 *  and someone leaves a comment on it.
 *
 *  Beats: 1 Remy's reply in the channel; 2 the page, beside it, opening with
 *  the same line over the Open deals view; 3 and 4 ordinary messages, a day
 *  apart, pushing the reply up and past the top; 5 a comment on "One is stuck
 *  on us", and the Comment button counting it.
 *
 *  No phone strip: the point is the two side by side, one moving and one
 *  not, and either alone tells half of it. */
export function PagesThreadVsPage() {
    return (
        <Vignette className={v.root} beats={[1100, 1700, 1800, 1700, 1700]} hold={3800}
            label="In a team chat channel, Priya asks Remy what’s stuck in the pipeline, and Remy replies that two deals can close by Friday and one is stuck on us: Northstar is waiting on the security overview. Beside it, Remy’s page Pipeline this week opens with the same line above its Open deals view. Over the next two days ordinary messages fill the channel and push the reply out of sight, while the page stays put and someone leaves a comment on One is stuck on us.">
            <div className={v.stage} />

            {/* The channel: drawn plainly, no one's chrome. */}
            <section className={`${v.pane} ${v.chat}`}>
                <div className={`${k.bar} ${v.head}`}><span className={v.hash}>#</span><b>sales</b></div>
                <div className={v.feed}>
                    <Line><p className={v.day}>Tuesday</p></Line>
                    <Line><Post from="Dev" at="3:48">Redwing has the walkthrough video.</Post></Line>
                    <Line><Post from="Aditi" at="4:02">Harbor is signed. Handing it to June.</Post></Line>
                    <Line><Post from="Priya" at="4:10"><span className={v.at}>@Remy</span> what’s stuck in the pipeline?</Post></Line>
                    <Line beat={1}><Post from="Remy" at="4:10">Two deals can close by Friday. One is stuck on us: Northstar is waiting on the security overview.</Post></Line>

                    <Line beat={3}><p className={v.day}>Wednesday</p></Line>
                    <Line beat={3}><Post from="Aditi" at="9:05">who has the Saltbox order form?</Post></Line>
                    <Line beat={3} n={1}><Post from="Dev" at="9:12">moving standup to 10</Post></Line>

                    <Line beat={4}><p className={v.day}>Thursday</p></Line>
                    <Line beat={4}><Post from="Priya" at="11:20">Fern Works wants the Harbor case study</Post></Line>
                    <Line beat={4} n={1}><Post from="Aditi" at="11:34">can someone take the 3pm demo?</Post></Line>
                </div>
                <div className={v.composer}><span>Message #sales</span><i><SendIcon size={13} /></i></div>
            </section>

            {/* The page, in Remy's space. */}
            <section className={`${v.pane} ${v.page}`}>
                <div className={`${k.bar} ${v.pageHead}`}>
                    <span>Remy /</span><b>Pipeline this week</b>
                    <span className={v.pill}><ChatIcon size={14} /><span className={v.pillLabel}>Comment</span><span className={v.pillCount}>1</span></span>
                    <span className={v.share}>Share</span>
                </div>
                <div className={v.doc}>
                    <p className={k.title}>Pipeline this week</p>
                    <p className={`${k.text} ${v.lead}`}>Two deals can close by Friday. <span className={v.mark}>One is stuck on us</span>.</p>

                    <div className={v.view}>
                        <div className={v.viewBar}>
                            <span className={v.viewKind}><TableIcon size={13} />Open deals</span>
                            <span>8 rows</span>
                            <span className={v.viewBtn}><RefreshIcon size={13} /></span>
                            <span className={v.viewBtn}>Open table</span>
                        </div>
                        <table className={`${k.table} ${v.table}`}>
                            <thead><tr><th>Company</th><th>Stage</th><th>Next step</th></tr></thead>
                            <tbody>{DEALS.map(([company, stage, next]) => (
                                <tr key={company}><td>{company}</td><td>{stage}</td><td>{next}</td></tr>
                            ))}</tbody>
                        </table>
                    </div>

                    <p className={`${k.heading} ${v.after}`}>Stuck on us</p>
                    <p className={`${k.text} ${v.bullet}`}>Northstar asked for the security overview on Tuesday. The approved version is drafted and waiting for your yes.</p>
                </div>
            </section>
        </Vignette>
    );
}
