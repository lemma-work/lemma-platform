import type { CSSProperties } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-slack-working.module.css";

const REPLY = "Nine assets are still open. The launch post is due first, and it’s yours.".split(" ");

/* The preview as the Slack card carries it: a query's first five rows, laid
   out by the backend's fixed-width table (display_resource_preview.py). Each
   cell is cut to 14 characters with an ellipsis, and columns are admitted
   until a 38-character line is spent, so `due` doesn't fit and the three
   that do are asset, owner and status. */
const BLOCK = [
    "asset           owner  status",
    "--------------  -----  --------------",
    "Launch post     Priya  In review",
    "Pricing page …  Aditi  Drafting",
    "Customer quot…  Kit    Waiting on cu…",
    "Press release   Priya  In review",
    "Social thread   Kit    Drafting",
];

const step = (n: number) => ({ ["--n" as string]: n }) as CSSProperties;

/** In a busy Slack channel, the teammate answers when it's mentioned and
 *  brings a preview of what's in its space. Priya asks Kit in #launch what's
 *  still open; the eyes reaction says it's working, the answer streams into
 *  the thread, and the first five rows of the open assets come with it, read
 *  with Priya's access, with a button to open the table in Lemma.
 *
 *  Beats: 1 the eyes reaction on Priya's message; 2 Kit's reply, streamed
 *  in word by word; 3 the preview arriving under it, row by row; 4 the
 *  reaction taken back off, the answer complete.
 *
 *  On a phone it shows the left of the thread, and the thread narrows to
 *  that strip so the reply wraps inside it. */
export function ChannelsSlackWorking() {
    return (
        <Vignette className={v.root} height={460} phone={{ x: 10, width: 376 }} beats={[900, 1300, 2300, 1700]} hold={3600}
            label="In Slack, in a thread in #launch, Priya writes: @Kit what's still open for Team plans? An eyes reaction appears on her message while Kit works. Kit's reply streams into the thread: Nine assets are still open. The launch post is due first, and it's yours. Under it comes a preview read with Priya's access, 5 of 9 records, with the first five rows in three columns, asset, owner and status: Launch post, Priya, In review; Pricing page copy, Aditi, Drafting; Customer quote, Northfield, Kit, Waiting on customer; Press release, Priya, In review; Social thread, Kit, Drafting; and an Open in Lemma button. The eyes reaction goes away as the answer lands.">
            <div className={k.bar}><img className={v.logo} src="/connector-logos/slack.svg" alt="" /><b>Thread</b><span>#launch</span><i /><i /><i /></div>
            <div className={`${k.body} ${v.thread}`}>
                <div className={v.message}>
                    <span className={`${k.person} ${v.avatar}`}>P</span>
                    <div className={v.column}>
                        <p className={v.who}><b>Priya</b><time>10:42 AM</time></p>
                        <p className={v.said}><span className={v.mention}>@Kit</span> what’s still open for Team plans?</p>
                        <div className={v.reactions}><span className={v.reaction}><span className={v.eyes} aria-hidden="true">👀</span>1</span></div>
                    </div>
                </div>

                <p className={v.replies}><span>1 reply</span></p>

                <div className={`${v.message} ${v.answer}`}>
                    <img className={`${k.face} ${v.avatar}`} src={FACES.Kit} alt="" />
                    <div className={v.column}>
                        <p className={v.who}><b>Kit</b><time>10:42 AM</time></p>
                        <p className={v.said}>{REPLY.map((word, index) => (
                            <span key={index} className={v.word} style={step(index)}>{word} </span>
                        ))}</p>
                        <div className={v.preview}>
                            <p className={v.title}>Query results</p>
                            <p className={v.count}>5 of 9 records</p>
                            <pre className={v.block}>{BLOCK.map((line, index) => (
                                <span key={index} className={v.line} style={step(index)}>{line}</span>
                            ))}</pre>
                            <span className={`${k.btn} ${v.open}`} style={step(BLOCK.length)}>Open in Lemma</span>
                        </div>
                    </div>
                </div>
            </div>
        </Vignette>
    );
}
