import type { CSSProperties } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-slack-working.module.css";

const REPLY = "22 people reported it. Every reply waits for Dev to say #482 is live.".split(" ");

/* The preview as the Slack card carries it: a query's first five rows, laid
   out by the backend's fixed-width table (display_resource_preview.py). Each
   cell is cut to 14 characters with an ellipsis, and columns are admitted
   until a 38-character line is spent, so `where` doesn't fit and the three
   that do are reporter, plan and state. The rows are retry_replies for
   #482, newest first, as the sample space seeds them. */
const BLOCK = [
    "reporter    plan        state",
    "----------  ----------  -------------",
    "@maria.k    Free        Waits for Dev",
    "@kenji      Pro         Waits for Dev",
    "@noor       Pro         Waits for Dev",
    "Northfield  Enterprise  With Sam",
    "@priya.n    Pro         Waits for Dev",
];

const step = (n: number) => ({ ["--n" as string]: n }) as CSSProperties;

/** In a busy Slack channel, the teammate answers when it's mentioned and
 *  brings a preview of what's in its space. Sam asks Kit in #feedback who's
 *  waiting on the dates fix; the eyes reaction says it's working, the answer
 *  streams into the thread, and the first five retry replies come with it,
 *  read with Sam's access, with a button to open the table in Lemma.
 *
 *  Beats: 1 the eyes reaction on Sam's message; 2 Kit's reply, streamed
 *  in word by word; 3 the preview arriving under it, row by row; 4 the
 *  reaction taken back off, the answer complete.
 *
 *  On a phone it shows the left of the thread, and the thread narrows to
 *  that strip so the reply wraps inside it. */
export function ChannelsSlackWorking() {
    return (
        <Vignette className={v.root} height={460} phone={{ x: 10, width: 376 }} beats={[900, 1300, 2300, 1700]} hold={3600}
            label="In Slack, in a thread in #feedback, Sam writes: @Kit who's waiting on the dates fix? An eyes reaction appears on the message while Kit works. Kit's reply streams into the thread: 22 people reported it. Every reply waits for Dev to say #482 is live. Under it comes a preview read with Sam's access, 5 of 22 records, with the first five retry replies in three columns, reporter, plan and state: @maria.k, Free, Waits for Dev; @kenji, Pro, Waits for Dev; @noor, Pro, Waits for Dev; Northfield, Enterprise, With Sam; @priya.n, Pro, Waits for Dev; and an Open in Lemma button. The eyes reaction goes away as the answer lands.">
            <div className={k.bar}><img className={v.logo} src="/connector-logos/slack.svg" alt="" /><b>Thread</b><span>#feedback</span><i /><i /><i /></div>
            <div className={`${k.body} ${v.thread}`}>
                <div className={v.message}>
                    <span className={`${k.person} ${v.avatar}`}>S</span>
                    <div className={v.column}>
                        <p className={v.who}><b>Sam</b><time>10:42 AM</time></p>
                        <p className={v.said}><span className={v.mention}>@Kit</span> who’s waiting on the dates fix?</p>
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
                            <p className={v.count}>5 of 22 records</p>
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
