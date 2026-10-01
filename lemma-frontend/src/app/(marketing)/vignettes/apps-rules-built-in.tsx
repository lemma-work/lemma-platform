import type { CSSProperties } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./apps-rules-built-in.module.css";

type Row = { name: string; format: string; owner: string; notes?: string; glyph: string; thumb: string; status: string; next: string; blocked?: boolean };

/* Launch studio's release plan, row for row as the app draws it with the
   landing page approved (marketing/kit/model.ts: nextStep, the table cells). */
const ROWS: Row[] = [
    { name: "Landing page", format: "Web · Product launch", owner: "Priya", notes: "1 open note", glyph: "▤", thumb: v.thumbLanding, status: "Approved", next: "Ready to ship" },
    { name: "Customer announcement", format: "Email · Existing customers", owner: "Priya", glyph: "✉︎", thumb: v.thumbEmail, status: "Needs review", next: "Ready for your review" },
    { name: "Product walkthrough", format: "Storyboard · 3 frames", owner: "Dev", glyph: "▷", thumb: v.thumbFilm, status: "Needs review", next: "3 of 3 frames to update", blocked: true },
    { name: "Customer story", format: "Web · Customer proof", owner: "Priya", glyph: "¶", thumb: v.thumbStory, status: "Needs review", next: "Customer permission needed", blocked: true },
];
const TYPED = "faster";

/** The rules are in the screen: Launch studio knows that editing approved
 *  copy cancels the approval, so the plan undoes itself without anyone
 *  remembering to.
 *
 *  Beats: 1 the landing page row is clicked and its Edit copy rises over the
 *  plan, the row still in view; 2 the pointer double-clicks "better" in the
 *  headline; 3 "faster" typed over it, the draft now unsaved; 4 the row's
 *  status flips to Needs review and its next step to Save the edited draft;
 *  5 the plan's one approval drains away: 0 of 4.
 *
 *  On a phone it shows the plan up to its Status column: the edit, the
 *  status that flips and the count all sit there, so only Next step is
 *  left out. */
export function AppsRulesBuiltIn() {
    return (
        <Vignette className={v.root} phone={{ x: 8, width: 438 }} beats={[1400, 1100, 1400, 1300, 1200]} hold={2800}
            label="In Launch studio, the app Kit built for Acme's launches, the release plan shows the landing page approved, one of four assets. Someone opens the landing page in Edit copy and changes one word of its headline. The landing page goes straight back to Needs review, its next step reads Save the edited draft, and the plan drops to none of four approved.">
            <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Launch studio</b><span>· an app</span><i /><i /><i /></div>

            <div className={v.studio}>
                <nav className={v.views}>
                    <span className={v.viewOn}>Release plan <em className={v.flip}><span className={v.one}>1/4</span><span className={v.zero}>0/4</span></em></span>
                    <span>Assets</span>
                </nav>

                <header className={v.head}>
                    <div>
                        <span className={v.kicker}>Release plan</span>
                        <p className={v.title}>Import flow launch</p>
                        <p className={v.sub}>Target Thu, Oct 8 · <span className={v.flip}><span className={v.one}>1</span><span className={v.zero}>0</span></span> of 4 approved</p>
                    </div>
                </header>

                <div className={v.progress} aria-hidden="true"><span><i className={v.fill} /></span><span /><span /><span /></div>

                <div className={v.table}>
                    <div className={v.thead}><span>Asset</span><span>Owner</span><span>Revision</span><span>Status</span><span>Next step</span></div>
                    {ROWS.map((row, index) => (
                        <div key={row.name} className={index === 0 ? `${v.row} ${v.picked}` : v.row}>
                            <span className={v.asset}>
                                <span className={`${v.thumb} ${row.thumb}`}>{row.glyph}</span>
                                <span className={v.name}><b>{row.name}</b><small>{row.format}</small></span>
                            </span>
                            <span className={v.owner}><i>{row.owner[0]}</i>{row.owner}</span>
                            <span className={v.rev}>v3{row.notes && <small>{row.notes}</small>}</span>
                            {index === 0 ? (
                                <>
                                    <span className={v.flip}>
                                        <span className={`${v.status} ${v.statusOk} ${v.approved}`}>Approved</span>
                                        <span className={`${v.status} ${v.review}`}>Needs review</span>
                                    </span>
                                    <span className={v.flip}>
                                        <span className={`${v.next} ${v.ready}`}>Ready to ship</span>
                                        <span className={`${v.next} ${v.blocked} ${v.save}`}>Save the edited draft</span>
                                    </span>
                                </>
                            ) : (
                                <>
                                    <span><span className={v.status}>{row.status}</span></span>
                                    <span className={row.blocked ? `${v.next} ${v.blocked}` : v.next}>{row.next}</span>
                                </>
                            )}
                        </div>
                    ))}
                </div>

                <p className={v.history}><span>02</span>You approved Landing page v3.</p>

                {/* The landing page's own screen, in Edit copy, cropped to its headline. */}
                <div className={v.sheet}>
                    <div className={v.sheetHead}>
                        <b>Landing page</b>
                        <span className={v.modes}><span>Preview</span><span className={v.modeOn}>Edit copy</span><span>Compare</span></span>
                        <span className={`${v.flip} ${v.state}`}><span className={v.clean}>Saved · v3</span><span className={v.dirty}>Unsaved changes</span></span>
                    </div>
                    <div className={v.editor}>
                        <span className={v.label}>Headline</span>
                        <div className={v.field}>
                            Your first import.<br />
                            The start of{" "}
                            <span className={v.word}>
                                <span className={v.old}>better</span>
                                {TYPED.split("").map((letter, index) => (
                                    <span key={index} className={v.typed} style={{ "--n": index } as CSSProperties}>{letter}</span>
                                ))}
                                <span className={`${k.caret} ${v.caret}`} />
                            </span>{" "}work.
                        </div>
                        <div className={v.actions}>
                            <span className={v.saveBtn}>Save new revision</span>
                            <span className={v.discardBtn}>Discard unsaved copy</span>
                        </div>
                    </div>
                </div>
            </div>

            <svg className={v.pointer} width="18" height="22" viewBox="0 0 18 22" aria-hidden="true">
                <path d="M2 1.5v16.2l4.3-4.1 2.7 6.3 2.9-1.2-2.7-6.2h6z" fill="var(--paper)" stroke="var(--ink)" strokeWidth="1.4" strokeLinejoin="round" />
            </svg>
        </Vignette>
    );
}
