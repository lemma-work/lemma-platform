import type { CSSProperties, ReactNode } from "react";
import { ArrowUp, Check, Paperclip, SquaresFour } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./apps-outgrew-chat.module.css";

/* Harbor's working sample, row for row as Customer launchpad lists it.
   `flags` names the cells the import check flags, each with the index of the
   reply's item (in the order June wrote them) that lands there. */
type Field = "email" | "joined";
const ROWS: { id: number; company: string; email: string; joined: string; flags?: Partial<Record<Field, number>> }[] = [
    { id: 1, company: "Aster Studio", email: "hello@aster.example", joined: "14/09/2026", flags: { joined: 0 } },
    { id: 2, company: "Moss & Co", email: "ops@moss.example", joined: "2026-09-15" },
    { id: 3, company: "Wren Supply", email: "team.wren.example", joined: "16/09/2026", flags: { email: 3, joined: 1 } },
    { id: 4, company: "Fern Works", email: "hello@fern.example", joined: "2026-09-17" },
    { id: 5, company: "Oak House", email: "ops@oak.example", joined: "18/09/2026", flags: { joined: 2 } },
];
const STEPS: [string, boolean][] = [["Account access", true], ["Source received", true], ["Columns mapped", true], ["Sample validated", false], ["First import", false]];
const ITEMS = ["14/09/2026", "16/09/2026", "18/09/2026", "team.wren.example"];
/* Each item leaves from where June wrote it in the reply and lands on its
   cell in the grid: [left, top, to left, to top], in canvas px, measured
   from `data-src` and `data-dst` at ?beat=6: the start is the item's own
   top-left less the chip's padding; the end puts the chip, lifted 4px and
   scaled to the grid's 12px, over the cell's text, so it fades into it. */
const CARRY: [number, number, number, number][] = [[45, 48, 540, 230], [123, 48, 540, 280], [45, 67, 540, 330], [45, 87, 410, 280]];

const n = (index: number) => ({ ["--n" as string]: index }) as CSSProperties;

/* A line of the conversation. `beat` is the beat it arrives on; `leave` the
   beat it goes on. */
function Line({ beat, leave, children }: { beat: number; leave?: number; children: ReactNode }) {
    return <div className={`${v.line} ${v["in" + beat]} ${leave ? v["out" + leave] : ""}`}><div>{children}</div></div>;
}

function Reply({ children }: { children: ReactNode }) {
    return (
        <div className={v.reply}>
            <img className={`${k.face} ${v.face}`} src={FACES.June} alt="" />
            <div><div className={v.who}>June</div>{children}</div>
        </div>
    );
}

function Item({ index }: { index: number }) {
    return <span className={v.src} style={n(index)} data-src={index}>{ITEMS[index]}</span>;
}

/** An answer outgrows the chat: June says which of Harbor's rows won't
 *  import, the conversation moves on until that answer is half out of view,
 *  and asked for something Dev can work in, June hands back an app with the
 *  same four problems sitting in its grid.
 *
 *  Beats: 1 the question, sent; 2 June's answer; 3 two days later, another
 *  exchange pushes it up; 4 "Make this something Dev can work in", and the
 *  working line; 5 Customer launchpad opens beside the chat, three steps
 *  ticked over the file's rows; 6 the four items lift out of the old answer
 *  and settle in the grid as flagged cells; 7 the app's card in the chat.
 *
 *  No phone strip: the chat is centred for beats 1–4 and the app opens on
 *  the right for 5–7, with the items carried between them, so any strip
 *  narrow enough to read cuts one half of the story mid-sentence. */
