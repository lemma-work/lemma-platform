import type { CSSProperties } from "react";
import { ArrowUp, CaretDown, CaretRight, ChatCircle, Check, MagnifyingGlass, Paperclip, Table, Waveform } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./tables-from-a-sentence.module.css";

const n = (index: number) => ({ ["--n" as string]: index }) as CSSProperties;

/* The button puts the start of the sentence in the chat; the rest is typed,
   a word at a time so a line only ever breaks between words. */
const STARTED = "Set up a table to track ";
const FINISHED = "what each buyer is waiting on us for, with a due date.";
const WORDS = FINISHED.split(" ").map((word, index, all) => (index < all.length - 1 ? word + " " : word));
const STARTS = WORDS.map((_, index) => WORDS.slice(0, index).join("").length);

/* follow_ups, as the chat's preview of a table draws its first rows: the
   column names as they are, and every value as it is stored. */
const ROWS: [string, string, string][] = [
    ["Northstar: security overview to Anita", "false", "2026-10-02"],
    ["Saltbox: chase the order form", "false", "2026-10-03"],
    ["Oak & Ivy: SSO answer for Lena", "false", "2026-10-04"],
];

/** A table from one finished sentence: on Remy's empty Tables page you press
 *  "Ask Remy to set one up", the chat beside it starts the sentence, you
 *  finish it, and Remy makes Follow Ups, its first rows already in, with row
 *  security on from the start.
 *
 *  Beats: 1 the button, pressed, and "Set up a table to track " in the
 *  composer; 2 the rest of the sentence, typed; 3 sent, and Remy working;
 *  4 Remy's reply, the table's preview under it, whole; 5 the Tables page
 *  lists Follow Ups, marked RLS, own rows each.
 *
 *  On a phone it shows the chat alone: the sentence arriving in the box,
 *  and the table it made, whose preview is as narrow as its rows let it be. */
