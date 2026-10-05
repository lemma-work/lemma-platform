import type { ReactNode } from "react";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-three-apps.module.css";

/* Where each message's line leaves its card and where it lands in Kit's
   space, in canvas px: [from x, from y, to x, to y]. The lines are drawn
   once, in the order the messages arrive, and stay. */
const LINES: [number, number, number, number][] = [
    [238, 81, 282, 134],
    [238, 192, 282, 186],
    [238, 303, 282, 270],
];
const path = ([x1, y1, x2, y2]: [number, number, number, number]) => {
    const mid = (x1 + x2) / 2;
    return `M${x1} ${y1} C${mid} ${y1} ${mid} ${y2} ${x2} ${y2}`;
};

/** One message as it arrived: where from, who, what they wrote. */
function Message({ className, from, who, children }: { className: string; from: ReactNode; who: string; children: ReactNode }) {
    return (
        <div className={`${k.card} ${v.message} ${className}`}>
            <p className={v.from}>{from}</p>
            <div className={v.said}>
                <span className={k.person}>{who[0]}</span>
                <p><b>{who}</b>{children}</p>
            </div>
        </div>
    );
}

/** A row of Kit's feedback themes whose fix changes state, and the tag
 *  naming whose access the change was made with. */
function Theme({ className, name, owner, was, now, as }: { className: string; name: string; owner: string; was: string; now: string; as: string }) {
    return (
        <div className={`${v.row} ${className}`}>
            <span className={v.asset}>{name}<small>{owner}</small></span>
            <span className={v.status}><span className={`${v.pill} ${v.was}`}>{was}</span><span className={`${v.pill} ${v.now}`}>{now}</span></span>
            <span className={v.tag}>as {as}</span>
        </div>
    );
}

/** Ask from Slack, WhatsApp or email, and the work lands in the teammate's
 *  space. Dev, Alex and Sam each tell Kit from a different app; each change
 *  is made on the same themes list, with the access of whoever said it.
 *
 *  Beats: 1 a line from Dev's Slack message into Kit's space, and #482
 *  turns Live, as Dev, who shipped it; 2 Alex's WhatsApp arrives; 3 its
 *  line, and #478 turns Live, as Alex; 4 Sam's forwarded email arrives;
 *  5 its line, and Northfield's retry reply is ticked over to Sam, as Sam.
 *
 *  On a phone it shows Kit's space alone, where the work lands: each line
 *  comes in from the edge, and the tag on the row says who asked. */
export function ChannelsThreeApps() {
    return (
        <Vignette className={v.root} beats={[1700, 1700, 1300, 1700, 1300]} hold={3600} height={384} phone={{ x: 274, width: 352 }}
            label="Three people tell Kit from three apps. In Slack, in #feedback, Dev writes: @Kit #482 is live. Send the replies. In Kit's space, on the feedback themes, Dates import as text goes from Merged to Live, made as Dev, who shipped it. On WhatsApp, Alex writes: #478 is out, the digest times are right now. Wrong timezone in emails goes from Merged to Live, made as Alex. By email, Sam forwards: Northfield, call me before you email. The retry reply to Northfield is ticked over to Sam, made as Sam. Three messages from three apps, and three changes in one place, each made with the access of the person who sent it.">
            <div className={v.stage} />

            <Message className={v.slack} who="Dev"
                from={<><img src="/connector-logos/slack.svg" alt="" />Slack<em>· #feedback</em></>}>
                <span className={v.mention}>@Kit</span> #482 is live. Send the replies.
            </Message>
            <Message className={v.whatsapp} who="Alex"
                from={<><img src="/connector-logos/whatsapp.svg" alt="" />WhatsApp</>}>
                #478 is out, the digest times are right now.
            </Message>
            <Message className={v.email} who="Sam"
                from={<><span className={v.at} aria-hidden="true">@</span>Email<em>· to Kit</em></>}>
                Fwd: Northfield, “call me before you email”
            </Message>

            <div className={`${k.card} ${v.panel}`}>
                <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Kit’s space</b><span>· Feedback themes</span><i /><i /><i /></div>
                <div className={v.sheet}>
                    <p className={v.head}><span>Theme</span><span>Fix</span></p>
                    <Theme className={v.dates} name="Dates import as text" owner="Dev · #482" was="Merged" now="Live" as="Dev" />
                    <Theme className={v.zones} name="Wrong timezone in emails" owner="Alex · #478" was="Merged" now="Live" as="Alex" />

                    <p className={`${v.head} ${v.checksHead}`}><span>Retry replies</span></p>
                    <div className={`${v.row} ${v.check}`}>
                        <i className={`${k.check} ${v.box}`} />
                        <span className={v.asset}><span className={v.checkName}>Northfield hears from Sam first</span><small>Enterprise · #482</small></span>
                        <span className={v.tag}>as Sam</span>
                    </div>
                </div>
            </div>

            <svg className={v.lines} width="640" height="384" viewBox="0 0 640 384" aria-hidden="true">
                {LINES.map((line, index) => (
                    <g key={index} className={`${v.line} ${[v.l1, v.l2, v.l3][index]}`}>
                        <path d={path(line)} pathLength={1} />
                        <circle className={v.start} cx={line[0]} cy={line[1]} r={3} />
                        <circle className={v.end} cx={line[2]} cy={line[3]} r={3} />
                    </g>
                ))}
            </svg>
        </Vignette>
    );
}
