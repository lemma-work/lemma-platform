"use client";

import { FACES } from "@/marketing/product-pages";
import { ChatIcon, CheckIcon, ChevronDownIcon, CloseIcon } from "@/ui/icons";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./pages-comment-handoff.module.css";

/** A comment handed to the teammate: on Scout's page, someone selects a
 *  sentence, chooses Comment and asks @Scout for quotes. The thread says
 *  where things stand in the product's own words while Scout works; the
 *  quotes arrive in the page, and Scout's reply in the thread.
 *
 *  Beats: 1 the sentence selected, the selection toolbar over it; 2 the
 *  pointer on Comment; 3 the draft thread in the panel, quoting the words;
 *  4 the ask typed into the box; 5 posted, the words marked, "will pick this
 *  up"; 6 "is working on it", with Watch; 7 the quotes, in the page under the
 *  point; 8 Scout's reply in the thread.
 *
 *  On a phone it shows the comments alone, where most of it happens: the
 *  words quoted, the ask, where it stands, and the reply naming the quotes
 *  it added to the page. */
export function PagesCommentHandoff() {
    return (
        <Vignette className={v.root} beats={[800, 1200, 900, 900, 2100, 1400, 1400, 1300]} hold={2400} height={440} phone={{ x: 368, width: 272 }}
            label="On Scout's page Why trials stall, someone selects the sentence Two of those stalled on the same step: mapping their export, chooses Comment, and writes: at Scout, quote the two who stalled on mapping. The comment says Scout will pick this up in a moment, then that Scout is working on it, with a Watch link. Two quotes appear in the page under that point, from Northstar's operations lead and Birch's founder, and Scout replies in the thread that it added them.">
            <div className={`${k.bar} ${v.head}`}>
                <img className={k.face} src={FACES.Scout} alt="" />
                <span>Scout</span><span>/</span><span>Pages</span><span>/</span><b>Why trials stall</b>
                <span className={v.pill}><ChatIcon size={14} /><span className={v.pillLabel}>Comment</span><span className={v.pillCount}>1</span></span>
                <span className={v.share}>Share</span>
            </div>

            {/* The page. */}
            <div className={v.doc}>
                <p className={v.h1}>Why trials stall</p>
                <p className={v.lead}>Five lost trials from September, in their own words.</p>
                <p className={v.h2}>What it might mean</p>
                <ul className={v.list}>
                    <li>
                        <b>Setup effort</b> comes up in three of five.{" "}
                        <span className={v.mark}><span className={v.sel}>Two of those stalled on the same step: mapping their export.</span></span>
                        <div className={v.quotes}>
                            <div>
                                <blockquote className={v.quote}>
                                    <span>“…mapping our export is where we stalled.”</span>
                                    <small>Northstar, operations lead</small>
                                </blockquote>
                                <blockquote className={v.quote}>
                                    <span>“We ran out of time mapping our export.”</span>
                                    <small>Birch, founder</small>
                                </blockquote>
                            </div>
                        </div>
                    </li>
                    <li><b>Budget</b> comes up once, from a team that finished the trial happily. That one is a counterexample, not a pattern.</li>
                    <li><b>Nobody owned it</b> at Willow. No feature fixes that.</li>
                </ul>

                {/* The toolbar pages show over a selection. */}
                <div className={v.seltool}>
                    <span className={v.tool}><ChatIcon size={13} />Ask for change</span>
                    <i className={v.sep} />
                    <span className={v.icon}>B</span>
                    <span className={`${v.icon} ${v.italic}`}>I</span>
                    <i className={v.sep} />
                    <span className={v.tool}>Bulleted list<ChevronDownIcon size={11} /></span>
                    <i className={v.sep} />
                    <span className={`${v.tool} ${v.choose}`}><ChatIcon size={13} weight="duotone" />Comment</span>
                </div>
            </div>

            {/* The comments, beside it. */}
            <aside className={v.panel}>
                <div className={v.panelHead}>
                    <span className={v.panelTitle}>Comments</span>
                    <span className={`${v.tab} ${v.tabOn}`}>Open<em className={v.tabCount}> 1</em></span>
                    <span className={v.tab}>Resolved</span>
                    <span className={v.close}><CloseIcon size={13} /></span>
                </div>
                <div className={v.comments}>
                    <p className={v.quiet}>No comments. Select some text and choose Comment — or @Scout there to ask for a change.</p>

                    <article className={v.thread}>
                        <blockquote className={v.threadQuote}>Two of those stalled on the same step: mapping their export.</blockquote>

                        {/* The box to write in, while it is a draft. */}
                        <div className={`${v.fold} ${v.draft}`}><div>
                            <div className={v.box}>
                                <span className={v.line}>
                                    <i className={`${k.caret} ${v.c0}`} />
                                    <span className={v.placeholder}>Comment, or @Scout to ask…</span>
                                    <span className={`${v.typed} ${v.t1}`}>@Scout quote the two who</span>
                                    <i className={`${k.caret} ${v.c1}`} />
                                </span>
                                <span className={v.line}>
                                    <span className={`${v.typed} ${v.t2}`}>stalled on mapping</span>
                                    <i className={`${k.caret} ${v.c2}`} />
                                </span>
                            </div>
                            <p className={v.note}>Scout will read this and answer here.</p>
                            <div className={v.boxActs}>
                                <span className={v.cancel}>Cancel</span>
                                <span className={v.send}>Comment</span>
                            </div>
                        </div></div>

                        {/* Posted. */}
                        <div className={`${v.fold} ${v.asked}`}><div>
                            <div className={v.msg}>
                                <span className={`${k.person} ${v.initials}`}>Y</span>
                                <div>
                                    <p className={v.who}><b>You</b><small>just now</small></p>
                                    <p className={v.text}><span className={v.at}>@Scout</span> quote the two who stalled on mapping</p>
                                </div>
                            </div>
                        </div></div>

                        {/* Where things stand, while Scout has it. */}
                        <div className={`${v.fold} ${v.waiting}`}><div>
                            <p className={v.status}>
                                <img className={`${k.face} ${v.statusFace}`} src={FACES.Scout} alt="" />
                                <span className={v.statusText}>
                                    <span className={`${v.said} ${v.soon}`}><span>Scout will pick this up in a moment…</span></span>
                                    <span className={`${v.said} ${v.working}`}><span>Scout is working on it…</span></span>
                                </span>
                                <span className={v.watch}>Watch</span>
                            </p>
                        </div></div>

                        {/* Scout's reply. */}
                        <div className={`${v.fold} ${v.reply}`}><div>
                            <div className={`${v.msg} ${v.replyMsg}`}>
                                <img className={`${k.face} ${v.replyFace}`} src={FACES.Scout} alt="" />
                                <div>
                                    <p className={v.who}><b>Scout</b><small>just now</small></p>
                                    <p className={v.text}>Added the two quotes under the setup point, from Northstar and Birch.</p>
                                </div>
                            </div>
                        </div></div>

                        <div className={`${v.fold} ${v.acts}`}><div>
                            <div className={v.actsRow}>
                                <span>Reply</span>
                                <span><CheckIcon size={12} />Resolve</span>
                            </div>
                        </div></div>
                    </article>
                </div>
            </aside>

            <svg className={v.pointer} width="18" height="22" viewBox="0 0 18 22" aria-hidden="true">
                <path d="M2 1.5v16.2l4.3-4.1 2.7 6.3 2.9-1.2-2.7-6.2h6z" fill="var(--ink)" stroke="var(--paper)" strokeWidth="1.4" strokeLinejoin="round" />
            </svg>
        </Vignette>
    );
}
