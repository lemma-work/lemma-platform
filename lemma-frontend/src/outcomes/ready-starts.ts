/** Ready starts for a new Lemma: a name and what it does, filled in with
 *  one tap. Pure, so the phone app offers the same ones
 *  (`lemma-mobile/src/areas/more/begin/new-lemma.tsx`). */
export const READY: readonly { name: string; purpose: string }[] = [
    { name: "Hiring", purpose: "Takes every candidate from the careers form to an offer" },
    { name: "Support", purpose: "Answers every customer question, and raises the hard ones" },
    { name: "Follow-ups", purpose: "Follows up every lead before it goes cold" },
    { name: "Reviews", purpose: "Reviews every pull request, so nobody waits" },
];
