/** Whether what somebody typed is the teammate's name, for the one act that
 *  asks for it: deleting.
 *
 *  The point of typing the name is that the person has read whose page they
 *  are on — not that they can reproduce its capitals. So case is ignored and
 *  so is the space a phone keyboard adds after a word, and runs of spaces
 *  count as one. Everything else has to match: "Ki" is not "Kit", and an
 *  empty box never names anybody, even a teammate whose name trims to
 *  nothing. */
export function namesTeammate(typed: string, name: string): boolean {
    const wanted = tidy(name);
    return wanted.length > 0 && tidy(typed) === wanted;
}

function tidy(text: string): string {
    return text.trim().replace(/\s+/g, " ").toLocaleLowerCase();
}
