import type { CSSProperties, ReactNode } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-round-up.module.css";

const step = (n: number) => ({ ["--n" as string]: n }) as CSSProperties;

/** One message in the #launch thread: who, when, what they wrote. */
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
            <div className={v.ask}><img className={v.kit} src={FACES.Kit} alt="" /><span><small>On behalf of Sam:</small>{ask}</span></div>
            <div className={v.answer}><span className={`${k.person} ${v.initial}`}>{who[0]}</span><span>{children}</span></div>
        </div>
    );
}

/** Asking around: one ask in a Slack thread, three people on three apps,
 *  one message back in the thread once the last of them has answered.
 *
 *  Sam asks Kit in #launch for the launch status from Priya, Aditi and
 *  Rohan. Kit messages each of them itself, on the app it reaches them on,
 *  says who it's waiting on, and stops. The answers come in hours apart and
 *  in their own conversations; the thread stays quiet until the third, then
 *  gets one round-up.
 *
 *  Beats: 1 Kit's reply in the thread, and its question in each person's
 *  app; 2 Rohan answers by email, 1 of 3; 3 Priya answers in Slack, 2 of 3;
 *  4 Aditi answers on WhatsApp, 3 of 3; 5 the round-up in the thread, a
 *  line per person.
 *
 *  On a phone it shows the thread alone: the counter under it marks each
 *  answer as it comes in, and the round-up lands in it. */
export function ChannelsRoundUp() {
    return (
        <Vignette className={v.root} height={440} phone={{ x: 18, width: 364 }} beats={[1400, 2500, 1300, 1800, 1000]} hold={3600}
            label="In Slack, in a thread in #launch, Sam writes: @Kit get launch status from Priya, Aditi and Rohan. Kit replies in the thread: Asked Priya here in Slack, Aditi on WhatsApp and Rohan by email. I'll post when all three have answered. Beside the thread, Kit's question lands in each person's own app, headed On behalf of Sam. The answers come in one at a time, hours apart, while the thread stays quiet and a counter under it goes from 1 of 3 to 3 of 3: Rohan by email, demo video's cut, uploading tonight; Priya in Slack, press release is signed off; Aditi on WhatsApp, pricing copy is with finance. Only after the third answer does Kit post one round-up in the thread, a line for each person.">
            <div className={v.stage} />

            <div className={v.left}>
                <div className={`${k.card} ${v.thread}`}>
                    <div className={k.bar}><img className={v.logo} src="/connector-logos/slack.svg" alt="" /><b>Thread</b><span>· #launch</span></div>
                    <div className={v.feed}>
                        <Post who="Sam" at="10:04 AM"><span className={v.mention}>@Kit</span> get launch status from Priya, Aditi and Rohan.</Post>

                        <div className={v.replies}><span className={v.one}>1 reply</span><span className={v.two}>2 replies</span></div>

                        <Post className={v.reply} kit who="Kit" at="10:04 AM">
                            Asked Priya here in Slack, Aditi on WhatsApp and Rohan by email. I’ll post when all three have answered.
                        </Post>

                        <Post className={v.roundup} kit who="Kit" at="3:41 PM">
                            <span className={v.line} style={step(0)}>All three have answered.</span>
                            <span className={v.line} style={step(1)}><b>Priya:</b> press release is signed off</span>
                            <span className={v.line} style={step(2)}><b>Aditi:</b> pricing copy is with finance</span>
                            <span className={v.line} style={step(3)}><b>Rohan:</b> demo video’s cut, uploading tonight</span>
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
                <Tile className={v.priya} n={0} who="Priya" ask="Where’s the press release?" at="1:52 PM"
                    app={<><img src="/connector-logos/slack.svg" alt="" />Slack</>}>
                    press release is signed off
                </Tile>
                <Tile className={v.aditi} n={1} who="Aditi" ask="Where’s the pricing copy?" at="3:40 PM"
                    app={<><img src="/connector-logos/whatsapp.svg" alt="" />WhatsApp</>}>
                    pricing copy is with finance
                </Tile>
                <Tile className={v.rohan} n={2} who="Rohan" ask="Where’s the demo video?" at="11:26 AM"
                    app={<><span className={v.at} aria-hidden="true">@</span>Email</>}>
                    demo video’s cut, uploading tonight
                </Tile>
            </div>
        </Vignette>
    );
}
