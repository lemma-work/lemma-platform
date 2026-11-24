import type { ReactNode } from "react";

import { Chapter, Figure, Section, Statement } from "./parts";

/** The five things software work already had, each with how it looks on a code change and on a quote. */
const FIVE: [string, ReactNode, ReactNode][] = [
    ["One copy", <><code>main</code>, the one repository everyone works on</>, <>One row in the quotes table</>],
    ["Who can change it", <>Six people can merge</>, <>Sales can edit. Managers approve.</>],
    [
        "Checks that run themselves",
        <><span className="gb-ok">✓</span> 214 tests passed</>,
        <><span className="gb-ok">✓</span> Total matches the price list<br /><span className="gb-no">✕</span> 18% is over the 15% limit</>,
    ],
    ["A yes before it goes out", <>Approved by a reviewer</>, <>Waiting for a manager</>],
    ["A record", <>Merged. Named, dated, and can be undone.</>, <>Drafted by an agent, changed by a manager, sent</>],
];

/** Chapter two: why AI took off in software first. Figure 6 and the four tests. */
export function ChapterTwo() {
    return (
        <>
            <Chapter n={2} />
            <Section id="code">
                <p>There is one kind of work where AI already runs on its own. A developer hands an agent a task in the evening and reviews the finished change in the morning. The agent wrote the code, ran the tests and asked for a review.</p>
                <p>Models are good at code, but that is not the whole reason. Software work was already arranged so that an AI could step into it. Long before AI, engineers had built five things for each other.</p>
                <Figure n={6} title="The same five things, on a code change and on a quote">
                    <div className="gb-five">
                        <ol className="gb-five-k" aria-hidden="true">
                            {FIVE.map(([label]) => <li key={label}>{label}</li>)}
                        </ol>
                        <div className="gb-card gb-five-col">
                            <div className="gb-five-h"><span>Fix rounding in invoice totals</span><span className="gb-tag">code</span></div>
                            {FIVE.map(([label, code]) => <p key={label} data-k={label}>{code}</p>)}
                        </div>
                        <div className="gb-card gb-five-col gb-five-us">
                            <div className="gb-five-h"><span>Quote, school chairs</span><span className="gb-tag">in a Lemma</span></div>
                            {FIVE.map(([label, , quote]) => <p key={label} data-k={label}>{quote}</p>)}
                        </div>
                    </div>
                </Figure>
                <p>Now look at how a quote is handled in most places today. The facts are in inboxes and spreadsheets. Whoever has the file can change it. Nothing checks it. It goes out when someone presses send, and afterwards nobody can say exactly what was sent, or why. An AI dropped into that has nothing to hold on to, so it ends up in a chat window off to the side.</p>
                <p className="gb-pull gb-wide">Code had a shape. Most work does not have one yet.</p>
            </Section>
            <Section id="cheap">
                <p>For as long as there have been offices, doing the work was the expensive part. Writing the quote, chasing the reply, putting the Monday report together: each cost an hour of someone&rsquo;s day. AI has made that hour cheap.</p>
                <p>What stays expensive is everything around the hour. Knowing what a good quote looks like here. Having the customer&rsquo;s history to hand. Checking the result, and catching the one that should never go out.</p>
                <p>Those costs can be paid once. A pricing rule written down once applies to every quote after it. A check set up once runs every time. Put the effort into setting the work up, and everything that comes off the line is better for it. That is the oldest idea in manufacturing, and it is why we use the word factory.</p>
            </Section>
            <Section id="factory">
                <p>A factory, in this guide, is a team&rsquo;s everyday work set up so that four things are true.</p>
                <ol className="gb-tests gb-wide">
                    <li><b>shared</b><span>The work lives in one place, and everyone who does it works there.</span></li>
                    <li><b>running</b><span>It starts itself, on a schedule or when something arrives, and comes to a person only when it needs one.</span></li>
                    <li><b>checked</b><span>Anything that matters waits for a yes, and every action has a name on it.</span></li>
                    <li><b>improving</b><span>What it is taught is written down where everyone can read it, so each run starts ahead of the last.</span></li>
                </ol>
                <p>It takes all four. Shared but not running is a tidy database. Running but not shared is one person&rsquo;s script. Running but not checked is something nobody will trust with anything that matters.</p>
            </Section>
            <Statement label="the word" line="In Lemma, each factory is called a Lemma." />
            <Section id="not">
                <p><b className="gb-lead">Not an assistant.</b> An assistant makes one person&rsquo;s day lighter, and that is worth having. But if the result is &ldquo;I feel less swamped&rdquo;, you have an assistant. If it is &ldquo;we did more, and did it better&rdquo;, you have a factory.</p>
                <p><b className="gb-lead">Not a line without people.</b> Some use the word for software that is written and shipped with nobody looking. We mean the opposite. People decide what the work is, approve what goes out and fix what is wrong. The routine goes to the line so the judgment can stay with them.</p>
                <p><b className="gb-lead">Not a flowchart.</b> Boxes and arrows that call a model at step three are an automation. They give the work nowhere to live and people no screen to open, so when something breaks, nobody can see where.</p>
            </Section>
            <Section id="behind">
                <p>The word that matters in &ldquo;a factory behind every person&rdquo; is behind. The person stays in front. The seller still owns the customer. The recruiter still makes the offer. Behind them, the follow-ups go out, the quote is drafted from the right prices, and the report is ready on Monday.</p>
                <p>On a team, people end up doing three things, and one person can do all three. Everyone asks. Whoever knows the subject steers. Someone says yes.</p>
            </Section>
        </>
    );
}
