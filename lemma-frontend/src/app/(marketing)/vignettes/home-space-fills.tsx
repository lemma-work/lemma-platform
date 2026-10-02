import type { ReactNode } from "react";
import { ArrowUp, CaretRight, Check, FileText, FlowArrow, Folder, Paperclip, SquaresFour, Table } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./home-space-fills.module.css";

/* A line of the conversation, arriving on beat `beat`; a reply arrives a
   moment after the ask it answers. */
function Line({ beat, late, children }: { beat: number; late?: boolean; children: ReactNode }) {
    return <div className={`${v.line} ${v["in" + beat]} ${late ? v.late : ""}`}><div>{children}</div></div>;
}

/* Kit's turn: its face, its name, the run's steps row, what it said. */
function Reply({ steps, children }: { steps: ReactNode; children: ReactNode }) {
    return (
        <div className={v.reply}>
            <img className={`${k.face} ${v.face}`} src={FACES.Kit} alt="" />
            <div className={v.col}>
                <p className={v.who}>Kit</p>
                <div className={v.stepsRow}>{steps}</div>
                {children}
            </div>
        </div>
    );
}

/* The closed steps row of a finished run: "✓ 2 steps ›". */
function Done({ count, className }: { count: number; className?: string }) {
    return <span className={`${v.steps} ${className ?? ""}`}><i><Check size={10} weight="bold" /></i>{count} steps<CaretRight size={11} /></span>;
}

/* What a reply made, left in the chat as a link to where it now lives. */
function Made({ icon, name }: { icon: ReactNode; name: string }) {
    return <span className={v.link}>{icon}{name}</span>;
}

/* One of the space's places, and the slot its answer settles into. */
function Place({ icon, name, slot, className, children }: { icon: ReactNode; name: string; slot?: string; className?: string; children?: ReactNode }) {
    return (
        <>
            <span className={`${v.item} ${className ?? ""}`}>{icon}{name}</span>
            {slot && <div className={`${v.slot} ${slot}`}>{children}</div>}
        </>
    );
}

const LABEL = "In Kit's space, four separate asks in one chat, and each answer leaves a link in the chat while the thing it made settles into its place. Write up what users told us this week: Kit wrote the report, and the page, Feedback report, week 40, settles under Pages. Keep every report, grouped into themes: a table, Feedback themes with 9 rows, settles under Tables. When a fix ships, tell everyone who reported it, once Dev says it's live: a workflow, Close the loop, settles under Workflows, its fourth step marked with Dev's initials. Put the whole loop on one screen: Kit works through four steps and an app, Feedback loop, settles under Apps. The chat has moved on to its last ask; the four places stay filled, and the workflow is waiting on a person, for Dev to confirm the fix is live.";

/** You ask in a chat, the work stays in its space: four asks to Kit in one
 *  chat, and each answer lands in its own place in the sidebar while the
 *  chat moves on. Each reply leaves a link with the name of what it made,
 *  and the card lifts off that link.
 *
 *  Drawn in two shapes with the same beats: wide (640×400, the sidebar
 *  beside the chat) and, on a phone, tall (340×640, the sidebar over the
 *  chat), so its words stay readable at a phone's width. Only one shows.
 *
 *  Beats: 1 the report: a page card into Pages; 2 the themes: a table
 *  card into Tables; 3 closing the loop: a workflow card into Workflows,
 *  the first exchange gone off the top; 4 one screen: the steps row works,
 *  closes to four steps, and a dark app tile settles under Apps; 5 the
 *  hold: the workflow card says it is waiting on a person. */
export function HomeSpaceFills() {
    return (
        <>
            <Vignette className={`${v.root} ${v.wide}`} beats={BEATS} hold={2600} label={LABEL}><Scene /></Vignette>
            <Vignette className={`${v.root} ${v.tall}`} width={340} height={640} beats={BEATS} hold={2600} label={LABEL}><Scene /></Vignette>
        </>
    );
}

const BEATS = [700, 1500, 1500, 1500, 1700];

