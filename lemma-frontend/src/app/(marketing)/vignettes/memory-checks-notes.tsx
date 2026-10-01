import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./memory-checks-notes.module.css";

/** One line of an index: a topic and the gloss that says what is in it. */
interface Line { topic: string; gloss: string; lit?: number; note?: { file: string; text: string } }

/* Remy's four indexes, narrowest first: the order they are read in. The
   lines are its sample notes, word for word; the two lit ones are what this
   question turns on. */
const INDEXES: { name: string; top: number; lines: Line[] }[] = [
    { name: "You, with Remy", top: 44, lines: [{ topic: "Your preferences", gloss: "Short drafts; you send them", lit: 0 }] },
    { name: "You", top: 114, lines: [] },
    { name: "Remy’s own work", top: 156, lines: [] },
    {
        name: "Everyone in Remy’s space", top: 198, lines: [
            {
                topic: "Northstar", gloss: "Anita signs; Raj evaluates", lit: 1,
                note: { file: "northstar.md", text: "Anita Rao is the buyer and signs. Raj is the technical evaluator: send him the detail, send her the decision." },
            },
            { topic: "Security documents", gloss: "Only the approved overview goes out" },
        ],
    },
];
const REPLY = "Anita Rao. She signs. Want a three-line draft to send her?".split(" ");

const Check = () => <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M2 5.2 4.1 7.2 8 3" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg>;
const Chevron = () => <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true"><path d="M3.8 2.2 6.6 5 3.8 7.8" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>;
const Send = () => <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 10V2.4M2.6 5.6 6 2.2l3.4 3.4" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>;

/** Before it replies, it checks what it has written down: you ask Remy who
 *  should get the Northstar contract, and before answering it reads its four
 *  indexes, narrowest first, finds the two lines that bear on the question,
 *  opens the note behind one of them, and answers from both.
 *
 *  Beats: 1 your question, sent; 2 the four indexes, fanned out in the order
 *  they are read; 3 the two lines that matter, lit; 4 the Northstar line
 *  opened into its note; 5 the reply, short as you like it, naming the
 *  person who signs.
 *
 *  On a phone it shows the conversation, where the answer lands, and the
 *  left of the indexes: enough to see them fan out, light and open. */
export function MemoryChecksNotes() {
    return (
        <Vignette className={v.root} beats={[500, 1600, 1600, 1300, 1700]} hold={3800} height={420} phone={{ x: 0, width: 380 }}
            label="You ask Remy who should get the Northstar contract. Before it replies, it reads its four indexes of notes narrowest first: yours with Remy, yours, its own work, and everyone in its space. Two lines matter, your preference for short drafts and the Northstar line, and it opens the Northstar note: Anita Rao is the buyer and signs, Raj is the technical evaluator. It answers: Anita Rao. She signs. Want a three-line draft to send her?">
            <div className={k.bar}><img className={k.face} src={FACES.Remy} alt="" /><b>Remy</b><span>· a conversation</span><i /><i /><i /></div>
            <div className={`${k.body} ${v.stage}`}>
                <div className={v.chat}>
                    <p className={v.ask}>Who should get the Northstar contract?</p>
                    <div className={v.reply}>
                        <p className={v.who}><img className={k.face} src={FACES.Remy} alt="" />Remy</p>
                        <span className={v.steps}>
                            <span className={v.working}><i className={v.state}><i className={v.pulse} /></i>Working</span>
                            <span className={v.done}><i className={v.state}><Check /></i>1 step<Chevron /></span>
                        </span>
                        <p className={v.said}>{REPLY.map((word, index) => (
                            <span key={index} style={{ ["--n" as string]: index }}>{word} </span>
                        ))}</p>
                    </div>
                    <div className={v.composer}><span>Ask Remy…</span><i><Send /></i></div>
                </div>

                <div className={v.tray}>
                    <p className={v.trayHead}>Read before every reply</p>
                    {INDEXES.map((index, n) => (
                        <div key={index.name} className={`${k.card} ${v.index}`}
                            style={{ top: index.top, zIndex: INDEXES.length - n, ["--n" as string]: n, ["--rise" as string]: index.top - INDEXES[0].top + "px" }}>
                            <p className={v.indexHead}><em>{n + 1}</em>{index.name}{index.lines.length === 0 && <span className={k.muted}>Nothing yet</span>}</p>
                            {index.lines.map((line) => (
                                <div key={line.topic}>
                                    <p className={line.lit === undefined ? v.line : `${v.line} ${v.lit}`} style={{ ["--n" as string]: line.lit ?? 0 }}>
                                        <span>{line.topic}</span><span className={v.gloss}>· {line.gloss}</span>
                                    </p>
                                    {line.note && (
                                        <div className={v.noteWrap}>
                                            <div className={v.note}>
                                                <span className={v.file}>{line.note.file}</span>
                                                <span>{line.note.text}</span>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    ))}
                </div>
            </div>
        </Vignette>
    );
}
