import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./memory-fix.module.css";

/* Remy's shared notes, row for row as About lists them. */
const NOTES: { topic: string; gloss: string; at: string; fresh: boolean }[] = [
    { topic: "Northstar", gloss: "Anita signs; Raj evaluates", at: "Wed", fresh: true },
    { topic: "Security documents", gloss: "Only the approved overview goes out", at: "Sat", fresh: true },
    { topic: "Follow ups", gloss: "Read colleagues’ threads before chasing", at: "Sep 19", fresh: false },
];
const JUMPS = ["People", "Channels", "Taught", "Remembers", "Standing work", "Hands work to"];

/** A note is a file you fix in the page editor: open Northstar from what
 *  Remy remembers, change the deadline, and the next answer that draws on the
 *  note says the new day.
 *
 *  Beats: 1 the pointer rests on Northstar, its path in the tooltip; 2 the
 *  note opens in the page editor; 3 "Friday" selected, the selection toolbar
 *  over it; 4 "Thursday" typed in its place, and nothing announces the save;
 *  5 Priya's conversation slides in with her question; 6 Remy's answer.
 *
 *  On a phone it shows the list and the note, where the fix lands, and stops
 *  short of Priya's conversation, which would only show as an edge. */
export function MemoryFix() {
    return (
        <Vignette className={v.root} beats={[1100, 2000, 1300, 1300, 1600, 1400]} hold={3400} phone={{ x: 10, width: 358 }}
            label="On Remy's About page, under what Remy remembers, someone opens the Northstar note. It opens in the page editor, and they change the procurement deadline from Friday to Thursday. Then Priya asks Remy when the Northstar deadline is, and Remy answers Thursday, for procurement.">
            <div className={k.bar}>
                <img className={k.face} src={FACES.Remy} alt="" />
                <span>Remy</span><span>/</span>
                <span className={v.crumbAbout}>About</span>
                <span className={v.crumbNote}>/</span><b className={v.crumbNote}>Northstar</b>
                <i /><i /><i />
            </div>
            <div className={k.body}>
                <div className={v.about}>
                    <div className={v.jumps}>
                        {JUMPS.map((jump) => <span key={jump} className={jump === "Remembers" ? v.jumpOn : undefined}>{jump}</span>)}
                    </div>
                    <div className={v.section}>
                        <div className={v.head}>
                            <p className={k.title}>What Remy remembers</p>
                            <p className={v.headNote}>Written down as it works. Open a note to read or fix it.</p>
                        </div>
                        <div className={v.tabs}>
                            <span className={v.tabOn}>Shared<em>3</em></span>
                            <span>Personal<em>1</em></span>
                        </div>
                        <p className={v.scope}>Everyone in Remy’s space can read these.</p>
                        <ul className={v.notes}>
                            {NOTES.map((note, index) => (
                                <li key={note.topic} className={index === 0 ? v.picked : undefined}>
                                    <span className={note.fresh ? `${v.dot} ${v.dotFresh}` : v.dot} />
                                    <span className={v.topic}>{note.topic}</span>
                                    <span className={v.gloss}>· {note.gloss}</span>
                                    <span className={v.at}>{note.at}</span>
                                </li>
                            ))}
                        </ul>
                    </div>
                    <span className={v.tip}>/memory/northstar.md</span>
                </div>

                <div className={v.doc}>
                    <p className={v.h1}>Northstar</p>
                    <ul className={v.bullets}>
                        <li>Anita Rao is the buyer and signs. Raj is the technical evaluator: send him the detail, send her the decision.</li>
                        <li>
                            Procurement deadline is{" "}
                            <span className={v.swap}>
                                <span className={v.old}>Friday</span>
                                <span className={v.typed}>Thursday</span>
                                <span className={`${k.caret} ${v.caret}`} />
                                <span className={v.seltool}>
                                    <span className={v.ask}>
                                        <svg width="13" height="13" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 3.5h10a1 1 0 0 1 1 1V10a1 1 0 0 1-1 1H7l-3 2.5V11H3a1 1 0 0 1-1-1V4.5a1 1 0 0 1 1-1z" fill="none" stroke="currentColor" strokeWidth="1.3" strokeLinejoin="round" /></svg>
                                        Ask for change
                                    </span>
                                    <i className={v.sep} />
                                    <span className={v.mark}>B</span>
                                    <span className={`${v.mark} ${v.italic}`}>I</span>
                                    <span className={`${v.mark} ${v.strike}`}>S</span>
                                </span>
                            </span>.
                        </li>
                    </ul>
                </div>

                <div className={v.chat}>
                    <div className={v.chatHead}>
                        <img className={k.face} src={FACES.Remy} alt="" />
                        <span className={v.chatTitle}><span>Remy</span><small>Priya’s conversation</small></span>
                    </div>
                    <div className={v.thread}>
                        <div className={v.asked}>
                            <span className={v.askedWho}>Priya</span>
                            <p className={v.askedText}>When’s the Northstar deadline?</p>
                        </div>
                        <div className={v.reply}>
                            <img className={k.face} src={FACES.Remy} alt="" />
                            <div>
                                <span className={v.replyWho}>Remy</span>
                                <span className={v.steps}>
                                    <i><svg width="9" height="9" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></i>
                                    1 step
                                </span>
                                <p className={v.replyText}>Thursday, for procurement.</p>
                            </div>
                        </div>
                    </div>
                    <div className={v.composer}>Ask Remy…</div>
                </div>

                <svg className={v.pointer} width="18" height="22" viewBox="0 0 18 22" aria-hidden="true">
                    <path d="M2 1.5v16.2l4.3-4.1 2.7 6.3 2.9-1.2-2.7-6.2h6z" fill="var(--ink)" stroke="var(--paper)" strokeWidth="1.4" strokeLinejoin="round" />
                </svg>
            </div>
        </Vignette>
    );
}
