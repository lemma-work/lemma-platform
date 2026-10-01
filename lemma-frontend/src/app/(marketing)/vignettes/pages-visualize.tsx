import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./pages-visualize.module.css";

const ROWS: [string, string][] = [
    ["Northstar", "Setup effort"],
    ["Birch", "Setup effort"],
    ["Elm", "Setup effort"],
    ["Pine", "Budget"],
    ["Willow", "No owner"],
];
const BARS: [string, number][] = [["Setup effort", 3], ["Budget", 1], ["No owner", 1]];
/* Each theme leaves from its first row's theme cell and lands in the page,
   under the working line: [from top, to top], in canvas px. */
const CARRY: [number, number][] = [[69, 146], [162, 176], [193, 206]];

/** Visualize, in a page: you type /, pick Visualize, and the teammate pulls
 *  the rows it needs out of a table and puts a chart where you were typing.
 *
 *  Beats: 1 the slash menu; 2 the teammate's working line; 3 the table it
 *  reads, rising beside the page; 4 the rows it counts, lit; 5 their themes
 *  carried across into the page; 6 the chart, drawn in place of the working
 *  line; 7 the table goes back, leaving the page with its chart.
 *
 *  On a phone it shows the page alone: the themes come in from the table
 *  past its right edge, and the chart is drawn where they land. */
export function PagesVisualize() {
    return (
        <Vignette className={v.root} phone={{ x: 4, width: 356 }} beats={[1100, 1300, 1400, 1100, 1300, 1100, 900]} hold={3200}
            label="In a page, someone types slash and picks Visualize. The teammate opens the interviews table, counts the themes, and draws a bar chart into the page where the cursor was.">
            <div className={k.bar}><img className={k.face} src={FACES.Scout} alt="" /><b>Why trials stall</b><span>· a page</span><i /><i /><i /></div>
            <div className={k.body}>
                <div className={v.page}>
                    <p className={k.title}>Why trials stall</p>
                    <p className={k.text}>Five lost trials from September, in their own words.</p>
                    <p className={k.heading}>What people said</p>

                    <div className={v.slot}>
                        <span className={v.typing}>/<span className={k.caret} /></span>
                        <div className={`${k.menu} ${v.menu}`}>
                            <small>Ask</small>
                            <span>Ask Scout to write</span>
                            <span className={k.itemOn}>Visualize <em className={k.muted}>a chart, drawn live</em></span>
                            <small>Create</small>
                            <span>Table view</span>
                        </div>
                        <span className={`${k.marker} ${v.marker}`}><img className={k.face} src={FACES.Scout} alt="" />Scout is writing: a chart of the themes</span>
                        <figure className={`${k.card} ${v.chart}`}>
                            <figcaption><b>Setup effort comes up in three of five.</b><span className={k.muted}>Interviews by theme · from the interviews table</span></figcaption>
                            {BARS.map(([theme, count]) => (
                                <div key={theme} className={v.barRow}>
                                    <span>{theme}</span>
                                    <i style={{ width: count * 62 }} />
                                    <em>{count}</em>
                                </div>
                            ))}
                        </figure>
                    </div>
                    <span className={k.skel} style={{ width: "88%" }} />
                    <span className={k.skel} style={{ width: "64%" }} />
                </div>

                <div className={`${k.card} ${v.source}`}>
                    <p className={v.sourceHead}><span className={k.muted}>Table</span> interviews</p>
                    <table className={k.table}>
                        <thead><tr><th>Company</th><th>Theme</th></tr></thead>
                        <tbody>{ROWS.map(([company, theme], index) => (
                            <tr key={company} className={v.row} style={{ ["--n" as string]: index }}><td>{company}</td><td>{theme}</td></tr>
                        ))}</tbody>
                    </table>
                </div>

                {BARS.map(([theme], index) => (
                    <span key={theme} className={`${k.chip} ${v.carried}`}
                        style={{ ["--n" as string]: index, top: CARRY[index][0], ["--dy" as string]: CARRY[index][1] - CARRY[index][0] + "px" }}>{theme}</span>
                ))}
            </div>
        </Vignette>
    );
}
