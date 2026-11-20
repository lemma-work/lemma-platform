"use client";

import type { Pod } from "@/data";
import { listed, type BuiltPlugin } from "@/site/plugins/catalog";
import { AccountsBlock } from "./accounts-block";
import { GlyphIcon } from "./glyphs";
import { InstallStrip } from "./install-strip";
import type { InstallRun } from "./install-run";
import type { PlaceKey } from "./reach";
import { PlaceLogo } from "./stage";

const NAMES: Record<PlaceKey, string> = { EMAIL: "Email", WHATSAPP: "WhatsApp", TELEGRAM: "Telegram", SLACK: "Slack" };

/** The first thing to ask each app's Lemma, said as somebody would. */
const FIRST_ASK: Record<string, string> = {
    mail: "What needs a reply from me today?",
    meetings: "Brief me before my next meeting",
    today: "What needs me today?",
    "people-map": "Who haven’t I followed up with?",
    tasks: "What’s on my list this week?",
    "deal-tracker": "Which deals moved this week?",
    "booking-page": "Make me a page for 30-minute calls",
    "lead-research": "Look up the people who wrote to me this week",
    "brand-kit": "Read my brand from my website",
    "deck-maker": "Make a deck about what we do",
};

/** "Personal is ready, Deepak." -- the whole Lemma, put together: its apps,
 *  where it can be reached, who is in it, a few first things to ask, and
 *  the way into its first app. */
export function ReadyStep({ pod, firstName, plugins, run, reach, shared, onOpen, onAsk }: {
    pod: Pod;
    firstName: string;
    plugins: readonly BuiltPlugin[];
    run: InstallRun;
    reach: readonly PlaceKey[];
    shared: boolean;
    onOpen: (app: string | null) => void;
    onAsk: (text: string) => void;
}) {
    const added = run.added;
    const first = added[0] ?? null;
    const going = plugins.length > 0 && !run.done;
    const who = firstName.trim() ? `, ${firstName.trim()}` : "";
    const places = reach.length > 0 ? reach : [];
    const chat = places.filter((key) => key !== "EMAIL").map((key) => NAMES[key]);
    const reachLine = places.length === 0
        ? ""
        : `You can reach me ${chat.length > 0 ? `on ${listed(chat)}${places.includes("EMAIL") ? " and by email" : ""}` : "by email"}, and I answer wherever you write.`;
    /* The Lemma is ready the moment it is made; its apps may still be on
       their way, and it says so rather than making anyone wait. */
    const appsLine = plugins.length === 0
        ? ""
        : going
            ? `${added.length > 0 ? `${listed(added.map((one) => one.name))} ${added.length === 1 ? "is" : "are"} in, and the rest are on their way.` : "Your apps are on their way."} You don’t have to wait: talk to me now, and I’ll tell you in our chat when they’re all set.`
            : `${listed(added.map((one) => one.name))} ${added.length === 1 ? "is" : "are"} in.`;
    const asks = added.slice(0, 2).map((one) => ({ text: FIRST_ASK[one.slug] ?? `Show me what ${one.name} can do`, where: `In ${one.name}`, slug: one.slug }));
    return (
        <>
            {going && <InstallStrip plugins={plugins} run={run} />}
            <h1 className="ar__t">{pod.name} is ready{who}.</h1>
            <p className="ar__model">{[appsLine, reachLine].filter(Boolean).join(" ") || "Ask me anything, and I’ll get to work."}</p>
            <div className="arw" aria-label={`What ${pod.name} has`}>
                <div>
                    <b>Apps</b>
                    <span className="arw__pills">
                        {plugins.length > 0
                            ? plugins.map((one) => <span key={one.slug} className="arw__pill" data-going={run.rows[one.slug]?.kind === "added" ? undefined : ""}><span className="arw__icon"><GlyphIcon slug={one.slug} size={12} /></span>{one.name}</span>)
                            : <span className="arw__pill arw__pill--plain">None yet</span>}
                    </span>
                </div>
                <div>
                    <b>Reach me</b>
                    <span className="arw__pills">
                        {places.length > 0
                            ? places.map((key) => <span key={key} className="arw__pill"><span className="arw__icon"><PlaceLogo place={key} size={12} /></span>{NAMES[key]}</span>)
                            : <span className="arw__pill arw__pill--plain">Inside Lemma</span>}
                    </span>
                </div>
                <div>
                    <b>People</b>
                    <span className="arw__pills"><span className="arw__pill arw__pill--plain">{shared ? "You and your team" : "Just you"}</span></span>
                </div>
            </div>
            <AccountsBlock pod={pod} added={added} heading="Connect what they read, to fill them" />
            {(asks.length > 0 || chat.includes("WhatsApp")) && (
                <>
                    <span className="ar__label">Try one of these first</span>
                    <div className="art">
                        {asks.map((ask) => (
                            <button key={ask.slug} type="button" className="art__try" onClick={() => onAsk(ask.text)}>
                                <span className="arw__icon arw__icon--big"><GlyphIcon slug={ask.slug} size={15} /></span>
                                <span className="art__text">{ask.text}<small>{ask.where}</small></span>
                                <span className="art__go" aria-hidden="true">→</span>
                            </button>
                        ))}
                        {chat.includes("WhatsApp") && (
                            <button type="button" className="art__try" onClick={() => onAsk("Send me the day’s plan on WhatsApp at 9 every weekday")}>
                                <span className="arw__icon arw__icon--big"><PlaceLogo place="WHATSAPP" size={15} /></span>
                                <span className="art__text">Send me the day’s plan on WhatsApp at 9<small>Every weekday</small></span>
                                <span className="art__go" aria-hidden="true">→</span>
                            </button>
                        )}
                    </div>
                </>
            )}
            <div className="ar__acts">
                <span className="ar__sum">{shared ? `Invite people to ${pod.name} from inside it.` : "To work with someone, start another Lemma and invite them."}</span>
                {first && <button type="button" className="ar__quiet" onClick={() => onOpen(null)}>Go to {pod.name}</button>}
                <button type="button" className="ar__btn ar__btn--primary" autoFocus onClick={() => onOpen(first?.app ?? null)}>
                    {first ? `Open ${first.name}` : `Open ${pod.name}`}
                </button>
            </div>
        </>
    );
}
