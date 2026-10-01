import type { CSSProperties, ReactNode } from "react";
import { ArrowClockwise, Check, Code, TreeStructure, UserCircle } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./workflows-steps.module.css";

/* The one route the run takes, in px under the bar: along the top row at the
   height of the glyphs, round the end of it, back along the gap between the
   rows and into the bottom row. The dot rides it (`offset-path`) and the
   faint line is the same path, drawn as far as the dot has gone, so the two
   can never disagree. Its length, and where each tile's stop falls on it,
   are in the module as `--at`. */
const ROUTE = "M4 70H617A12 12 0 0 1 629 82V174A12 12 0 0 1 617 186H72A12 12 0 0 0 60 198V218A12 12 0 0 0 72 230H454";
const LENGTH = 1727.4;

/** One kind of step, as a flat tile: the glyph the run page gives it, the
 *  words the product uses for it, and what this run left there. */
function Tile({ className, glyph, says, children }: { className: string; glyph: ReactNode; says: string; children?: ReactNode }) {
    return (
        <section className={`${v.tile} ${className}`}>
            {glyph}
            <p className={v.says}>{says}</p>
            {children}
        </section>
    );
}

/* The run page's state glyph: an empty ring, an amber one while it waits on
   somebody, a filled tick once it is done. */
const Ring = ({ className = "" }: { className?: string }) => <span className={`${v.ring} ${className}`}><Check size={11} weight="bold" /></span>;

/** Seven kinds of step, and one run's path through them. Every kind a
 *  workflow can be built from sits on the canvas as a muted tile, in the
 *  order this run visits them, and a dot carries the run from one to the
 *  next, lighting each as it goes.
 *
 *  Beats: 1 the dot enters June's step, which reports 212 rows that won't
 *  import; 2 the branch lights one arm and leaves the other not taken; 3 the
 *  dot circles the loop while its tag counts Pass 1, 2, 3; 4 it rests on the
 *  wait while the clock hand sweeps once; 5 it crosses to the person's step,
 *  which holds amber, waiting on Dev; 6 Dev answers and the ring turns to a
 *  tick; 7 it passes the code step, Done, and lands on the end: Finished,
 *  every tile lit and the route it took drawn faintly through them. */
export function WorkflowsSteps() {
    return (
        <Vignette className={v.root} beats={[1400, 1500, 1300, 2400, 1900, 2300, 900]} hold={3000}
            label="The seven kinds of step a workflow is built from, laid out as tiles in the order one run visits them: Hands it to June, Branches, Repeats for each item, Waits, Asks a person, Runs code, Ends the run. A dot carries the run through them. June's step reports 212 rows that won't import; the branch takes the path that fixes the dates and the other path is not taken; the loop runs three passes; the wait picks up again after two days; the person's step waits on Dev until Dev answers; the code step is done; and the run finishes, with every tile lit and the route it took drawn as one line through them.">
            <div className={k.bar}>
                <img className={k.face} src={FACES.June} alt="" /><b>June</b><span>· a run</span>
                <span className={v.status}>
                    <span className={v.statusDot} />
                    <span className={v.words}>
                        <span className={v.sayStart}>Starting</span>
                        <span className={v.sayRun}>Running</span>
                        <span className={v.sayTimer}>Waiting on a timer</span>
                        <span className={v.sayPerson}>Waiting on a person</span>
                        <span className={v.sayDone}>Completed</span>
                    </span>
                </span>
                <i /><i /><i />
            </div>

            <div className={v.stage}>
                <p className={v.heading}>Steps</p>
                <svg className={v.route} viewBox="0 0 640 364" aria-hidden="true"><path d={ROUTE} pathLength={LENGTH} /></svg>

                <Tile className={v.t1} says="Hands it to June" glyph={<span className={`${v.glyph} ${v.bot}`}><img src={FACES.June} alt="" /></span>}>
                    <p className={`${v.out} ${v.found}`}>212 won’t import</p>
                </Tile>
                <Tile className={v.t2} says="Branches" glyph={<span className={v.glyph}><TreeStructure size={15} /></span>}>
                    <div className={`${v.out} ${v.arms}`}>
                        <span className={v.taken}>Fix the dates</span>
                        <span className={v.other}>Import · not taken</span>
                    </div>
                </Tile>
                <Tile className={v.t3} says="Repeats for each item" glyph={<span className={v.glyph}><ArrowClockwise size={15} /></span>}>
                    <em className={`${v.out} ${v.pass}`}><span>Pass 1</span><span>Pass 2</span><span>Pass 3</span></em>
                </Tile>
                <Tile className={v.t4} says="Waits" glyph={
                    <span className={v.glyph}>
                        <svg className={v.clock} viewBox="0 0 256 256" width={15} height={15} aria-hidden="true">
                            <circle cx="128" cy="128" r="96" />
                            <line x1="128" y1="128" x2="184" y2="128" />
                            <line className={v.hand} x1="128" y1="128" x2="128" y2="80" />
                        </svg>
                    </span>}>
                    <p className={`${v.out} ${v.waited}`}>Waited 2 days</p>
                </Tile>
                <Tile className={v.t5} says="Asks a person" glyph={<span className={`${v.glyph} ${v.form}`}><UserCircle size={15} /></span>}>
                    <p className={`${v.out} ${v.state} ${v.asked}`}>
                        <Ring />
                        <span className={v.flip}><span className={v.askWait}>Waiting on Dev</span><span className={v.askDone}>Dev answered</span></span>
                    </p>
                </Tile>
                <Tile className={v.t6} says="Runs code" glyph={<span className={`${v.glyph} ${v.code}`}><Code size={15} /></span>}>
                    <p className={`${v.out} ${v.state} ${v.ran}`}><Ring className={v.ringDone} />Done</p>
                </Tile>
                <Tile className={v.t7} says="Ends the run" glyph={<span className={`${v.glyph} ${v.end}`}><Ring /></span>}>
                    <p className={`${v.out} ${v.finished}`}>Finished</p>
                </Tile>

                <span className={v.dot} style={{ offsetPath: `path("${ROUTE}")` } as CSSProperties}><i /></span>
            </div>
        </Vignette>
    );
}
