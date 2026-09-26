/** The first morning, before there is anywhere to stand.
 *
 *  Somebody signing in for the first time is in one of three situations, and
 *  the backend can tell them apart — the app simply never asked. They were
 *  invited and the invitation is sitting there unread; or their company is
 *  already on Lemma and their email domain is allowed in; or they are the
 *  first person here and something has to be made.
 *
 *  Only the last one is a decision, and it is the one this file is mostly
 *  about: a workspace for one person, or one for a company. Getting it wrong
 *  is expensive in both directions. A personal workspace for somebody whose
 *  colleagues are already here fragments the organization into one org per
 *  head; a company workspace opened to a domain nobody owns is worse, because
 *  `EMAIL_DOMAIN` on `gmail.com` means every Gmail address on earth may let
 *  itself in.
 *
 *  So the domain is a *guard and a default*, never the decision. The person is
 *  asked, the answer they are most likely to want is preselected, and the one
 *  answer that could open their workspace to the public is not offered at all
 *  where it would be unsafe. */

/** Mailbox providers anybody can sign up to.
 *
 *  Deliberately not a classifier. It cannot tell you that `alice@alice.design`
 *  is a personal address, and it is not asked to — the person answers that.
 *  Its one job is to refuse to hand a shared mailbox domain the keys, which is
 *  a question with a short and stable answer. */
const OPEN_TO_ANYONE = new Set([
    "gmail.com", "googlemail.com",
    "outlook.com", "hotmail.com", "hotmail.co.uk", "live.com", "msn.com",
    "yahoo.com", "yahoo.co.uk", "yahoo.co.in", "ymail.com", "rocketmail.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "gmx.com", "gmx.net", "mail.com", "mail.ru",
    "proton.me", "protonmail.com", "pm.me", "tutanota.com", "tuta.io",
    "zoho.com", "yandex.com", "yandex.ru", "fastmail.com", "hey.com",
    "qq.com", "163.com", "126.com", "sina.com", "naver.com", "daum.net",
    "rediffmail.com", "duck.com", "hushmail.com", "inbox.com",
]);

/** The domain half of an address, lowercased. Empty for anything that is not
 *  one — this reads a field a person typed somewhere else, and a screen that
 *  throws because an email came back malformed is a worse answer than a screen
 *  that treats it as unknown. */
export function domainOf(email: string | null | undefined): string {
    const said = (email ?? "").trim().toLowerCase();
    const at = said.lastIndexOf("@");
    /* A local part is required, not just an `@`: "@acme.com" carries a domain
       and is not an address, and trusting it would hand acme.com's keys to a
       string nobody could receive mail at. */
    if (at < 1) return "";
    const domain = said.slice(at + 1);
    return /^[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}$/.test(domain) ? domain : "";
}

/** Whether a workspace on this domain may be left open to it.
 *
 *  False for a shared mailbox provider, and false for a domain that could not
 *  be read — an unknown domain is not a domain somebody owns. */
export function canOpenToDomain(email: string | null | undefined): boolean {
    const domain = domainOf(email);
    return Boolean(domain) && !OPEN_TO_ANYONE.has(domain);
}

/** A first guess at what the company is called, for a field they can edit.
 *
 *  The first label and nothing cleverer. `ibm.com` comes out as "Ibm", which
 *  is wrong and is *fine*: this is a prefill in an editable box, not a name
 *  being written on anything. Title-casing every word to fix Ibm would break
 *  a longer name in a different way, and both are corrected by the person in
 *  the same keystroke. */
export function teamNameFor(email: string | null | undefined): string {
    const domain = domainOf(email);
    /* Nothing at all for a shared mailbox provider. The first label of
       `gmail.com` is "Gmail", and prefilling a company name box with the name
       of somebody's email provider is worse than leaving it empty: it is a
       suggestion that is confidently wrong, and it was offering to call a
       workspace Gmail. */
    if (!domain || OPEN_TO_ANYONE.has(domain)) return "";
    const first = domain.split(".")[0] ?? "";
    const words = first.replace(/[-_]+/g, " ").trim();
    return words.slice(0, 1).toUpperCase() + words.slice(1);
}

/** What the platform calls a workspace belonging to one person.
 *
 *  `"<who>'s Personal"` is not invented here — `shortOrgName` in `data/live.ts`
 *  already reads names in this shape back off the wire and shortens them for
 *  the switcher. Matching it means a workspace this app makes is indexed,
 *  displayed and shortened exactly like one the platform made. */
export function personalNameFor(name: string | null | undefined): string {
    /* The first name, and only a real one. The local part of an address is
       not a name — this screen offered somebody "deepakjha0196+99's Personal",
       which is a mailbox with a possessive stuck on it. Where there is no name
       to use, the workspace is called Personal and the sentence about it goes
       away rather than being filled in with the nearest string to hand. */
    const who = (name ?? "").trim().split(/\s+/)[0] ?? "";
    return who ? who + "'s Personal" : "Personal";
}

/** The heading over the arrival rungs.
 *
 *  "You've been invited" only when somebody did invite you. A domain match is
 *  not an invitation — nobody put this person's name down, their company is
 *  simply here already — and telling them they were invited sends them
 *  looking for an email that does not exist. */
export function arrivalHeading(invitations: number, matches: number): string {
    if (invitations > 0) return "You’ve been invited";
    if (matches > 0) return "Your team is already here";
    return "Who will you be working with?";
}

/** Which kind of organization is preselected.
 *
 *  On a local install nobody else can reach this server until Sharing is
 *  turned on, so "my team" would be a team of one for now — "just me" is the
 *  honest default there, whatever the domain says. Elsewhere a company
 *  address is a reason to expect colleagues and a shared mailbox is not. */
export function defaultOrgKind(email: string | null | undefined, local: boolean): "personal" | "team" {
    if (local) return "personal";
    return canOpenToDomain(email) ? "team" : "personal";
}
