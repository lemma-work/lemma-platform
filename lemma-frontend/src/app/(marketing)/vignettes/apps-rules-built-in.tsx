import type { CSSProperties } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./apps-rules-built-in.module.css";

type Row = { name: string; format: string; owner: string; pr: string; notes?: string; glyph: string; thumb: string; status: string; next: string; blocked?: boolean };

/* Feedback loop's retry replies for this week's fixes, one row per theme,
   once Dev has confirmed #482 is live (marketing/kit/model.ts: the themes,
   their PRs and the replies for #482). */
const ROWS: Row[] = [
    { name: "Dates import as text", format: "22 people · 3 via Sam", owner: "Dev", pr: "#482", notes: "Merged", glyph: "▦", thumb: v.thumbDates, status: "Live", next: "Ready to send" },
    { name: "Notifications on mobile", format: "31 people · mostly in app", owner: "Dev", pr: "#490", notes: "In review", glyph: "▯", thumb: v.thumbMobile, status: "Not live", next: "Waits for #490 to merge", blocked: true },
    { name: "Large imports time out", format: "41 people · P0", owner: "Dev", pr: "—", notes: "LIN-251", glyph: "⇪", thumb: v.thumbImports, status: "No fix", next: "Nothing to promise yet", blocked: true },
    { name: "Wrong timezone in emails", format: "14 asked · 4 say it works", owner: "Alex", pr: "#478", notes: "Live Mon", glyph: "◷", thumb: v.thumbZones, status: "Sent", next: "Ask the rest Friday" },
];
const TYPED = "#490";

/** The rules are in the screen: Feedback loop knows a retry reply can only
 *  promise a fix that is live, so a reply that names the wrong PR holds
 *  itself without anyone remembering to check.
 *
 *  Beats: 1 the dates row is clicked and its Edit reply rises over the
 *  list, the row still in view; 2 the pointer double-clicks "#482" in the
 *  reply; 3 "#490" typed over it, the draft now unsaved; 4 the row's status
 *  flips to Not live and its next step to Waits for Dev on #490; 5 the
 *  list's one ready reply drains away: 0 of 4.
 *
 *  On a phone it shows the plan up to its Status column: the edit, the
 *  status that flips and the count all sit there, so only Next step is
 *  left out. */
export function AppsRulesBuiltIn() {
    return (
        <Vignette className={v.root} phone={{ x: 8, width: 438 }} beats={[1400, 1100, 1400, 1300, 1200]} hold={2800}
            label="In Feedback loop, the app Kit built for Acme's product team, this week's retry replies show the reply for Dates import as text ready to send, one of four, because Dev confirmed #482 is live. Someone opens that reply in Edit reply and changes #482 to #490, a fix still in review. The reply goes straight to Not live, its next step reads Waits for Dev on #490, and the list drops to none of four ready to send.">
            <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Feedback loop</b><span>· an app</span><i /><i /><i /></div>

            <div className={v.studio}>
                <nav className={v.views}>
                    <span className={v.viewOn}>Retry replies <em className={v.flip}><span className={v.one}>1/4</span><span className={v.zero}>0/4</span></em></span>
                    <span>Themes</span>
                </nav>

                <header className={v.head}>
                    <div>
                        <span className={v.kicker}>Retry replies</span>
                        <p className={v.title}>This week’s fixes</p>
                        <p className={v.sub}>Week 40 · <span className={v.flip}><span className={v.one}>1</span><span className={v.zero}>0</span></span> of 4 ready to send</p>
                    </div>
                </header>

                <div className={v.progress} aria-hidden="true"><span><i className={v.fill} /></span><span /><span /><span /></div>

                <div className={v.table}>
                    <div className={v.thead}><span>Theme</span><span>Owner</span><span>PR</span><span>Fix</span><span>Next step</span></div>
                    {ROWS.map((row, index) => (
                        <div key={row.name} className={index === 0 ? `${v.row} ${v.picked}` : v.row}>
                            <span className={v.asset}>
                                <span className={`${v.thumb} ${row.thumb}`}>{row.glyph}</span>
                                <span className={v.name}><b>{row.name}</b><small>{row.format}</small></span>
                            </span>
                            <span className={v.owner}><i>{row.owner[0]}</i>{row.owner}</span>
                            <span className={v.rev}>{row.pr}{row.notes && <small>{row.notes}</small>}</span>
                            {index === 0 ? (
                                <>
                                    <span className={v.flip}>
                                        <span className={`${v.status} ${v.statusOk} ${v.live}`}>Live</span>
                                        <span className={`${v.status} ${v.held}`}>Not live</span>
                                    </span>
                                    <span className={v.flip}>
                                        <span className={`${v.next} ${v.ready}`}>Ready to send</span>
                                        <span className={`${v.next} ${v.blocked} ${v.save}`}>Waits for Dev on #490</span>
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

                <p className={v.history}><span>08:41</span>Dev confirmed #482 is live.</p>

                {/* The dates reply's own screen, in Edit reply, cropped to its text. */}
                <div className={v.sheet}>
                    <div className={v.sheetHead}>
                        <b>Dates import as text</b>
                        <span className={v.modes}><span>Preview</span><span className={v.modeOn}>Edit reply</span><span>Who gets it</span></span>
                        <span className={`${v.flip} ${v.state}`}><span className={v.clean}>Saved</span><span className={v.dirty}>Unsaved changes</span></span>
                    </div>
                    <div className={v.editor}>
                        <span className={v.label}>Reply</span>
                        <div className={v.field}>
                            Hi {"{name}"}, your dates import as dates now.<br />
                            It’s fixed in{" "}
                            <span className={v.word}>
                                <span className={v.old}>#482</span>
                                {TYPED.split("").map((letter, index) => (
                                    <span key={index} className={v.typed} style={{ "--n": index } as CSSProperties}>{letter}</span>
                                ))}
                                <span className={`${k.caret} ${v.caret}`} />
                            </span>, live for everyone. Try again?
                        </div>
                        <div className={v.actions}>
                            <span className={v.saveBtn}>Save reply</span>
                            <span className={v.discardBtn}>Discard changes</span>
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
