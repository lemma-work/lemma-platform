import type { ReactNode } from "react";
import { Check, Eye } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./channels-approval.module.css";

/** One message in the thread: who, when, what they said. */
function Post({ className, who, at, children }: { className?: string; who: string; at: ReactNode; children: ReactNode }) {
    return (
        <div className={`${v.message} ${className ?? ""}`}>
            {who === "Kit"
                ? <img className={`${k.face} ${v.avatar}`} src={FACES.Kit} alt="" />
                : <span className={`${k.person} ${v.avatar}`}>{who[0]}</span>}
            <div className={v.column}>
                <p className={v.who}><b>{who}</b><span className={v.at}>{at}</span></p>
                {children}
            </div>
        </div>
    );
}

/** Someone's pointer in the channel, with their name on it. */
function Pointer({ className, who }: { className: string; who: string }) {
    return (
        <div className={`${v.pointer} ${className}`} aria-hidden="true">
            <svg width="16" height="21" viewBox="0 0 16 21"><path d="M1.5 1.5v15.6l4-3.7 2.7 6.1 2.7-1.2-2.7-6h5.4z" /></svg>
            <span>{who}</span>
        </div>
    );
}

/** A yes that only the person who asked can give. In #launch Priya asks Kit
 *  to send the launch post to the press list; emailing the press list needs
 *  a yes, so Kit answers with Slack's own Approve and Deny buttons and a
 *  one-line preview of the call. Rohan taps Approve first and is told, in a
 *  note only he sees, that it isn't his to answer; the card stays as it
 *  was. Priya taps Approve, the buttons give way to Done, and the post goes.
 *
 *  Beats: 1 Kit's approval card under Priya's ask; 2 Rohan's pointer comes
 *  in and taps Approve; 3 the note only Rohan sees, the card unchanged;
 *  4 Rohan's pointer goes and Priya's comes in and taps Approve; 5 the two
 *  buttons fold into one done line; 6 Kit's line saying it's sent.
 *
 *  On a phone it shows the left of the thread, and the thread narrows to
 *  that strip so every message wraps inside it. */
export function ChannelsApproval() {
    return (
        <Vignette className={v.root} height={440} phone={{ x: 4, width: 376 }} beats={[1100, 1300, 1500, 2300, 1800, 1000]} hold={3200}
            label="In Slack, in a thread in #launch, Priya writes: @Kit send the launch post to the press list at 10. Kit answers with an approval card: Send the launch post to the press list? It shows a one-line preview of the email it will send, to the press list with the subject Team plans are live, and two buttons, Approve and Deny. Rohan taps Approve. A note that only Rohan can see says: I can't tell that this is yours to answer. Reply with your decision instead and I'll take it. The card doesn't change. Then Priya taps Approve, the buttons give way to a line that says Done, and Kit confirms the launch post went to the press list.">
            <div className={k.bar}><img className={v.logo} src="/connector-logos/slack.svg" alt="" /><b>Thread</b><span>#launch</span><i /><i /><i /></div>
            <div className={`${k.body} ${v.thread}`}>
                <Post who="Priya" at="9:58 AM">
                    <p className={v.said}><span className={v.mention}>@Kit</span> send the launch post to the press list at 10.</p>
                </Post>

                <Post className={v.ask} who="Kit" at="9:58 AM">
                    {/* The message as Slack draws the app's blocks, inline in
                        the thread: the ask, the call it will make as a quoted
                        line, and the two buttons. */}
                    <div className={v.card}>
                        <p className={v.title}><b>Approval needed:</b> Send the launch post to the press list?</p>
                        <p className={v.action}>
                            <span>Action:</span>
                            <code>send_email(to=[&quot;li@techwire.co&quot;,…], subject=Team plans are live)</code>
                        </p>
                        <div className={v.choices}>
                            <span className={`${k.btnPrimary} ${v.approve}`}>Approve</span>
                            <span className={`${k.btn} ${v.deny}`}>Deny</span>
                            <span className={v.done}><Check size={13} weight="bold" />Done</span>
                        </div>
                    </div>
                </Post>

                <Post className={v.note} who="Kit" at={<><Eye size={13} />Only visible to Rohan</>}>
                    <p className={v.said}>I can’t tell that this is yours to answer. Reply with your decision instead and I’ll take it.</p>
                </Post>

                <Post className={v.sent} who="Kit" at="10:00 AM">
                    <p className={v.said}>Sent. The launch post is out to the press list.</p>
                </Post>
            </div>

            <Pointer className={v.rohan} who="Rohan" />
            <Pointer className={v.priya} who="Priya" />
        </Vignette>
    );
}
