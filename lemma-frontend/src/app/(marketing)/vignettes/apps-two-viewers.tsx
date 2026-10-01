import type { CSSProperties, ReactNode } from "react";
import { ArrowRight, LockSimple } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./apps-two-viewers.module.css";

/* Where Deal desk is served: `<public_slug>.<app_base_domain>`. */
const ADDRESS = "deal-desk.apps.lemma.work";

/* Deal desk's accounts with the owners the sample seeds them with
   (marketing/apps/remy.tsx): Priya owns Northstar and Cedar, you own Birch.
   The table keeps each person to their own rows, so a window is only ever
   sent the rows its viewer owns. */
const PRIYA: [string, string][] = [["Northstar", "Anita Rao"], ["Cedar", "Leena Shah"]];
const YOURS: [string, string][] = [["Birch", "Owen Park"]];

const n = (index: number) => ({ ["--n" as string]: index }) as CSSProperties;

/** A browser window at Deal desk's address. The thin line under the address
 *  bar is the page loading. */
function Window({ index, children }: { index: number; children: ReactNode }) {
    return (
        <div className={v.window}>
            <div className={v.chrome}>
                <span className={v.address} style={n(index)}><LockSimple size={11} weight="fill" />{ADDRESS}</span>
                <i className={v.loading} />
            </div>
            {children}
        </div>
    );
}

/** Deal desk's account register, holding whatever rows the table sent. */
function Register({ rows, owner }: { rows: [string, string][]; owner: string }) {
    return (
        <div className={v.app}>
            <p className={v.head}>Accounts <span className={v.count}>{rows.length}</span></p>
            <div className={v.thead}><span>Account</span><span>Owner</span></div>
            {rows.map(([name, buyer]) => (
                <div key={name} className={v.row}>
                    <span><b>{name}</b><small>{buyer}</small></span>
                    <span>{owner}</span>
                </div>
            ))}
        </div>
    );
}

/** Two people open one app, and each gets the rows they're allowed to see.
 *  Priya's window lists the two accounts she owns. Yours, at the same
 *  address, is sent only Birch: there is no row for Northstar or Cedar to
 *  hide. A third account, never let in, gets the React gate instead.
 *
 *  Beats: 1 your window opens at the same address; 2 it fills with Birch,
 *  the one account you own; 3 a third window opens there, signed in to an
 *  account without access, on the app's loader; 4 the loader gives way to
 *  the gate, Request access to Deal desk; 5 the one address, lit in all
 *  three windows.
 *
 *  On a phone it shows Priya's window and yours, the two that tell it:
 *  one address, different rows. Sam's gate is left out. */
export function AppsTwoViewers() {
    return (
        <Vignette className={v.root} phone={{ x: 6, width: 418 }} beats={[1300, 1000, 1200, 900, 1300]} hold={2800}
            label="Deal desk, one app, open at the same address in three browser windows. Priya's window lists the two accounts she owns, Northstar and Cedar. Your window opens the same address and lists only Birch, the account you own; Priya's rows never reach it. A third window, signed in to an account that hasn't been let in, shows Access required: Request access to Deal desk, with a Request access button.">
            <div className={k.bar}><img className={k.face} src={FACES.Remy} alt="" /><b>Deal desk</b><span>· an app</span><i /><i /><i /></div>

            <div className={`${v.viewer} ${v.priya}`}>
                <p className={v.who}><span className={k.person}>P</span>Priya</p>
                <Window index={0}><Register rows={PRIYA} owner="Priya" /></Window>
            </div>

            <div className={`${v.viewer} ${v.you}`}>
                <p className={v.who}><span className={k.person}>Y</span>You</p>
                <Window index={1}><Register rows={YOURS} owner="You" /></Window>
            </div>

            <div className={`${v.viewer} ${v.sam}`}>
                <p className={v.who}><span className={k.person}>S</span>Sam<em>· not let in</em></p>
                <Window index={2}>
                    <div className={v.gate}>
                        <div className={v.loader}>
                            <span className={v.icon}>DD</span>
                            <p className={v.loaderName}>Deal desk</p>
                            <p className={v.loaderCopy}>Bringing your app into focus</p>
                        </div>
                        <section className={v.card}>
                            <span className={v.eyebrow}><i />Access required</span>
                            <span className={v.icon}>DD</span>
                            <p className={v.title}>Request access to Deal desk</p>
                            <p className={v.copy}>This account is signed in, but it doesn’t have access to Deal desk yet.</p>
                            <span className={v.button}>Request access <ArrowRight size={13} /></span>
                        </section>
                    </div>
                </Window>
            </div>
        </Vignette>
    );
}
