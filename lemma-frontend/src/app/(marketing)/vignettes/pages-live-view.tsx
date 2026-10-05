import { ArrowClockwise, Table } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./pages-live-view.module.css";

/* What the view "Themes still open" returns: the feedback_themes
   rows not yet Closed, most reports first, as the sample space answers it.
   Dates import as text is the one that closes. */
const LEAVING = "Dates import as text";
const OPEN: [string, number, string][] = [
    ["Large imports time out", 41, "No fix yet"],
    ["Notifications on mobile", 31, "Fix in progress"],
    ["Bulk edit missing", 26, "No fix yet"],
    [LEAVING, 22, "Merged"],
    ["Upload spinner never ends", 5, "No fix yet"],
];
/* The table itself, a few of its rows, Closed ones included. */
const TABLE: [string, string][] = [
    [LEAVING, "Merged"],
    ["Large imports time out", "No fix yet"],
    ["Bulk edit missing", "No fix yet"],
    ["Login link expired", "Closed"],
    ["Column mapping resets", "Closed"],
    ["Duplicate rows on re-import", "Closed"],
];

/** A table view in a page reads its rows when the page is opened or
 *  refreshed: Dates import as text is marked Closed in the table once #482
 *  is live, the report stays as it was until someone presses Refresh, and
 *  then the row has gone from the view without anyone editing the report.
 *
 *  Beats: 1 the feedback_themes table, sliding in beside the report; 2 the
 *  dates theme set to Closed in the table, the report unchanged; 3 the
 *  cursor carried to the view's Refresh and pressing it; 4 the dates theme
 *  folding out of the view, the count ticking from 5 rows to 4.
 *
 *  On a phone it shows the report alone, the view at its full width: the
 *  cursor comes in from the table to press Refresh, and the row leaves. */
export function PagesLiveView() {
    return (
        <Vignette className={v.root} phone={{ x: 4, width: 398 }} beats={[1500, 1200, 1900, 1400]} hold={2800}
            label="Kit's page, Feedback report, week 40, is open at Still open, where a table view called Themes still open lists 5 rows, most reports first: Large imports time out, Notifications on mobile, Bulk edit missing, Dates import as text, merged, and Upload spinner never ends. Beside it, in the feedback_themes table, Dates import as text is set to Closed, and the report does not change. Someone presses Refresh on the view: the dates theme drops out of it and the count goes to 4 rows. Nobody edited the report.">
            <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Feedback report, week 40</b><span>· a page</span><i /><i /><i /></div>
            <div className={k.body}>
                <div className={v.page}>
                    <p className={`${k.heading} ${v.heading}`}>Still open</p>
                    <div className={v.view}>
                        <div className={v.viewBar}>
                            <span className={v.kind}><Table size={13} />Themes still open</span>
                            <span className={v.count}><span className={v.before}>5 rows</span><span className={v.after}>4 rows</span></span>
                            <span className={`${v.act} ${v.refresh}`} title="Refresh"><ArrowClockwise size={13} /></span>
                            <span className={v.act}>Open table</span>
                            <span className={v.grip} aria-hidden="true">⋮⋮</span>
                        </div>
                        <div className={`${v.line} ${v.head}`}><span>Theme</span><span>Reports</span><span>Stage</span></div>
                        {OPEN.map(([theme, reports, stage]) => (
                            <div key={theme} className={`${v.line} ${theme === LEAVING ? v.leaving : ""}`}><span>{theme}</span><span>{reports}</span><span>{stage}</span></div>
                        ))}
                    </div>
                    <p className={k.heading}>Nobody owns these yet</p>
                    <p className={`${k.text} ${v.note}`}>Bulk edit, 26 reports. Two Enterprise renewals mention it (Sam).</p>
                </div>

                <div className={`${k.card} ${v.source}`}>
                    <p className={v.sourceHead}><span className={k.muted}>Table</span> feedback_themes</p>
                    <table className={k.table}>
                        <thead><tr><th>Theme</th><th>Stage</th></tr></thead>
                        <tbody>{TABLE.map(([theme, stage], index) => (
                            <tr key={theme} className={index === 0 ? v.changed : undefined}>
                                <td><span className={v.name}>{theme}</span></td>
                                <td>{index === 0
                                    ? <span className={`${v.pill} ${v.swap}`}><span className={v.was}>Merged</span><span className={v.now}>Closed</span></span>
                                    : <span className={v.pill}>{stage}</span>}</td>
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
