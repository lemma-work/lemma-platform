import { CATALOG_ORDER, MORE_PLUGINS } from "./more";
import { PARTS } from "./parts";
import type {
    BundleParts,
    Gap,
    Plugin,
    PluginSlug,
    Stage,
    TableKind,
} from "./types";

/**
 * The first plugins: each a Lemma that already runs, with the apps it starts
 * with. What it is made of is in `parts.ts`, read from its bundle; this file
 * holds what it is for, which shared tables it makes and uses, how it stands
 * with the other plugins, and what it still lacks.
 *
 * `makes` and `uses` say what the bundle does today, not what the catalog
 * hopes for: a link that is drawn but not built is a `planned` link and a gap.
 */
const TEN: Plugin[] = [
    {
        slug: "people-map",
        name: "People map",
        kind: "base",
        jobs: ["everyone", "sales", "founder"],
        headline: "Everyone you deal with, kept for you",
        lede: "Reads your mail and calendar and keeps the people and companies you work with: who they are, when you last spoke, and what each of you owes the other.",
        does: [
            "A page for each person: where things stand, with sources",
            "Promises both ways, read from mail, with the reply drafted",
            "A brief before every meeting with someone outside",
        ],
        makes: ["person", "company", "touch", "commitment", "meeting"],
        uses: [],
        install: null,
        links: [
            {
                slug: "deal-tracker",
                state: "works",
                why: "Its deals name the people and companies kept here.",
            },
            {
                slug: "smart-inbox",
                state: "clashes",
                why: "Same mail engine under the same names; whichever installs last overwrites the other.",
            },
            {
                slug: "booking-page",
                state: "planned",
                why: "A booking should land here as a person and a meeting.",
            },
        ],
        gaps: [
            {
                kind: "split-from-bundle",
                text: "People map and Deal tracker are one bundle today. Splitting them means Deal tracker's deal signals stop being written by People map's triage.",
            },
            {
                kind: "table-mismatch",
                text: "Conversations are two tables, thread and message, and only from mail. Chats are not conversations yet.",
            },
            {
                kind: "not-yet-shared",
                text: "Follow-ups and Meetings ride inside this plugin rather than standing as their own.",
            },
        ],
    },
    {
        slug: "deal-tracker",
        name: "Deal tracker",
        kind: "job",
        jobs: ["sales", "founder"],
        headline: "Deals that move when the conversation does",
        lede: "Proposes a new deal or a stage change from what was said in mail, shows the sentence it came from, and waits for your yes. The board is the team's; the mail stays yours.",
        does: [
            "The team's deals on a board you drag",
            "Changes read from conversations, waiting for a yes",
            "Each deal marked on track, quiet or at risk every night",
        ],
        makes: ["deal"],
        uses: ["person", "company", "touch"],
        install: null,
        basis: [
            {
                template: "smart-inbox",
                text: "An earlier Smart Inbox, for Gmail only, installs from GitHub today.",
            },
        ],
        missing: ["Pipedrive"],
        links: [
            {
                slug: "people-map",
                state: "works",
                why: "Needs it: deals are proposed from its conversations and name its people.",
            },
        ],
        gaps: [
            {
                kind: "split-from-bundle",
                text: "Has no agent or function of its own yet: People map's triage writes its signals and People map's nightly pass sets deal health.",
            },
            {
                kind: "name-collision",
                text: "Its signal table holds proposed deal changes, but signal is the shared name for engineering alerts, which PR review also makes.",
            },
        ],
    },
    {
        slug: "deck-maker",
        name: "Deck maker",
        kind: "tool",
        jobs: ["everyone", "sales", "founder", "marketing"],
        headline: "A deck from a sentence, edited by asking",
        lede: "Give it a topic. It drafts an outline for you to approve, writes the deck card by card, changes the whole deck when you ask in plain words, and exports PowerPoint.",
        does: [
            "An outline first, so you agree on the story",
            "Cards written one at a time as you watch",
            "Restyle, retone or reorder the deck by asking",
        ],
        makes: [],
        uses: [],
        install: null,
        links: [
            {
                slug: "brand-kit",
                state: "planned",
                why: "Decks keep their own themes; reading your Brand is not built.",
            },
        ],
        gaps: [
            {
                kind: "set-up-outside-import",
                text: "Agent permissions live in loose payload files applied by hand, not in the agents themselves.",
            },
        ],
    },
    {
        slug: "brand-kit",
        name: "Brand kit",
        kind: "base",
        jobs: ["everyone", "marketing"],
        headline: "Your brand, learned from your website",
        lede: "Give it your site. It comes back with your colours, type, logos and voice, each with where it found them, then makes on-brand pieces from a chat or the studio.",
        does: [
            "Colours, fonts and logos read from your own pages",
            "Every choice shows the page it came from",
            "One-pagers and social posts made on the brand",
        ],
        makes: ["brand"],
        uses: [],
        install: { owner: "deepak-jha-kgp", repo: "draper" },
        links: [
            {
                slug: "deck-maker",
                state: "planned",
                why: "Decks should read the brand instead of their own themes.",
            },
            {
                slug: "content-calendar",
                state: "planned",
                why: "Posts should be drafted on the brand's voice.",
            },
        ],
        gaps: [
            {
                kind: "set-up-outside-import",
                text: "Its playbooks and starter templates are copied in by a script, not by the import.",
            },
        ],
    },
    {
        slug: "content-calendar",
        name: "Content calendar",
        kind: "job",
        jobs: ["marketing", "founder"],
        headline: "From a passing idea to a post",
        lede: "Catch an idea in a few words. It links it to what you said before, researches it, drafts in your voice and adapts the draft for each channel. You approve every step.",
        does: [
            "Ideas linked to what you've already said",
            "Research with quotes and sources before drafting",
            "One draft, adapted for LinkedIn, X and a newsletter",
        ],
        makes: ["content"],
        uses: [],
        install: null,
        basis: [
            {
                text: "Lemma's Content starter, offered in a new Lemma, keeps two posts going out every week.",
            },
        ],
        links: [
            {
                slug: "brand-kit",
                state: "planned",
                why: "Drafts should follow the brand's voice; today it keeps its own.",
            },
        ],
        gaps: [
            {
                kind: "missing-piece",
                text: "No calendar yet: drafts carry no publish date and nothing schedules a post.",
            },
            {
                kind: "missing-piece",
                text: "Nothing runs on its own: no workflows or schedules, so agents work only when the app asks.",
            },
            {
                kind: "undeclared-account",
                text: "Capture from Telegram or voice and posting to LinkedIn or X are described but not connected.",
            },
            {
                kind: "set-up-outside-import",
                text: "The voice guide is a seed file outside the import.",
            },
        ],
    },
    {
        slug: "user-feedback",
        name: "User feedback",
        kind: "job",
        jobs: ["product", "founder"],
        headline: "What people say about your product",
        lede: "Listens on YouTube, Reddit, TikTok, X and LinkedIn for your product and its rivals, pulls out every opinion in the speaker's own words, and files it under the feature it is about.",
        does: [
            "Every opinion with its exact words and source",
            "Features ranked by what people like and dislike",
            "A weekly listen, with shifts flagged when they happen",
        ],
        makes: ["feedback", "feature", "mention"],
        uses: [],
        install: null,
        links: [
            {
                slug: "people-map",
                state: "planned",
                why: "Feedback isn't tied yet to the people and companies who gave it.",
            },
        ],
        gaps: [
            {
                kind: "missing-piece",
                text: "Feedback comes only from public posts. Support tickets, sales calls and app reviews are not read yet.",
            },
        ],
    },
    {
        slug: "pr-review",
        name: "PR review",
        kind: "job",
        jobs: ["dev"],
        headline: "Every pull request read before you are",
        lede: "Reviews each pull request against your team's own guidelines, sorts it by risk, and sends it to the one person who has to decide. Each finding is a card to call.",
        does: [
            "Pull requests sorted by who has to act",
            "Reviewed against the guidelines your team writes",
            "Findings fixed by an agent when you say so",
        ],
        makes: ["repo", "pr", "signal"],
        uses: [],
        install: null,
        basis: [
            {
                text: "Lemma's Reviews starter, offered in a new Lemma, gets every pull request a review on the day it opens.",
            },
        ],
        missing: ["GitLab"],
        links: [
            {
                slug: "people-map",
                state: "clashes",
                why: "Its person table is your team, under the same name as People map's.",
            },
        ],
        gaps: [
            {
                kind: "name-collision",
                text: "Its person table holds the team's GitHub and Slack handles, under the same name as People map's person, with different access rules.",
            },
            {
                kind: "made-for-one-person",
                text: "Some parts are one team's own: a check for internal data in pull requests and a seeded list of automations.",
            },
            {
                kind: "set-up-outside-import",
                text: "Automations and settings are written by a script after the import.",
            },
        ],
    },
    {
        slug: "project-management",
        name: "Project management",
        kind: "job",
        jobs: ["pm", "ops"],
        headline: "Work handed to people and agents alike",
        lede: "Projects and issues on one board, where an issue can go to a person or to an agent. Agents post progress as comments and hand the issue back when they need you.",
        does: [
            "Projects and issues, assigned to people or agents",
            "Progress, files and questions kept on the issue",
            "Reply to an issue and the agent picks up again",
        ],
        makes: ["project", "task"],
        uses: [],
        install: { owner: "deepak-jha-kgp", repo: "roundtable" },
        links: [],
        gaps: [
            {
                kind: "table-mismatch",
                text: "Assignees are names, not People, so a person here is not a person anywhere else.",
            },
            {
                kind: "set-up-outside-import",
                text: "Knowledge and playbooks sit in a seed folder outside the import.",
            },
            {
                kind: "missing-piece",
                text: "Its app manifest is in the older format, with no description.",
            },
        ],
    },
    {
        slug: "smart-inbox",
        name: "Smart inbox",
        kind: "tool",
        jobs: ["everyone", "founder", "sales"],
        headline: "Mail sorted the way you'd sort it",
        lede: "Sorts Gmail and Outlook into groups you describe in plain words, drafts replies in your voice, brings threads back when a follow-up is due, and briefs you before meetings.",
        does: [
            "Groups you write in a sentence, not filters",
            "Replies drafted in your voice; nothing sends alone",
            "Follow-ups timed, and a brief before each meeting",
        ],
        makes: ["person", "touch", "commitment", "meeting"],
        uses: [],
        private: true,
        install: null,
        links: [
            {
                slug: "people-map",
                state: "clashes",
                why: "Should use People map's tables; today it makes its own copies under the same names.",
            },
        ],
        gaps: [
            {
                kind: "table-mismatch",
                text: "Its person table is a smaller copy of People map's. Installed after People map, the import plans to drop 13 columns and stops unless you confirm.",
            },
            {
                kind: "name-collision",
                text: "15 functions, 4 agents, 10 workflows and 2 schedules share names with People map's; 6 of the functions and the triage agent differ, so whichever installs last wins.",
            },
        ],
    },
    {
        slug: "booking-page",
        name: "Booking page",
        kind: "tool",
        jobs: ["everyone", "sales", "founder"],
        headline: "A page where people book your time",
        lede: "Shows open times from your calendar, asks the person to confirm their email, sends the invite, and writes you a short brief on who's coming.",
        does: [
            "Open times kept in step with your calendar",
            "An email code before anything is booked",
            "A brief on who's coming, before the call",
        ],
        makes: [],
        uses: [],
        install: null,
        links: [
            {
                slug: "people-map",
                state: "planned",
                why: "A booking should add the person and the meeting there.",
            },
        ],
        gaps: [
            {
                kind: "made-for-one-person",
                text: "Made for one person: the app, a file and two agents' instructions name its maker.",
            },
            {
                kind: "set-up-outside-import",
                text: "The booking rules are written by a set-up script, not the import.",
            },
        ],
    },
];

