/* The guide's table of contents. The contents list under the short version
   and the contents menu in the header both read it, and every section on the
   page takes its id from here, so a link can never point at nothing. */

export type GuideSection = { id: string; n: string; title: string };
export type GuideChapter = {
    id: string;
    n: number;
    /** The number as a word, for the chapter band. */
    word: string;
    title: string;
    /** One line on what the chapter is for, in the contents. */
    gist: string;
    sections: GuideSection[];
};

export const CHAPTERS: GuideChapter[] = [
    {
        id: "ch1",
        n: 1,
        word: "one",
        title: "How we work with AI today",
        gist: "Alone, in a chat, one question at a time.",
        sections: [
            { id: "week", n: "1.1", title: "A week you may recognise" },
            { id: "shared", n: "1.2", title: "Work is shared. The AI is not." },
            { id: "chat", n: "1.3", title: "Chat is where work starts" },
            { id: "steering", n: "1.4", title: "Steering" },
            { id: "learning", n: "1.5", title: "Learning from all of us" },
            { id: "map", n: "1.6", title: "Four ways to work with AI" },
        ],
    },
    {
        id: "ch2",
        n: 2,
        word: "two",
        title: "Why code got there first",
        gist: "Engineers had already given their work a shape.",
        sections: [
            { id: "code", n: "2.1", title: "Where it already works" },
            { id: "cheap", n: "2.2", title: "The doing got cheap" },
            { id: "factory", n: "2.3", title: "What we mean by a factory" },
            { id: "not", n: "2.4", title: "What a factory is not" },
            { id: "behind", n: "2.5", title: "Behind, not instead" },
        ],
    },
    {
        id: "ch3",
        n: 3,
        word: "three",
        title: "The shape of work",
        gist: "What a Lemma is made of, followed through one quote.",
        sections: [
            { id: "layers", n: "3.1", title: "Four layers" },
            { id: "things", n: "3.2", title: "Tables and files" },
            { id: "workers", n: "3.3", title: "Functions and agents" },
            { id: "automations", n: "3.4", title: "Automations" },
            { id: "apps", n: "3.5", title: "Apps" },
            { id: "rules", n: "3.6", title: "The rules and the record" },
            { id: "quote", n: "3.7", title: "One quote, start to finish" },
        ],
    },
    {
        id: "ch4",
        n: 4,
        word: "four",
        title: "Starting one",
        gist: "One job you repeat, handed over a step at a time.",
        sections: [
            { id: "small", n: "4.1", title: "Start with one job" },
            { id: "nodev", n: "4.2", title: "No developer required" },
            { id: "trust", n: "4.3", title: "Hand it over in steps" },
        ],
    },
    {
        id: "ch5",
        n: 5,
        word: "five",
        title: "Where people meet it",
        gist: "In the tools they already use.",
        sections: [
            { id: "ways", n: "5.1", title: "Not another tab" },
            { id: "outside", n: "5.2", title: "Outsiders, other Lemmas and your own AI" },
        ],
    },
    {
        id: "ch6",
        n: 6,
        word: "six",
        title: "Yours to keep",
        gist: "Open source, your choice of model, and a way out.",
        sections: [
            { id: "where", n: "6.1", title: "Runs where you choose" },
            { id: "model", n: "6.2", title: "Your choice of model" },
            { id: "export", n: "6.3", title: "Take it with you" },
        ],
    },
];

const SECTIONS = new Map(
    CHAPTERS.flatMap((chapter) => chapter.sections.map((section) => [section.id, section] as const)),
);

/** A section's number and title, by its id. Throws on an id the contents do not list. */
export function section(id: string): GuideSection {
    const found = SECTIONS.get(id);
    if (!found) throw new Error(`The guide has no section "${id}"`);
    return found;
}

/** A chapter by its number. */
export function chapter(n: number): GuideChapter {
    const found = CHAPTERS.find((each) => each.n === n);
    if (!found) throw new Error(`The guide has no chapter ${n}`);
    return found;
}
