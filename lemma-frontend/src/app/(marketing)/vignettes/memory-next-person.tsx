"use client";

import { FACES } from "@/marketing/product-pages";
import { CheckIcon, ChevronRightIcon, MemoryIcon, SendIcon } from "@/ui/icons";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./memory-next-person.module.css";

/** The next person: you correct Remy in the app on Monday, it writes a shared
 *  note, and on Wednesday Priya asks it in a team-chat channel and the answer
 *  already carries the correction. She never saw your conversation.
 *
 *  Beats: 1 the "noted this" line under its reply; 2 the note, rising out of
 *  that line to sit between the two places, under "Shared"; 3 Priya's question
 *  in the channel; 4 Remy's answer; 5 a line from the note into that answer.
 *
 *  On a phone it shows the note and Wednesday's channel, where the answer
 *  lands; Monday's conversation is left out, and the line runs off to it. */
export function MemoryNextPerson() {
    return (
        <Vignette className={v.root} beats={[1500, 1300, 2200, 1300, 1400]} hold={3600} height={380} phone={{ x: 252, width: 380 }}
            label="On Monday, in the app, you tell Remy that Raj isn’t the buyer at Northstar: Anita signs and Raj only evaluates. A line under its reply says Remy noted this, Northstar, and the note, Anita signs; Raj evaluates, settles among the shared notes. On Wednesday, in a team chat channel, Priya asks Remy who should get the Northstar contract, and Remy answers Anita Rao, she signs, keeping Raj on the technical detail. A line joins the note to that answer.">
            <div className={v.stage} />

            {/* Monday, in the app. */}
            <section className={`${v.pane} ${v.app}`}>
                <div className={`${k.bar} ${v.head}`}><img className={k.face} src={FACES.Remy} alt="" /><b>Remy</b><span className={`${k.chip} ${v.day}`}>Mon</span></div>
                <div className={v.thread}>
                    <p className={v.you}>Raj isn’t the buyer at Northstar. Anita signs; Raj only evaluates.</p>
                    <div className={v.reply}>
                        <img className={k.face} src={FACES.Remy} alt="" />
                        <div>
                            <p className={v.who}>Remy</p>
                            <span className={v.steps}><i><CheckIcon size={10} /></i>1 step<ChevronRightIcon size={11} /></span>
                            <p className={v.said}>Thanks. I’ll send Raj the technical detail and bring decisions to Anita.</p>
                            <p className={v.noted}><MemoryIcon size={13} aria-hidden="true" /><span>Remy noted this</span><span className={v.topic}>· Northstar</span></p>
                        </div>
                    </div>
                </div>
                <div className={v.composer}><span>Ask Remy…</span><i><SendIcon size={13} /></i></div>
            </section>

            {/* Wednesday, in a team-chat channel: drawn plainly, no one's chrome. */}
            <section className={`${v.pane} ${v.chat}`}>
                <div className={`${k.bar} ${v.head}`}><span className={v.hash}>#</span><b>sales</b><span className={`${k.chip} ${v.day}`}>Wed</span></div>
                <div className={v.feed}>
                    <div className={v.post}>
                        <span className={k.person}>D</span>
                        <div><p className={v.name}>Dev <time>9:41</time></p><p className={v.text}>Harbor signed this morning.</p></div>
                    </div>
                    <div className={`${v.post} ${v.ask}`}>
                        <span className={k.person}>P</span>
                        <div><p className={v.name}>Priya <time>10:14</time></p><p className={v.text}><span className={v.at}>@Remy</span> who should get the Northstar contract?</p></div>
                    </div>
                    <div className={`${v.post} ${v.answer}`}>
                        <img className={k.face} src={FACES.Remy} alt="" />
                        <div><p className={v.name}>Remy <time>10:14</time></p><p className={v.text}>Anita Rao. She signs. I’ll keep Raj on the technical detail.</p></div>
                    </div>
                </div>
                <div className={v.composer}><span /><i><SendIcon size={13} /></i></div>
            </section>

            {/* The note, kept where everyone in the space reads it. */}
            <p className={v.shared}>Shared</p>
            <div className={`${k.card} ${v.note}`}>
                <p className={v.noteTopic}><i />Northstar</p>
                <p className={v.noteText}>Anita signs;<br />Raj evaluates</p>
            </div>

            <svg className={v.link} width="640" height="380" viewBox="0 0 640 380" aria-hidden="true">
                <path className={v.trail} pathLength={1} d="M 240 273 C 280 273, 296 248, 296 184" />
                <path className={v.into} pathLength={1} d="M 344 184 C 344 222, 360 236, 395 236" />
                <circle cx="395" cy="236" r="3" />
            </svg>
        </Vignette>
    );
}
