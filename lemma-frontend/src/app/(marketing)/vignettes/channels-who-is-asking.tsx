import type { ReactNode } from "react";
import { Check, Eye, LockSimple, PencilSimple } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-who-is-asking.module.css";

/* Kit's launch_assets as the sample space seeds them, a few of its rows:
   [asset, owner, status]. The press release is the one asked about. */
const ASSETS: [string, string, string][] = [
    ["Launch post", "Priya", "In review"],
    ["Pricing page copy", "Aditi", "Drafting"],
    ["Demo video, 90s", "Rohan", "Drafting"],
    ["Press release", "Priya", "In review"],
    ["Changelog entry", "Kit", "Ready"],
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

/** Same thread, same teammate, two people. Sam asks Kit in #launch to mark
 *  the press release Ready; he can view the launch assets but not edit them,
 *  so the request runs as Sam and nothing moves, though Kit itself could.
 *  Priya, who can edit them, says yes in the same thread, and the same
 *  change goes through as her.
 *
 *  Beats: 1 Sam's message, and under his name what he may do; 2 the row
 *  nudges toward Ready and stops, a lock settles on it, and the note says
 *  it ran as Sam; 3 Priya's reply, and what she may do; 4 the row flips to
 *  Ready, noted as run as Priya; 5 Kit's short done reply under hers.
 *
 *  On a phone it shows Kit's space alone, where the outcome lands: the
 *  notes under the table say whose request each change ran as. */
export function ChannelsWhoIsAsking() {
    return (
        <Vignette className={v.root} beats={[1300, 1500, 2400, 1500, 1700]} hold={3400} height={420} phone={{ x: 324, width: 308 }}
            label="A Slack thread in #launch. Sam, who can view Kit's launch assets but not edit them, writes: @Kit mark the press release Ready, Priya's fine with it. Beside the thread, in Kit's space, the press release row reads In review. It starts toward Ready and stops, a lock settles on it, and a note says it ran as Sam: can view, can't change, nothing moved. Priya, who can edit the launch assets, replies in the same thread: @Kit yes, mark it Ready. The row turns Ready, noted as run as Priya, and Kit replies under her message that it's done.">
            <div className={v.stage} />

            {/* The thread, drawn plainly: Slack's mark, no one's chrome. */}
            <section className={`${k.card} ${v.thread}`}>
                <div className={`${k.bar} ${v.head}`}><img className={v.logo} src="/connector-logos/slack.svg" alt="" /><b>#launch</b><span>· Thread</span></div>
                <div className={v.feed}>
                    <Post who="Aditi" at="9:48">Pricing page draft is up for comments.</Post>
                    <Post className={v.sam} who="Sam" at="10:02"
                        access={<span className={`${k.chip} ${v.access}`}><Eye size={12} />can view Launch assets</span>}>
                        <span className={v.at}>@Kit</span> mark the press release Ready, Priya’s fine with it.
                    </Post>
                    <Post className={v.priya} who="Priya" at="10:05"
                        access={<span className={`${k.chip} ${v.access}`}><PencilSimple size={12} />can edit Launch assets</span>}>
                        <span className={v.at}>@Kit</span> yes, mark it Ready.
                    </Post>
                    <Post className={v.kit} who="Kit" at="10:05">Done. The press release is Ready.</Post>
                </div>
                <div className={v.composer}>Reply…</div>
            </section>

            {/* Kit's space: the launch assets everyone works from. */}
            <section className={`${k.card} ${v.panel}`}>
                <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Kit’s space</b><span>· Launch assets</span><i /><i /><i /></div>
                <div className={v.sheet}>
                    <p className={v.cols}><span>Asset</span><span>Owner</span><span>Status</span></p>
                    {ASSETS.map(([asset, owner, status]) => {
                        const asked = asset === "Press release";
                        return (
                            <div key={asset} className={`${v.row} ${asked ? v.asked : ""}`}>
                                <span className={v.asset}>{asset}</span>
                                <span className={v.owner}>{owner}</span>
                                {asked
                                    ? <span className={`${v.pill} ${v.slot}`}><span className={v.track}><span>In review</span><span>Ready</span></span></span>
                                    : <span className={v.pill}>{status}</span>}
                                {asked ? <span className={v.lock}><LockSimple size={11} weight="fill" /></span> : <span />}
                            </div>
                        );
                    })}
                </div>
            </section>

            {/* Who each request ran as, set apart from the chat. */}
            <p className={`${v.note} ${v.noteSam}`}><span className={v.glyph}><LockSimple size={11} weight="fill" /></span><span><b>Ran as Sam:</b> can view, can’t change.<br />Nothing moved.</span></p>
            <p className={`${v.note} ${v.notePriya}`}><span className={`${v.glyph} ${v.glyphOk}`}><Check size={11} weight="bold" /></span><span><b>Ran as Priya:</b> can edit. Now Ready.</span></p>
        </Vignette>
    );
}