/** Every plugin, in the catalog's order. */
export const PLUGINS: Plugin[] = CATALOG_ORDER.map((slug) => {
    const plugin = [...TEN, ...MORE_PLUGINS].find((p) => p.slug === slug);
    if (!plugin) throw new Error(`Plugin "${slug}" is ordered but not written.`);
    return plugin;
});

export function getPlugin(slug: string | null | undefined): Plugin | null {
    return PLUGINS.find((p) => p.slug === slug) ?? null;
}

/** What its bundle holds, for the plugins whose bundle has been read. */
export function partsOf(plugin: Plugin): BundleParts | null {
    return PARTS[plugin.slug] ?? null;
}

export function stageOf(plugin: Plugin): Stage {
    if (plugin.install) return "ready";
    if (partsOf(plugin) || plugin.runs) return "runs";
    if (plugin.basis?.length) return "partly";
    return "planned";
}

export const STAGE_NAME: Record<Stage, string> = {
    ready: "Ready to install",
    runs: "Runs today",
    partly: "Partly built",
    planned: "Not built yet",
};

const STAGE_RANK: Record<Stage, number> = {
    ready: 0,
    runs: 1,
    partly: 2,
    planned: 3,
};

/** Closest to installing first, then the catalog's order. */
export function byStage(a: Plugin, b: Plugin): number {
    return (
        STAGE_RANK[stageOf(a)] - STAGE_RANK[stageOf(b)] ||
        PLUGINS.indexOf(a) - PLUGINS.indexOf(b)
    );
}