export function AppsOutgrewChat() {
    return (
        <Vignette className={v.root} beats={[600, 800, 1400, 1100, 1000, 1000, 1700]} hold={2200}
            label="In a chat, someone asks June which of Harbor's rows won't import. June answers that three dates are written day first, 14/09/2026, 16/09/2026 and 18/09/2026, and that row 3's email, team.wren.example, has no @. Two days later another exchange pushes that answer up out of view. They ask June to make this something Dev can work in, and an app called Customer launchpad opens beside the chat: an import checklist with three steps done above Harbor's five rows, where the three dates and the email sit as flagged cells, 4 issues across 3 rows, with Import 5 sample rows greyed out. In the chat, June's reply is a card: Customer launchpad, app, Open.">
            <div className={k.bar}><img className={k.face} src={FACES.June} alt="" /><span>June /</span><b>Harbor’s first import</b><i /><i /><i /></div>
            <div className={v.ground} />

            {/* The app, opened beside the conversation as a tab of its own. */}
            <section className={v.app}>
                <div className={v.appHead}>
                    <span className={`${v.tab} ${v.tabOn}`}>Harbor<em>First import blocked</em></span>
                    <span className={v.tab}>Cedar</span>
                </div>
                <div className={v.appBody}>
                    <div className={v.titleRow}><div className={v.appTitle}>Validate customer records</div><span className={v.state}>Needs data cleanup</span></div>
                    <div className={v.meta}>Import job HB-001 · 5 rows · 3 mapped fields</div>
                    <ol className={v.steps}>
                        {STEPS.map(([name, done], index) => (
                            <li key={name} className={done ? v.done : undefined} style={n(index)}>
                                <i>{done ? <Check size={11} weight="bold" /> : index + 1}</i>{name}
                            </li>
                        ))}
                    </ol>
                    <div className={v.sheet}>
                        <table className={`${k.table} ${v.grid}`}>
                            <thead><tr><th>Row</th><th>Company</th><th>Email</th><th>Start date</th></tr></thead>
                            <tbody>{ROWS.map((row) => (
                                <tr key={row.id}>
                                    <td>{row.id}</td>
                                    <td>{row.company}</td>
                                    {(["email", "joined"] as const).map((field) => {
                                        const item = row.flags?.[field];
                                        return item === undefined
                                            ? <td key={field}>{row[field]}</td>
                                            : <td key={field} className={v.bad}><span style={n(item)} data-dst={item}>{row[field]}</span></td>;
                                    })}
                                </tr>
                            ))}</tbody>
                        </table>
                        <div className={v.foot}>
                            <span className={v.issues}>4 issues across 3 rows</span>
                            <span className={v.import}>Import 5 sample rows</span>
                        </div>
                    </div>
                </div>
            </section>

            {/* The conversation: centred until the app opens beside it. */}
            <section className={v.chat}>
                <div className={v.feed}>
                    <Line beat={1}><div className={v.day}>Tue, Sep 22</div><div className={v.you}>Which Harbor rows won’t import?</div></Line>
                    <Line beat={2}>
                        <Reply>
                            <div className={v.said}>Three dates are written day first: <Item index={0} />, <Item index={1} />, <Item index={2} />. Row 3’s email, <Item index={3} />, has no @.</div>
                        </Reply>
                    </Line>
                    <Line beat={3}>
                        <div className={v.day}>Thu, Sep 24</div>
                        <div className={v.you}>Can we book their training for the 25th?</div>
                        <Reply><div className={v.said}>Not until the first import passes.</div></Reply>
                    </Line>
                    <Line beat={4}><div className={v.you}>Make this something Dev can work in.</div></Line>
                    <Line beat={4} leave={7}><div className={v.working}><i />June is working…</div></Line>
                    <Line beat={7}>
                        <Reply>
                            <span className={v.resource}>
                                <SquaresFour size={18} />
                                <span className={v.resourceBody}><span className={v.resourceName}>Customer launchpad</span><span className={v.resourceType}>app</span></span>
                                <span className={v.resourceGo}>Open</span>
                            </span>
                        </Reply>
                    </Line>
                </div>
                <div className={v.composer}>
                    <Paperclip size={15} />
                    <span className={v.typed}>Which Harbor rows won’t import?<span className={k.caret} /></span>
                    <span className={v.placeholder}>Ask June…</span>
                    <i><ArrowUp size={14} /></i>
                </div>
            </section>

            {/* The four items, carried from the answer into the grid. */}
            {ITEMS.map((item, index) => (
                <span key={item} className={v.carry} style={{ ...n(index), left: CARRY[index][0], top: CARRY[index][1], ["--dx" as string]: CARRY[index][2] - CARRY[index][0] + "px", ["--dy" as string]: CARRY[index][3] - CARRY[index][1] + "px" }}>
                    <span className={v.lift}>{item}</span>
                </span>
            ))}
        </Vignette>
    );
}
