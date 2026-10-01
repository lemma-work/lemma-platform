import { FACES } from "@/marketing/product-pages";
import { Vignette } from "./vignette";
import k from "./kit.module.css";
import v from "./apps-live-row.module.css";

/* Deal desk's FOLLOW-UPS rail, row for row as Remy's app lists it
   (marketing/apps/remy.tsx): the account, its due date or a tick when the
   commitment is done, the buyer, the next commitment. */
type Item = { name: string; due: string; person: string; task: string; done?: boolean };
const ITEMS: Item[] = [
    { name: "Northstar", due: "09-25", person: "Anita Rao", task: "Share approved security overview" },
    { name: "Cedar", due: "09-25", person: "Leena Shah", task: "Check Thursday’s evaluation result" },
    { name: "Harbor", due: "✓", person: "Mira Chen", task: "Confirm onboarding owner has the scope", done: true },
];
/* The row Remy writes into the table the app reads. */
const BIRCH: Item = { name: "Birch", due: "09-28", person: "Owen Park", task: "Prepare discovery questions" };

function Row({ item, className }: { item: Item; className?: string }) {
    return (
        <div className={`${v.item} ${className ?? ""}`}>
            <span className={v.itemTop}><span className={v.name}>{item.name}</span><span className={item.done ? `${v.due} ${v.done}` : v.due}>{item.due}</span></span>
            <span className={v.person}>{item.person}</span>
            <span className={v.task}>{item.task}</span>
        </div>
    );
}

/** A live list in an app: Deal desk is open on Northstar, its follow-ups
 *  rail counting two open. You ask Remy, in the conversation beside it, to
 *  add a follow-up for Birch; Remy writes the row, and it slides into the
 *  rail you already have open, with nothing reloading.
 *
 *  Beats: 1 Remy's reply, "Added a follow-up for Birch."; 2 the Birch row
 *  slides into the rail under a highlight and the count goes from 2 to 3;
 *  3 the highlight settles, the row staying softly marked as new.
 *
 *  On a phone it shows the rail, where Birch's row lands, with the edge of
 *  Remy's panel beside it. */
export function AppsLiveRow() {
    return (
        <Vignette className={v.root} width={640} height={440} phone={{ x: 0, width: 336 }} beats={[1700, 1400, 2000]} hold={3000}
            label="Deal desk, the app Remy built, is open on Northstar. Its follow-ups list counts two open, Northstar and Cedar, with Harbor done. In the conversation beside it, you tell Remy that Birch wants a discovery call next week and ask for a follow-up, and Remy replies: Added a follow-up for Birch. A Birch row, Prepare discovery questions, due 09-28, slides into the list you already have open, and the count goes from 2 to 3. Nothing else on the screen reloads.">
            <div className={k.bar}><img className={k.face} src={FACES.Remy} alt="" /><b>Deal desk</b><span>· an app</span><i /><i /><i /></div>
            <div className={`${k.body} ${v.app}`}>
                <header className={v.head}>
                    <span className={v.back}>← All accounts</span>
                    <span className={v.avatar}>N</span>
                    <span className={v.account}><span>Northstar</span><small>Logistics · 42-person operations team</small></span>
                    <span className={v.stage}>Stage<span className={v.select}>Procurement</span></span>
                    <span className={v.value}>$12,000<small>Annual value</small></span>
                </header>

                <div className={v.detail}>
                    <aside className={v.rail}>
                        <p className={v.railHead}>
                            <span>Follow-ups</span>
                            <span className={v.count}><span className={v.two}>2</span><span className={v.three}>3</span></span>
                        </p>
                        {ITEMS.map((item, index) => <Row key={item.name} item={item} className={index === 0 ? v.on : undefined} />)}
                        <div className={v.arrive}><Row item={BIRCH} className={v.birch} /></div>
                    </aside>

                    <section className={v.reader}>
                        <nav className={v.tabs}><span className={v.tabOn}>Conversation</span><span>Notes</span></nav>
                        <div className={v.mailHead}>
                            <span className={`${v.avatar} ${v.small}`}>AR</span>
                            <span className={v.from}><span>Anita Rao</span><small>anita@northstar.example.invalid</small></span>
                            <time>Tuesday · 14:32</time>
                        </div>
                        <p className={v.subject}>Re: Security overview for procurement</p>
                        <p className={v.mail}>Thanks for the walkthrough. Can you send the security overview before our procurement review on Friday? Raj also needs to understand the deployment options.</p>
                        <div className={v.draft}><span>Reply draft</span><em>Needs your review</em></div>
                    </section>
                </div>

                <div className={v.chat}>
                    <div className={v.chatHead}>
                        <img className={k.face} src={FACES.Remy} alt="" />
                        <span className={v.chatTitle}><span>Remy</span><small>On Deal desk</small></span>
                        {/* Open full size, and minimise: the panel's own two controls. */}
                        <span className={v.ctl}><svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M10 2.5h3.5V6M6 13.5H2.5V10M13.5 10v3.5H10M2.5 6V2.5H6" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
                        <span className={v.ctl}><svg width="14" height="14" viewBox="0 0 16 16" aria-hidden="true"><path d="M13 6H10V3M3 10h3v3M10 13v-3h3M6 3v3H3" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg></span>
                    </div>
                    <div className={v.thread}>
                        <p className={v.asked}>Birch wants a discovery call next week. Add a follow-up?</p>
                        <div className={v.reply}>
                            <img className={k.face} src={FACES.Remy} alt="" />
                            <div>
                                <span className={v.replyWho}>Remy</span>
                                <span className={v.steps}>
                                    <i><svg width="9" height="9" viewBox="0 0 12 12" aria-hidden="true"><path d="M2.5 6.2l2.3 2.3 4.7-5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg></i>
                                    1 step
                                    <svg className={v.chev} width="10" height="10" viewBox="0 0 12 12" aria-hidden="true"><path d="M4.5 2.5L8 6l-3.5 3.5" fill="none" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" /></svg>
                                </span>
                                <p className={v.replyText}>Added a follow-up for Birch.</p>
                            </div>
                        </div>
                    </div>
                    <div className={v.composer}><span>Ask Remy…</span><span className={v.send}><svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true"><path d="M6 10V2.5M2.8 5.5L6 2.3l3.2 3.2" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" /></svg></span></div>
                </div>
            </div>
        </Vignette>
    );
}