/** Plugins that bring a table of this kind, closest to installing first. */
export function makersOf(kind: TableKind): Plugin[] {
    return PLUGINS.filter((p) => p.makes.includes(kind)).sort(byStage);
}

/** Plugins that use or read a table of this kind. */
export function usersOf(kind: TableKind): Plugin[] {
    return PLUGINS.filter(
        (p) => p.uses.includes(kind) || p.reads?.includes(kind),
    ).sort(byStage);
}

/** The import address the install button opens, or null until the plugin is
 *  published somewhere the install flow can read. */
export function installHref(plugin: Plugin): string | null {
    if (!plugin.install) return null;
    const { owner, repo } = plugin.install;
    return `/import/github/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
}

/** Everything a plugin lacks: the gaps written down, plus the ones the data
 *  already shows (not built, a service Lemma can't reach, a plugin it needs
 *  that can't install, no install source, an app outside the bundle, an
 *  account used but not asked for, an app with no cover). */
export function gapsOf(plugin: Plugin): Gap[] {
    const parts = partsOf(plugin);
    const stage = stageOf(plugin);
    const derived: Gap[] = [];
    if (stage === "planned")
        derived.push({
            kind: "not-built",
            text: "Not built yet: nothing here runs today.",
        });
    if (stage === "partly")
        derived.push({
            kind: "not-built",
            text: "Only part of it is built, so there is no plugin to install yet.",
        });
    if (plugin.missing?.length)
        derived.push({
            kind: "no-connector",
            text: `Lemma can't connect to ${list(plugin.missing)} yet.`,
        });
    const unready = (plugin.needs ?? [])
        .map((slug) => getPlugin(slug)!)
        .filter((p) => !p.install);
    if (unready.length)
        derived.push({
            kind: "needs-unready",
            text: `Needs ${list(unready.map((p) => p.name))}, which can't be installed yet.`,
        });
    if (stage === "runs" && !plugin.install)
        derived.push({
            kind: "no-install-source",
            text: "Not published anywhere the install flow can read, so it cannot be installed yet.",
        });
    if (parts) {
        const outside = parts.apps.filter((a) => !a.inBundle);
        if (outside.length)
            derived.push({
                kind: "app-outside-bundle",
                text: `${list(outside.map((a) => a.title))} ${outside.length > 1 ? "live" : "lives"} beside the bundle, not in it, so an install brings no screen.`,
            });
        const undeclared = parts.connectors.filter((c) => !c.declared);
        if (undeclared.length)
            derived.push({
                kind: "undeclared-account",
                text: `${list(undeclared.map((c) => c.service))} ${undeclared.length > 1 ? "are" : "is"} used but never asked for, so an install doesn't connect ${undeclared.length > 1 ? "them" : "it"}.`,
            });
        const bare = parts.apps.filter((a) => !a.cover && a.shape !== "page");
        if (bare.length)
            derived.push({
                kind: "no-cover",
                text: `No cover image yet for ${list(bare.map((a) => a.title))}; the pictures here are drawn stand-ins.`,
            });
    }
    return [...derived, ...plugin.gaps];
}

/** Plugins that link to this one without it linking back still belong on its
 *  page: a link is one fact about two plugins. */
export function linksOf(plugin: Plugin) {
    const own = plugin.links;
    const back = PLUGINS.flatMap((p) =>
        p.links
            .filter(
                (l) =>
                    l.slug === plugin.slug &&
                    !own.some((o) => o.slug === p.slug),
            )
            .map((l) => ({ ...l, slug: p.slug })),
    );
    return [...own, ...back].map((l) => ({
        ...l,
        plugin: getPlugin(l.slug)!,
    }));
}

/** The GitHub templates a plugin installs from or starts from. */
export function templatesUsedByPlugins(): Set<string> {
    return new Set(
        PLUGINS.flatMap((p) =>
            (p.basis ?? []).flatMap((b) => (b.template ? [b.template] : [])),
        ),
    );
}

export function list(items: string[]): string {
    if (items.length < 2) return items[0] ?? "";
    return items.slice(0, -1).join(", ") + " and " + items[items.length - 1];
}

export type { Plugin, PluginSlug };
