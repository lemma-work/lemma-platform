import type { CSSProperties, ReactNode } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-round-up.module.css";

const step = (n: number) => ({ ["--n" as string]: n }) as CSSProperties;

/** One message in the #feedback thread: who, when, what they wrote. */
function Post({ className, kit, who, at, children }: { className?: string; kit?: boolean; who: string; at: string; children: ReactNode }) {
    return (
        <div className={`${v.post} ${className ?? ""}`}>
            {kit ? <img className={k.face} src={FACES.Kit} alt="" /> : <span className={k.person}>{who[0]}</span>}
            <div className={v.column}>
                <p className={v.name}><b>{who}</b><time>{at}</time></p>
                <div className={v.said}>{children}</div>
            </div>
        </div>
    );
}

/** One person's own conversation with Kit, in the app Kit reached them on:
 *  Kit's question, headed with whose authority it carries as every such
 *  message is, then their answer once they get to it. */
function Tile({ className, n, app, who, ask, at, children }: { className: string; n: number; app: ReactNode; who: string; ask: string; at: string; children: ReactNode }) {
    return (
        <div className={`${k.card} ${v.tile} ${className}`} style={step(n)}>
            <div className={v.app}>{app}<em>· {who}</em><time>{at}</time></div>
            <div className={v.ask}><img className={v.kit} src={FACES.Kit} alt="" /><span><small>On behalf of you:</small>{ask}</span></div>
            <div className={v.answer}><span className={`${k.person} ${v.initial}`}>{who[0]}</span><span>{children}</span></div>
        </div>
    );
}

/** Asking around: one ask in a Slack thread, three people on three apps,
 *  one message back in the thread once the last of them has answered.
 *
 *  You ask Kit in #feedback for an update from Dev, Alex and Sam before
 *  the weekly report. Kit messages each of them itself, on the app it reaches them on,
 *  says who it's waiting on, and stops. The answers come in hours apart and
 *  in their own conversations; the thread stays quiet until the third, then
 *  gets one round-up.
 *
 *  Beats: 1 Kit's reply in the thread, and its question in each person's
 *  app; 2 Sam answers by email, 1 of 3; 3 Dev answers in Slack, 2 of 3;
 *  4 Alex answers on WhatsApp, 3 of 3; 5 the round-up in the thread, a
 *  line per person.
 *
 *  On a phone it shows the thread alone: the counter under it marks each
 *  answer as it comes in, and the round-up lands in it. */
export function ChannelsRoundUp() {
    return (
        <Vignette className={v.root} height={440} phone={{ x: 18, width: 364 }} beats={[1400, 2500, 1300, 1800, 1000]} hold={3600}
            label="In Slack, in a thread in #feedback, you write: @Kit get an update from Dev, Alex and Sam. Kit replies in the thread: Asked Dev here in Slack, Alex on WhatsApp and Sam by email. I'll post when all three have answered. Beside the thread, Kit's question lands in each person's own app, headed On behalf of you. The answers come in one at a time, hours apart, while the thread stays quiet and a counter under it goes from 1 of 3 to 3 of 3: Sam by email, Brightpath's renewal needs bulk edit; Dev in Slack, #490 ships Friday, Android next week; Alex on WhatsApp, timezone fix is live, no new reports. Only after the third answer does Kit post one round-up in the thread, a line for each person.">
            <div className={v.stage} />

            <div className={v.left}>
                <div className={`${k.card} ${v.thread}`}>
                    <div className={k.bar}><img className={v.logo} src="/connector-logos/slack.svg" alt="" /><b>Thread</b><span>· #feedback</span></div>
                    <div className={v.feed}>
                        <Post who="You" at="10:04 AM"><span className={v.mention}>@Kit</span> get an update from Dev, Alex and Sam.</Post>

                        <div className={v.replies}><span className={v.one}>1 reply</span><span className={v.two}>2 replies</span></div>

                        <Post className={v.reply} kit who="Kit" at="10:04 AM">
                            Asked Dev here in Slack, Alex on WhatsApp and Sam by email. I’ll post when all three have answered.
                        </Post>

                        <Post className={v.roundup} kit who="Kit" at="3:41 PM">
                            <span className={v.line} style={step(0)}>All three have answered.</span>
                            <span className={v.line} style={step(1)}><b>Dev:</b> #490 ships Friday, Android next week</span>
                            <span className={v.line} style={step(2)}><b>Alex:</b> timezone fix is live, no new reports</span>
                            <span className={v.line} style={step(3)}><b>Sam:</b> Brightpath’s renewal needs bulk edit</span>
                        </Post>
                    </div>
                </div>

                <div className={v.count}>
                    <i /><i /><i />
                    <span className={v.n}><b>0</b><b>1</b><b>2</b><b>3</b></span>
                    <span>of 3 answered</span>
                </div>
            </div>

            <div className={v.tiles}>
                <Tile className={v.dev} n={0} who="Dev" ask="Where’s the mobile fix?" at="1:52 PM"
                    app={<><img src="/connector-logos/slack.svg" alt="" />Slack</>}>
                    #490 ships Friday, Android next week
                </Tile>
                <Tile className={v.alex} n={1} who="Alex" ask="Is the timezone fix holding?" at="3:40 PM"
                    app={<><img src="/connector-logos/whatsapp.svg" alt="" />WhatsApp</>}>
                    timezone fix is live, no new reports
                </Tile>
                <Tile className={v.sam} n={2} who="Sam" ask="Who needs bulk edit most?" at="11:26 AM"
                    app={<><span className={v.at} aria-hidden="true">@</span>Email</>}>
                    Brightpath’s renewal needs bulk edit
                </Tile>
            </div>
        </Vignette>
    );
}