function Scene() {
    return (
        <>
            <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Kit</b><i /><i /><i /></div>
            <div className={v.ground} />

            {/* The conversation: beside the places, or under them on a phone. */}
            <section className={v.chat}>
                <div className={v.feed}>
                    <Line beat={1}><p className={v.you}>Write up what users told us this week.</p></Line>
                    <Line beat={1} late>
                        <Reply steps={<Done count={2} />}>
                            <p className={v.said}>Wrote the report.<Made icon={<FileText size={13} />} name="Feedback report, week 40" /></p>
                        </Reply>
                    </Line>
                    <Line beat={2}><p className={v.you}>Keep every report, grouped into themes.</p></Line>
                    <Line beat={2} late><Reply steps={<Done count={3} />}><p className={v.said}>Made a table.<Made icon={<Table size={13} />} name="Feedback themes" /></p></Reply></Line>
                    <Line beat={3}><p className={v.you}>When a fix ships, tell everyone who reported it, once Dev says it’s live.</p></Line>
                    <Line beat={3} late><Reply steps={<Done count={3} />}><p className={v.said}>Built a workflow.<Made icon={<FlowArrow size={13} />} name="Close the loop" /></p></Reply></Line>
                    <Line beat={4}><p className={v.you}>Put the whole loop on one screen.</p></Line>
                    <Line beat={4} late>
                        <Reply steps={<>
                            <span className={`${v.steps} ${v.live}`}><i><b /></i>Working<em>Deploying Feedback loop</em><CaretRight size={11} /></span>
                            <Done count={4} className={v.closed} />
                        </>}>
                            <p className={`${v.said} ${v.last}`}>Built an app.<Made icon={<SquaresFour size={13} />} name="Feedback loop" /></p>
                        </Reply>
                    </Line>
                </div>
                <div className={v.composer}><Paperclip size={15} /><span>Ask Kit…</span><i><ArrowUp size={14} /></i></div>
            </section>

            {/* The space's places, in the sidebar's order, each over the slot
                its answer settles into. */}
            <nav className={v.side}>
                <Place icon={<FileText size={15} />} name="Pages" className={v.pages} slot={v.pageSlot}>
                    <div className={`${v.card} ${v.pageCard}`}>
                        <p className={v.name}>Feedback report, week 40</p>
                        <p className={v.gloss}>Large imports are the biggest gap.</p>
                    </div>
                </Place>
                <Place icon={<SquaresFour size={15} />} name="Apps" className={v.apps} slot={v.appSlot}>
                    <div className={`${v.card} ${v.appCard}`}>
                        <p className={v.appHead}><span>Feedback loop</span><em>31 of 44</em></p>
                        {[64, 82, 56, 72].map((width, index) => (
                            <span key={index} className={v.appRow}><i /><b style={{ width }} /><em /></span>
                        ))}
                    </div>
                </Place>
                <Place icon={<Table size={15} />} name="Tables" className={v.tables} slot={v.tableSlot}>
                    <div className={`${v.card} ${v.tableCard}`}>
                        <p className={v.name}>Feedback themes<span className={v.gloss}> · 9 rows</span></p>
                        <p className={v.miniRow}><span>Large imports time out</span><span>Dev</span><em>No fix</em></p>
                        <p className={v.miniRow}><span>Login link expired</span><span>Dev</span><em className={v.ready}>Closed</em></p>
                    </div>
                </Place>
                <Place icon={<Folder size={15} />} name="Files" className={v.files} />
                <Place icon={<FlowArrow size={15} />} name="Workflows" className={v.workflows} slot={v.flowSlot}>
                    <div className={`${v.card} ${v.flowCard}`}>
                        <p className={v.name}>Close the loop</p>
                        <p className={v.spine}><i /><i /><i /><span className={v.pr}>DE</span><i /></p>
                        <p className={v.meta}>When a PR merges</p>
                        <p className={v.waiting}><b />Waiting on a person</p>
                    </div>
                </Place>
                <span className={v.chats}>Chats</span>
            </nav>
        </>
    );
}
