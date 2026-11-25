import { Chapter, Figure, Section, Statement } from "./parts";

/** What reaches you at each stage of handing a job over. */
const STAGES: { title: string; sent?: string; rows: [string, string][]; cap: string }[] = [
    {
        title: "It drafts, you send",
        rows: [["Draft: school chairs", "Send"], ["Draft: lab stools", "Send"], ["Draft: teacher desks", "Send"]],
        cap: "Nothing leaves without you, and what you change shows where it is weak.",
    },
    {
        title: "It sends the routine ones",
        sent: "9 sent today",
        rows: [["School chairs, 18% off", "Review"]],
        cap: "Anything over a limit you set still waits for a yes.",
    },
    {
        title: "You see the exceptions",
        sent: "41 sent this week",
        rows: [["Unsure: asks for a delivery date", "Answer"]],
        cap: "You stop reading the ones that went well.",
    },
];

/** Chapter four: how to start. Figure 10. */
export function ChapterFour() {
    return (
        <>
            <Chapter n={4} />
            <Section id="small">
                <p>Nobody builds a factory in an afternoon. Start with one job you keep doing: the follow-ups after a call, the quote that takes an hour, the report three people put together every Monday. Pick one you do badly today. It gives you a real test, and something to compare the result with.</p>
                <p>Making the Lemma takes less time than choosing the job. It needs a name, and nothing else is required. It opens on a page with a few ways to begin: ask it for what you need, bring in what you already have, or start from a template.</p>
            </Section>
            <Section id="nodev">
                <p>Most of what makes software hard to run has nothing to do with the work. It is the database, the sign-in, the permissions, the scheduled jobs, the passwords and the hosting. A team that wants better quotes should never have to learn any of it.</p>
                <p>In a Lemma that layer is already there. A table comes with its rules about who can see it. An automation comes with a record of every run. An app opens for the people already on the team, as themselves. So building comes down to saying what you need and correcting what you get.</p>
                <p>Developers can go further with the command line and the SDK, in the same Lemma, on the same tables.</p>
            </Section>
            <Section id="trust">
                <p>Not trusting it on the first day is reasonable. Trust comes from a record, and the record starts empty. So hand the job over in steps, and watch how much still needs you.</p>
                <Figure n={10} title="What still needs you, as a job is handed over">
                    <ol className="gb-stages">
                        {STAGES.map((stage, i) => (
                            <li key={stage.title}>
                                <p className="gb-stage-k"><b>{i + 1}</b>{stage.title}</p>
                                <div className="gb-card gb-inbox" aria-hidden="true">
                                    <p className="gb-inbox-h"><span>Needs you</span><i>{stage.rows.length}</i></p>
                                    {stage.sent && <p className="gb-inbox-sent">{stage.sent}</p>}
                                    {stage.rows.map(([what, act]) => (
                                        <p className="gb-inbox-r" key={what}><span>{what}</span><span className="gb-btn">{act}</span></p>
                                    ))}
                                </div>
                                <p className="gb-cap">{stage.cap}</p>
                            </li>
                        ))}
                    </ol>
                </Figure>
                <p>Every step can be taken back. If the drafts get worse, go back to sending them yourself. And the second job goes faster than the first, because the customers, the prices and the people are already there.</p>
            </Section>
            <Statement label="the first step" line="Start with one thing you repeat." />
        </>
    );
}
