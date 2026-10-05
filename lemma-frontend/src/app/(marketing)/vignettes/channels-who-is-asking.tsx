import type { ReactNode } from "react";
import { Check, Eye, LockSimple, PencilSimple } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-who-is-asking.module.css";

/* Kit's feedback_themes as the sample space seeds them, a few of its rows:
   [theme, owner, where its fix is]. Dates import as text is the one asked
   about: #482 has merged, and only Dev, who shipped it, can say it's live. */
const THEMES: [string, string, string][] = [
    ["Large imports time out", "Dev", "No fix"],
    ["Notifications on mobile", "Dev", "In review"],
    ["Dates import as text", "Dev", "Merged"],
    ["Login link expired", "Dev", "Live"],
    ["Column mapping resets", "Alex", "Live"],
];

/** One message in the thread: who, when, what they may do, what they said. */
function Post({ className, who, at, access, children }: { className?: string; who: string; at: string; access?: ReactNode; children: ReactNode }) {
    return (
        <div className={`${v.post} ${className ?? ""}`}>
            {who === "Kit" ? <img className={k.face} src={FACES.Kit} alt="" /> : <span className={k.person}>{who[0]}</span>}
            <div>
                <p className={v.name}>{who}<time>{at}</time></p>
                {access}
                <p className={v.text}>{children}</p>
            </div>
        </div>
    );
}

/** Same thread, same teammate, two people. Sam asks Kit in #feedback to
 *  mark #482 live and send the retry replies; Sam can view the themes, but
 *  only the engineer who shipped a fix can say it's live, so the request
 *  runs as Sam and nothing moves, though Kit itself could. Dev, who shipped
 *  it, says yes in the same thread, and the same change goes through as Dev.
 *
 *  Beats: 1 Sam's message, and under the name what Sam may do; 2 the row
 *  nudges toward Live and stops, a lock settles on it, and the note says
 *  it ran as Sam; 3 Dev's reply, and what Dev may do; 4 the row flips to
 *  Live, noted as run as Dev; 5 Kit's short done reply under Dev's.
 *
 *  On a phone it shows Kit's space alone, where the outcome lands: the
 *  notes under the table say whose request each change ran as. */
export function ChannelsWhoIsAsking() {
    return (
        <Vignette className={v.root} beats={[1300, 1500, 2400, 1500, 1700]} hold={3400} height={420} phone={{ x: 324, width: 308 }}
            label="A Slack thread in #feedback. Sam, who can view Kit's feedback themes but can't confirm a fix, writes: @Kit #482 is live, send the dates replies. Dev's fine with it. Beside the thread, in Kit's space, the row for Dates import as text reads Merged. It starts toward Live and stops, a lock settles on it, and a note says it ran as Sam: can view, can't confirm, nothing moved. Dev, who shipped #482, replies in the same thread: @Kit yes, it's live. The row turns Live, noted as run as Dev, and Kit replies under Dev's message that it's done: 19 replies are going out.">
            <div className={v.stage} />

            {/* The thread, drawn plainly: Slack's mark, no one's chrome. */}
            <section className={`${k.card} ${v.thread}`}>
                <div className={`${k.bar} ${v.head}`}><img className={v.logo} src="/connector-logos/slack.svg" alt="" /><b>#feedback</b><span>· Thread</span></div>
                <div className={v.feed}>
                    <Post who="Alex" at="9:48">Digest times look right now.</Post>
                    <Post className={v.sam} who="Sam" at="10:02"
                        access={<span className={`${k.chip} ${v.access}`}><Eye size={12} />can view Feedback themes</span>}>
                        <span className={v.at}>@Kit</span> #482 is live, send the dates replies. Dev’s fine with it.
                    </Post>
                    <Post className={v.dev} who="Dev" at="10:05"
                        access={<span className={`${k.chip} ${v.access}`}><PencilSimple size={12} />shipped #482</span>}>
                        <span className={v.at}>@Kit</span> yes, it’s live.
                    </Post>
                    <Post className={v.kit} who="Kit" at="10:05">Done. 19 replies are going out.</Post>
                </div>
                <div className={v.composer}>Reply…</div>
            </section>

            {/* Kit's space: the themes everyone works from. */}
            <section className={`${k.card} ${v.panel}`}>
                <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Kit’s space</b><span>· Feedback themes</span><i /><i /><i /></div>
                <div className={v.sheet}>
                    <p className={v.cols}><span>Theme</span><span>Owner</span><span>Fix</span></p>
                    {THEMES.map(([theme, owner, status]) => {
                        const asked = theme === "Dates import as text";
                        return (
                            <div key={theme} className={`${v.row} ${asked ? v.asked : ""}`}>
                                <span className={v.asset}>{theme}</span>
                                <span className={v.owner}>{owner}</span>
                                {asked
                                    ? <span className={`${v.pill} ${v.slot}`}><span className={v.track}><span>Merged</span><span>Live</span></span></span>
                                    : <span className={v.pill}>{status}</span>}
                                {asked ? <span className={v.lock}><LockSimple size={11} weight="fill" /></span> : <span />}
                            </div>
                        );
                    })}
                </div>
            </section>

            {/* Who each request ran as, set apart from the chat. */}
            <p className={`${v.note} ${v.noteSam}`}><span className={v.glyph}><LockSimple size={11} weight="fill" /></span><span><b>Ran as Sam:</b> can view, can’t confirm.<br />Nothing moved.</span></p>
            <p className={`${v.note} ${v.noteDev}`}><span className={`${v.glyph} ${v.glyphOk}`}><Check size={11} weight="bold" /></span><span><b>Ran as Dev:</b> shipped it. Now Live.</span></p>
        </Vignette>
    );
}
