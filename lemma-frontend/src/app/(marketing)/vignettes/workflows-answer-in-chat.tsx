import type { ReactNode } from "react";
import { ArrowUp, CaretDown, CaretLeft, CaretRight, Check, Code, UserCircle } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./workflows-answer-in-chat.module.css";

/** One row of the run's steps, as the run page draws it: who does the step,
 *  the step's name, how it stands and for how long. A row that changes in
 *  the vignette passes what it becomes (`now`, `nowTime`) and CSS swaps it in. */
function Step({ className, actor, who, what, state, word, time, now, nowTime, open }: {
    className?: string;
    actor: ReactNode;
    who: string;
    what: string;
    state: "done" | "waiting" | "pending";
    word: string;
    time?: string;
    now?: "done" | "running";
    nowTime?: string;
    open?: boolean;
}) {
    return (
        <li className={`${v.row} ${className ?? ""}`}>
            {actor}
            <span className={v.who}><b>{who}</b><small>{what}</small></span>
            <span className={v.state}>
                <span className={v.was}><Glyph state={state} />{word}</span>
                {now && <span className={v.now}><Glyph state={now} />{now === "done" ? "Done" : "Running"}</span>}
            </span>
            <span className={v.time}>
                <span className={v.was}>{time}</span>
                {nowTime && <span className={v.now}>{nowTime}</span>}
            </span>
            {/* A step that hasn't started has nothing to open, so the run
                page leaves its chevron out; it comes once the step starts. */}
            {open ? <CaretDown className={v.chev} size={12} />
                : <CaretRight className={`${v.chev} ${state === "pending" ? v.now : ""}`} size={12} />}
        </li>
    );
}

function Glyph({ state }: { state: "done" | "waiting" | "pending" | "running" }) {
    return <i className={v.glyph} data-state={state}>{state === "done" && <Check size={9} weight="bold" />}</i>;
}

/** A person's step, answered from the chat. June's First import check is
 *  waiting on Dev to approve the fix. The teammate sends him one message
 *  naming what the form asks for, with no buttons under it: he answers in a
 *  sentence, June fills the form in from his words and submits it, and the
 *  run moves on to the import.
 *
 *  Beats: 1 June's message arrives on Dev's phone; 2 his reply types into the
 *  message box; 3 he sends it; 4 on the run page the form fills from his
 *  words, the box from "Yes, import it" and the note from the rest, and is
 *  submitted; 5 June says it's done; 6 the step turns Done and Run the import
 *  starts.
 *
 *  On a phone it shows Dev's chat alone, which is the point: the question,
 *  his sentence, and June saying the import is running. */
export function WorkflowsAnswerInChat() {
    return (
        <Vignette className={v.root} beats={[1100, 1300, 1800, 900, 2500, 1300]} hold={3000} phone={{ x: 10, width: 240 }}
            label="On the left, Dev's chat with June. On the right, the run page of First import check, where the step Dev approves the fix has been waiting on a person for 4 hours, with Run the import not started below it. June messages Dev: First import check needs your input. It asks for: Import the corrected file, Note for June. There are no buttons; Dev types a reply and sends it: Yes, import it. Tell Harbor the dates are fixed. On the run page the form fills in from his words: Import the corrected file is ticked, Note for June reads Tell Harbor the dates are fixed, and it is submitted. June replies: Submitted for you. The import is running now. Dev approves the fix turns Done, and Run the import starts.">
            <div className={v.stage} />

            {/* Dev's phone: his conversation with the teammate, in plain chat. */}
            <section className={v.phone}>
                <header className={v.phoneHead}>
                    <CaretLeft size={14} />
                    <img className={`${k.face} ${v.phoneFace}`} src={FACES.June} alt="" />
                    <b>June</b>
                </header>
                <div className={v.feed}>
                    <p className={v.day}>Today</p>
                    <div className={`${v.line} ${v.in1}`}>
                        <div><div className={v.theirs}>
                            <p>First import check needs your input.</p>
                            <p>It asks for: Import the corrected file, Note for June.</p>
                        </div></div>
                    </div>
                    <div className={`${v.line} ${v.in3}`}>
                        <div><p className={v.mine}><mark className={v.m1}>Yes, import it.</mark> <mark className={v.m2}>Tell Harbor the dates are fixed.</mark></p></div>
                    </div>
                    <div className={`${v.line} ${v.in5}`}>
                        <div><p className={v.theirs}>Submitted for you. The import is running now.</p></div>
                    </div>
                </div>
                <div className={v.composer}>
                    <span className={v.placeholder}>Message</span>
                    <span className={v.typed}>
                        <span className={v.t1}>Yes, import it. Tell</span>
                        <span className={v.t2}>Harbor the dates are</span>
                        <span className={v.t3}>fixed.<span className={`${k.caret} ${v.caret}`} /></span>
                    </span>
                    <i className={v.send}><ArrowUp size={13} weight="bold" /></i>
                </div>
            </section>

            {/* The run, as its page shows it: where it stands, then its steps. */}
            <section className={v.run}>
                <header className={v.head}>
                    <p className={v.name}>First import check</p>
                    <span className={v.status}>
                        <span className={`${v.pill} ${v.pillWait}`}><i />Waiting on a person</span>
                        <span className={`${v.pill} ${v.pillGo}`}><i />Running</span>
                    </span>
                </header>
                <p className={v.meta}>
                    <span className={v.count}><span className={v.was}>3</span><span className={v.now}>4</span></span> of 5 steps · Started when a row changed
                </p>
                <p className={v.steps}>Steps</p>
                <ol className={v.tree}>
                    <Step actor={<img className={v.face} src={FACES.June} alt="" />} who="June" what="Prepare a corrected file" state="done" word="Done" time="57m" />
                    <Step className={v.dev} actor={<span className={`${v.actor} ${v.formActor}`}><UserCircle size={15} /></span>}
                        who="A person" what="Dev approves the fix" state="waiting" word="Waiting" time="4h so far" now="done" nowTime="4h" open />
                    {/* The step's form, open under it as the run page shows a
                        waiting form; June fills it in from Dev's words. */}
                    <li className={v.form}>
                        <div className={v.fill}>
                            <p className={v.tick}><i className={v.box} /><span>Import the corrected file<em> *</em></span></p>
                            <p className={v.label}>Note for June</p>
                            <p className={v.field}><span className={v.note}>Tell Harbor the dates are fixed.</span></p>
                            <span className={v.submitSlot}><span className={`${k.btnPrimary} ${v.submit}`}>Submit</span></span>
                        </div>
                    </li>
                    <Step className={v.import} actor={<span className={v.actor}><Code size={14} /></span>}
                        who="run-import" what="Run the import" state="pending" word="Not yet" now="running" nowTime="2s so far" />
                    <li className={v.end}><Glyph state="pending" />Finish</li>
                </ol>
            </section>
        </Vignette>
    );
}
