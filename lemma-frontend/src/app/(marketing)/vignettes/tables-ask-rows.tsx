import type { CSSProperties } from "react";
import { ArrowClockwise, ArrowUp, CaretDown, CaretRight, Check, CornersIn, CornersOut, MagnifyingGlass, Paperclip, Table, Waveform, X } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./tables-ask-rows.module.css";

const n = (index: number) => ({ ["--n" as string]: index }) as CSSProperties;

/* Remy's deals, as the board draws them: the piles in the order the stages
   first appear in the table, each card a company and its value, written the
   way the board writes them (thousands separated from 10,000 up). */
type Card = [company: string, value: string];
const PILES: { stage: string; cards: Card[] }[] = [
    { stage: "Procurement", cards: [["Northstar", "12,000"], ["Saltbox", "6400"]] },
    { stage: "Evaluating", cards: [["Cedar Health", "18,500"], ["Oak & Ivy", "7200"], ["Fern Works", "9900"]] },
    { stage: "Won", cards: [["Harbor", "9600"], ["Moss & Co", "5200"]] },
    { stage: "Discovery", cards: [["Birch", "4800"], ["Redwing", "15,000"], ["Pinehurst", "3600"]] },
];

/* The question, typed a word at a time so a line only breaks between words. */
const ASKED = "Which open deals are over 10,000, and what do they add up to?";
const WORDS = ASKED.split(" ").map((word, index, all) => (index < all.length - 1 ? word + " " : word));
const STARTS = WORDS.map((_, index) => WORDS.slice(0, index).join("").length);

/* The one read-only query Remy ran, as the chat prints it above its rows,
   and the rows as they come back: the column names as they are, and every
   value as it is stored. */
const SQL = "SELECT company, stage, value FROM deals WHERE stage <> 'Won' AND value > 10000 ORDER BY value DESC";
const ROWS: [string, string, string][] = [
    ["Cedar Health", "Evaluating", "18500"],
    ["Redwing", "Discovery", "15000"],
    ["Northstar", "Procurement", "12000"],
];

/** Ask the table: the deals board is open, with the table's own Ask panel
 *  beside it. Someone asks which open deals are over 10,000 and what they
 *  add up to; Remy answers with the total and, under it, the rows from the
 *  one read-only query it ran, the query printed above them: the same three
 *  deals that sit on the board beside it. The board itself is not lit up,
 *  because the product draws nothing there from a chat's query.
 *
 *  Beats: 1 the question typed into the composer; 2 sent, and Remy working;
 *  3 the answer, "45,500 across three deals."; 4 the query's rows under it,
 *  the SELECT above them.
 *
 *  On a phone it shows the panel, where all four beats happen, and the
 *  Discovery pile beside it, from its left edge. */
