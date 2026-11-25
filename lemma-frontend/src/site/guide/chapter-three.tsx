import { Chapter, Figure, Section, Statement } from "./parts";

/** The layers of a Lemma, top to bottom, each with what it holds for the quote. */
const LAYERS: { name: string; role: string; items: { t: string; code?: boolean; n?: number }[] }[] = [
    { name: "apps", role: "where people see the work and step in", items: [{ t: "Quotes waiting for a yes", n: 3 }, { t: "Pipeline this week" }] },
    { name: "automations", role: "what decides when it happens", items: [{ t: "When a quote request arrives" }, { t: "Every Monday at 08:00" }] },
    { name: "functions and agents", role: "what does the work", items: [{ t: "quote_total", code: true }, { t: "check_discount", code: true }, { t: "Quote drafter, an agent" }] },
    { name: "tables and files", role: "what the work is about", items: [{ t: "customers" }, { t: "price list" }, { t: "quotes" }, { t: "price-list.pdf" }, { t: "contracts" }] },
];

/** The quote's run, step by step, as the record keeps it. */
const RUN: { at: string; by: string; text: string; hold?: boolean }[] = [
    { at: "09:12", by: "automation", text: "A quote request arrives, and the work starts." },
    { at: "09:12", by: "agent", text: "Reads the request and this customer’s history, and drafts the quote from today’s prices." },
    { at: "09:13", by: "function", text: "Totals it at 3,936.00. The 18% discount is over the 15% limit." },
    { at: "09:13", by: "waiting", text: "Stops, and appears in the manager’s list. Nothing has gone out.", hold: true },
    { at: "11:40", by: "manager", text: "Changes one line, and says yes." },
    { at: "11:40", by: "record", text: "Sent. Who drafted it, checked it, changed it and approved it is kept." },
];

