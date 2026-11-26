import { LemmaBars } from "./frame";
import { Chapter, Figure, Section, Statement } from "./parts";

/** The same Lemma, drawn the same way inside each of the three places it can run. */
function SameLemma() {
    return (
        <div className="gb-same">
            <p className="gb-same-h"><i className="gb-av gb-av-l"><LemmaBars /></i>Sales</p>
            <p><span>Apps</span><span className="gb-num">3</span></p>
            <p><span>Tables</span><span className="gb-num">4</span></p>
            <p><span>Automations</span><span className="gb-num">2</span></p>
            <p><span>Taught</span><span className="gb-num">12</span></p>
        </div>
    );
}

const PLACES: { bar: string; kind: string; title: string; text: string }[] = [
    { bar: "lemma.work", kind: "gb-win-web", title: "Our cloud", text: "Sign up and start. Nothing to install." },
    { bar: "Lemma · Local mode", kind: "gb-win-mac", title: "Your Mac", text: "The app installs what it needs and runs on your machine." },
    { bar: "your-server", kind: "gb-win-srv", title: "Your own server", text: "One Linux machine with Docker, for the whole team." },
];

/** Chapter six: why a Lemma belongs to the team that runs it. Figure 12, and the close. */
export function ChapterSix() {
    return (
        <>
            <Chapter n={6} />
            <Section id="where">
                <p>A team&rsquo;s way of working ends up inside a Lemma: its customers, its rules, and what it has been taught about doing the job well. That should not be something a vendor can take away, and we are a vendor.</p>
                <p>So Lemma runs on our cloud, on your own Mac, or on a server you control, and it is the same software in all three.</p>
                <Figure n={12} title="The same Lemma, wherever it runs">
                    <div className="gb-places">
                        {PLACES.map((place) => (
                            <div className="gb-place" key={place.title}>
                                <div className={`gb-card gb-win ${place.kind}`} aria-hidden="true">
                                    <p className="gb-win-bar"><i /><i /><i /><span>{place.bar}</span></p>
                                    <SameLemma />
                                </div>
                                <h4>{place.title}</h4>
                                <p>{place.text}</p>
                            </div>
                        ))}
                    </div>
                </Figure>
                <p>The core is open source under AGPLv3, and the SDKs, the command line and the skills are Apache-2.0. You can read every line of what runs your work.</p>
            </Section>
            <Section id="model">
                <p>No one company will have the best model for every job, or keep it for long. A Lemma works with the model you choose: a subscription you already pay for, Lemma&rsquo;s hosted models, or your own key. Each agent in a Lemma can use a different one.</p>
            </Section>
            <Section id="export">
                <p>A Lemma can be exported as a bundle and brought in somewhere else. The bundle never includes a password or a connected account. It records what kind of account is needed, so whoever brings it in knows what to connect, and bringing one in shows the plan before anything changes.</p>
                <p>That is also how good work travels. A Lemma someone else made is one you can read, change and make your own.</p>
            </Section>
            <Statement label="what stays the same" line="A factory behind every person.">
                <p>This guide has been about where AI sits: out of one person&rsquo;s chat, and into the work a team shares. Almost everything else stays as it was. The seller still knows the customer. Someone still decides what good looks like, and says yes. People still want to spend their day on the part of the work that is theirs. A factory behind each of them is how they get to.</p>
            </Statement>
        </>
    );
}
