import type { ReactNode } from "react";
/* The product's own icons (ui/icons.tsx names them Apps, Attach, Close,
   Expand and Send), from the server build so this needs no client code. */
import { ArrowUp as SendIcon, CornersOut as ExpandIcon, Paperclip as AttachIcon, SquaresFour as AppsIcon, X as CloseIcon } from "@phosphor-icons/react/dist/ssr";
import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./apps-widget-to-app.module.css";

/* Scout's research widget, as marketing/conversation-widgets.ts writes it:
   each theme's count, and its bar as a share of the largest. */
const BARS: [string, number][] = [["Setup effort", 3], ["Budget", 1], ["No owner", 1]];

/* The widget, drawn as it draws itself: its own bordered section, header,
   bars, the line from Birch's interview, and its footer. */
function Widget({ className }: { className: string }) {
    return (
        <section className={`${v.widget} ${className}`}>
            <header><b>What stalled these trials?</b><small>5 interviews</small></header>
            {BARS.map(([theme, count]) => (
                <div key={theme} className={v.barRow}><span>{theme}</span><i style={{ ["--w" as string]: count / 3 }} /><b>{count}</b></div>
            ))}
            <blockquote>“We ran out of time mapping our export.”<small>Birch · Interview B2</small></blockquote>
            <footer>Small, selected sample. Compare converted trials before drawing a broader conclusion. Fictional research data.</footer>
        </section>
    );
}

/* A line of the conversation, arriving on its beat: it opens at the bottom,
   over the box, and pushes what is above it up. */
function Line({ beat, children }: { beat: number; children: ReactNode }) {
    return <div className={`${v.line} ${v["in" + beat]}`}><div><div className={v.turn}>{children}</div></div></div>;
}

/* Your turn: on the far side, under your name. */
function You({ at, children }: { at: string; children: ReactNode }) {
    return <div className={v.you}><p className={v.head}><span>You</span><time>{at}</time></p><p className={v.mine}>{children}</p></div>;
}

/* Scout's turn: its face, its name, and what it put in the conversation. */
function Scout({ at, children }: { at: string; children: ReactNode }) {
    return (
        <div className={v.reply}>
            <img className={k.face} src={FACES.Scout} alt="" />
            <div className={v.col}><p className={v.head}><span className={v.mate}>Scout</span><time>{at}</time></p>{children}</div>
        </div>
    );
}

/** A widget becomes an app: asked what stalled the trials, Scout answers in
 *  the conversation with its small chart; asked for it every Monday, the
 *  chart lifts out of the thread into a tab of its own beside the
 *  conversation, and Scout's reply is the app's card.
 *
 *  Beats: 1 your question, sent; 2 Scout's answer, the widget's bars drawn
 *  in; 3 "I'll want this every Monday. Can it be an app?"; 4 the
 *  conversation makes way and a tab opens beside it, named Stalled trials;
 *  5 the widget lifts out of the thread and settles in the tab; 6 Scout's
 *  reply: the app's card, Open.
 *
 *  No phone strip: the conversation is centred for beats 1–3, then moves
 *  left as the tab opens on the right, and the widget crosses between
 *  them, so any strip narrow enough to read cuts one half of the story. */
export function AppsWidgetToApp() {
    return (
        <Vignette className={v.root} width={640} height={440} beats={[800, 800, 1800, 1200, 800, 1400]} hold={3200}
            label="In a conversation with Scout, someone asks what stalled these trials. Scout answers that three of the five mention setup effort, with a small chart drawn in the conversation: What stalled these trials, 5 interviews, setup effort 3, budget 1 and no owner 1, under it Birch's line, We ran out of time mapping our export. They write: I'll want this every Monday. Can it be an app? A tab named Stalled trials opens beside the conversation, the chart lifts out of the thread and settles in it, and Scout's reply in the thread is a card: Stalled trials, app, Open.">
            <div className={`${k.bar} ${v.bar}`}><img className={k.face} src={FACES.Scout} alt="" /><span>Scout /</span><b>Chat</b><i /><i /><i /></div>
            <div className={v.ground} />

            {/* The conversation: centred while it is alone on the stage, on
                the left once something sits beside it. */}
            <div className={v.convo}>
                <div className={v.feed}>
                    <Line beat={1}><You at="10:02">What stalled these trials?</You></Line>
                    <Line beat={2}>
                        <Scout at="10:02">
                            <p className={v.said}>Three of the five mention setup effort.</p>
                            <div className={v.slot}><div><Widget className={v.inThread} /></div></div>
                        </Scout>
                    </Line>
                    <Line beat={3}><You at="10:04">I’ll want this every Monday. Can it be an app?</You></Line>
                    <Line beat={6}>
                        <Scout at="10:09">
                            <span className={v.card}>
                                <AppsIcon size={20} />
                                <span className={v.cardBody}><span>Stalled trials</span><small>app</small></span>
                                <em>Open</em>
                            </span>
                        </Scout>
                    </Line>
                </div>
                <div className={v.floor}>
                    <div className={v.composer}>
                        <AttachIcon size={16} />
                        <span className={v.box}>
                            <span className={v.typed}>What stalled these trials?<span className={k.caret} /></span>
                            <span className={v.hint}>Ask Scout…</span>
                        </span>
                        <i><SendIcon size={14} /></i>
                    </div>
                </div>
            </div>
            <div className={v.fade} />

            {/* The tab beside the conversation: its name on the pane's bar,
                and the app it shows. */}
            <section className={v.pane}>
                <div className={v.paneBar}><span>Stalled trials</span><i><ExpandIcon size={16} /></i><i><CloseIcon size={16} /></i></div>
                <Widget className={v.inApp} />
            </section>
        </Vignette>
    );
}
