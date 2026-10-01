import type { CSSProperties } from "react";
import { CaretDown, CaretRight, ChatCircle, Check, Code, CornersIn, CornersOut, LinkSimple } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./pages-ask-for-change.module.css";

const n = (value: number) => ({ ["--n" as string]: value }) as CSSProperties;
const TYPED = "one sentence";

/** Ask for change, on one point of a page: you select a passage, choose Ask
 *  for change in the toolbar over it, and say what's wrong with it. The
 *  teammate rewrites those words and nothing else, and says in the page's
 *  chat what it changed.
 *
 *  Beats: 1 the Budget point, selected with a drag; 2 the selection toolbar
 *  over it; 3 the pointer on Ask for change; 4 the toolbar turned into the
 *  field; 5 "one sentence" typed in; 6 Enter: the field goes and the page's
 *  chat opens, Scout working; 7 the two sentences fold into one, the points
 *  around it untouched; 8 Scout's reply, in a line.
 *
 *  On a phone it shows the page up to where the chat opens: the selection,
 *  Ask for change, "one sentence" and the point folding into one. The
 *  lines run on past the edge, and the chat stays out of the strip. */
export function PagesAskForChange() {
    return (
        <Vignette className={v.root} width={640} height={440} phone={{ x: 4, width: 314 }} beats={[900, 1000, 800, 900, 800, 1300, 1300, 1300]} hold={2200}
            label="In Scout's research memo Why trials stall, someone selects the Budget point under What it might mean, chooses Ask for change in the toolbar above it and types one sentence. Scout rewrites only that point, folding its two sentences into one, while the points above and below keep every word, and the page's chat shows Scout's reply: Made the Budget point one sentence and left the rest as it was.">
            <div className={k.bar}><img className={k.face} src={FACES.Scout} alt="" /><b>Why trials stall</b><span>· a page</span><i /><i /><i /></div>
            <div className={k.body}>
                <div className={v.page}>
                    <p className={v.h2}>What it might mean</p>
                    <ul className={v.list}>
                        <li><b>Setup effort</b> comes up in three of five. Two of those stalled on the same step: mapping their export.</li>
                        <li>
                            <span className={v.line}><span className={v.sel}><b>Budget</b> comes up once, from a team that finished the trial happily<span className={v.swap}><span className={v.stop}>.</span><span className={v.colon}>:</span></span></span></span>
                            <span className={v.line}><span className={`${v.sel} ${v.later}`}><span className={v.cut}>That one is </span>a counterexample, not a pattern.</span></span>
                        </li>
                        <li><b>Nobody owned it</b> at Willow. No feature fixes that.</li>
                    </ul>
                    <p className={v.h2}>What would change our mind</p>
                    <span className={k.skel} style={{ width: "86%" }} />
                    <span className={k.skel} style={{ width: "58%" }} />
                    <p className={v.h2}>What we&apos;ll try</p>
                    <span className={k.skel} style={{ width: "72%" }} />
                </div>

                {/* The selection toolbar, then the field it turns into. */}
                <div className={v.tool}>
                    <div className={v.tools}>
                        <span className={`${v.btn} ${v.askBtn}`}><ChatCircle size={15} /> Ask for change</span>
                        <i className={v.sep} />
                        <span className={v.icon}><b>B</b></span>
                        <span className={v.icon}><em>I</em></span>
                        <span className={v.icon}><s>S</s></span>
                        <span className={v.icon}><Code size={15} /></span>
                        <span className={v.icon}><LinkSimple size={15} /></span>
                        <i className={v.sep} />
                        <span className={v.btn}>Bulleted list <CaretDown size={12} /></span>
                        <i className={v.sep} />
                        <span className={v.btn}><ChatCircle size={15} weight="duotone" /> Comment</span>
                    </div>
                    <div className={v.form}>
                        <span className={v.field}>
                            <span className={v.placeholder}>Ask Scout to change this…</span>
                            <span className={v.typed}>{TYPED.split("").map((char, index) => <span key={index} style={n(index)}>{char}</span>)}</span>
                            <span className={`${k.caret} ${v.caret}`} />
                        </span>
                        <span className={v.send}>Ask</span>
                    </div>
                </div>

                <svg className={v.pointer} width="16" height="22" viewBox="0 0 16 22" aria-hidden="true">
                    <path d="M1.5 1.5v16.2l4.1-3.9 2.6 6.1 2.6-1.1-2.6-6h5.6z" />
                </svg>

                {/* The page's chat: the Ask pill, opened into the conversation. */}
                <span className={v.pill}><img className={v.pillFace} src={FACES.Scout} alt="" />Ask</span>
                <section className={v.chat}>
                    <header className={v.chatHead}>
                        <img className={k.face} src={FACES.Scout} alt="" />
                        <span className={v.chatTitle}><span>Scout</span><small>On Why trials stall</small></span>
                        <CornersOut size={16} />
                        <CornersIn size={16} />
                    </header>
                    <div className={v.reply}>
                        <img className={v.replyFace} src={FACES.Scout} alt="" />
                        <div>
                            <p className={v.who}>Scout</p>
                            <span className={v.steps}>
                                <span className={v.working}><i className={v.state}><i className={v.pulse} /></i>Working<CaretRight className={v.chev} size={12} /></span>
                                <span className={v.done}><i className={v.state}><Check size={11} /></i>1 step<CaretRight className={v.chev} size={12} /></span>
                            </span>
                            <p className={v.said}>Made the Budget point one sentence and left the rest as it was.</p>
                        </div>
                    </div>
                </section>
            </div>
        </Vignette>
    );
}