export function TablesFromASentence() {
    return (
        <Vignette className={v.root} phone={{ x: 212, width: 420 }} beats={[1100, 1300, 2300, 1300, 2300]} hold={3400}
            label="Remy's Tables page is empty: No tables yet, with a button that says Ask Remy to set one up. Pressing it puts 'Set up a table to track' in the chat beside the page, and the rest of the sentence types itself in: what each buyer is waiting on us for, with a due date. Remy replies that it made Follow Ups, with the three it knows about so far, over a preview of the follow_ups table with the columns follow_up, done and due: Northstar, security overview to Anita; Saltbox, chase the order form; Oak & Ivy, SSO answer for Lena; each with a due date. The Tables page now lists Follow Ups, marked RLS, own rows each.">
            <div className={k.bar}><img className={k.face} src={FACES.Remy} alt="" /><span>Remy /</span><b>Tables</b><i /><i /><i /></div>

            {/* ── Remy's Tables page ── */}
            <section className={v.tables}>
                <div className={v.head}>
                    <p className={v.h1}>Tables</p>
                    <span className={v.newBtn}>New <CaretDown size={12} /></span>
                </div>
                <span className={v.search}><MagnifyingGlass size={14} />Search</span>

                <div className={v.empty}>
                    <svg className={v.art} viewBox="0 0 176 112" aria-hidden="true">
                        <rect className={v.artCard} x="14" y="12" width="148" height="88" rx="9" />
                        <path className={v.artSoft} d="M14 32V21a9 9 0 0 1 9-9h130a9 9 0 0 1 9 9v11z" />
                        <path className={v.artRule} d="M14 32h148M14 55h148M14 78h148M62 12v88M112 12v88" />
                        <rect className={v.artInk} x="24" y="19.5" width="26" height="5" rx="2.5" />
                        <rect className={v.artInk} x="72" y="19.5" width="22" height="5" rx="2.5" />
                        <rect className={v.artInk} x="122" y="19.5" width="18" height="5" rx="2.5" />
                        <rect className={v.artLine} x="24" y="41.5" width="30" height="4" rx="2" />
                        <rect className={v.artLine} x="72" y="41.5" width="26" height="4" rx="2" />
                        <rect className={v.artPill} x="122" y="38.5" width="30" height="10" rx="5" />
                        <rect className={v.artLine} x="24" y="64.5" width="24" height="4" rx="2" />
                        <rect className={v.artLine} x="72" y="64.5" width="32" height="4" rx="2" />
                        <rect className={v.artPillOther} x="122" y="61.5" width="24" height="10" rx="5" />
                        <rect className={v.artLine} x="24" y="87.5" width="28" height="4" rx="2" />
                        <rect className={v.artLine} x="72" y="87.5" width="20" height="4" rx="2" />
                        <rect className={v.artPill} x="122" y="84.5" width="30" height="10" rx="5" />
                    </svg>
                    <p className={v.emptyTitle}>No tables yet</p>
                    <p className={v.emptyLine}>Tables keep what your team tracks, one row each; Remy sets them up and fills them in.</p>
                    <span className={v.primary}><ChatCircle size={14} />Ask Remy to set one up</span>
                    <span className={v.starters}><span className={v.starter}>Tasks</span><span className={v.starter}>Contacts</span></span>
                </div>

                <div className={v.list}>
                    <p className={v.th}>Name</p>
                    <div className={v.row}>
                        <span className={v.glyph}><Table size={17} /></span>
                        <span className={v.label}>
                            <span className={v.name}>Follow Ups<em className={v.rls}>RLS</em></span>
                            <small>What each buyer is waiting on us for</small>
                            <span className={v.access}>
                                <span className={v.faces}><i>PR</i><i>AD</i></span>Own rows each
                            </span>
                        </span>
                    </div>
                </div>
            </section>

            {/* ── The chat beside it ── */}
            <section className={v.chat}>
                <div className={v.feed}>
                    <div className={`${v.line} ${v.quiet}`}><div>
                        <div className={v.quietIn}>
                            <img className={v.mark} src={FACES.Remy} alt="" />
                            <p className={v.quietTitle}>What should Remy work on?</p>
                            <p className={v.quietBody}>Send a message to start a new conversation.</p>
                        </div>
                    </div></div>

                    <div className={`${v.line} ${v.in3}`}><div>
                        <p className={v.you}>{STARTED}{FINISHED}</p>
                        <div className={v.reply}>
                            <img className={`${k.face} ${v.face}`} src={FACES.Remy} alt="" />
                            <div className={v.col}>
                                <p className={v.who}>Remy</p>
                                <span className={v.steps}>
                                    <span className={v.working}><i className={v.state}><i className={v.pulse} /></i>Working</span>
                                    <span className={v.done}><i className={v.state}><Check size={10} /></i>2 steps<CaretRight className={v.chev} size={11} /></span>
                                </span>
                                <div className={`${v.line} ${v.in4}`}><div>
                                    <p className={v.said}>Made Follow Ups, with the three I know about so far.</p>
                                    <figure className={v.preview}>
                                        <figcaption className={v.previewHead}>
                                            <span className={v.previewName}>follow_ups</span>
                                            <span className={v.previewMeta}>table</span>
                                            <span className={v.open}>Open</span>
                                        </figcaption>
                                        <table className={v.grid}>
                                            <thead><tr><th>follow_up</th><th>done</th><th>due</th></tr></thead>
                                            <tbody>{ROWS.map(([what, done, due]) => (
                                                <tr key={what}><td>{what}</td><td>{done}</td><td>{due}</td></tr>
                                            ))}</tbody>
                                        </table>
                                    </figure>
                                </div></div>
                            </div>
                        </div>
                    </div></div>
                </div>

                <div className={v.composer}>
                    <Paperclip className={v.attach} size={16} />
                    <span className={v.field}>
                        <span className={v.placeholder}>Ask Remy…</span>
                        <span className={v.typed}>
                            {STARTED}
                            {WORDS.map((word, index) => (
                                <span key={index} className={v.word}>
                                    {word.split("").map((char, at) => <span key={at} className={v.ch} style={n(STARTS[index] + at)}>{char}</span>)}
                                </span>
                            ))}
                            <span className={`${k.caret} ${v.caret}`} />
                        </span>
                    </span>
                    <Waveform className={v.wave} size={17} />
                    <i className={v.send}><ArrowUp size={15} /></i>
                </div>
            </section>

            <svg className={v.pointer} width="16" height="22" viewBox="0 0 16 22" aria-hidden="true">
                <path d="M1.5 1.5v16.2l4.1-3.9 2.6 6.1 2.6-1.1-2.6-6h5.6z" />
            </svg>
        </Vignette>
    );
}
