import type { ReactNode } from "react";
import { Check, Clock, Envelope, FlowArrow, FunnelSimple, Play, Rows } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./workflows-starts.module.css";

/** A workflow page's "How it starts" card, at a small scale: what starts it,
 *  the thing that just did, and at the bottom whose access the run has. */
function Start({ name, what, tone, children, runsAs }: { name?: string; what: string; tone: string; children: ReactNode; runsAs: string }) {
    return (
        <section className={`${v.card} ${tone}`}>
            <header className={v.head}>
                <h2>How it starts</h2>
                {name && <span><FlowArrow size={13} />{name}</span>}
            </header>
            <p className={v.what}>{what}</p>
            {children}
            <p className={v.runs}><i className={`${k.dot} ${v.dot}`} /><span>{runsAs}</span></p>
        </section>
    );
}

/** Four ways a run begins, and whose access each one has: June's workflows,
 *  each started by its own kind of thing, each running as one person.
 *
 *  Beats: 1 the Monday schedule fires on Onboarding digest; 2 a row for
 *  Harbor's first file lands on First import check; 3 a newsletter reaches
 *  the email start and stops at its condition, and nothing runs; 4 Harbor's
 *  question passes the condition with a tick, and that one runs; 5 someone
 *  presses Run now on Training reminder. Every card then shows a run, and
 *  under it whose access that run has.
 *
 *  On a phone it shows the left column, the schedule and the email start:
 *  three of the five beats, the condition among them. */
export function WorkflowsStarts() {
    return (
        <Vignette className={v.root} width={640} height={466} beats={[1300, 1500, 1500, 1800, 2400]} hold={3300} phone={{ x: 6, width: 317 }}
            label="Four How it starts cards from June's workflows, each ending with whose access its run has. Onboarding digest's Monday 09:00 schedule fires; it runs as whoever turned it on. A row for Harbor's first file is added in imports, and First import check runs as whoever owns that row. On Customer questions, started by a new email through your own connected account, a newsletter stops at the condition only emails from customers and nothing runs; an email from Harbor asking about training passes it with a tick, and it runs as the person whose account it came through. Someone presses Run now on Training reminder, and it runs as that person. At the end all four show a run.">
            <div className={k.bar}><img className={k.face} src={FACES.June} alt="" /><b>Workflows</b><span>· how each one starts</span><i /><i /><i /></div>
            <div className={`${k.body} ${v.grid}`}>
                <Start tone={v.digest} name="Onboarding digest" what="A schedule, on the times you choose." runsAs="Runs as whoever turned it on.">
                    <div className={v.slot}>
                        <span className={v.fired}><Clock size={14} />On Mondays at 09:00<em>· fired just now</em></span>
                    </div>
                </Start>

                <Start tone={v.imports} name="First import check" what="A row added in imports." runsAs="Runs as whoever owns the row that changed.">
                    <div className={v.slot}>
                        <span className={`${k.chip} ${v.carried} ${v.row}`}><Rows size={13} />Harbor · first file</span>
                    </div>
                </Start>

                <Start tone={v.email} name="Customer questions" what="A new email, through your own connected account." runsAs="Each person turns it on for themselves; it runs as them, with their own accounts.">
                    <div className={v.lane}>
                        <span className={`${k.chip} ${v.carried} ${v.refused}`}><Envelope size={13} />Newsletter</span>
                        <span className={v.passing}>
                            <span className={`${k.chip} ${v.carried} ${v.passed}`}><Envelope size={13} />Harbor: a question about training</span>
                        </span>
                    </div>
                    <p className={v.filter}><FunnelSimple size={13} />only emails from customers<Check className={v.tick} size={13} weight="bold" /></p>
                    <div className={v.slot} />
                </Start>

                <Start tone={v.manual} name="Training reminder" what="Someone presses Run now." runsAs="Runs when someone starts it, as that person.">
                    <div className={v.slot}>
                        <span className={`${k.btnPrimary} ${v.run}`}><Play size={12} weight="fill" />Run now</span>
                    </div>
                </Start>
            </div>

            <span className={v.cursor} aria-hidden="true">
                <svg className={v.press} width="16" height="22" viewBox="0 0 16 22"><path d="M1.5 1.5v15.2l4-3.9 2.7 6.4 2.6-1.1-2.7-6.2h5.6z" /></svg>
            </span>
        </Vignette>
    );
}