/** Chapter three: what a Lemma is made of, followed through one quote. Figures 7 to 9. */
export function ChapterThree() {
    return (
        <>
            <Chapter n={3} />
            <Section id="layers">
                <p>Whatever the job, a Lemma is built from the same four layers. At the bottom are the things the work is about. Above them, the workers that act on those things. Above that, what decides when they act. On top, the screens where people see the work and step in. Two things run through all four: who may do what, and a record of what was done.</p>
                <Figure n={7} title="What a Lemma is made of, filled in for quotes">
                    <div className="gb-stack">
                        <div className="gb-layers">
                            {LAYERS.map((layer) => (
                                <div className="gb-layer" key={layer.name}>
                                    <p className="gb-layer-k"><b>{layer.name}</b><span>{layer.role}</span></p>
                                    <p className="gb-layer-v">
                                        {layer.items.map((item) => (
                                            <span className="gb-chip" key={item.t}>
                                                {item.code ? <code>{item.t}</code> : item.t}
                                                {item.n !== undefined && <i>{item.n}</i>}
                                            </span>
                                        ))}
                                    </p>
                                </div>
                            ))}
                        </div>
                        <p className="gb-rail"><span>who may do what</span><span>what was done</span></p>
                    </div>
                </Figure>
            </Section>
            <Section id="things">
                <p>Start with what the work is about: the customers, the price list, the quotes, and the emails and documents around them. A Lemma keeps these in two forms. Tables hold things that come in rows, one customer or one quote each, with the same columns every time. Files hold documents: the contract, the price list as it was sent, the notes from a call.</p>
                <p>Three things matter more than where the data is stored. <b className="gb-lead">A table keeps its shape:</b> if a quote must have a customer, a quote without one is refused, whether a person or the AI tried to add it. <b className="gb-lead">Files can be read:</b> an uploaded contract becomes text that can be searched, so the AI can use what is in it, and so can you. <b className="gb-lead">Rows know who is asking:</b> a seller can see their own deals while an admin sees all of them.</p>
                <p>This is the one copy. Everything above it reads from it and writes to it.</p>
            </Section>
            <Section id="workers">
                <p>Two kinds of worker act on that copy, and the difference between them is the most useful idea in this guide.</p>
                <p><b className="gb-lead">A function</b> does one step exactly the same way every time. It totals the quote from the price list, or checks the discount against the limit. Given the same quote, it gives the same answer next year.</p>
                <p><b className="gb-lead">An agent</b> does the steps that need judgment. It reads a customer&rsquo;s email and works out what they want, then drafts a reply in the right tone. No two emails are alike, so no fixed rule covers them.</p>
            </Section>
            <Statement label="the rule" line="If you can write the step down exactly, make it a function. If someone would have to read and think, give it to an agent." />
            <section className="gb-s gb-s-tight">
                <div className="gb-g">
                    <p className="gb-k gb-lab" aria-hidden="true" />
                    <div className="gb-c">
                        <Figure n={8} title="Two kinds of worker on the same quotes">
                            <div className="gb-pair">
                                <div className="gb-pane">
                                    <p className="gb-pane-k">a function</p>
                                    <div className="gb-card gb-fn">
                                        <p className="gb-fn-h"><code>quote_total(quote)</code></p>
                                        <div className="gb-fn-rows">
                                            <span>40 × 120.00</span><span className="gb-num">4,800.00</span>
                                            <span>less 18%</span><span className="gb-num">−864.00</span>
                                            <b>total</b><b className="gb-num">3,936.00</b>
                                            <span>limit 15%</span><span className="gb-num gb-no">needs a yes</span>
                                        </div>
                                    </div>
                                    <p className="gb-cap">The same quote gives the same answer, today or next year.</p>
                                </div>
                                <div className="gb-pane">
                                    <p className="gb-pane-k">an agent</p>
                                    <div className="gb-card gb-agent">
                                        <div className="gb-io"><p className="gb-ask">&ldquo;Same as last year, but 40 instead of 30?&rdquo;</p><p className="gb-ans">Repeats last year&rsquo;s terms for 40 chairs.</p></div>
                                        <div className="gb-io"><p className="gb-ask">&ldquo;Can you match a cheaper quote we were sent?&rdquo;</p><p className="gb-ans">Asks a manager before offering anything.</p></div>
                                        <div className="gb-io"><p className="gb-ask">&ldquo;Will they arrive before term starts?&rdquo;</p><p className="gb-ans">Answers without promising a date.</p></div>
                                    </div>
                                    <p className="gb-cap">No two emails are alike, so each one is read and judged.</p>
                                </div>
                            </div>
                        </Figure>
                        <p>An agent can do arithmetic, and it will get the total right nearly every time. On a quote, nearly is the problem. So the agent drafts and the function checks. That is the job tests do for code: a check that runs every time and cannot be talked round.</p>
                        <p>Both work under the same rule as people. They get only the access they were given, and never more than the person they act for.</p>
                    </div>
                </div>
            </section>
            <Section id="automations">
                <p>Work that waits to be asked for is still a chat. An automation starts it without anyone having to remember. There are four ways for work to start:</p>
                <ul className="gb-dots">
                    <li><span><b className="gb-lead">A time.</b> Every Monday at eight, put the report together.</span></li>
                    <li><span><b className="gb-lead">A change.</b> A row is added to a table, say a new quote request.</span></li>
                    <li><span><b className="gb-lead">Something outside.</b> An app you have connected reports that something happened.</span></li>
                    <li><span><b className="gb-lead">A person.</b> Someone starts it by hand.</span></li>
                </ul>
                <p>An automation can also run steps in order: draft the quote, check it, and if the discount is over the limit, stop and ask a named person. The stop is the important part. The work can wait an hour or a week for the answer, and carries on from where it stopped.</p>
                <p>Because nobody is watching, an automation has to be dependable in dull ways. It does the work once even when the same signal arrives twice. Every run is listed with how it went. One that keeps failing is turned off and the people responsible are told why, so it never fails quietly for a month.</p>
            </Section>
            <Section id="apps">
                <p>Apps are the layer people touch: a screen made for one job. It would be easy to treat them as decoration. They are where most steering happens. Someone opens the list and sees three quotes waiting. They change a line in one, approve two and send the third back. Before, during and after, on the real work, in a minute, by the right person.</p>
                <p>An app is not a report about the work. It reads and writes the same tables as everything else, as the person using it, so what they can see and change there is exactly what they may see and change anywhere.</p>
                <p>Chat stays. It is still the best way to ask for something new. It stops being the only way in.</p>
            </Section>
            <Section id="rules">
                <p><b className="gb-lead">Who may do what.</b> Every table, file, function and app has a stated reach: one person, a few people, a role, or the whole team. Narrowing it takes access away at once. Whatever an agent or automation does, it does with the authority of the person it is working for, and nothing is deleted unless someone allowed it.</p>
                <p><b className="gb-lead">What was done.</b> Every action carries the name of who or what took it. Every run can be opened and followed step by step. A run can be stopped, and one that failed can be picked up again from the step that failed, unless that step would have to ask a person or pay for an agent’s work a second time; then you start a new run.</p>
                <p>These two are what make it safe to let the work run. A team can hand over the doing because it keeps the control.</p>
            </Section>
            <Section id="quote">
                <p>Here is the quote from chapter one again, this time in a Lemma.</p>
                <Figure n={9} title="One quote, as the record keeps it" note="It waits at the fourth step for as long as it takes. Nothing goes out without the yes.">
                    <ol className="gb-run">
                        {RUN.map((step) => (
                            <li key={step.text} className={step.hold ? "gb-hold" : undefined}>
                                <time>{step.at}</time>
                                <span className="gb-by">{step.by}</span>
                                <p>{step.text}</p>
                            </li>
                        ))}
                    </ol>
                </Figure>
                <p>The same four people took part as before. Nobody pasted anything into a chat, and the person who owns the pricing rule made the pricing call. If the manager wants their change to hold for every quote, they say so once, and it is written down where the next run will read it.</p>
            </Section>
        </>
    );
}
