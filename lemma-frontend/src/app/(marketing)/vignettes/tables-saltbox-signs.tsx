import { ArrowClockwise, ArrowUp, CaretDown, CaretRight, Check, CornersIn, CornersOut, MagnifyingGlass, Paperclip, Table, Waveform, X } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./tables-saltbox-signs.module.css";

/* Remy's deals, as the board draws them: the piles in the order the stages
   first appear in the table, each card a company and its value, written the
   way the board writes them. `move` marks Saltbox, the one card that changes
   pile. Won lists its cards in table order, so Saltbox lands between Harbor
   and Moss & Co. Discovery is drawn too, behind the panel, where the open
   Ask panel covers it in the product. */
type Card = [company: string, value: string, move?: "leaves" | "arrives"];
const PILES: { stage: string; was: number; now: number; cards: Card[] }[] = [
    { stage: "Procurement", was: 2, now: 1, cards: [["Northstar", "12,000"], ["Saltbox", "6400", "leaves"]] },
    { stage: "Evaluating", was: 3, now: 3, cards: [["Cedar Health", "18,500"], ["Oak & Ivy", "7200"], ["Fern Works", "9900"]] },
    { stage: "Won", was: 2, now: 3, cards: [["Harbor", "9600"], ["Saltbox", "6400", "arrives"], ["Moss & Co", "5200"]] },
    { stage: "Discovery", was: 3, now: 3, cards: [["Birch", "4800"], ["Redwing", "15,000"], ["Pinehurst", "3600"]] },
];

/** Kept current by whoever hears first: Priya tells Remy, in the deals
 *  table's Ask panel, that Saltbox signed. Remy works, replies, and only when
 *  the reply is done does the open board read its rows again, so Saltbox is
 *  simply in Won on the next frame: a redraw, never a slide.
 *
 *  Beats: 1 Priya's message in the panel; 2 Remy working, the board still as
 *  it was; 3 the reply done, "Moved Saltbox to Won and ticked off the
 *  chase."; 4 the board read again: Procurement 1, Won 3, Saltbox in Won.
 *
 *  On a phone it shows the Won pile and the panel: the message, the reply,
 *  and Saltbox turning up in Won, its count going from 2 to 3. */
export function TablesSaltboxSigns() {
    return (
        <Vignette className={v.root} phone={{ x: 282, width: 358 }} beats={[1300, 1300, 1600, 1300]} hold={3600}
            label="Remy's deals table is open as a board: Procurement 2, with Northstar and Saltbox; Evaluating 3; Won 2, with Harbor and Moss & Co. The table's Ask panel floats open over the right of the board, empty, under Ask about this table. Priya writes: Saltbox signed the order form. Remy works for a moment while the board stays as it was, then replies: Moved Saltbox to Won and ticked off the chase. Once the reply is done the board reads its rows again, and Saltbox is in the Won pile, between Harbor and Moss & Co: Procurement 1, Won 3, Evaluating unchanged.">
            <div className={`${k.bar} ${v.head}`}>
                <img className={k.face} src={FACES.Remy} alt="" />
                <span>Remy /</span><span>Tables /</span><b>Deals</b>
                <span className={v.refresh}><ArrowClockwise size={13} />Refresh</span>
                <span className={v.search}><MagnifyingGlass size={14} /></span>
                <span className={v.share}>Share</span>
            </div>

            <div className={v.page}>
                <p className={k.title}>Deals</p>
                <p className={k.muted}>10 rows loaded</p>
                <div className={v.tools}>
                    <span>Filter</span>
                    <span>Sort</span>
                    <span className={v.order}>Default order<CaretDown size={11} /></span>
                    <span>Board, grouped by stage.</span>
                </div>

                <div className={v.board}>
                    {PILES.map(({ stage, was, now, cards }) => (
                        <section key={stage} className={v.pile}>
                            <p className={v.pileHead}>
                                {stage}
                                <span className={v.count}><span className={v.was}>{was}</span><span className={v.now}>{now}</span></span>
                            </p>
                            <div className={v.cards}>
                                {cards.map(([company, value, move]) => (
                                    <div key={company} className={`${v.card} ${move ? v[move] : ""}`}>
                                        <span className={v.company}>{company}</span>
                                        <span className={v.value}>{value}</span>
                                    </div>
                                ))}
                            </div>
                            <span className={v.add}>＋ Add</span>
                        </section>
                    ))}
                </div>
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
                    <p className={v.mine}>Saltbox signed the order form.</p>
                    <div className={v.reply}>
                        <div className={v.replyHead}>
                            <img className={k.face} src={FACES.Remy} alt="" />
                            <span className={v.who}>Remy</span>
                            <time className={v.at}>14:06</time>
                        </div>
                        <span className={v.steps}>
                            <i className={v.state}><span className={v.pulse} /><Check className={v.tick} size={10} weight="bold" /></i>
                            <span className={v.what}><span className={v.working}>Working</span><span className={v.worked}>Worked for 6s · 2 steps</span></span>
                            <CaretRight className={v.chev} size={11} />
                        </span>
                        <p className={v.said}>Moved Saltbox to Won and ticked off the chase.</p>
                    </div>

                    <div className={v.quiet}>
                        <p className={v.quietHead}>Ask about this table</p>
                        <p className={v.quietText}>Questions about the rows, or changes to make.</p>
                    </div>
                </div>

                <div className={v.composer}>
                    <span className={v.words}>Ask Remy…</span>
                    <i className={v.attach}><Paperclip size={15} /></i>
                    <i className={v.wave}><Waveform size={16} /></i>
                    <i className={v.send}><ArrowUp size={14} /></i>
                </div>
            </section>
        </Vignette>
    );
}
