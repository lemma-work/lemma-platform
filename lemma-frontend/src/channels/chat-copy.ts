import { personName, sayList, type ChannelDetail, type Member, type SourcePlace } from "./model";
import { platformName } from "./sources";

/** The words a chat that lives on a channel says about itself: who it is
 *  with, who answers in it and how to ask, and where a message sent in it
 *  goes. Every one is a sentence a person would say, never the model's
 *  vocabulary -- no "channel", "source" or "line". */

/** Who a shared chat is with: "Just you", "You and Priya",
 *  "You, Priya and 3 more". The agents in it are not people. */
export function sharedWith(members: readonly Member[], me: string | null): string {
    const people = members.filter((one) => one.person.kind !== "AGENT");
    const youIn = people.some((one) => one.person.kind === "MEMBER" && one.person.id === me);
    const others = people
        .filter((one) => !(one.person.kind === "MEMBER" && one.person.id === me))
        .map((one) => personName(one.person, me));
    if (others.length === 0) return youIn ? "Just you" : "Nobody yet";
    const named = others.length > 3 ? [...others.slice(0, 2), others.length - 2 + " more"] : others;
    return sayList(youIn ? ["You", ...named] : named);
}

/** Who answers here, and how to ask: the one sentence a person needs before
 *  they write. In their default chat the teammate answers everything; in a
 *  group it answers when named, unless it was set to answer every message. */
export function answersHint(channel: Pick<ChannelDetail, "kind" | "members" | "sources">, teammate: string): string {
    if (channel.kind === "DIRECT") {
        const where = [...new Set(channel.sources.map((source) => platformName(source.platform)))];
        return teammate + " answers everything you send here" + (where.length ? ", and on " + sayList(where) : "") + ".";
    }
    const agents = channel.members.filter((one) => one.person.kind === "AGENT");
    const every = agents.filter((one) => one.answers === "EVERY_LINE").map((one) => one.person.name ?? "An agent");
    if (every.length) return sayList(every) + (every.length === 1 ? " answers" : " answer") + " every message.";
    const named = agents.map((one) => one.person.name).find(Boolean) ?? teammate;
    return "Type @ to ask " + named + ".";
}

/** One outside place, by what people call it there: "#launch on Slack",
 *  "Acme × Northwind on WhatsApp", "Telegram". */
export function placeName(place: Pick<SourcePlace, "platform" | "title">): string {
    const platform = platformName(place.platform);
    return place.title ? place.title + " on " + platform : platform;
}

/** The place a message goes to when nobody picks one: where the newest
 *  message came from. */
export function latestPlace(places: readonly SourcePlace[]): SourcePlace | null {
    const live = places.filter((place) => !place.pending);
    return live.reduce<SourcePlace | null>((best, place) => {
        if (!best) return place;
        return (place.lastInboundAt ?? "") > (best.lastInboundAt ?? "") ? place : best;
    }, null);
}

/** Where a message sent here goes, besides this chat: "Also goes to
 *  #launch on Slack", or to a contact, "Goes to Dana Ruiz by email". */
export function goesTo(channel: Pick<ChannelDetail, "kind" | "person">, place: Pick<SourcePlace, "platform" | "title">): string {
    if (channel.kind === "CONTACT") {
        const who = channel.person?.name ?? "them";
        const how = ["EMAIL", "RESEND", "GMAIL", "OUTLOOK"].includes(place.platform.toUpperCase()) ? "by email" : "on " + platformName(place.platform);
        return "Goes to " + who + " " + how;
    }
    return "Also goes to " + placeName(place);
}
