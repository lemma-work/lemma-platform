import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./apps-june-normalize.module.css";

/* Harbor's customers-september.csv, the five-row working sample as
   Customer launchpad opens it (marketing/apps/import-model.ts). A date
   cell carries its ISO reading when Normalize dates can find one. */
const ROWS: { id: number; company: string; email: string; joined: string; iso?: string; n?: number }[] = [
    { id: 1, company: "Aster Studio", email: "hello@aster.example", joined: "14/09/2026", iso: "2026-09-14", n: 0 },
    { id: 2, company: "Moss & Co", email: "ops@moss.example", joined: "2026-09-15" },
    { id: 3, company: "Wren Supply", email: "team.wren.example", joined: "16/09/2026", iso: "2026-09-16", n: 1 },
    { id: 4, company: "Fern Works", email: "hello@fern.example", joined: "2026-09-17" },
    { id: 5, company: "Oak House", email: "ops@oak.example", joined: "18/09/2026", iso: "2026-09-18", n: 2 },
];

/** Customer launchpad, the import checker June built: Dev presses
 *  Normalize dates and the three day-first dates take their one right
 *  reading, while row 3's email stays flagged for a person, and the import
 *  stays shut until the sample validates.
 *
 *  Beats: 1 the pointer goes to Normalize dates and presses it; 2 the three
 *  dates turn ISO and lose their tint, the count drops to one issue and the
 *  app says what it did; 3 the pointer goes to Validate sample and presses
 *  it; 4 the app says to resolve the flagged cell first, the email still
 *  flagged, Import still grey, training still on hold.
 *
 *  On a phone it shows the email and date columns with both buttons, where
 *  the dates change and the email stays flagged; the app's lines under the
 *  grid are left out. */
export function AppsJuneNormalize() {
    return (
        <Vignette className={v.root} width={640} height={480} phone={{ x: 238, width: 382 }} beats={[1300, 1000, 2000, 1000]} hold={3000}
            label="In Customer launchpad, the app June built for Harbor's first import, four cells are flagged: three dates written day first and row 3's email, team.wren.example. Someone presses Normalize dates: 14/09/2026, 16/09/2026 and 18/09/2026 become 2026-09-14, 2026-09-16 and 2026-09-18, and the app says it converted the valid dates and to check the remaining email issue. They press Validate sample, and the app says to resolve the flagged cells before importing: row 3's email needs a complete address. Import 5 sample rows stays greyed, and training stays on hold until the first import succeeds.">
            <div className={k.bar}><img className={k.face} src={FACES.June} alt="" /><b>Customer launchpad</b><span>· an app</span><i /><i /><i /></div>
            <div className={`${k.body} ${v.app}`}>
                <header className={v.head}>
                    <div>
                        <p className={v.eyebrow}>Harbor / Customer operations</p>
                        <p className={v.title}>Validate customer records</p>
                        <p className={k.muted}>Import job HB-001 · 5 rows · 3 mapped fields</p>
                    </div>
                    <span className={v.state}>Needs data cleanup</span>
                </header>

                <section className={v.bench}>
                    <div className={v.tools}>
                        <span className={v.views}><span className={v.viewOn}>Working sample</span><span>Original file</span></span>
                        <span className={`${k.btn} ${v.normalize}`}>Normalize dates</span>
                    </div>
                    <table className={v.grid}>
                        <thead><tr><th>Row</th><th>Company name → Company</th><th>Contact email → Email</th><th>Joined date → Start date</th></tr></thead>
                        <tbody>{ROWS.map(row => (
                            <tr key={row.id}>
                                <td>{row.id}</td>
                                <td>{row.company}</td>
                                <td className={row.email.includes("@") ? undefined : v.flag}>{row.email}</td>
                                {row.iso
                                    ? <td className={`${v.flag} ${v.date}`} style={{ ["--n" as string]: row.n }}>
                                        <span className={v.swap}><span className={v.was}>{row.joined}</span><span className={v.now}>{row.iso}</span></span>
                                    </td>
                                    : <td>{row.joined}</td>}
                            </tr>
                        ))}</tbody>
                    </table>
                    <footer className={v.foot}>
                        <span className={v.count}><span className={v.four}>4 issues across 3 rows</span><span className={v.one}>1 issue across 1 row</span></span>
                        <span className={`${k.btn} ${v.validate}`}>Validate sample</span>
                        <span className={`${k.btnPrimary} ${v.import}`}>Import 5 sample rows</span>
                    </footer>
                </section>

                <div className={v.status}>
                    <p className={v.message}>
                        <span className={v.converted}>Converted valid DD/MM/YYYY dates to ISO. Check the remaining email issue.</span>
                        <span className={v.resolve}>Resolve the flagged cells before importing.</span>
                    </p>
                    <p className={v.issue}>Row 3 · email: Use a complete email address.</p>
                </div>

                <section className={v.training}>
                    <b>Next: team training</b>
                    <span>Training stays on hold until the first import succeeds.</span>
                </section>
            </div>

            <span className={v.cursor} aria-hidden="true">
                <svg className={v.press} width="16" height="22" viewBox="0 0 16 22"><path d="M1.5 1.5v15.2l4-3.9 2.7 6.4 2.6-1.1-2.7-6.2h5.6z" /></svg>
            </span>
        </Vignette>
    );
}
