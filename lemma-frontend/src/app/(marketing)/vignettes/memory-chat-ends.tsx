import { ArrowUp, Brain, CaretRight, Check, FileText, FlowArrow, Folder, Paperclip, SquaresFour, Table } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./memory-chat-ends.module.css";

const REPLY = "Thanks. I’ll send Raj the technical detail and bring decisions to Anita.".split(" ");

/** Where a lesson goes when the chat ends: you correct Remy in a chat, it
 *  writes the lesson into a note in its space, and the chat closes while the
 *  note stays.
 *
 *  Beats: 1 your message; 2 Remy's reply, typed in; 3 the grey "noted this"
 *  line under it; 4 the note lifting off that line; 5 the note carried into
 *  Files, where it settles as northstar.md with a fresh dot; 6 the chat greys
 *  and folds away; 7 the note opens where the chat was, still in the space.
 *
 *  On a phone it shows the chat and the note that opens in its place; the
 *  places down the side are left out, so the note leaves to the left. */
export function MemoryChatEnds() {
    return (
        <Vignette className={v.root} beats={[500, 1300, 2000, 1200, 1300, 1600, 1300]} hold={3600} phone={{ x: 164, width: 380 }}
            label="In a chat beside Remy's space, someone says Raj isn't the buyer at Northstar: Anita signs and Raj only evaluates. Remy thanks them, and a grey line under the reply says Remy noted this, Northstar. The note lifts out of the chat and settles in the space's Files as northstar.md. Then the chat closes, and the note is still there: opened from Files, it reads that Anita Rao is the buyer and signs and Raj is the technical evaluator.">
            <div className={k.bar}><img className={k.face} src={FACES.Remy} alt="" /><b>Remy’s space</b><i /><i /><i /></div>
            <div className={v.ground} />

            <nav className={v.side}>
                <span className={v.item}><FileText size={16} />Pages</span>
                <span className={v.item}><SquaresFour size={16} />Apps</span>
                <span className={v.item}><Table size={16} />Tables</span>
                <span className={`${v.item} ${v.files}`}><Folder size={16} />Files</span>
                <span className={v.note}><i className={v.fresh} />northstar.md</span>
                <span className={v.item}><FlowArrow size={16} />Workflows</span>
            </nav>

            <div className={v.outline} />
            <article className={v.doc}>
                <p className={v.path}>/memory/northstar.md</p>
                <h3 className={v.docTitle}>Northstar</h3>
                <ul className={v.docList}>
                    <li>Anita Rao is the buyer and signs. Raj is the technical evaluator: send him the detail, send her the decision.</li>
                    <li>Procurement deadline is Friday.</li>
                </ul>
            </article>
            <section className={v.panel}>
                <div className={v.thread}>
                    <p className={v.you}>Raj isn’t the buyer at Northstar. Anita signs; Raj only evaluates.</p>
                    <div className={v.reply}>
                        <img className={`${k.face} ${v.face}`} src={FACES.Remy} alt="" />
                        <div className={v.col}>
                            <p className={v.who}>Remy</p>
                            <span className={v.steps}><i><Check size={10} weight="bold" /></i>1 step<CaretRight size={11} /></span>
                            <p className={v.said}>{REPLY.map((word, index) => (
                                <span key={index} className={v.word} style={{ ["--n" as string]: index }}>{word} </span>
                            ))}</p>
                            <p className={v.noted}><Brain size={13} /><span>Remy noted this</span><span>·</span><em>Northstar</em></p>
                        </div>
                    </div>
                </div>
                <div className={v.composer}><Paperclip size={15} /><span>Ask Remy…</span><i><ArrowUp size={14} /></i></div>
            </section>

            <div className={`${k.card} ${v.card}`}><FileText size={15} /><span><b>Northstar</b><span className={v.gloss}>&nbsp;· Anita signs; Raj evaluates</span></span></div>
        </Vignette>
    );
}
