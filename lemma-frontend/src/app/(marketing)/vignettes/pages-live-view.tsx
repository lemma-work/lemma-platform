import { ArrowClockwise, Table } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./pages-live-view.module.css";

/* What the view "Assets still open" returns: the launch_assets rows whose
   status is not Ready, soonest due first, as the sample space answers it. */
const OPEN: [string, string, string][] = [
    ["Launch post", "Priya", "In review"],
    ["Pricing page copy", "Aditi", "Drafting"],
    ["Customer quote, Northfield", "Kit", "Waiting on customer"],
    ["Press release", "Priya", "In review"],
    ["Social thread", "Kit", "Drafting"],
    ["Demo video, 90s", "Rohan", "Drafting"],
    ["Sales one-pager", "Aditi", "In review"],
    ["Webinar invite", "Kit", "Drafting"],
    ["App store screenshots", "Rohan", "Waiting on customer"],
];
/* The table itself, a few of its rows, Ready ones included. */
const TABLE: [string, string][] = [
    ["Launch post", "In review"],
    ["Pricing page copy", "Drafting"],
    ["Demo video, 90s", "Drafting"],
    ["Press release", "In review"],
    ["Changelog entry", "Ready"],
    ["Onboarding email", "Ready"],
];

/** A table view in a page reads its rows when the page is opened or
 *  refreshed: the launch post is marked Ready in the table, the brief stays
 *  as it was until someone presses Refresh, and then the row has gone from
 *  the view without anyone editing the brief.
 *
 *  Beats: 1 the launch_assets table, sliding in beside the brief; 2 the
 *  launch post set to Ready in the table, the brief unchanged; 3 the cursor
 *  carried to the view's Refresh and pressing it; 4 the launch post folding
 *  out of the view, the count ticking from 9 rows to 8.
 *
 *  On a phone it shows the brief alone, the view at its full width: the
 *  cursor comes in from the table to press Refresh, and the row leaves. */
export function PagesLiveView() {
    return (
        <Vignette className={v.root} phone={{ x: 4, width: 398 }} beats={[1500, 1200, 1900, 1400]} hold={2800}
            label="Kit's brief, Team plans launch, is open at What has to ship, where a table view called Assets still open lists 9 rows, the launch post from Priya in review at the top. Beside it, in the launch_assets table, the launch post is set to Ready, and the brief does not change. Someone presses Refresh on the view: the launch post drops out of it, the count goes to 8 rows, and the pricing page copy is now at the top. Nobody edited the brief.">
            <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Team plans launch</b><span>· a page</span><i /><i /><i /></div>
            <div className={k.body}>
                <div className={v.page}>
                    <p className={`${k.heading} ${v.heading}`}>What has to ship</p>
                    <div className={v.view}>
                        <div className={v.viewBar}>
                            <span className={v.kind}><Table size={13} />Assets still open</span>
                            <span className={v.count}><span className={v.nine}>9 rows</span><span className={v.eight}>8 rows</span></span>
                            <span className={`${v.act} ${v.refresh}`} title="Refresh"><ArrowClockwise size={13} /></span>
                            <span className={v.act}>Open table</span>
                            <span className={v.grip} aria-hidden="true">⋮⋮</span>
                        </div>
                        <div className={`${v.line} ${v.head}`}><span>Asset</span><span>Owner</span><span>Status</span></div>
                        {OPEN.map(([asset, owner, status], index) => (
                            <div key={asset} className={`${v.line} ${index === 0 ? v.leaving : ""}`}><span>{asset}</span><span>{owner}</span><span>{status}</span></div>
                        ))}
                    </div>
                </div>

                <div className={`${k.card} ${v.source}`}>
                    <p className={v.sourceHead}><span className={k.muted}>Table</span> launch_assets</p>
                    <table className={k.table}>
                        <thead><tr><th>Asset</th><th>Status</th></tr></thead>
                        <tbody>{TABLE.map(([asset, status], index) => (
                            <tr key={asset} className={index === 0 ? v.changed : undefined}>
                                <td>{asset}</td>
                                <td>{index === 0
                                    ? <span className={`${v.pill} ${v.swap}`}><span className={v.was}>In review</span><span className={v.now}>Ready</span></span>
                                    : <span className={v.pill}>{status}</span>}</td>
                            </tr>
                        ))}</tbody>
                    </table>
                </div>
            </div>

            <span className={v.cursor} aria-hidden="true">
                <svg className={v.press} width="16" height="22" viewBox="0 0 16 22"><path d="M1.5 1.5v15.2l4-3.9 2.7 6.4 2.6-1.1-2.7-6.2h5.6z" /></svg>
            </span>
        </Vignette>
    );
}
