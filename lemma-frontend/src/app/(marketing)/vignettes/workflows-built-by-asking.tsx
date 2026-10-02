import type { CSSProperties } from "react";
import { ArrowUp, CaretRight, Check, FlowArrow, Paperclip, Waveform } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./workflows-built-by-asking.module.css";

const n = (index: number) => ({ ["--n" as string]: index }) as CSSProperties;

/* What Priya says, the way she'd tell someone new. */
const SENTENCE = "When a customer sends their first file, check every row, fix what won’t import, and ask Dev before it runs.";

/* First import check, as its page lists the steps: the name each one was
   given, then what kind of step it is, in the app's own words. No step ids
   and no rule text, so the column reads as sentences. */
const STEPS: [string, string][] = [
    ["Check every row", "Hands it to June"],
    ["Anything that won’t import?", "Branches"],
    ["Prepare a corrected file", "Hands it to June"],
    ["Dev approves the fix", "Asks a person"],
    ["Run the import", "Runs run-import"],
];

/* The workflows June already runs, as the list shows them. */
const LIST: [string, string][] = [
    ["Onboarding digest", "Every Monday at 9: where each new customer stands, and who is waiting on whom."],
    ["Training reminder", "Two days before training, checks the import worked, then reminds the customer."],
];

/** A workflow from one sentence: the sentence waits in June's composer
 *  beside June's list of workflows; it sends, June works, and First import
 *  check's page opens and fills in, how it starts and then each step in order.
 *
 *  Beats: 1 sent, and in the thread; 2 June working; 3 the new page opens, with
 *  How it starts in one line; 4 How it runs, a row at a time; 5 June done, with a line to read
 *  it before turning it on.
 *
 *  On a phone it shows the right side, where the outcome lands: the list,
 *  then First import check opening over it and its steps writing themselves
 *  out. The sentence is quoted in the section's own text above it. */
export function WorkflowsBuiltByAsking() {
    return (
        <Vignette className={v.root} beats={[2000, 1000, 1300, 1100, 2700]} hold={3600} phone={{ x: 292, width: 340 }}
            label="On the left, a conversation with June. Priya's sentence waits in the composer: When a customer sends their first file, check every row, fix what won't import, and ask Dev before it runs. On the right, June's list of workflows, with two already in it. The sentence sends and June starts working. A new page, First import check, opens over the list, and How it starts fills with one line: A row added in imports. Then How it runs fills one step at a time: Check every row, hands it to June; Anything that won't import?, branches; Prepare a corrected file, hands it to June; Dev approves the fix, asks a person; Run the import, runs run-import. June finishes and says it made First import check, to read through before turning it on.">
            <div className={`${k.bar} ${v.bar}`}><img className={k.face} src={FACES.June} alt="" /><span>June /</span><b>Workflows</b><i /><i /><i /></div>

            {/* ── June's conversation ── */}
            <section className={v.chat}>
                <div className={v.quiet}>
                    <img className={v.mark} src={FACES.June} alt="" />
                    <p className={v.quietTitle}>What should June work on?</p>
                    <p className={v.quietBody}>Send a message to start a new conversation.</p>
                </div>

                <div className={v.feed}>
                    <div className={`${v.line} ${v.in1}`}><div>
                        <p className={v.you}>{SENTENCE}</p>
                    </div></div>

                    <div className={`${v.line} ${v.in2}`}><div>
                        <div className={v.reply}>
                            <img className={`${k.face} ${v.face}`} src={FACES.June} alt="" />
                            <div className={v.col}>
                                <p className={v.who}>June</p>
                                <span className={v.steps}>
                                    <span className={v.working}><i className={v.state}><i className={v.pulse} /></i>Working<em className={v.peek}>Writing the steps</em><CaretRight className={v.chev} size={11} /></span>
                                    <span className={v.done}><i className={v.state}><Check size={10} /></i>Worked for 52s<CaretRight className={v.chev} size={11} /></span>
                                </span>
                                <div className={`${v.line} ${v.in5}`}><div>
                                    <p className={v.said}>Made First import check. Read it through, then turn it on.</p>
                                </div></div>
                            </div>
                        </div>
                    </div></div>
                </div>

                <div className={v.composer}>
                    <Paperclip className={v.attach} size={16} />
                    <span className={v.field}>
                        <span className={v.placeholder}>Ask June…</span>
                        <span className={v.typed}>{SENTENCE}<span className={`${k.caret} ${v.caret}`} /></span>
                    </span>
                    <Waveform className={v.wave} size={17} />
                    <i className={v.send}><ArrowUp size={15} /></i>
                </div>
            </section>

            {/* ── The workflows list, before there is a new one in it ── */}
            <section className={v.list} aria-hidden="true">
                <p className={v.listTitle}>Workflows</p>
                <p className={v.listSub}>Steps that run in order, wait for people where they have to, and pick up again.</p>
                <ul className={v.rows}>
                    {LIST.map(([name, says]) => (
                        <li key={name} className={v.row}>
                            <span className={v.rowTile}><FlowArrow size={13} /></span>
                            <span className={v.rowText}><span className={v.rowName}>{name}</span><span className={v.rowSays}>{says}</span></span>
                        </li>
                    ))}
                </ul>
            </section>

            {/* ── First import check's page, once June has made it ── */}
            <section className={v.page}>
                <header className={v.head}>
                    <span className={v.tile}><FlowArrow size={18} /></span>
                    <p className={v.title}>First import check</p>
                    <span className={v.ask}>Ask to change it</span>
                </header>

                <div className={v.starts}>
                    <p className={v.h2}>How it starts</p>
                    <p className={v.what}>A row added in imports.</p>
                </div>

                <p className={`${v.h2} ${v.runs}`}>How it runs</p>
                <ol className={v.spine}>
                    {STEPS.map(([label, says], index) => (
                        <li key={label} className={v.step} style={n(index)}>
                            <span className={v.dot} />
                            <span className={v.say}><strong>{label}</strong><i>{says}</i></span>
                        </li>
                    ))}
                </ol>
            </section>
        </Vignette>
    );
}
