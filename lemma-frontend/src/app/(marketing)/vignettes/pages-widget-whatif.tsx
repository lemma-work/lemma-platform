import { ArrowUp, ChatCircle, CornersIn, FileText, Paperclip, X } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./pages-widget-whatif.module.css";

/* The deals in Evaluating, with the values written into the block when
   Remy built it. The class picks the row the cursor switches on. */
const DEALS: [string, string, string][] = [
    ["Cedar Health", "$18,500", v.cedar],
    ["Fern Works", "$9,900", v.fern],
    ["Oak & Ivy", "$7,200", ""],
];

/** A small tool in a page: the what-if Remy built into its pipeline page.
 *  Two switches add their deals to the month's total, and the tool's own
 *  button hands Remy a question, which waits in the message box unsent.
 *
 *  Beats: 1 the cursor switches on Cedar Health and the total counts up to
 *  $18,500; 2 it switches on Fern Works and the total counts on to $28,400;
 *  3 it presses the tool's button; 4 the conversation with Remy slides in
 *  beside the page, the question in its box, and the cursor rests by Send.
 *
 *  On a phone it shows the page and its tool: the switches, the total and
 *  the button pressed. The conversation opens past its right edge. */
export function PagesWidgetWhatif() {
    return (
        <Vignette className={v.root} phone={{ x: 4, width: 344 }} beats={[1200, 2700, 2600, 1300]} hold={3600}
            label="On Remy's page Pipeline this week, a small tool asks what happens if these close this month: the three deals in Evaluating, each with a switch, Cedar Health at $18,500, Fern Works at $9,900 and Oak & Ivy at $7,200, and a total of $0. Someone switches on Cedar Health and the total counts up to $18,500, then Fern Works, and it counts on to $28,400. They press the tool's own button, Ask Remy what's left on Cedar Health, and the conversation with Remy slides in beside the page with that question waiting in its message box. It is not sent.">
            <div className={`${k.bar} ${v.head}`}>
                <img className={k.face} src={FACES.Remy} alt="" />
                <span>Remy /</span><b>Pipeline this week</b>
                <span className={v.comment}><ChatCircle size={14} />Comment</span>
                <span className={v.share}>Share</span>
            </div>

            <div className={v.doc}>
                <p className={k.title}>Pipeline this week</p>
                <p className={`${k.text} ${v.lead}`}>Two deals can close by Friday. One is stuck on us.</p>
            </div>

            {/* The HTML block: the tool itself, drawn in the page. */}
            <section className={v.tool}>
                <p className={v.toolHead}>If these close this month</p>
                <p className={k.muted}>Evaluating · values as of 1 Oct</p>
                <div className={v.deals}>
                    {DEALS.map(([company, value, pick]) => (
                        <div key={company} className={`${v.deal} ${pick}`}>
                            <i className={v.switch} />
                            <span>{company}</span>
                            <em>{value}</em>
                        </div>
                    ))}
                </div>
                <div className={v.total}><span>Total</span><b className={v.sum}><i /><i /></b></div>
                <span className={v.ask}><ChatCircle size={14} />Ask Remy what’s left on Cedar Health</span>
            </section>

            <span className={v.pill}><img className={k.face} src={FACES.Remy} alt="" />Ask</span>

            {/* The page's conversation with Remy, as the floating chat draws it. */}
            <section className={v.chat}>
                <div className={v.chatHead}>
                    <img className={k.face} src={FACES.Remy} alt="" />
                    <span className={v.chatTitle}><span>Remy</span><small>On Pipeline this week</small></span>
                    <i className={v.min}><CornersIn size={15} /></i>
                </div>
                <div className={v.context}>
                    <span className={v.chip}><FileText size={14} /><span>Pipeline this week</span><em>Doc</em><X size={11} /></span>
                </div>
                <div className={v.quiet}>
                    <p className={v.quietHead}>Ask about this doc</p>
                    <p className={v.quietText}>Select a passage to quote it, or say what to change.</p>
                </div>
                <div className={v.composer}>
                    <i className={v.attach}><Paperclip size={16} /></i>
                    <p className={v.words}>What’s left before Cedar Health can close?<span className={k.caret} /></p>
                    <i className={v.send}><ArrowUp size={15} /></i>
                </div>
            </section>

            <svg className={v.cursor} width="18" height="24" viewBox="0 0 18 24" aria-hidden="true">
                <path d="M1.5 1.5v17.4l4.4-4.2 3 6.8 3.1-1.4-3-6.7h6.2z" />
            </svg>
        </Vignette>
    );
}
