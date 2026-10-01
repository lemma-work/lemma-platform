"use client";

import type { ReactNode } from "react";
import { FACES } from "@/marketing/product-pages";
import { CheckIcon, CodeIcon, TreeIcon, UserIcon } from "@/ui/icons";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./workflows-handoff.module.css";

/* June's imports table, as the sample space has it: a customer and the rows
   in their file. Harbor's first file is the row that starts the run. */
const IMPORTS: [string, string][] = [["Fern Works", "96"], ["Moss & Co", "312"], ["Birch", "58"]];
/* What the count passes through on its way to every row read. */
const COUNT = ["0", "412", "897", "1,366", "1,840"];

/** One step of the run, drawn as the run page draws it: who does it, what
 *  the step is called, and how it stands. Its state words sit on top of one
 *  another and the beats say which shows (`--s0`, `--s1`, `--s2`). */
function Step({ className, icon, label, words, out }: {
    className: string;
    icon: ReactNode;
    label: string;
    words: [string, string, string];
    out?: ReactNode;
}) {
    return (
        <li className={`${v.step} ${className}`}>
            <span className={v.head}>
                {icon}
                <span className={v.label}>{label}</span>
                <span className={v.state}>
                    <span className={v.glyph}><i /><CheckIcon size={10} weight="bold" /></span>
                    <span className={v.words}>
                        <em className={v.say0}>{words[0]}</em>
                        <em className={v.say1}>{words[1]}</em>
                        <em className={v.say2}>{words[2]}</em>
                    </span>
                </span>
            </span>
            {out && <span className={v.out}>{out}</span>}
        </li>
    );
}

const June = <img className={`${k.face} ${v.icon}`} src={FACES.June} alt="" />;

/** A workflow writing down the handoffs of a customer's first import: a new
 *  row in the imports table starts a run of First import check, June reads
 *  every row and prepares a fix, the run asks Dev and only Dev, and when Dev
 *  says go ahead, code runs the import.
 *
 *  Beats: 1 Harbor's row lands in the imports table; 2 it is carried to the
 *  top of the run, which starts; 3 June checks every row, counting to 1,840
 *  and finding 212 that won't import; 4 the decision takes the fixing arm and
 *  leaves the other not taken; 5 the corrected file, the original kept;
 *  6 Dev's step waits, and the run asks Dev in chat; 7 Dev answers, "Go
 *  ahead."; 8 the import runs and the run is Finished, with Dev's exchange
 *  still beside the step it answered.
 *
 *  On a phone it shows the run alone: Harbor's row slides in from the table
 *  off to the left, and the run's own pill and Dev's step say when Dev is
 *  asked and when he answers. */
export function WorkflowsHandoff() {
    return (
        <Vignette className={v.root} beats={[900, 900, 1200, 1600, 1300, 1100, 1500, 1200]} hold={3000} phone={{ x: 160, width: 316 }}
            label="June's First import check workflow, one run from start to finish. A row for Harbor's first file is added to the imports table and starts the run. June checks every row: 1,840 rows, 212 that won't import. The decision takes the fixing path, and the path straight to the import is marked not taken. June prepares a corrected file, harbor-fixed.csv, and keeps the original. The run then waits on Dev: a message asks, First import check needs your input, and Dev replies, Yes, import it. The import runs, and the run reads Finished, every step ticked, with Dev's exchange still beside the step it answered.">
            <div className={`${k.bar} ${v.bar}`}>{June}<b>First import check</b><span>· a run</span><i /><i /><i /></div>
            <div className={`${k.body} ${v.stage}`}>
                <div className={`${k.card} ${v.source}`}>
                    <p className={v.sourceHead}><span className={k.muted}>Table</span> imports</p>
                    <table className={k.table}>
                        <thead><tr><th>Customer</th><th>Rows</th></tr></thead>
                        <tbody>
                            {IMPORTS.map(([customer, rows]) => <tr key={customer}><td>{customer}</td><td>{rows}</td></tr>)}
                            <tr className={v.fresh}><td><span>Harbor</span></td><td><span>1,840</span></td></tr>
                        </tbody>
                    </table>
                </div>

                <section className={v.run}>
                    <div className={v.runHead}>
                        <span className={v.trigger}>When a row in imports is added</span>
                        <span className={v.pills}>
                            <span className={`${v.pill} ${v.pillRun}`}><i />Running</span>
                            <span className={`${v.pill} ${v.pillWait}`}><i />Waiting on a person</span>
                            <span className={`${v.pill} ${v.pillDone}`}><i />Completed</span>
                        </span>
                    </div>
                    <ol className={v.tree}>
                        <Step className={v.check} icon={June} label="Check every row" words={["Not yet", "Running", "Done"]}
                            out={<>
                                <span className={v.count}><span className={v.strip}>{COUNT.map((n) => <span key={n}>{n}</span>)}</span></span>
                                {" rows"}<span className={v.blocking}> · 212 won’t import</span>
                            </>} />
                        <Step className={v.decide} icon={<span className={v.icon}><TreeIcon size={14} /></span>} label="Anything that won’t import?" words={["Not yet", "", "Done"]} />
                        <li className={v.arms}>
                            <div className={`${v.arm} ${v.armFix}`}>
                                <p className={v.armLabel}><TreeIcon size={13} /><span>Prepare a corrected file</span><code>blocking &gt; 0</code></p>
                                <ol className={v.tree}>
                                    <Step className={v.fix} icon={June} label="Prepare a corrected file" words={["Not yet", "Running", "Done"]}
                                        out={<><span className={v.file}>harbor-fixed.csv</span> · original kept</>} />
                                    <Step className={v.ask} icon={<span className={`${v.icon} ${v.iconAsk}`}><UserIcon size={15} /></span>} label="Dev approves the fix" words={["Not yet", "Waiting", "Done"]} />
                                </ol>
                            </div>
                            <div className={`${v.arm} ${v.armStraight}`}>
                                <p className={v.armLabel}><TreeIcon size={13} /><span>Run the import</span><code>blocking == 0</code><em>not taken</em></p>
                            </div>
                        </li>
                        <Step className={v.go} icon={<span className={`${v.icon} ${v.iconCode}`}><CodeIcon size={15} /></span>} label="Run the import" words={["Not yet", "Running", "Done"]} />
                        <li className={`${v.step} ${v.end}`}>
                            <span className={v.glyph}><i /><CheckIcon size={10} weight="bold" /></span>
                            <span className={v.words}><em className={v.say0}>Finish</em><em className={v.say1} /><em className={v.say2}>Finished</em></span>
                        </li>
                    </ol>
                </section>

                <div className={`${k.bubble} ${v.asked}`}>
                    <span className={v.from}><img className={k.face} src={FACES.June} alt="" />June</span>
                    <span className={v.said}>First import check needs your input.</span>
                </div>
                <div className={`${k.bubble} ${v.answer}`}>
                    <span className={v.from}><span className={k.person}>D</span>Dev</span>
                    <span className={v.said}>Yes, import it.</span>
                </div>

                <span className={`${k.chip} ${v.carried}`}>Harbor · first file</span>
            </div>
        </Vignette>
    );
}
