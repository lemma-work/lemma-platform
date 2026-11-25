import { LemmaBars } from "./frame";
import { Chapter, Figure, Section } from "./parts";

/** Chapter five: where people reach a Lemma. Figure 11. */
export function ChapterFive() {
    return (
        <>
            <Chapter n={5} />
            <Section id="ways">
                <p>A new tool usually means a new tab, and tabs get forgotten. A Lemma is reached from wherever people already are.</p>
                <Figure n={11} title="One Lemma, met in four places">
                    <div className="gb-ways" aria-hidden="true">
                        <div className="gb-pane">
                            <p className="gb-pane-k">in team chat</p>
                            <div className="gb-card gb-tile">
                                <p className="gb-tile-h"># sales</p>
                                <div className="gb-say"><i className="gb-av">S</i><p><b>a seller</b>@Sales, quote 40 chairs on last year&rsquo;s terms?</p></div>
                                <div className="gb-say"><i className="gb-av gb-av-l"><LemmaBars /></i><p><b>Sales</b>Drafted. It needs a manager&rsquo;s yes, because the discount is 18%.</p></div>
                            </div>
                        </div>
                        <div className="gb-pane">
                            <p className="gb-pane-k">by email</p>
                            <div className="gb-card gb-tile">
                                <p className="gb-tile-h">Re: Quote request</p>
                                <div className="gb-say"><i className="gb-av gb-av-l"><LemmaBars /></i><p><b>Sales</b>Here is the quote for 40 school chairs. One question before it goes out: should delivery be included?</p></div>
                            </div>
                        </div>
                        <div className="gb-pane">
                            <p className="gb-pane-k">in its apps</p>
                            <div className="gb-card gb-app">
                                <div className="gb-app-h"><span>Quotes waiting for a yes</span><span className="gb-count">1</span></div>
                                <div className="gb-app-r"><span>School chairs</span><span className="gb-num">3,936.00</span><span className="gb-flag">18%</span><span className="gb-btn">Approve</span></div>
                            </div>
                        </div>
                        <div className="gb-pane">
                            <p className="gb-pane-k">in your own AI tool</p>
                            <div className="gb-card gb-tile">
                                <p className="gb-tile-h">Connected to Sales, as you</p>
                                <div className="gb-say"><i className="gb-av">Y</i><p><b>you</b>What is waiting on me in Sales?</p></div>
                                <div className="gb-say"><i className="gb-av gb-av-ai" /><p><b>your assistant</b>One quote: school chairs, 18% off. It needs your yes.</p></div>
                            </div>
                        </div>
                    </div>
                </Figure>
                <p>Every Lemma answers email from the start, in the thread it was written to. It can be added to Slack, Microsoft Teams, WhatsApp and Telegram, and a question asked there is answered there. Questions and approvals work in all of them: as buttons where the platform has buttons, and as a plain reply where it does not. A yes does not wait for anyone to open anything.</p>
                <p>And once work is running in several Lemmas, one list shows what is waiting on you across all of them.</p>
            </Section>
            <Section id="outside">
                <p>Most work involves people who will never sign in: a customer, a candidate, a supplier. A Lemma can know them as contacts, so the people it works with outside the team are kept in one place and not scattered across inboxes.</p>
                <p>One Lemma can ask another. Sales can ask Support what was promised to a customer, and the answer comes back to the one that asked.</p>
                <p>If you already work in an AI tool of your own, such as Claude or ChatGPT, you can connect it to a Lemma. It then acts as you, with what you are allowed to do and nothing more, and disconnecting it takes effect at once.</p>
            </Section>
        </>
    );
}