export function TablesAskRows() {
    return (
        <Vignette className={v.root} width={640} height={570} phone={{ x: 322, width: 318 }} beats={[1300, 2600, 1500, 1200]} hold={4600}
            label="Remy's deals table is open as a board, with the table's Ask panel open beside it and empty: Ask about this table, Questions about the rows, or changes to make. A question types itself into the panel and is sent: Which open deals are over 10,000, and what do they add up to? Remy works for a moment and answers: 45,500 across three deals. Under the answer is a query result, marked read-only query, with the SELECT it ran printed above three rows: Cedar Health, Evaluating, 18500; Redwing, Discovery, 15000; Northstar, Procurement, 12000. The same three deals sit on the board beside it.">
            <div className={`${k.bar} ${v.head}`}>
                <img className={k.face} src={FACES.Remy} alt="" />
                <span>Remy /</span><span>Tables /</span><b>Deals</b>
                <span className={v.refresh}><ArrowClockwise size={13} />Refresh</span>
                <span className={v.find}><MagnifyingGlass size={14} /></span>
                <span className={v.share}>Share</span>
            </div>

            <div className={v.page}>
                <p className={k.title}>Deals</p>
                <p className={k.muted}>10 rows loaded</p>
                <span className={v.search}><MagnifyingGlass size={14} />Search loaded rows…</span>
                <div className={v.tools}>
                    <span>Filter</span>
                    <span>Sort</span>
                    <span className={v.order}>Default order<CaretDown size={11} /></span>
                    <span>Board, grouped by stage.</span>
                </div>

                <div className={v.board}>
                    {PILES.map(({ stage, cards }) => (
                        <section key={stage} className={v.pile}>
                            <p className={v.pileHead}>{stage}<span className={v.count}>{cards.length}</span></p>
                            <div className={v.cards}>
                                {cards.map(([company, value]) => (
                                    <div key={company} className={v.card}>
                                        <span className={v.company}>{company}</span>
                                        <span className={v.value}>{value}</span>
                                    </div>
                                ))}
                            </div>
                            <span className={v.add}>＋ Add</span>
                        </section>
                    ))}
                </div>
                <p className={`${k.muted} ${v.total}`}>10 rows</p>
            </div>

            {/* The table's own conversation with Remy, as the floating chat
                draws it over the page. */}
            <section className={v.chat}>
                <div className={v.chatHead}>
                    <img className={k.face} src={FACES.Remy} alt="" />
                    <span className={v.chatTitle}><span>Remy</span><small>On Deals</small></span>
                    <i className={`${v.min} ${v.expand}`}><CornersOut size={15} /></i>
                    <i className={v.min}><CornersIn size={15} /></i>
                </div>
                <div className={v.context}>
                    <span className={v.chip}><Table size={13} /><span>Deals</span><em>Table</em><X size={11} /></span>
                </div>

                <div className={v.thread}>
                    <p className={v.mine}>{ASKED}</p>
                    <div className={v.reply}>
                        <div className={v.replyHead}>
                            <img className={k.face} src={FACES.Remy} alt="" />
                            <span className={v.who}>Remy</span>
                            <time className={v.at}>14:12</time>
                        </div>
                        <span className={v.steps}>
                            <i className={v.state}><span className={v.pulse} /><Check className={v.tick} size={10} weight="bold" /></i>
                            <span className={v.what}><span className={v.working}>Working</span><span className={v.worked}>Worked for 5s · 2 steps</span></span>
                            <CaretRight className={v.chev} size={11} />
                        </span>
                        <p className={v.said}>45,500 across three deals.</p>
                        <figure className={v.result}>
                            <figcaption className={v.resultHead}><span>Query result</span><small>read-only query</small></figcaption>
                            <pre className={v.sql}>{SQL}</pre>
                            <table className={v.grid}>
                                <thead><tr><th>company</th><th>stage</th><th>value</th></tr></thead>
                                <tbody>{ROWS.map(([company, stage, value]) => (
                                    <tr key={company}><td>{company}</td><td>{stage}</td><td>{value}</td></tr>
                                ))}</tbody>
                            </table>
                        </figure>
                    </div>

                    <div className={v.quiet}>
                        <p className={v.quietHead}>Ask about this table</p>
                        <p className={v.quietText}>Questions about the rows, or changes to make.</p>
                    </div>
                </div>

                {/* The floating chat's box: the words across the top, attach
                    on the left and voice and send on the right below them. */}
                <div className={v.composer}>
                    <span className={v.field}>
                        <span className={v.placeholder}>Ask Remy…</span>
                        <span className={v.typed}>
                            {WORDS.map((word, index) => (
                                <span key={index} className={v.word}>
                                    {word.split("").map((char, at) => <span key={at} className={v.ch} style={n(STARTS[index] + at)}>{char}</span>)}
                                </span>
                            ))}
                            <span className={`${k.caret} ${v.caret}`} />
                        </span>
                    </span>
                    <i className={v.attach}><Paperclip size={15} /></i>
                    <i className={v.wave}><Waveform size={16} /></i>
                    <i className={v.send}><ArrowUp size={14} /></i>
                </div>
            </section>
        </Vignette>
    );
}
