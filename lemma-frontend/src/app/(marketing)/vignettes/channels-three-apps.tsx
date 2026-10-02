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

/** A row of Kit's launch assets whose status changes, and the tag naming
 *  whose access the change was made with. */
function Asset({ className, name, owner, was, now, as }: { className: string; name: string; owner: string; was: string; now: string; as: string }) {
    return (
        <div className={`${v.row} ${className}`}>
            <span className={v.asset}>{name}<small>{owner}</small></span>
            <span className={v.status}><span className={`${v.pill} ${v.was}`}>{was}</span><span className={`${v.pill} ${v.now}`}>{now}</span></span>
            <span className={v.tag}>as {as}</span>
        </div>
    );
}

/** Ask from Slack, WhatsApp or email, and the work lands in the teammate's
 *  space. Priya, Aditi and Rohan each ask Kit from a different app; each
 *  change is made on the same launch list, with the access of whoever asked.
 *
 *  Beats: 1 a line from Priya's Slack message into Kit's space, and the
 *  press release turns Ready, as Priya; 2 Aditi's WhatsApp arrives; 3 its
 *  line, and the pricing page copy turns In review, as Aditi; 4 Rohan's
 *  forwarded email arrives; 5 its line, and the readiness check ticks, as
 *  Rohan.
 *
 *  On a phone it shows Kit's space alone, where the work lands: each line
 *  comes in from the edge, and the tag on the row says who asked. */
export function ChannelsThreeApps() {
    return (
        <Vignette className={v.root} beats={[1700, 1700, 1300, 1700, 1300]} hold={3600} height={384} phone={{ x: 274, width: 352 }}
            label="Three people ask Kit from three apps. In Slack, in #launch, Priya writes: @Kit the press release is signed off, mark it Ready. In Kit's space, on the launch assets list, the press release changes from In review to Ready, made as Priya. On WhatsApp, Aditi writes: pricing copy is with finance now, can you move it to In review? The pricing page copy changes from Drafting to In review, made as Aditi. By email, Rohan forwards: Northfield, quote cleared in writing. The readiness check Customer names cleared in writing is ticked, made as Rohan. Three messages from three apps, and three changes in one place, each made with the access of the person who asked.">
            <div className={v.stage} />

            <Message className={v.slack} who="Priya"
                from={<><img src="/connector-logos/slack.svg" alt="" />Slack<em>· #launch</em></>}>
                <span className={v.mention}>@Kit</span> the press release is signed off, mark it Ready.
            </Message>
            <Message className={v.whatsapp} who="Aditi"
                from={<><img src="/connector-logos/whatsapp.svg" alt="" />WhatsApp</>}>
                pricing copy is with finance now, can you move it to In review?
            </Message>
            <Message className={v.email} who="Rohan"
                from={<><span className={v.at} aria-hidden="true">@</span>Email<em>· to Kit</em></>}>
                Fwd: Northfield, quote cleared in writing
            </Message>

            <div className={`${k.card} ${v.panel}`}>
                <div className={k.bar}><img className={k.face} src={FACES.Kit} alt="" /><b>Kit’s space</b><span>· Launch assets</span><i /><i /><i /></div>
                <div className={v.sheet}>
                    <p className={v.head}><span>Asset</span><span>Status</span></p>
                    <Asset className={v.press} name="Press release" owner="Priya" was="In review" now="Ready" as="Priya" />
                    <Asset className={v.pricing} name="Pricing page copy" owner="Aditi" was="Drafting" now="In review" as="Aditi" />

                    <p className={`${v.head} ${v.checksHead}`}><span>Readiness checks</span></p>
                    <div className={`${v.row} ${v.check}`}>
                        <i className={`${k.check} ${v.box}`} />
                        <span className={v.asset}><span className={v.checkName}>Customer names cleared in writing</span><small>in 3 days</small></span>
                        <span className={v.tag}>as Rohan</span>
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
