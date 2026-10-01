import type { ReactNode } from "react";
import { ArrowUp, CaretDown, CaretRight, Check, Code, TreeStructure, UserCircle } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-step-finds-person.module.css";

/** One row of the run's steps, as the run page draws it: who does the step,
 *  the step's name, how it stands and how long it took. A row that changes
 *  during the vignette passes `now` and `nowTime`, and CSS swaps them in. */
function Step({ className, actor, who, what, state, word, time, now, nowTime, open }: {
    className?: string;
    actor: ReactNode;
    who: string;
    what: string;
    state: "done" | "waiting" | "pending";
    word: string;
    time?: string;
    now?: string;
    nowTime?: string;
    open?: boolean;
}) {
    return (
        <li className={`${v.row} ${className ?? ""}`} data-state={state}>
            {actor}
            <span className={v.who}><b>{who}</b><small>{what}</small></span>
            <span className={v.state}>
                <span className={v.was}><i className={v.glyph}>{state === "done" && <Check size={9} weight="bold" />}</i>{word}</span>
                {now && <span className={v.now}><i className={v.glyph}><Check size={9} weight="bold" /></i>{now}</span>}
            </span>
            <span className={v.time}>
                <span className={v.was}>{time}</span>
                {nowTime && <span className={v.now}>{nowTime}</span>}
            </span>
            {open ? <CaretDown className={v.chev} size={12} /> : <CaretRight className={v.chev} size={12} />}
        </li>
    );
}

const face = <img className={v.face} src={FACES.June} alt="" />;

/** The step that's waiting on Dev goes to Dev's phone. June's First import
 *  check is stuck at "Dev approves the fix"; the teammate writes to him on
 *  WhatsApp, naming the workflow and what the form asks for; he answers in
 *  plain words, the form fills from them, and the run carries on.
 *
 *  Beats: 1 the workflow's name leaves the waiting step and lands on Dev's
 *  phone as the message; 2 his reply types into the message box; 3 he sends
 *  it; 4 the form fills from his words, the box from "Yes, import it" and the
 *  note from "tell Harbor the dates are fixed"; 5 the step turns Done and Run the
 *  import starts.
 *
 *  On a phone it shows the run alone, where the outcome lands: the name
 *  leaves the waiting step off to the right, and the form fills in. */
export function ChannelsStepFindsPerson() {
    return (
        <Vignette className={v.root} width={640} height={444} phone={{ x: 24, width: 382 }} beats={[1300, 1900, 1800, 1100, 1900]} hold={3200}
            label="June's First import check is waiting on a person at the step Dev approves the fix, with a form asking whether to import the corrected file and for a note for June. A message goes to Dev on WhatsApp: First import check needs your input. It asks for: Import the corrected file, Note for June. Dev replies: Yes, import it, tell Harbor the dates are fixed. In the run, the form fills from his words: the Import the corrected file box is ticked and the note for June reads Tell Harbor the dates are fixed. Dev approves the fix turns Done, and Run the import starts.">
            <div className={v.stage} />

            {/* The run, as its page shows it: where it stands, then its steps. */}
            <section className={`${k.card} ${v.run}`}>
                <header className={v.head}>
                    <p className={v.name}>First import check</p>
                    <span className={v.status}>
                        <i />
                        <span className={v.was}>Waiting on a person</span>
                        <span className={v.now}>Running</span>
                    </span>
                </header>
                <p className={v.meta}>
                    <span className={v.count}><span className={v.was}>3</span><span className={v.now}>4</span></span> of 5 steps · Started when a row changed
                </p>
                <p className={v.steps}>Steps</p>
                <ol className={v.tree}>
                    <Step actor={face} who="June" what="Check every row" state="done" word="Done" time="3m" />
                    <Step actor={<span className={v.actor}><TreeStructure size={13} /></span>} who="Decision" what="Anything that won’t import?" state="done" word="Done" />
                    <li className={v.arms}>
                        <div className={v.arm}>
                            <p className={v.armLabel}><TreeStructure size={12} />Prepare a corrected file</p>
                            <ol className={v.tree}>
                                <Step actor={face} who="June" what="Prepare a corrected file" state="done" word="Done" time="57m" />
                                <Step className={v.dev} actor={<span className={`${v.actor} ${v.formActor}`}><UserCircle size={14} /></span>}
                                    who="A person" what="Dev approves the fix" state="waiting" word="Waiting" time="4h so far" now="Done" nowTime="4h" open />
                            </ol>
                            <div className={v.form}>
                                <p className={v.tick}><i className={`${k.check} ${v.box}`} />Import the corrected file <em>*</em></p>
                                <p className={v.label}>Note for June</p>
                                <p className={v.field}><span className={v.note}>Tell Harbor the dates are fixed</span></p>
                                <span className={`${k.btnPrimary} ${v.submit}`}>Submit</span>
                            </div>
                        </div>
                    </li>
                    <Step className={v.import} actor={<span className={v.actor}><Code size={13} /></span>}
                        who="run-import" what="Run the import" state="pending" word="Not yet" now="Running" />
                </ol>
            </section>

            {/* Dev's phone: the chat he last used with the teammate. */}
            <div className={`${k.card} ${v.phone}`}>
                <p className={v.phoneHead}><img src="/connector-logos/whatsapp.svg" alt="" />WhatsApp<span className={`${k.person} ${v.owner}`}>D</span></p>
                <div className={v.incoming}>
                    <p>First import check needs your input.</p>
                    <p>It asks for: Import the corrected file, Note for June.</p>
                </div>
                <p className={v.outgoing}><mark className={v.m1}>Yes, import it.</mark> <mark className={v.m2}>Tell Harbor the dates are fixed.</mark></p>
                <div className={v.compose}>
                    <p className={v.input}>
                        <span className={v.placeholder}>Message</span>
                        <span className={v.typed}><span className={v.t1}>Yes, import it. Tell</span><span className={v.t2}>Harbor the dates are fixed.</span></span>
                    </p>
                    <span className={v.send}><ArrowUp size={14} weight="bold" /></span>
                </div>
            </div>

            {/* The workflow's name, carried from the waiting step to the phone. */}
            <span className={`${k.chip} ${v.carried}`}>First import check</span>
        </Vignette>
    );
}
