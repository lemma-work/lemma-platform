/** How the Lemma opens when it says hello: by the name they gave, if any. */
export function hello(name: string): string {
    return name.trim() ? `Nice to meet you, ${name.trim()}.` : "Welcome.";
}

/** What a Lemma is, in the words of the one saying it: a shared place for a
 *  team's Lemma, a place of one's own for a workspace of one. */
export function lemmaIs(shared: boolean, org: string): string {
    return shared
        ? `A Lemma is a shared place for one part of your work: an AI you can ask, apps for the work, and the people in it. Everyone at ${org} will be in this one.`
        : "A Lemma is a place for one part of your work: an AI you can ask, and apps for the work. This one is yours alone; when you want to work with someone, you’ll start another and invite them.";
}
